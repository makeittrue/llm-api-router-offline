import { useEffect, useState } from "react";
import { getGlobalProviders } from "@/api/services";
import {
  EmptyState,
  LoadingState,
  Table,
  TableShell,
  Td,
  Th,
} from "@/components/ui/DataTable";
import { useToast } from "@/context/ToastContext";
import type { GlobalProvider } from "@/types/api";

interface ProvidersPageProps {
  onStatsChange: (count: number) => void;
}

export function ProvidersPage({ onStatsChange }: ProvidersPageProps) {
  const { showToast } = useToast();
  const [providers, setProviders] = useState<GlobalProvider[]>([]);
  const [loading, setLoading] = useState(true);

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
      <div>
        <h2 className="text-lg font-semibold text-slate-900">全局服务商</h2>
        <p className="mt-1 text-sm text-slate-500">
          来自服务端 config.yaml 的预设服务商，供路由配置时快速选择。
        </p>
      </div>
      <TableShell>
        <Table>
          <thead className="bg-slate-50">
            <tr>
              <Th>服务商名称</Th>
              <Th>API 地址</Th>
              <Th>类型</Th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 bg-white">
            {providers.map((provider) => (
              <tr key={provider.name}>
                <Td className="font-medium text-slate-900">{provider.name}</Td>
                <Td>{provider.base_url}</Td>
                <Td>{provider.api_type}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </TableShell>
    </div>
  );
}
