import { useCallback, useEffect, useState } from "react";
import { getGlobalProviders, getProviderBalances } from "@/api/services";
import { Button } from "@/components/ui/Button";
import {
  EmptyState,
  LoadingState,
  Table,
  TableShell,
  Td,
  Th,
} from "@/components/ui/DataTable";
import { useToast } from "@/context/ToastContext";
import type { GlobalProvider, ProviderBalance } from "@/types/api";

interface ProvidersPageProps {
  onStatsChange: (count: number) => void;
}

// 各运营商余额分项字段的中文标签（键名来自后端归一化结果）
const COMPONENT_LABELS: Record<string, string> = {
  granted_balance: "赠金",
  topped_up_balance: "充值",
  voucher_balance: "代金券",
  cash_balance: "现金",
  recharge_amount: "充值",
  give_amount: "赠送",
  total_spend_amount: "累计消费",
};

function formatAmount(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return value.toFixed(2);
}

function BalanceCell({ balance }: { balance?: ProviderBalance }) {
  if (!balance) {
    return <span className="text-slate-400">—</span>;
  }
  if (balance.status === "unsupported") {
    return (
      <div>
        <span className="text-slate-400">不支持查询</span>
        {balance.error ? (
          <p className="mt-0.5 text-xs text-slate-400">{balance.error}</p>
        ) : null}
      </div>
    );
  }
  if (balance.status === "error") {
    return (
      <div>
        <span className="font-medium text-rose-600">查询失败</span>
        {balance.error ? (
          <p className="mt-0.5 max-w-xs break-all text-xs text-rose-500">
            {balance.error}
          </p>
        ) : null}
      </div>
    );
  }
  return (
    <div className="space-y-1">
      {balance.balances.length === 0 ? (
        <span className="text-slate-400">无余额数据</span>
      ) : null}
      {balance.balances.map((item, index) => (
        <div key={`${item.currency}-${index}`}>
          <span className="font-medium text-slate-900">
            {item.currency} {formatAmount(item.available_balance)}
          </span>
          {item.components && Object.keys(item.components).length > 0 ? (
            <p className="text-xs text-slate-500">
              {Object.entries(item.components)
                .map(
                  ([key, value]) =>
                    `${COMPONENT_LABELS[key] ?? key} ${formatAmount(value)}`,
                )
                .join(" / ")}
            </p>
          ) : null}
        </div>
      ))}
      {balance.is_available === false ? (
        <p className="text-xs font-medium text-rose-600">
          余额不足，可能无法调用
        </p>
      ) : null}
    </div>
  );
}

export function ProvidersPage({ onStatsChange }: ProvidersPageProps) {
  const { showToast } = useToast();
  const [providers, setProviders] = useState<GlobalProvider[]>([]);
  const [loading, setLoading] = useState(true);
  const [balanceMap, setBalanceMap] = useState<Record<string, ProviderBalance>>({});
  const [balanceLoading, setBalanceLoading] = useState(false);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        const data = await getGlobalProviders();
        setProviders(data.providers);
        onStatsChange(data.providers.length);
      } catch (error) {
        showToast(error instanceof Error ? error.message : "加载服务商失败", "error");
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [onStatsChange, showToast]);

  const refreshBalances = useCallback(async () => {
    setBalanceLoading(true);
    try {
      const data = await getProviderBalances();
      const map: Record<string, ProviderBalance> = {};
      for (const item of data.providers) {
        map[item.name] = item;
      }
      setBalanceMap(map);
    } catch (error) {
      showToast(error instanceof Error ? error.message : "查询余额失败", "error");
    } finally {
      setBalanceLoading(false);
    }
  }, [showToast]);

  if (loading) return <LoadingState />;
  if (providers.length === 0) {
    return (
      <EmptyState
        title="暂无全局服务商"
        description="请在 config.yaml 中配置 providers 后刷新页面。"
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">全局服务商</h2>
          <p className="mt-1 text-sm text-slate-500">
            来自服务端 config.yaml 的预设服务商，供路由配置时快速选择。
          </p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          onClick={refreshBalances}
          disabled={balanceLoading}
        >
          {balanceLoading ? "查询中..." : "查询余额"}
        </Button>
      </div>
      <TableShell>
        <Table>
          <thead className="bg-slate-50">
            <tr>
              <Th>服务商名称</Th>
              <Th>API 地址</Th>
              <Th>类型</Th>
              <Th>实时余额</Th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 bg-white">
            {providers.map((provider) => (
              <tr key={provider.name}>
                <Td className="font-medium text-slate-900">{provider.name}</Td>
                <Td>{provider.base_url}</Td>
                <Td>{provider.api_type}</Td>
                <Td>
                  {balanceLoading && !balanceMap[provider.name] ? (
                    <span className="text-slate-400">查询中...</span>
                  ) : (
                    <BalanceCell balance={balanceMap[provider.name]} />
                  )}
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </TableShell>
    </div>
  );
}
