import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { getModelPricing, refreshRemotePricing } from "@/api/services";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import {
  EmptyState,
  LoadingState,
  Table,
  TableShell,
  Td,
  Th,
} from "@/components/ui/DataTable";
import { useToast } from "@/context/ToastContext";
import type {
  BillingPriceSource,
  BillingRulePricing,
  ModelPricingResponse,
  PriceSet,
} from "@/types/api";
import { formatCost, formatDateTime, formatTokens } from "@/utils/format";

const PAGE_SIZE = 50;

const EFFECTIVE_STYLE: Record<
  BillingPriceSource,
  { text: string; variant: "success" | "neutral" | "danger" }
> = {
  yaml: { text: "yaml 优先", variant: "success" },
  "yaml+models.dev": { text: "远程补充", variant: "neutral" },
  "yaml-missing-price": { text: "yaml 缺价", variant: "danger" },
};

function priceText(
  value: number | null | undefined,
  currency: string,
  unit = 1_000_000,
): string {
  if (value === null || value === undefined) return "-";
  return `${formatCost(value, currency)} / ${formatTokens(unit)}`;
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="mt-1 truncate text-sm font-medium text-slate-900" title={value}>
        {value}
      </p>
    </div>
  );
}

function RulePrices({ prices, currency, unit }: {
  prices: PriceSet;
  currency: string;
  unit: number;
}) {
  return (
    <div className="space-y-0.5 whitespace-nowrap text-xs">
      <p>输入 {priceText(prices.input_price, currency, unit)}</p>
      <p>输出 {priceText(prices.output_price, currency, unit)}</p>
      <p className="text-slate-500">
        缓存读 {priceText(prices.cache_read_price, currency, unit)}
        {" / "}
        写 {priceText(prices.cache_write_price, currency, unit)}
      </p>
    </div>
  );
}

function RuleRow({ rule }: { rule: BillingRulePricing }) {
  const currency = rule.currency || "CNY";
  const style = EFFECTIVE_STYLE[rule.effective];
  return (
    <tr>
      <Td className="font-medium text-slate-900">
        {rule.provider}
        {rule.provider_aliases.length > 0 ? (
          <p className="mt-0.5 text-xs font-normal text-slate-500">
            别名：{rule.provider_aliases.join(", ")}
          </p>
        ) : null}
      </Td>
      <Td>
        {rule.provider_model_patterns.length > 0 ? (
          <span className="break-all text-xs">{rule.provider_model_patterns.join(", ")}</span>
        ) : (
          <span className="text-slate-400">全部模型</span>
        )}
      </Td>
      <Td>
        <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-700">
          {rule.match_mode || "exact"}
        </code>
        {rule.token_tier_count > 0 || rule.time_window_count > 0 ? (
          <p className="mt-0.5 text-xs text-slate-500">
            {rule.token_tier_count > 0 ? `${rule.token_tier_count} 个分档` : ""}
            {rule.token_tier_count > 0 && rule.time_window_count > 0 ? " · " : ""}
            {rule.time_window_count > 0 ? `${rule.time_window_count} 个时段` : ""}
          </p>
        ) : null}
      </Td>
      <Td className="whitespace-nowrap text-xs text-slate-600">
        {formatTokens(rule.unit)} tokens
      </Td>
      <Td>
        <RulePrices prices={rule.yaml_prices} currency={currency} unit={rule.unit} />
        {rule.remote_prices ? (
          <div className="mt-1 border-t border-dashed border-slate-200 pt-1 text-xs">
            <p className="text-slate-500">远程补价（CNY）</p>
            <p>
              输入 {priceText(rule.remote_prices.input_price, "CNY", rule.unit)}
            </p>
            <p>
              输出 {priceText(rule.remote_prices.output_price, "CNY", rule.unit)}
            </p>
          </div>
        ) : null}
      </Td>
      <Td>
        <Badge variant={style.variant}>{style.text}</Badge>
        {rule.remote_full_id ? (
          <p className="mt-1 break-all text-xs text-slate-500">
            models.dev: {rule.remote_full_id}
          </p>
        ) : null}
        {rule.source_url ? (
          <a
            className="mt-1 block truncate text-xs text-brand-600 hover:underline"
            href={rule.source_url}
            target="_blank"
            rel="noreferrer"
            title={rule.source_url}
          >
            价格来源
          </a>
        ) : null}
      </Td>
      <Td className="max-w-[16rem] text-xs text-slate-500">
        {rule.note || "-"}
      </Td>
    </tr>
  );
}

export function PricingPage() {
  const { showToast } = useToast();
  const [data, setData] = useState<ModelPricingResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [provider, setProvider] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [reloadNonce, setReloadNonce] = useState(0);
  // 请求序号：只有最新一次请求的结果才允许写入 state，避免过期响应覆盖新数据
  const requestIdRef = useRef(0);

  const fetchPage = useCallback(
    async (nextOffset: number) => {
      const requestId = requestIdRef.current + 1;
      requestIdRef.current = requestId;
      setLoading(true);
      try {
        const result = await getModelPricing({
          provider: provider || undefined,
          q: query || undefined,
          offset: nextOffset,
          limit: PAGE_SIZE,
        });
        if (requestId !== requestIdRef.current) return;
        setData(result);
      } catch (error) {
        if (requestId !== requestIdRef.current) return;
        showToast(error instanceof Error ? error.message : "加载模型价格失败", "error");
      } finally {
        if (requestId === requestIdRef.current) setLoading(false);
      }
    },
    [provider, query, showToast],
  );

  useEffect(() => {
    void fetchPage(offset);
  }, [fetchPage, offset, reloadNonce]);

  const handleSearch = () => {
    setOffset(0);
    setQuery(searchInput.trim());
  };

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      const status = await refreshRemotePricing();
      showToast(`价格目录已刷新（${status.entry_count ?? 0} 条）`, "success");
      // 回到第一页并强制重新拉取（effect 会带上新的 offset，不会用旧的闭包值）
      setOffset(0);
      setReloadNonce((nonce) => nonce + 1);
    } catch (error) {
      showToast(error instanceof Error ? error.message : "刷新价格目录失败", "error");
    } finally {
      setRefreshing(false);
    }
  };

  const status = data?.status;
  const total = data?.remote.total ?? 0;
  const rules = data?.rules ?? [];
  const items = data?.remote.items ?? [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">模型价格</h2>
          <p className="mt-1 text-sm text-slate-500">
            计费价格由 config.yaml 规则优先，未写价的模型由后台从 models.dev 自动补价（USD 按汇率换算为 CNY）。
          </p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          onClick={handleRefresh}
          disabled={refreshing || status?.enabled === false}
        >
          <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
          {refreshing ? "刷新中..." : "手动刷新目录"}
        </Button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Meta
          label="目录状态"
          value={
            status?.enabled === false
              ? "未启用"
              : status?.loaded
                ? `已加载（${status.source ?? "-"}）`
                : "尚未加载"
          }
        />
        <Meta
          label="目录条目"
          value={`${status?.entry_count ?? 0} 条 / ${status?.provider_count ?? 0} 个服务商`}
        />
        <Meta
          label="最近刷新"
          value={status?.last_refresh_at ? formatDateTime(status.last_refresh_at) : "-"}
        />
        <Meta label="USD → CNY 汇率" value={String(data?.remote.usd_to_cny ?? status?.usd_to_cny ?? "-")} />
      </div>

      {status?.enabled === false ? (
        <p className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          远程价格目录未启用（billing.remote_pricing.enabled），当前仅使用 config.yaml 中显式写入的价格。
        </p>
      ) : null}
      {status?.last_error ? (
        <p className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          价格目录异常：{status.last_error}
        </p>
      ) : null}
      {status?.last_cache_write_error ? (
        <p className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          缓存写入失败：{status.last_cache_write_error}
        </p>
      ) : null}

      <section className="space-y-3">
        <div>
          <h3 className="text-base font-semibold text-slate-900">YAML 计费规则</h3>
          <p className="mt-1 text-sm text-slate-500">
            共 {rules.length} 条规则。「yaml 优先」表示显式写价生效；「远程补充」表示 yaml 缺价、由 models.dev 补齐；「yaml 缺价」表示两处都未取到价，按 0 计费。
          </p>
        </div>
        {rules.length === 0 ? (
          <EmptyState title="暂无计费规则" description="config.yaml 的 billing.rules 为空。" />
        ) : (
          <TableShell>
            <Table>
              <thead className="bg-slate-50">
                <tr>
                  <Th>服务商</Th>
                  <Th>模型匹配</Th>
                  <Th>匹配方式</Th>
                  <Th>单位</Th>
                  <Th>yaml 单价</Th>
                  <Th>生效来源</Th>
                  <Th>备注</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 bg-white">
                {rules.map((rule, index) => (
                  <RuleRow key={`${rule.provider}-${index}`} rule={rule} />
                ))}
              </tbody>
            </Table>
          </TableShell>
        )}
      </section>

      <section className="space-y-3">
        <div>
          <h3 className="text-base font-semibold text-slate-900">models.dev 远程目录</h3>
          <p className="mt-1 text-sm text-slate-500">
            价格原值为 USD / 1M tokens，括号内为按汇率换算的 CNY。「yaml 覆盖」表示该模型会被同名的 config.yaml 规则接管。
          </p>
        </div>

        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            handleSearch();
          }}
        >
          <div className="w-52">
            <Select
              label="服务商"
              value={provider}
              onChange={(event) => {
                setProvider(event.target.value);
                setOffset(0);
              }}
            >
              <option value="">全部服务商</option>
              {(data?.remote.providers ?? []).map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </Select>
          </div>
          <div className="w-64">
            <Input
              label="搜索模型"
              placeholder="如 gpt-4o / deepseek"
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
            />
          </div>
          <Button type="submit" size="sm">
            搜索
          </Button>
          {query || provider ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setSearchInput("");
                setQuery("");
                setProvider("");
                setOffset(0);
              }}
            >
              重置
            </Button>
          ) : null}
        </form>

        {loading && !data ? (
          <LoadingState />
        ) : items.length === 0 ? (
          <EmptyState
            title="没有匹配的模型"
            description={
              status?.enabled === false
                ? "远程价格目录未启用。"
                : "可调整服务商 / 搜索关键词，或点击「手动刷新目录」重新拉取。"
            }
          />
        ) : (
          <>
            <TableShell>
              <Table>
                <thead className="bg-slate-50">
                  <tr>
                    <Th>服务商</Th>
                    <Th>模型</Th>
                    <Th>输入（USD / CNY）</Th>
                    <Th>输出（USD / CNY）</Th>
                    <Th>缓存读 / 写（USD）</Th>
                    <Th>归属</Th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 bg-white">
                  {items.map((item) => (
                    <tr key={`${item.provider_id}/${item.full_id}`}>
                      <Td className="whitespace-nowrap text-xs text-slate-600">
                        {item.provider_id}
                      </Td>
                      <Td className="font-medium text-slate-900">
                        {item.model}
                        <a
                          className="ml-2 text-xs font-normal text-brand-600 hover:underline"
                          href={item.source_url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          models.dev
                        </a>
                      </Td>
                      <Td className="whitespace-nowrap">
                        {priceText(item.usd.input, "USD")}
                        <p className="text-xs text-slate-500">{priceText(item.cny.input, "CNY")}</p>
                      </Td>
                      <Td className="whitespace-nowrap">
                        {priceText(item.usd.output, "USD")}
                        <p className="text-xs text-slate-500">{priceText(item.cny.output, "CNY")}</p>
                      </Td>
                      <Td className="whitespace-nowrap text-xs text-slate-600">
                        {priceText(item.usd.cache_read, "USD")}
                        <p className="text-slate-500">{priceText(item.usd.cache_write, "USD")}</p>
                      </Td>
                      <Td>
                        {item.covered_by ? (
                          <Badge variant="success">yaml 覆盖</Badge>
                        ) : (
                          <Badge variant="neutral">仅远程</Badge>
                        )}
                        {item.covered_by ? (
                          <p className="mt-1 text-xs text-slate-500">
                            {item.covered_by.provider}（{item.covered_by.match_mode || "exact"}）
                          </p>
                        ) : null}
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </TableShell>
            <div className="flex items-center justify-between text-sm text-slate-600">
              <span>
                共 {total} 条，当前显示第 {total === 0 ? 0 : offset + 1} - {offset + items.length} 条
              </span>
              <div className="flex gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={offset === 0 || loading}
                  onClick={() => setOffset(Math.max(offset - PAGE_SIZE, 0))}
                >
                  上一页
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={offset + PAGE_SIZE >= total || loading}
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                >
                  下一页
                </Button>
              </div>
            </div>
          </>
        )}
      </section>
    </div>
  );
}