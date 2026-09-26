from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.config import BillingConfig, BillingRuleConfig, BillingTimeWindowConfig, BillingTokenTierConfig
from app.pricing_catalog import PricingCatalog, RemotePrice, normalize


def _normalize_text(value: str | None) -> str:
    return (value or "").strip().lower()


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_nested(mapping: Any, *paths: tuple[str, ...]) -> Any:
    for path in paths:
        cur = mapping
        ok = True
        for key in path:
            if not isinstance(cur, dict):
                ok = False
                break
            cur = cur.get(key)
        if ok and cur is not None:
            return cur
    return None


def _parse_time(value: str | None) -> time | None:
    if not value:
        return None
    try:
        return time.fromisoformat(value)
    except ValueError:
        return None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class BillingTokenBreakdown:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    regular_input_tokens: int = 0


def extract_billing_tokens(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    usage_raw: dict[str, Any] | None,
) -> BillingTokenBreakdown:
    usage = usage_raw if isinstance(usage_raw, dict) else {}
    cached = _to_int(
        _get_nested(
            usage,
            ("cached_input_tokens",),
            ("cache_read_input_tokens",),
            ("prompt_cache_hit_tokens",),
            ("input_cached_tokens",),
            ("prompt_tokens_details", "cached_tokens"),
            ("prompt_tokens_details", "cache_read_input_tokens"),
        )
    )
    cache_write = _to_int(
        _get_nested(
            usage,
            ("cache_write_input_tokens",),
            ("cache_creation_input_tokens",),
            ("prompt_cache_write_tokens",),
            ("input_cache_write_tokens",),
            ("prompt_tokens_details", "cache_creation_input_tokens"),
            ("prompt_tokens_details", "cache_write_input_tokens"),
        )
    )
    cached = max(0, min(cached, prompt_tokens))
    cache_write = max(0, min(cache_write, max(prompt_tokens - cached, 0)))
    regular = max(prompt_tokens - cached - cache_write, 0)
    return BillingTokenBreakdown(
        prompt_tokens=max(prompt_tokens, 0),
        completion_tokens=max(completion_tokens, 0),
        cached_input_tokens=cached,
        cache_write_tokens=cache_write,
        regular_input_tokens=regular,
    )


def _provider_matches(rule: BillingRuleConfig, provider_name: str | None) -> bool:
    provider = _normalize_text(provider_name)
    if not provider:
        return False
    candidates = {_normalize_text(rule.provider), *(_normalize_text(v) for v in rule.provider_aliases)}
    return provider in candidates


def _model_matches(rule: BillingRuleConfig, provider_model: str | None) -> bool:
    patterns = [p for p in rule.provider_model_patterns if p]
    model = _normalize_text(provider_model)
    if not patterns:
        return True
    if not model:
        return False
    mode = _normalize_text(rule.match_mode) or "exact"
    if mode == "prefix":
        return any(model.startswith(_normalize_text(pattern)) for pattern in patterns)
    if mode == "contains":
        return any(_normalize_text(pattern) in model for pattern in patterns)
    return any(model == _normalize_text(pattern) for pattern in patterns)


def _window_matches(window: BillingTimeWindowConfig, when_utc: datetime) -> bool:
    try:
        tz = ZoneInfo(window.timezone or "UTC")
    except Exception:
        tz = timezone.utc
    local_dt = when_utc.astimezone(tz)
    start_at = _parse_datetime(window.start_at)
    end_at = _parse_datetime(window.end_at)
    if start_at and when_utc < start_at.astimezone(timezone.utc):
        return False
    if end_at and when_utc > end_at.astimezone(timezone.utc):
        return False
    if window.weekdays and local_dt.weekday() not in window.weekdays:
        return False
    start_time = _parse_time(window.start_time)
    end_time = _parse_time(window.end_time)
    if start_time and end_time:
        current = local_dt.timetz().replace(tzinfo=None)
        if start_time <= end_time:
            if not (start_time <= current <= end_time):
                return False
        else:
            if not (current >= start_time or current <= end_time):
                return False
    elif start_time:
        if local_dt.timetz().replace(tzinfo=None) < start_time:
            return False
    elif end_time:
        if local_dt.timetz().replace(tzinfo=None) > end_time:
            return False
    return True


def _token_tier_matches(tier: BillingTokenTierConfig, prompt_tokens: int) -> bool:
    if tier.min_prompt_tokens is not None and prompt_tokens < tier.min_prompt_tokens:
        return False
    if tier.max_prompt_tokens is not None and prompt_tokens > tier.max_prompt_tokens:
        return False
    return True


def _resolve_prices(
    rule: BillingRuleConfig,
    when_utc: datetime,
    prompt_tokens: int,
    base_input: float | None,
    base_output: float | None,
    base_cache_read: float | None,
    base_cache_write: float | None,
) -> tuple[float | None, float | None, float | None, float | None, str | None, str | None]:
    """在基础价（yaml 显式价或远程补价）之上应用 token 分档与时段价。"""
    input_price = base_input
    output_price = base_output
    cache_read_price = base_cache_read
    cache_write_price = base_cache_write
    matched_tier_name: str | None = None
    if rule.token_tiers:
        matched_tier = next((tier for tier in rule.token_tiers if _token_tier_matches(tier, prompt_tokens)), None)
        if matched_tier is None:
            return input_price, output_price, cache_read_price, cache_write_price, None, None
        matched_tier_name = matched_tier.name
        if matched_tier.input_price is not None:
            input_price = matched_tier.input_price
        if matched_tier.output_price is not None:
            output_price = matched_tier.output_price
        if matched_tier.cache_read_price is not None:
            cache_read_price = matched_tier.cache_read_price
        if matched_tier.cache_write_price is not None:
            cache_write_price = matched_tier.cache_write_price
    matched_window_name: str | None = None
    for window in rule.time_windows:
        if not _window_matches(window, when_utc):
            continue
        matched_window_name = window.name
        if window.input_price is not None:
            input_price = window.input_price
        if window.output_price is not None:
            output_price = window.output_price
        if window.cache_read_price is not None:
            cache_read_price = window.cache_read_price
        if window.cache_write_price is not None:
            cache_write_price = window.cache_write_price
        break
    return input_price, output_price, cache_read_price, cache_write_price, matched_window_name, matched_tier_name


# models.dev 的价格基准固定为「每 1M tokens」
REMOTE_PRICE_UNIT = 1_000_000


def _remote_scale(unit: int) -> float:
    """把「每 1M tokens」的远程价换算到指定 unit 基准的倍率。"""
    return max(int(unit), 1) / REMOTE_PRICE_UNIT


def _scale_price(value: float | None, scale: float) -> float | None:
    return None if value is None else value * scale


def _resolve_remote_price(
    pricing_catalog: PricingCatalog | None,
    provider_name: str | None,
    rule: BillingRuleConfig | None,
    model_names: list[str | None],
    *,
    allow_prefix: bool = True,
) -> RemotePrice | None:
    """在远程价格目录中查价；provider 优先按规则显式指定，再按名称/别名映射。"""
    if pricing_catalog is None or not pricing_catalog.enabled or not pricing_catalog.loaded:
        return None
    names: list[str | None] = []
    if rule is not None:
        names.append(rule.models_dev_provider)
        names.append(rule.provider)
        names.extend(rule.provider_aliases)
    names.append(provider_name)
    candidates = pricing_catalog.provider_candidates(names)
    if not candidates:
        return None
    for model in model_names:
        price = pricing_catalog.lookup(candidates, model, allow_prefix=allow_prefix)
        if price is not None:
            return price
    return None


def calculate_request_cost(
    *,
    billing_config: BillingConfig | None,
    provider_name: str | None,
    provider_model: str | None,
    prompt_tokens: int,
    completion_tokens: int,
    usage_raw: dict[str, Any] | None,
    created_at: datetime | None = None,
    pricing_catalog: PricingCatalog | None = None,
) -> dict[str, Any] | None:
    if billing_config is None or not billing_config.enabled:
        return None
    when_utc = created_at or datetime.now(timezone.utc)
    if when_utc.tzinfo is None:
        when_utc = when_utc.replace(tzinfo=timezone.utc)

    remote_cfg = billing_config.remote_pricing
    matched_rule: BillingRuleConfig | None = None
    for rule in billing_config.rules:
        if _provider_matches(rule, provider_name) and _model_matches(rule, provider_model):
            matched_rule = rule
            break

    tokens = extract_billing_tokens(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        usage_raw=usage_raw,
    )
    remote: RemotePrice | None = None
    source = "yaml"

    if matched_rule is not None:
        base_input = matched_rule.input_price
        base_output = matched_rule.output_price
        base_cache_read = matched_rule.cache_read_price
        base_cache_write = matched_rule.cache_write_price
        # yaml 显式写价优先（含显式 0.0 的免费模型）；仅主价缺失时才用远程补空缺。
        # match_mode=exact 的规则只允许精确补价，避免被相近模型的前缀价格误补。
        if base_input is None or base_output is None:
            remote = _resolve_remote_price(
                pricing_catalog,
                provider_name,
                matched_rule,
                [provider_model, *matched_rule.provider_model_patterns],
                allow_prefix=_normalize_text(matched_rule.match_mode) != "exact",
            )
            if remote is not None:
                # 远程价固定是「每 1M tokens」，需按本规则的 unit 换算后再参与计费。
                scale = _remote_scale(matched_rule.unit)
                if base_input is None:
                    base_input = _scale_price(remote.input, scale)
                if base_output is None:
                    base_output = _scale_price(remote.output, scale)
                if base_cache_read is None:
                    base_cache_read = _scale_price(remote.cache_read, scale)
                if base_cache_write is None:
                    base_cache_write = _scale_price(remote.cache_write, scale)
                source = "yaml+models.dev"
        # 远程也没补到 → 回落 0.0，保持「命中规则必产生一条计费记录」的旧行为。
        if base_input is None:
            base_input = 0.0
        if base_output is None:
            base_output = 0.0

        input_price, output_price, cache_read_price, cache_write_price, matched_window_name, matched_tier_name = _resolve_prices(
            matched_rule, when_utc, tokens.prompt_tokens, base_input, base_output, base_cache_read, base_cache_write
        )
        if matched_rule.token_tiers and matched_tier_name is None:
            return None
        unit = max(int(matched_rule.unit), 1)
        currency = matched_rule.currency or billing_config.default_currency
        rule_provider = matched_rule.provider
        rule_model_patterns = matched_rule.provider_model_patterns
        match_mode = matched_rule.match_mode
        source_url = matched_rule.source_url
        source_urls = matched_rule.source_urls
        note = matched_rule.note
    else:
        # yaml 无规则：按远程目录合成（受开关与匹配门槛限制，命中不了则保持不记费）。
        if not remote_cfg.enabled or not remote_cfg.synthesize_unknown_models:
            return None
        remote = _resolve_remote_price(pricing_catalog, provider_name, None, [provider_model])
        if remote is None or (remote.input is None and remote.output is None):
            return None
        input_price = remote.input if remote.input is not None else 0.0
        output_price = remote.output if remote.output is not None else 0.0
        cache_read_price = remote.cache_read
        cache_write_price = remote.cache_write
        matched_window_name = None
        matched_tier_name = None
        unit = REMOTE_PRICE_UNIT
        currency = billing_config.default_currency
        rule_provider = f"models.dev:{remote.provider_id}"
        rule_model_patterns = [remote.model_id]
        match_mode = remote.match_kind
        source_url = remote.source_url
        source_urls = [remote.source_url]
        note = f"models.dev 后台价格（USD×{remote.usd_to_cny}）"
        source = "models.dev"

    effective_cache_read_price = input_price if cache_read_price is None else cache_read_price
    effective_cache_write_price = input_price if cache_write_price is None else cache_write_price

    regular_input_cost = tokens.regular_input_tokens * input_price / unit
    output_cost = tokens.completion_tokens * output_price / unit
    cache_read_cost = tokens.cached_input_tokens * effective_cache_read_price / unit
    cache_write_cost = tokens.cache_write_tokens * effective_cache_write_price / unit
    total_cost = round(
        regular_input_cost + output_cost + cache_read_cost + cache_write_cost,
        billing_config.round_digits,
    )

    return {
        "currency": currency,
        "unit": unit,
        "provider": provider_name,
        "provider_model": provider_model,
        "rule_provider": rule_provider,
        "rule_model_patterns": rule_model_patterns,
        "match_mode": match_mode,
        "matched_window": matched_window_name,
        "matched_token_tier": matched_tier_name,
        "source_url": source_url,
        "source_urls": source_urls,
        "note": note,
        "source": source,
        "remote_provider": remote.provider_id if remote is not None else None,
        "remote_model": remote.model_id if remote is not None else None,
        "usd_to_cny": remote.usd_to_cny if remote is not None else None,
        "prompt_tokens": tokens.prompt_tokens,
        "completion_tokens": tokens.completion_tokens,
        "cached_input_tokens": tokens.cached_input_tokens,
        "cache_write_tokens": tokens.cache_write_tokens,
        "regular_input_tokens": tokens.regular_input_tokens,
        "prices": {
            "input_price": _to_float(input_price),
            "output_price": _to_float(output_price),
            "cache_read_price": _to_float(effective_cache_read_price),
            "cache_write_price": _to_float(effective_cache_write_price),
        },
        "costs": {
            "regular_input_cost": round(regular_input_cost, billing_config.round_digits),
            "output_cost": round(output_cost, billing_config.round_digits),
            "cache_read_cost": round(cache_read_cost, billing_config.round_digits),
            "cache_write_cost": round(cache_write_cost, billing_config.round_digits),
            "total_cost": total_cost,
        },
    }


def describe_billing_rules(
    billing_config: BillingConfig | None,
    pricing_catalog: PricingCatalog | None = None,
) -> list[dict[str, Any]]:
    """列出 config.yaml 计费规则及其生效价来源，供价格页面展示。

    effective 取值：yaml（yaml 价生效，含基础价齐全或由分档/时段价供价）/
    yaml+models.dev（基础价缺任一、由远程补）/
    yaml-missing-price（基础价缺失且远程未匹配 → 按 0 计费）。
    """
    if billing_config is None:
        return []
    rows: list[dict[str, Any]] = []
    for rule in billing_config.rules:
        # 与 calculate_request_cost 保持一致：input/output 缺任一都会触发远程补价。
        missing_base_price = rule.input_price is None or rule.output_price is None
        has_override_price = any(
            tier.input_price is not None or tier.output_price is not None
            for tier in rule.token_tiers
        ) or any(
            window.input_price is not None or window.output_price is not None
            for window in rule.time_windows
        )
        remote: RemotePrice | None = None
        if missing_base_price and pricing_catalog is not None and pricing_catalog.loaded:
            remote = _resolve_remote_price(
                pricing_catalog,
                rule.provider,
                rule,
                # 只按模型规则匹配；provider_aliases 是服务商别名，不能当作模型名参与查价
                list(rule.provider_model_patterns),
                allow_prefix=_normalize_text(rule.match_mode) != "exact",
            )
        if remote is not None:
            effective = "yaml+models.dev"
        elif not missing_base_price or has_override_price:
            effective = "yaml"
        else:
            effective = "yaml-missing-price"
        # 远程价按「每 1M tokens」给出，换算到本规则的 unit 基准后与 yaml 价同口径展示。
        remote_prices: dict[str, float | None] | None = None
        if remote is not None:
            scale = _remote_scale(rule.unit)
            remote_prices = {
                "input_price": _scale_price(remote.input, scale),
                "output_price": _scale_price(remote.output, scale),
                "cache_read_price": _scale_price(remote.cache_read, scale),
                "cache_write_price": _scale_price(remote.cache_write, scale),
            }
        rows.append({
            "provider": rule.provider,
            "provider_aliases": rule.provider_aliases,
            "provider_model_patterns": rule.provider_model_patterns,
            "match_mode": rule.match_mode,
            "currency": rule.currency,
            "unit": rule.unit,
            "yaml_prices": {
                "input_price": rule.input_price,
                "output_price": rule.output_price,
                "cache_read_price": rule.cache_read_price,
                "cache_write_price": rule.cache_write_price,
            },
            "token_tier_count": len(rule.token_tiers),
            "time_window_count": len(rule.time_windows),
            "source_url": rule.source_url,
            "note": rule.note,
            "effective": effective,
            "remote_full_id": remote.full_id if remote is not None else None,
            "remote_prices": remote_prices,
        })
    return rows


def match_yaml_rule_for_catalog_model(
    billing_config: BillingConfig | None,
    pricing_catalog: PricingCatalog | None,
    provider_id: str | None,
    model: str | None,
) -> BillingRuleConfig | None:
    """判断某条 models.dev 目录条目是否已被某条 yaml 规则覆盖（用于页面标注）。"""
    target = normalize(provider_id)
    if billing_config is None or pricing_catalog is None or not target:
        return None
    for rule in billing_config.rules:
        candidates = pricing_catalog.provider_candidates(
            [rule.models_dev_provider, rule.provider, *rule.provider_aliases]
        )
        if target not in {normalize(c) for c in candidates}:
            continue
        if _model_matches(rule, model):
            return rule
    return None
