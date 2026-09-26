"""远程价格目录（models.dev）与计费补价的单元测试（不联网）。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.billing import (
    calculate_request_cost,
    describe_billing_rules,
    match_yaml_rule_for_catalog_model,
)
from app.config import (
    BillingConfig,
    BillingRuleConfig,
    BillingTokenTierConfig,
    RemotePricingConfig,
)
from app.pricing_catalog import PricingCatalog, build_index


def _sample_api() -> dict:
    return {
        "deepseek": {
            "models": {
                "deepseek/deepseek-v4-flash": {"cost": {"input": 0.14, "output": 0.28, "cache_read": 0.0028}},
                "deepseek/no-cost": {"name": "no cost entry"},
            }
        },
        "openai": {
            "models": {
                "openai/gpt-4o": {"cost": {"input": 2.5, "output": 10, "cache_read": 1.25}},
                "openai/gpt-4o-2024-08-06": {"cost": {"input": 2.5, "output": 10}},
            }
        },
        "moonshotai": {
            "models": {
                "moonshotai/kimi-k3": {"cost": {"input": 3, "output": 15, "cache_read": 0.3}},
            }
        },
        "alibaba-cn": {
            "models": {
                "alibaba-cn/qwen3.7-flash": {"cost": {"input": 0.1666, "output": 1.0}},
            }
        },
    }


class PricingCatalogBuildTest(unittest.TestCase):
    def test_build_index_skips_entries_without_cost(self):
        index = build_index(_sample_api())
        self.assertIn("deepseek", index)
        self.assertIn("deepseek-v4-flash", index["deepseek"])
        self.assertNotIn("no-cost", index["deepseek"])

    def test_build_index_handles_bad_input(self):
        self.assertEqual(build_index(None), {})
        self.assertEqual(build_index({"x": {"models": "oops"}}), {})

    def test_provider_candidates_mapping(self):
        catalog = PricingCatalog(RemotePricingConfig())
        self.assertEqual(catalog.provider_candidates(["moonshot"]), ["moonshotai", "moonshotai-cn"])
        self.assertEqual(catalog.provider_candidates(["qwen"]), ["alibaba", "alibaba-cn"])
        self.assertEqual(catalog.provider_candidates(["doubao"]), [])
        self.assertEqual(catalog.provider_candidates(["some-new-provider"]), ["some-new-provider"])
        # 去重且保持优先序
        self.assertEqual(catalog.provider_candidates(["kimi", "moonshot"]), ["moonshotai", "moonshotai-cn"])

    def test_provider_alias_override(self):
        catalog = PricingCatalog(RemotePricingConfig(provider_aliases={"qwen": ["alibaba-cn"]}))
        self.assertEqual(catalog.provider_candidates(["qwen"]), ["alibaba-cn"])


class PricingCatalogLookupTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache_path = Path(self._tmp.name) / "api.json"
        self.cache_path.write_text(json.dumps(_sample_api()), encoding="utf-8")
        self.catalog = PricingCatalog(RemotePricingConfig(cache_path=str(self.cache_path)))
        self.assertTrue(self.catalog.load_cache_sync())

    def tearDown(self):
        self._tmp.cleanup()

    def test_exact_match_and_usd_to_cny(self):
        price = self.catalog.lookup(["deepseek"], "deepseek-v4-flash")
        self.assertIsNotNone(price)
        self.assertEqual(price.match_kind, "exact")
        self.assertAlmostEqual(price.input, round(0.14 * 7.2, 6))
        self.assertAlmostEqual(price.output, round(0.28 * 7.2, 6))
        self.assertAlmostEqual(price.cache_read, round(0.0028 * 7.2, 6))
        self.assertIsNone(price.cache_write)
        self.assertEqual(price.source_url, "https://models.dev/deepseek/deepseek-v4-flash")

    def test_prefix_match_both_directions(self):
        # 请求名比 catalog 长
        forward = self.catalog.lookup(["openai"], "gpt-4o-2024-08-06-extra")
        self.assertIsNotNone(forward)
        self.assertEqual(forward.match_kind, "prefix")
        # 请求名比 catalog 短
        backward = self.catalog.lookup(["openai"], "gpt-4o")
        self.assertIsNotNone(backward)
        self.assertEqual(backward.match_kind, "exact")

    def test_short_name_not_matched(self):
        self.assertIsNone(self.catalog.lookup(["openai"], "gpt"))

    def test_allow_prefix_false_only_exact(self):
        # 关闭前缀兜底后，只有完全同名才命中
        self.assertIsNone(self.catalog.lookup(["openai"], "gpt-4o-2024", allow_prefix=False))
        exact = self.catalog.lookup(["openai"], "gpt-4o", allow_prefix=False)
        self.assertIsNotNone(exact)
        self.assertEqual(exact.match_kind, "exact")

    def test_unknown_provider_returns_none(self):
        self.assertIsNone(self.catalog.lookup(["doubao"], "doubao-pro"))

    def test_status_and_cache_age(self):
        status = self.catalog.status()
        self.assertTrue(status["loaded"])
        self.assertEqual(status["source"], "cache")
        self.assertGreaterEqual(status["entry_count"], 4)
        self.assertIsNotNone(status["cache_age_seconds"])
        self.assertIn("last_cache_write_error", status)


class CalculateCostWithRemoteTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        cache_path = Path(self._tmp.name) / "api.json"
        cache_path.write_text(json.dumps(_sample_api()), encoding="utf-8")
        self.catalog = PricingCatalog(RemotePricingConfig(cache_path=str(cache_path)))
        self.assertTrue(self.catalog.load_cache_sync())

    def tearDown(self):
        self._tmp.cleanup()

    def _config(self, rules, **remote_kwargs) -> BillingConfig:
        return BillingConfig(rules=rules, remote_pricing=RemotePricingConfig(**remote_kwargs))

    def _calc(self, config, catalog, provider="deepseek", model="deepseek-v4-flash"):
        return calculate_request_cost(
            billing_config=config,
            provider_name=provider,
            provider_model=model,
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
            usage_raw=None,
            pricing_catalog=catalog,
        )

    def test_yaml_explicit_price_wins(self):
        config = self._config([
            BillingRuleConfig(
                provider="deepseek",
                provider_model_patterns=["deepseek-v4-flash"],
                input_price=1.5,
                output_price=4.5,
                cache_read_price=0.05,
                cache_write_price=1.5,
            )
        ])
        result = self._calc(config, self.catalog)
        self.assertIsNotNone(result)
        self.assertEqual(result["source"], "yaml")
        self.assertEqual(result["prices"]["input_price"], 1.5)
        # 1M input + 1M output = 1.5 + 4.5
        self.assertAlmostEqual(result["costs"]["total_cost"], 6.0, places=8)

    def test_yaml_missing_price_filled_from_remote(self):
        config = self._config([
            BillingRuleConfig(
                provider="moonshot",
                provider_aliases=["moonshot", "kimi"],
                provider_model_patterns=["kimi-k3"],
                match_mode="prefix",
                input_price=None,
                output_price=None,
            )
        ])
        result = self._calc(config, self.catalog, provider="moonshot", model="kimi-k3")
        self.assertIsNotNone(result)
        self.assertEqual(result["source"], "yaml+models.dev")
        self.assertAlmostEqual(result["prices"]["input_price"], round(3 * 7.2, 6))
        self.assertEqual(result["remote_provider"], "moonshotai")

    def test_exact_rule_does_not_prefix_fill(self):
        # match_mode=exact 且缺价时只允许精确补价：gpt-4o-2024 不应命中 catalog 的 gpt-4o
        config = self._config([
            BillingRuleConfig(
                provider="openai",
                provider_model_patterns=["gpt-4o-2024"],
                match_mode="exact",
                input_price=None,
                output_price=None,
            )
        ])
        result = self._calc(config, self.catalog, provider="openai", model="gpt-4o-2024")
        self.assertIsNotNone(result)
        self.assertEqual(result["source"], "yaml")
        self.assertEqual(result["costs"]["total_cost"], 0.0)

    def test_prefix_rule_still_fills(self):
        config = self._config([
            BillingRuleConfig(
                provider="openai",
                provider_model_patterns=["gpt-4o-2024"],
                match_mode="prefix",
                input_price=None,
                output_price=None,
            )
        ])
        result = self._calc(config, self.catalog, provider="openai", model="gpt-4o-2024")
        self.assertEqual(result["source"], "yaml+models.dev")

    def test_remote_fill_respects_rule_unit(self):
        # 远程价是「每 1M tokens」；unit=1000 时必须折算，否则会放大 1000 倍
        config = self._config([
            BillingRuleConfig(
                provider="moonshot",
                provider_model_patterns=["kimi-k3"],
                match_mode="prefix",
                unit=1000,
            )
        ])
        result = self._calc(config, self.catalog, provider="moonshot", model="kimi-k3")
        self.assertEqual(result["source"], "yaml+models.dev")
        self.assertEqual(result["unit"], 1000)
        # 远程 3 USD/1M → 21.6 CNY/1M，折算到每 1K 为 0.0216
        self.assertAlmostEqual(result["prices"]["input_price"], 21.6 * 0.001, places=10)
        # 1M prompt + 1M completion 的总成本与 unit 无关：21.6(in) + 108(out)
        self.assertAlmostEqual(result["costs"]["total_cost"], round(21.6 + 108.0, 8), places=6)

    def test_no_rule_uses_synthesized_remote_price(self):
        config = self._config([])
        result = self._calc(config, self.catalog, provider="openai", model="gpt-4o")
        self.assertIsNotNone(result)
        self.assertEqual(result["source"], "models.dev")
        self.assertEqual(result["rule_provider"], "models.dev:openai")
        self.assertEqual(result["rule_model_patterns"], ["gpt-4o"])
        self.assertEqual(result["unit"], 1_000_000)
        self.assertAlmostEqual(result["prices"]["input_price"], round(2.5 * 7.2, 6))

    def test_unmapped_provider_no_cost(self):
        config = self._config([])
        result = self._calc(config, self.catalog, provider="doubao", model="doubao-pro")
        self.assertIsNone(result)

    def test_synthesize_disabled_skips_unknown_models(self):
        config = self._config([], synthesize_unknown_models=False)
        result = self._calc(config, self.catalog, provider="openai", model="gpt-4o")
        self.assertIsNone(result)

    def test_remote_disabled_skips_unknown_models(self):
        config = self._config([], enabled=False)
        result = self._calc(config, self.catalog, provider="openai", model="gpt-4o")
        self.assertIsNone(result)

    def test_no_catalog_keeps_legacy_behavior(self):
        # 命中规则但无远程目录：与旧行为一致（未写价 → 0）
        config = self._config([
            BillingRuleConfig(provider="primary", provider_model_patterns=["m"]),
        ])
        result = calculate_request_cost(
            billing_config=config,
            provider_name="primary",
            provider_model="m",
            prompt_tokens=1000,
            completion_tokens=1000,
            usage_raw=None,
            pricing_catalog=None,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["source"], "yaml")
        self.assertEqual(result["costs"]["total_cost"], 0.0)

    def test_no_catalog_no_rule_still_returns_none(self):
        config = self._config([])
        result = self._calc(config, None, provider="openai", model="gpt-4o")
        self.assertIsNone(result)

    def test_yaml_free_model_not_overridden(self):
        # 显式 0.0 的免费模型不应被远程价覆盖
        config = self._config([
            BillingRuleConfig(
                provider="deepseek",
                provider_model_patterns=["deepseek-v4-flash"],
                input_price=0.0,
                output_price=0.0,
            )
        ])
        result = self._calc(config, self.catalog)
        self.assertEqual(result["source"], "yaml")
        self.assertEqual(result["costs"]["total_cost"], 0.0)


class PricingCatalogListingTest(unittest.TestCase):
    """价格页面用的目录列举接口。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache_path = Path(self._tmp.name) / "api.json"
        self.cache_path.write_text(json.dumps(_sample_api()), encoding="utf-8")
        self.catalog = PricingCatalog(RemotePricingConfig(cache_path=str(self.cache_path)))
        self.assertTrue(self.catalog.load_cache_sync())

    def tearDown(self):
        self._tmp.cleanup()

    def test_list_entries_pagination_and_filters(self):
        all_entries = self.catalog.list_entries(limit=100)
        self.assertEqual(all_entries["total"], 5)
        self.assertEqual(
            all_entries["providers"], ["alibaba-cn", "deepseek", "moonshotai", "openai"]
        )
        self.assertEqual(all_entries["usd_to_cny"], 7.2)

        page = self.catalog.list_entries(offset=1, limit=2)
        self.assertEqual(page["total"], 5)
        self.assertEqual(len(page["items"]), 2)

        openai_only = self.catalog.list_entries(provider_id="OpenAI")
        self.assertEqual(openai_only["total"], 2)

        matched = self.catalog.list_entries(query="gpt-4o-2024")
        self.assertEqual(matched["total"], 1)
        item = matched["items"][0]
        self.assertEqual(item["provider_id"], "openai")
        self.assertEqual(item["model"], "gpt-4o-2024-08-06")
        self.assertEqual(item["full_id"], "openai/gpt-4o-2024-08-06")
        self.assertAlmostEqual(item["usd"]["input"], 2.5)
        self.assertAlmostEqual(item["cny"]["input"], round(2.5 * 7.2, 6))
        self.assertIsNone(item["usd"]["cache_write"])

    def test_list_entries_without_data(self):
        empty = PricingCatalog(
            RemotePricingConfig(cache_path=str(Path(self._tmp.name) / "missing.json"))
        )
        listing = empty.list_entries()
        self.assertEqual(listing["total"], 0)
        self.assertEqual(listing["items"], [])
        self.assertEqual(listing["providers"], [])


class DescribeBillingRulesTest(unittest.TestCase):
    """YAML 规则展示：生效价来源标注。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        cache_path = Path(self._tmp.name) / "api.json"
        cache_path.write_text(json.dumps(_sample_api()), encoding="utf-8")
        self.catalog = PricingCatalog(RemotePricingConfig(cache_path=str(cache_path)))
        self.assertTrue(self.catalog.load_cache_sync())

    def tearDown(self):
        self._tmp.cleanup()

    def test_effective_flags(self):
        config = BillingConfig(
            rules=[
                BillingRuleConfig(
                    provider="deepseek",
                    provider_model_patterns=["deepseek-v4-flash"],
                    input_price=1.5,
                    output_price=4.5,
                ),
                BillingRuleConfig(
                    provider="moonshot",
                    provider_model_patterns=["kimi-k3"],
                    match_mode="prefix",
                ),
                BillingRuleConfig(provider="doubao", provider_model_patterns=["doubao-pro"]),
            ],
            remote_pricing=RemotePricingConfig(),
        )
        rows = describe_billing_rules(config, self.catalog)
        self.assertEqual(
            [row["effective"] for row in rows],
            ["yaml", "yaml+models.dev", "yaml-missing-price"],
        )
        self.assertIsNone(rows[0]["remote_full_id"])
        self.assertEqual(rows[0]["yaml_prices"]["input_price"], 1.5)
        self.assertEqual(rows[1]["remote_full_id"], "moonshotai/kimi-k3")
        self.assertAlmostEqual(rows[1]["remote_prices"]["input_price"], round(3 * 7.2, 6))
        self.assertIsNone(rows[2]["remote_prices"])

    def test_without_catalog_or_config(self):
        self.assertEqual(describe_billing_rules(None, self.catalog), [])
        config = BillingConfig(
            rules=[BillingRuleConfig(provider="openai", provider_model_patterns=["gpt-4o"])]
        )
        rows = describe_billing_rules(config, None)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["effective"], "yaml-missing-price")

    def test_partial_base_price_labeled_as_remote_filled(self):
        # 只写了 input_price：标注必须是 yaml+models.dev，与真实计费口径一致
        config = BillingConfig(
            rules=[
                BillingRuleConfig(
                    provider="moonshot",
                    provider_model_patterns=["kimi-k3"],
                    match_mode="prefix",
                    input_price=5.0,
                )
            ]
        )
        rows = describe_billing_rules(config, self.catalog)
        self.assertEqual(rows[0]["effective"], "yaml+models.dev")
        self.assertEqual(rows[0]["remote_full_id"], "moonshotai/kimi-k3")

        result = calculate_request_cost(
            billing_config=config,
            provider_name="moonshot",
            provider_model="kimi-k3",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
            usage_raw=None,
            pricing_catalog=self.catalog,
        )
        self.assertEqual(result["source"], "yaml+models.dev")

    def test_provider_aliases_not_used_as_model_names(self):
        # provider 别名是服务商别名，不能被当成模型名去查远程价（否则会命中无关模型）
        config = BillingConfig(
            rules=[
                BillingRuleConfig(
                    provider="openai",
                    provider_aliases=["gpt-4o"],
                    provider_model_patterns=["does-not-exist"],
                    input_price=None,
                    output_price=None,
                )
            ]
        )
        rows = describe_billing_rules(config, self.catalog)
        self.assertEqual(rows[0]["effective"], "yaml-missing-price")
        self.assertIsNone(rows[0]["remote_full_id"])
        self.assertIsNone(rows[0]["remote_prices"])

    def test_remote_prices_displayed_in_rule_unit(self):
        config = BillingConfig(
            rules=[
                BillingRuleConfig(
                    provider="moonshot",
                    provider_model_patterns=["kimi-k3"],
                    match_mode="prefix",
                    unit=1000,
                )
            ]
        )
        rows = describe_billing_rules(config, self.catalog)
        self.assertAlmostEqual(rows[0]["remote_prices"]["input_price"], 21.6 * 0.001, places=10)

    def test_tier_only_price_labeled_as_yaml(self):
        # 基础价全缺、但分档价供价：不应标成 yaml-missing-price
        config = BillingConfig(
            rules=[
                BillingRuleConfig(
                    provider="doubao",
                    provider_model_patterns=["doubao-pro"],
                    token_tiers=[
                        BillingTokenTierConfig(name="t", input_price=1.0, output_price=2.0)
                    ],
                )
            ]
        )
        rows = describe_billing_rules(config, self.catalog)
        self.assertEqual(rows[0]["effective"], "yaml")

    def test_uppercase_match_mode_is_not_prefix_filled(self):
        # match_mode 大小写不敏感：Exact 也必须按精确补价，不被 gpt-4o 的前缀价误补
        config = BillingConfig(
            rules=[
                BillingRuleConfig(
                    provider="openai",
                    provider_model_patterns=["gpt-4o-2024"],
                    match_mode="Exact",
                )
            ]
        )
        result = calculate_request_cost(
            billing_config=config,
            provider_name="openai",
            provider_model="gpt-4o-2024",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
            usage_raw=None,
            pricing_catalog=self.catalog,
        )
        self.assertEqual(result["source"], "yaml")
        self.assertEqual(result["costs"]["total_cost"], 0.0)

    def test_match_yaml_rule_for_catalog_model(self):
        config = BillingConfig(
            rules=[
                BillingRuleConfig(
                    provider="openai",
                    provider_model_patterns=["gpt-4o"],
                    match_mode="exact",
                )
            ]
        )
        hit = match_yaml_rule_for_catalog_model(config, self.catalog, "openai", "gpt-4o")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.provider, "openai")
        # 同 provider 但模型不匹配 → 未被覆盖
        self.assertIsNone(
            match_yaml_rule_for_catalog_model(config, self.catalog, "openai", "gpt-4o-2024-08-06")
        )
        # provider 不匹配
        self.assertIsNone(
            match_yaml_rule_for_catalog_model(config, self.catalog, "deepseek", "deepseek-v4-flash")
        )
        # provider 走别名映射（moonshot → moonshotai）
        alias_config = BillingConfig(
            rules=[
                BillingRuleConfig(
                    provider="moonshot",
                    provider_model_patterns=["kimi-k3"],
                    match_mode="prefix",
                )
            ]
        )
        self.assertIsNotNone(
            match_yaml_rule_for_catalog_model(alias_config, self.catalog, "moonshotai", "kimi-k3")
        )
        # 缺参数时返回 None
        self.assertIsNone(match_yaml_rule_for_catalog_model(None, self.catalog, "openai", "gpt-4o"))
        self.assertIsNone(match_yaml_rule_for_catalog_model(config, None, "openai", "gpt-4o"))
        self.assertIsNone(match_yaml_rule_for_catalog_model(config, self.catalog, "", "gpt-4o"))


class PricingCatalogRefreshErrorTest(unittest.IsolatedAsyncioTestCase):
    """刷新失败时保留有诊断价值的错误原因。"""

    async def test_fetch_error_not_overwritten_by_cache_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 缓存不存在 + 联网失败：应保留网络错误，而不是被 cache load 失败覆盖
            catalog = PricingCatalog(
                RemotePricingConfig(cache_path=str(Path(tmp) / "missing.json"))
            )
            with patch("app.pricing_catalog.httpx.AsyncClient", side_effect=OSError("no network")):
                ok = await catalog.refresh(force=True)
            self.assertFalse(ok)
            self.assertIn("fetch failed", catalog.status()["last_error"])


if __name__ == "__main__":
    unittest.main()