import { useCallback, useEffect, useState } from "react";
import { getGlobalProviders, getProviderBalances } from "@/api/services";
import { Button } from "@/components/ui/Button";
import { BalanceCell } from "@/components/ui/BalanceCell";
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

export function ProvidersPage({ onStatsChange }: ProvidersPageProps) {
  const { showToast } = useToast();
  const [providers, setProviders] = useState<GlobalProvider[]>([]);
  const [loading, setLoading] = useState(true);
  const [balanceMap, setBalanceMap] = useState<Record<string, ProviderBalance>>({});
  const [balanceLoading, setBalanceLoading] = useState(false);

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

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        const data = await getGlobalProviders();
        setProviders(data.providers);
        onStatsChange(data.providers.length);
        // 进入页面自动查询一次余额，无需手动点击
        if (data.providers.length > 0) void refreshBalances();
      } catch (error) {
        showToast(error instanceof Error ? error.message : "加载服务商失败", "error");
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [onStatsChange, showToast, refreshBalances]);

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
