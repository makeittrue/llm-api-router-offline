import { useEffect, useMemo, useState } from "react";
import { Pencil, Plus, Trash2 } from "lucide-react";
import {
  createUserRoute,
  deleteUserRoute,
  getBillingProviders,
  getDefaultRoute,
  getUserRoutes,
  updateDefaultRoute,
  updateUserRoute,
} from "@/api/services";
import { Button } from "@/components/ui/Button";
import {
  EmptyState,
  LoadingState,
  Table,
  TableShell,
  Td,
  Th,
} from "@/components/ui/DataTable";
import { Textarea } from "@/components/ui/Textarea";
import { Toggle } from "@/components/ui/Toggle";
import { RouteModal } from "@/components/modals/RouteModal";
import { useToast } from "@/context/ToastContext";
import type {
  DefaultRouteConfig,
  ProviderOption,
  UserRoute,
} from "@/types/api";
import { formatDateTime } from "@/utils/format";

interface RoutesPageProps {
  onStatsChange: (routeCount: number, models: string[], defaultRoute: DefaultRouteConfig) => void;
}

export function RoutesPage({ onStatsChange }: RoutesPageProps) {
  const { showToast } = useToast();
  const [routes, setRoutes] = useState<UserRoute[]>([]);
  const [providers, setProviders] = useState<ProviderOption[]>([]);
  const [defaultRoute, setDefaultRoute] = useState<DefaultRouteConfig>({
    enabled: false,
    models: [],
  });
  const [defaultModelsText, setDefaultModelsText] = useState("");
  const [loading, setLoading] = useState(true);
  const [savingDefault, setSavingDefault] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingRoute, setEditingRoute] = useState<UserRoute | null>(null);

  const loadData = async () => {
    setLoading(true);
    try {
      const [routesData, defaultData, providerData] = await Promise.all([
        getUserRoutes(),
        getDefaultRoute(),
        getBillingProviders(),
      ]);
      setRoutes(routesData.routes);
      setDefaultRoute(defaultData);
      setDefaultModelsText(defaultData.models.join("\n"));
      setProviders(providerData.providers);
      onStatsChange(
        routesData.routes.length,
        routesData.routes.map((route) => route.model),
        defaultData,
      );
    } catch (error) {
      showToast(error instanceof Error ? error.message : "加载路由失败", "error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const defaultStatus = useMemo(() => {
    if (!defaultRoute.enabled) return "当前未启用";
    return `已启用${defaultRoute.updated_at ? ` · 更新于 ${formatDateTime(defaultRoute.updated_at)}` : ""}`;
  }, [defaultRoute]);

  const handleSaveDefault = async () => {
    const models = defaultModelsText
      .split("\n")
      .map((item) => item.trim())
      .filter(Boolean);
    if (defaultRoute.enabled && models.length === 0) {
      showToast("启用 default 自动降级前，请至少填写一个候选模型", "error");
      return;
    }
    setSavingDefault(true);
    try {
      const data = await updateDefaultRoute({
        enabled: defaultRoute.enabled,
        models,
      });
      setDefaultRoute(data);
      setDefaultModelsText(data.models.join("\n"));
      showToast("default 配置已保存", "success");
      onStatsChange(
        routes.length,
        routes.map((route) => route.model),
        data,
      );
    } catch (error) {
      showToast(error instanceof Error ? error.message : "保存失败", "error");
    } finally {
      setSavingDefault(false);
    }
  };

  const handleDelete = async (route: UserRoute) => {
    if (!window.confirm(`确定删除路由「${route.model}」吗？`)) return;
    try {
      await deleteUserRoute(route.id);
      showToast("删除成功", "success");
      await loadData();
    } catch (error) {
      showToast(error instanceof Error ? error.message : "删除失败", "error");
    }
  };

  const handleSaveRoute = async (payload: Record<string, string>, routeId?: number) => {
    try {
      if (routeId) {
        await updateUserRoute(routeId, payload);
      } else {
        await createUserRoute(payload);
      }
      showToast("保存成功", "success");
      setModalOpen(false);
      setEditingRoute(null);
      await loadData();
    } catch (error) {
      showToast(error instanceof Error ? error.message : "保存失败", "error");
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">我的私有路由</h2>
          <p className="mt-1 text-sm text-slate-500">
            配置模型映射与上游服务商，支持 default 自动降级链。
          </p>
        </div>
        <Button
          onClick={() => {
            setEditingRoute(null);
            setModalOpen(true);
          }}
        >
          <Plus className="h-4 w-4" />
          新增路由
        </Button>
      </div>

      <div className="rounded-xl border border-brand-100 bg-brand-50/60 p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="flex-1 space-y-4">
            <Toggle
              checked={defaultRoute.enabled}
              onChange={(enabled) =>
                setDefaultRoute((prev) => ({ ...prev, enabled }))
              }
              label="启用 model=default"
              description='客户端只需传 "model": "default"，网关将按候选顺序自动降级。'
            />
            <Textarea
              label="候选模型顺序（每行一个）"
              rows={4}
              value={defaultModelsText}
              onChange={(event) => setDefaultModelsText(event.target.value)}
              placeholder={"gpt-4o\ndeepseek-chat\nglm-4"}
            />
            <p className="text-xs text-slate-500">{defaultStatus}</p>
          </div>
          <Button onClick={handleSaveDefault} disabled={savingDefault}>
            {savingDefault ? "保存中..." : "保存 default 配置"}
          </Button>
        </div>
      </div>

      {loading ? (
        <LoadingState />
      ) : routes.length === 0 ? (
        <EmptyState
          title="还没有私有路由"
          description="添加第一条路由，开始将请求转发到你的上游模型。"
          action={
            <Button onClick={() => setModalOpen(true)}>
              <Plus className="h-4 w-4" />
              新增路由
            </Button>
          }
        />
      ) : (
        <TableShell>
          <Table>
            <thead className="bg-slate-50">
              <tr>
                <Th>模型名称</Th>
                <Th>服务商</Th>
                <Th>服务商模型</Th>
                <Th>API 地址</Th>
                <Th>创建时间</Th>
                <Th>操作</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 bg-white">
              {routes.map((route) => (
                <tr key={route.id} className="hover:bg-slate-50/80">
                  <Td className="font-medium text-slate-900">{route.model}</Td>
                  <Td>{route.provider_name}</Td>
                  <Td>{route.provider_model}</Td>
                  <Td className="max-w-xs truncate">{route.provider_base_url}</Td>
                  <Td>{formatDateTime(route.created_at)}</Td>
                  <Td>
                    <div className="flex justify-end gap-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setEditingRoute(route);
                          setModalOpen(true);
                        }}
                      >
                        <Pencil className="h-4 w-4" />
                        编辑
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-rose-600 hover:bg-rose-50 hover:text-rose-700"
                        onClick={() => handleDelete(route)}
                      >
                        <Trash2 className="h-4 w-4" />
                        删除
                      </Button>
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </TableShell>
      )}

      <RouteModal
        open={modalOpen}
        route={editingRoute}
        providers={providers}
        onClose={() => {
          setModalOpen(false);
          setEditingRoute(null);
        }}
        onSave={handleSaveRoute}
      />
    </div>
  );
}
