#!/usr/bin/env python3
"""
sync_models_dev.py — 从 models.dev 同步模型价格到 config.yaml 的 billing rules。

models.dev 是社区维护的 AI 模型元数据库，api.json 中包含 provider/model/价格/上下文等。
本脚本：
  1. 拉取（或读取本地缓存的）api.json
  2. 对 config.yaml 中每个 billing rule，按 provider + provider_model 在 catalog 中查找
  3. 默认仅打印对比表，--apply 才会写回 config.yaml
  4. 写回时只覆盖 input_price/output_price/cache_read_price/cache_write_price，
     保留 token_tiers/time_windows/source_urls/note 等字段，并在 note 中追加同步时间戳

用法：
  python scripts/sync_models_dev.py
  python scripts/sync_models_dev.py --apply
  python scripts/sync_models_dev.py --usd-to-cny 7.2 --apply
  python scripts/sync_models_dev.py --config /path/to/config.yaml
  python scripts/sync_models_dev.py --cache .cache/models-dev-api.json
  python scripts/sync_models_dev.py --refresh

注意：
  - models.dev 价格单位是 USD / 1M tokens；config.yaml 是 CNY / 1M tokens
  - 中国厂商（deepseek/qwen/zhipu/xiaomi/doubao/moonshot）在 models.dev 不一定收录
  - 未匹配的 rule 会跳过；汇率默认 7.2，可用 --usd-to-cny 覆盖
  - --apply 会先备份 config.yaml 到 config.yaml.bak，再写入
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

import yaml

API_URL = "https://models.dev/api.json"
DEFAULT_CONFIG = "config.yaml"
DEFAULT_CACHE = ".cache/models-dev-api.json"
DEFAULT_USD_TO_CNY = 7.2
CACHE_TTL_SECONDS = 24 * 3600  # 24 小时内复用缓存
MATCH_MODE_EXACT = "exact"
MATCH_MODE_PREFIX = "prefix"
MATCH_MODE_CONTAINS = "contains"


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def load_api(cache_path: Path, refresh: bool) -> dict[str, Any]:
    """加载 api.json：缓存有效则用缓存，否则从 models.dev 拉取。"""
    use_cache = False
    if cache_path.exists() and not refresh:
        age = time.time() - cache_path.stat().st_mtime
        if age < CACHE_TTL_SECONDS:
            use_cache = True

    if use_cache:
        log(f"[cache] 读取本地缓存：{cache_path}")
        with cache_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    log(f"[fetch] 从 models.dev 拉取：{API_URL}")
    req = Request(API_URL, headers={"User-Agent": "llm-api-router-sync/1.0"})
    with urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log(f"[cache] 已写入：{cache_path}")
    return data


def normalize(s: str | None) -> str:
    return (s or "").strip().lower()


def iter_api_entries(api: dict[str, Any]):
    """枚举 api.json 中的 (provider_id, model_id, entry)。

    结构：{ "<provider_id>": { "models": { "<provider>/<model_id>": { ... } } } }
    model_id 取 "<provider>/<model>" 的后半部分。
    """
    for provider_id, provider_data in api.items():
        if not isinstance(provider_data, dict):
            continue
        models = provider_data.get("models")
        if not isinstance(models, dict):
            continue
        for full_id, entry in models.items():
            if not isinstance(entry, dict):
                continue
            # full_id 形如 "openai/gpt-4o"；取后半段作为本路由用的 model_id
            model_id = full_id.split("/", 1)[1] if "/" in full_id else full_id
            yield provider_id, model_id, full_id, entry


def find_api_entry(
    api: dict[str, Any],
    provider: str,
    model_patterns: list[str],
    match_mode: str,
) -> tuple[str, str, str, dict[str, Any]] | None:
    """在 api.json 中按 provider + 模型匹配规则找到第一个匹配项。"""
    provider_norm = normalize(provider)
    patterns = [normalize(p) for p in model_patterns if p]
    if not patterns:
        return None

    for cat_provider, cat_model, cat_full_id, entry in iter_api_entries(api):
        if normalize(cat_provider) != provider_norm:
            continue
        for pattern in patterns:
            if match_mode == MATCH_MODE_PREFIX:
                if cat_model.startswith(pattern):
                    return cat_provider, cat_model, cat_full_id, entry
            elif match_mode == MATCH_MODE_CONTAINS:
                if pattern in cat_model:
                    return cat_provider, cat_model, cat_full_id, entry
            else:  # exact
                if cat_model == pattern:
                    return cat_provider, cat_model, cat_full_id, entry
    return None


def extract_prices(entry: dict[str, Any]) -> dict[str, float | None] | None:
    """从 catalog 条目中提取价格（USD / 1M tokens）。"""
    cost = entry.get("cost")
    if not isinstance(cost, dict):
        return None
    return {
        "input": cost.get("input"),
        "output": cost.get("output"),
        "cache_read": cost.get("cache_read"),
        "cache_write": cost.get("cache_write"),
    }


def to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f < 0:
        return None
    return f


def convert_to_cny(usd_price: float | None, rate: float) -> float | None:
    if usd_price is None:
        return None
    return round(usd_price * rate, 6)


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class _IndentDumper(yaml.SafeDumper):
    """让序列项相对父键多缩进 2 空格（匹配 config.yaml 既有风格）。"""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> Any:  # noqa: ARG002
        return super().increase_indent(flow, False)


def save_config(path: Path, data: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(
            data,
            f,
            Dumper=_IndentDumper,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            indent=2,
            width=1000,  # 避免长字符串被折行
        )


def format_price(v: float | None) -> str:
    if v is None:
        return "—"
    if v == 0:
        return "0"
    if v < 0.01:
        return f"{v:.6f}"
    return f"{v:.4f}"


def build_comparison(
    config: dict[str, Any],
    api: dict[str, Any],
    usd_to_cny: float,
) -> list[dict[str, Any]]:
    """对 config 中每个 billing rule 与 api 对比，返回对比行列表。"""
    billing = config.get("billing") or {}
    rules = billing.get("rules") or []
    rows: list[dict[str, Any]] = []

    for idx, rule in enumerate(rules):
        provider = rule.get("provider", "")
        patterns = rule.get("provider_model_patterns") or []
        match_mode = rule.get("match_mode", MATCH_MODE_EXACT)
        model_label = "/".join(patterns) if patterns else "(no patterns)"

        row = {
            "idx": idx,
            "provider": provider,
            "model": model_label,
            "match_mode": match_mode,
            "old_input": rule.get("input_price"),
            "old_output": rule.get("output_price"),
            "old_cache_read": rule.get("cache_read_price"),
            "old_cache_write": rule.get("cache_write_price"),
            "new_input": None,
            "new_output": None,
            "new_cache_read": None,
            "new_cache_write": None,
            "matched_provider": None,
            "matched_model": None,
            "matched_full_id": None,
            "status": "no-match",
        }

        match = find_api_entry(api, provider, patterns, match_mode)
        if match is None:
            rows.append(row)
            continue

        cat_provider, cat_model, cat_full_id, entry = match
        prices = extract_prices(entry)
        if prices is None:
            row["status"] = "no-pricing"
            row["matched_provider"] = cat_provider
            row["matched_model"] = cat_model
            row["matched_full_id"] = cat_full_id
            rows.append(row)
            continue

        row["matched_provider"] = cat_provider
        row["matched_model"] = cat_model
        row["matched_full_id"] = cat_full_id
        row["new_input"] = convert_to_cny(to_float(prices["input"]), usd_to_cny)
        row["new_output"] = convert_to_cny(to_float(prices["output"]), usd_to_cny)
        row["new_cache_read"] = convert_to_cny(to_float(prices["cache_read"]), usd_to_cny)
        row["new_cache_write"] = convert_to_cny(to_float(prices["cache_write"]), usd_to_cny)
        row["status"] = "matched"
        rows.append(row)

    return rows


def print_comparison(rows: list[dict[str, Any]], usd_to_cny: float) -> None:
    print(f"\nmodels.dev → config.yaml 价格对比（USD→CNY 汇率: {usd_to_cny}）\n")
    header = (
        f"{'#':>3}  {'provider':<10}  {'model':<32}  "
        f"{'in_old':>9}  {'in_new':>9}  {'out_old':>9}  {'out_new':>9}  "
        f"{'cR_new':>9}  {'cW_new':>9}  {'status':<11}  {'matched':<28}"
    )
    print(header)
    print("-" * len(header))
    for r in rows:
        matched = ""
        if r["matched_full_id"]:
            matched = r["matched_full_id"]
        print(
            f"{r['idx']:>3}  {r['provider']:<10}  {r['model'][:32]:<32}  "
            f"{format_price(r['old_input']):>9}  {format_price(r['new_input']):>9}  "
            f"{format_price(r['old_output']):>9}  {format_price(r['new_output']):>9}  "
            f"{format_price(r['new_cache_read']):>9}  {format_price(r['new_cache_write']):>9}  "
            f"{r['status']:<11}  {matched[:28]:<28}"
        )
    print()


def apply_to_config(
    config: dict[str, Any],
    rows: list[dict[str, Any]],
    usd_to_cny: float,
) -> int:
    """将匹配到的价格写回 config；返回修改的 rule 数。"""
    billing = config.setdefault("billing", {})
    rules = billing.setdefault("rules", [])
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    changed = 0

    for r in rows:
        if r["status"] != "matched":
            continue
        idx = r["idx"]
        if idx >= len(rules):
            continue
        rule = rules[idx]

        # 只在拿到值时才覆盖
        updates = [
            ("input_price", r["new_input"]),
            ("output_price", r["new_output"]),
            ("cache_read_price", r["new_cache_read"]),
            ("cache_write_price", r["new_cache_write"]),
        ]
        modified = False
        for key, val in updates:
            if val is None:
                continue
            if rule.get(key) != val:
                rule[key] = val
                modified = True

        if not modified:
            continue

        # 追加同步来源到 source_urls，标注 note
        source_urls = rule.setdefault("source_urls", [])
        models_dev_url = f"https://models.dev/{r['matched_full_id']}"
        if models_dev_url not in source_urls:
            source_urls.append(models_dev_url)
        # 主 source_url 保留原值；不强行覆盖
        note = rule.get("note") or ""
        sync_tag = f"[models.dev sync {now} USD×{usd_to_cny}]"
        if sync_tag not in note:
            rule["note"] = f"{note} {sync_tag}".strip()
        changed += 1

    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 models.dev 同步模型价格到 config.yaml")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help=f"config.yaml 路径（默认：{DEFAULT_CONFIG}）")
    parser.add_argument("--cache", default=DEFAULT_CACHE, help=f"api.json 本地缓存路径（默认：{DEFAULT_CACHE}）")
    parser.add_argument("--refresh", action="store_true", help="忽略缓存，强制重新拉取 api.json")
    parser.add_argument(
        "--usd-to-cny",
        type=float,
        default=DEFAULT_USD_TO_CNY,
        help=f"USD→CNY 汇率（默认：{DEFAULT_USD_TO_CNY}）",
    )
    parser.add_argument("--apply", action="store_true", help="将匹配到的价格写入 config.yaml（默认仅对比）")
    args = parser.parse_args(argv)

    config_path = Path(args.config).resolve()
    cache_path = Path(args.cache).resolve()

    if not config_path.exists():
        log(f"[error] config.yaml 不存在：{config_path}")
        return 2

    try:
        api = load_api(cache_path, args.refresh)
    except URLError as e:
        log(f"[error] 拉取 api.json 失败：{e}")
        log(f"[hint]  可手动下载 {API_URL} 到 {cache_path} 后重试")
        return 3
    except json.JSONDecodeError as e:
        log(f"[error] api.json 解析失败：{e}")
        return 4

    config = load_config(config_path)
    rows = build_comparison(config, api, args.usd_to_cny)
    print_comparison(rows, args.usd_to_cny)

    matched = sum(1 for r in rows if r["status"] == "matched")
    no_pricing = sum(1 for r in rows if r["status"] == "no-pricing")
    no_match = sum(1 for r in rows if r["status"] == "no-match")
    log(
        f"[summary] 共 {len(rows)} 条 rule：匹配 {matched}，"
        f"无价格 {no_pricing}，未匹配 {no_match}"
    )

    if not args.apply:
        if matched > 0:
            log("[hint] 加 --apply 将把匹配到的价格写入 config.yaml（会先备份到 config.yaml.bak）")
        return 0

    changed = apply_to_config(config, rows, args.usd_to_cny)
    if changed == 0:
        log("[summary] 没有需要更新的 rule")
        return 0

    log(
        "[warn] --apply 会用 PyYAML 重新序列化 config.yaml：注释会丢失，"
        "行内列表会被展开，引号风格可能变化。原文件会备份到 .bak。"
    )
    log("[warn] 若在意格式，建议按上方对比表手动修改，不使用 --apply。")

    # 备份原文件
    backup = config_path.with_suffix(config_path.suffix + ".bak")
    save_config(backup, load_config(config_path))
    log(f"[backup] 原配置已备份到：{backup}")

    save_config(config_path, config)
    log(f"[done] 已更新 {changed} 条 rule，写入：{config_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
