"""回补历史 call_logs 中缺失的计费字段（estimated_cost / billing_currency / billing_rule / billing_meta）。

适用场景：在 config.yaml 新增计费规则（如 GLM-5.2）后，规则生效前产生的成功调用记录
其 estimated_cost 等字段为 NULL。本脚本用当前计费规则对这些记录重新计算并回写。

用法：
    # 干跑（只打印，不写库）
    python -m app.billing_backfill --dry-run

    # 正式回补
    python -m app.billing_backfill

    # 指定配置文件 / 数据库
    python -m app.billing_backfill --config /path/to/config.yaml

    # 仅回补指定 provider_model（默认回补所有 estimated_cost IS NULL 的成功记录）
    python -m app.billing_backfill --provider-model glm-5.2
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.billing import calculate_request_cost
from app.config import load_config


def _parse_created_at(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _build_usage_raw(row: dict[str, Any]) -> dict[str, Any]:
    """根据已存储的 token 列重建 usage_raw，供 calculate_request_cost 使用。"""
    pt = int(row.get("prompt_tokens") or 0)
    ct = int(row.get("completion_tokens") or 0)
    tt = int(row.get("total_tokens") or (pt + ct))
    cached = int(row.get("cached_input_tokens") or 0)
    cache_write = int(row.get("cache_write_tokens") or 0)
    usage: dict[str, Any] = {
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "total_tokens": tt,
    }
    # 仅当有缓存命中时写入详情，避免对无缓存记录产生干扰
    if cached > 0:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
        usage["cached_input_tokens"] = cached
    if cache_write > 0:
        usage.setdefault("prompt_tokens_details", {})["cache_creation_input_tokens"] = cache_write
        usage["cache_write_input_tokens"] = cache_write
    return usage


def _row_factory(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def backfill(
    db_path: str,
    billing_config: Any,
    *,
    provider_model: str | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = _row_factory

    where = "status = 'success' AND estimated_cost IS NULL"
    params: list[Any] = []
    if provider_model:
        where += " AND provider_model = ?"
        params.append(provider_model)

    rows = conn.execute(
        f"SELECT id, provider, provider_model, prompt_tokens, completion_tokens, "
        f"total_tokens, cached_input_tokens, cache_write_tokens, created_at, log_meta "
        f"FROM call_logs WHERE {where}",
        params,
    ).fetchall()
    conn.close()

    stats = {"scanned": 0, "updated": 0, "skipped_no_rule": 0}
    updates: list[tuple[float, str | None, str | None, str | None, int]] = []

    for row in rows:
        stats["scanned"] += 1
        provider_name = row.get("provider")
        model = row.get("provider_model")
        pt = int(row.get("prompt_tokens") or 0)
        ct = int(row.get("completion_tokens") or 0)
        created_at = _parse_created_at(row.get("created_at"))

        # 优先从 log_meta 中恢复原始 usage（含厂商特定字段），无法解析时用列重建
        usage_raw: dict[str, Any] | None = None
        log_meta_raw = row.get("log_meta")
        if log_meta_raw:
            try:
                meta = json.loads(log_meta_raw)
                if isinstance(meta, dict):
                    usage_raw = (
                        (meta.get("response") or {}).get("usage_raw")
                        or (meta.get("stream") or {}).get("usage_raw")
                    )
            except (TypeError, ValueError):
                usage_raw = None
        if not isinstance(usage_raw, dict):
            usage_raw = _build_usage_raw(row)

        result = calculate_request_cost(
            billing_config=billing_config,
            provider_name=provider_name,
            provider_model=model,
            prompt_tokens=pt,
            completion_tokens=ct,
            usage_raw=usage_raw,
            created_at=created_at,
        )
        if result is None:
            stats["skipped_no_rule"] += 1
            continue

        estimated_cost = float((result.get("costs") or {}).get("total_cost") or 0)
        currency = result.get("currency")
        patterns = [str(p) for p in (result.get("rule_model_patterns") or [])]
        billing_rule = f"{result.get('rule_provider')}:{'|'.join(patterns)}"
        try:
            billing_meta_json = json.dumps(result, ensure_ascii=False)
        except (TypeError, ValueError):
            billing_meta_json = json.dumps({"_error": "backfill 序列化失败"}, ensure_ascii=False)
        updates.append((estimated_cost, currency, billing_rule, billing_meta_json, row["id"]))
        stats["updated"] += 1

    if dry_run:
        print(f"[DRY-RUN] 扫描 {stats['scanned']} 条，将回写 {stats['updated']} 条，"
              f"跳过(无匹配规则) {stats['skipped_no_rule']} 条")
        for estimated_cost, currency, billing_rule, _, rid in updates[:10]:
            print(f"  id={rid} cost={estimated_cost} currency={currency} rule={billing_rule}")
        if len(updates) > 10:
            print(f"  ... 其余 {len(updates) - 10} 条略")
        return stats

    if updates:
        conn = sqlite3.connect(db_path)
        conn.executemany(
            "UPDATE call_logs SET estimated_cost = ?, billing_currency = ?, "
            "billing_rule = ?, billing_meta = ? WHERE id = ?",
            updates,
        )
        conn.commit()
        conn.close()

    print(f"[DONE] 扫描 {stats['scanned']} 条，回写 {stats['updated']} 条，"
          f"跳过(无匹配规则) {stats['skipped_no_rule']} 条")
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="回补 call_logs 中缺失的计费字段")
    parser.add_argument("--config", dest="config_path", default=None,
                        help="配置文件路径，默认读取 LLM_ROUTER_CONFIG 或 config.yaml")
    parser.add_argument("--provider-model", default=None,
                        help="仅回补指定 provider_model（默认全部 estimated_cost IS NULL 的成功记录）")
    parser.add_argument("--dry-run", action="store_true", help="只打印不写库")
    args = parser.parse_args()

    app_config = load_config(args.config_path)
    if not app_config.billing.enabled:
        print("[ERROR] billing.enabled = false，无法回补", file=sys.stderr)
        return 1

    db_path = app_config.log.db_path
    if not Path(db_path).exists():
        print(f"[ERROR] 数据库不存在: {db_path}", file=sys.stderr)
        return 1

    backfill(
        db_path=db_path,
        billing_config=app_config.billing,
        provider_model=args.provider_model,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
