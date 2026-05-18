from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.config import BillingConfig, BillingRuleConfig, BillingTimeWindowConfig, BillingTokenTierConfig


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
) -> tuple[float, float, float | None, float | None, str | None, str | None]:
    input_price = rule.input_price
    output_price = rule.output_price
    cache_read_price = rule.cache_read_price
    cache_write_price = rule.cache_write_price
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


def calculate_request_cost(
    *,
    billing_config: BillingConfig | None,
    provider_name: str | None,
    provider_model: str | None,
    prompt_tokens: int,
    completion_tokens: int,
    usage_raw: dict[str, Any] | None,
    created_at: datetime | None = None,
) -> dict[str, Any] | None:
    if billing_config is None or not billing_config.enabled:
        return None
    when_utc = created_at or datetime.now(timezone.utc)
    if when_utc.tzinfo is None:
        when_utc = when_utc.replace(tzinfo=timezone.utc)

    matched_rule: BillingRuleConfig | None = None
    for rule in billing_config.rules:
        if _provider_matches(rule, provider_name) and _model_matches(rule, provider_model):
            matched_rule = rule
            break
    if matched_rule is None:
        return None

    tokens = extract_billing_tokens(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        usage_raw=usage_raw,
    )
    input_price, output_price, cache_read_price, cache_write_price, matched_window_name, matched_tier_name = _resolve_prices(
        matched_rule, when_utc, tokens.prompt_tokens
    )
    if matched_rule.token_tiers and matched_tier_name is None:
        return None
    effective_cache_read_price = input_price if cache_read_price is None else cache_read_price
    effective_cache_write_price = input_price if cache_write_price is None else cache_write_price
    unit = max(int(matched_rule.unit), 1)

    regular_input_cost = tokens.regular_input_tokens * input_price / unit
    output_cost = tokens.completion_tokens * output_price / unit
    cache_read_cost = tokens.cached_input_tokens * effective_cache_read_price / unit
    cache_write_cost = tokens.cache_write_tokens * effective_cache_write_price / unit
    total_cost = round(
        regular_input_cost + output_cost + cache_read_cost + cache_write_cost,
        billing_config.round_digits,
    )

    return {
        "currency": matched_rule.currency or billing_config.default_currency,
        "unit": unit,
        "provider": provider_name,
        "provider_model": provider_model,
        "rule_provider": matched_rule.provider,
        "rule_model_patterns": matched_rule.provider_model_patterns,
        "match_mode": matched_rule.match_mode,
        "matched_window": matched_window_name,
        "matched_token_tier": matched_tier_name,
        "source_url": matched_rule.source_url,
        "source_urls": matched_rule.source_urls,
        "note": matched_rule.note,
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
