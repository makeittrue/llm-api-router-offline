import { useCallback, useEffect, useMemo, useState } from "react";
import { getGlobalProviders, getLogSummary, getUserRoutes } from "@/api/services";
import { Header, Sidebar } from "@/components/layout/AppShell";
import { StatCards } from "@/components/layout/StatCards";
import { LoginModal } from "@/components/modals/LoginModal";
import { TokenModal } from "@/components/modals/TokenModal";
import { ChartsPage } from "@/features/charts/ChartsPage";
import { LogsPage } from "@/features/logs/LogsPage";
import { NotificationsPage } from "@/features/notifications/NotificationsPage";
import { ProvidersPage } from "@/features/providers/ProvidersPage";
import { RoutesPage } from "@/features/routes/RoutesPage";
import { useAuth } from "@/context/AuthContext";
import type { DashboardStats, DefaultRouteConfig, TabId } from "@/types/api";
import { formatCurrencyTotals, formatTokens } from "@/utils/format";

export default function App() {
  const { isAuthenticated } = useAuth();
  const [activeTab, setActiveTab] = useState<TabId>("routes");
  const [tokenModalOpen, setTokenModalOpen] = useState(false);
  const [stats, setStats] = useState<DashboardStats>({
    routeCount: 0,
    providerCount: 0,
    totalCalls: 0,
    totalTokens: 0,
    totalCost: "-",
  });
  const [modelOptions, setModelOptions] = useState<string[]>([]);

  useEffect(() => {
    if (!isAuthenticated) return;
    const fetchGlobalStats = async () => {
      try {
        const [summaryData, providersData, routesData] = await Promise.all([
          getLogSummary(),
          getGlobalProviders(),
          getUserRoutes(),
        ]);
        
        let totalCalls = 0;
        let totalTokens = 0;
        const currencyTotals: Record<string, number> = {};
        summaryData.data.forEach((item) => {
          totalCalls += item.call_count;
          totalTokens += item.total_tokens || 0;
          if (item.billing_currency) {
            currencyTotals[item.billing_currency] =
              (currencyTotals[item.billing_currency] || 0) +
              Number(item.estimated_cost || 0);
          }
        });
        
        setStats({
          routeCount: routesData.routes.length,
          providerCount: providersData.providers.length,
          totalCalls,
          totalTokens,
          totalCost: formatCurrencyTotals(currencyTotals),
        });
      } catch (e) {
        console.error("Failed to load global stats", e);
      }
    };
    fetchGlobalStats();
  }, [isAuthenticated]);

  const handleRouteStats = useCallback(
    (routeCount: number, models: string[], defaultRoute: DefaultRouteConfig) => {
      setStats((prev) => ({ ...prev, routeCount }));
      const options = [...new Set(models)].sort();
      if (defaultRoute.enabled && defaultRoute.models.length > 0) {
        options.unshift("default");
      }
      setModelOptions(options);
    },
    [],
  );

  const handleProviderStats = useCallback((providerCount: number) => {
    setStats((prev) => ({ ...prev, providerCount }));
  }, []);

  const handleChartStats = useCallback(
    (totalCalls: number, totalTokens: number, totalCost: string) => {
      setStats((prev) => ({
        ...prev,
        totalCalls,
        totalTokens,
        totalCost,
      }));
    },
    [],
  );

  const statView = useMemo(
    () => ({
      ...stats,
      totalTokens: formatTokens(Number(stats.totalTokens) || 0),
    }),
    [stats],
  );

  if (!isAuthenticated) {
    return <LoginModal />;
  }

  return (
    <div className="min-h-screen lg:flex">
      <Sidebar activeTab={activeTab} onTabChange={setActiveTab} />
      <div className="flex min-h-screen flex-1 flex-col">
        <Header
          activeTab={activeTab}
          onTabChange={setActiveTab}
          onShowToken={() => setTokenModalOpen(true)}
        />
        <main className="flex-1 px-4 py-6 sm:px-6 lg:px-8">
          <div className="mb-6">
            <StatCards stats={statView} />
          </div>
          <div className="panel p-6">
            {activeTab === "routes" ? (
              <RoutesPage onStatsChange={handleRouteStats} />
            ) : null}
            {activeTab === "providers" ? (
              <ProvidersPage onStatsChange={handleProviderStats} />
            ) : null}
            {activeTab === "logs" ? <LogsPage modelOptions={modelOptions} /> : null}
            {activeTab === "charts" ? (
              <ChartsPage onStatsChange={handleChartStats} />
            ) : null}
            {activeTab === "notifications" ? <NotificationsPage /> : null}
          </div>
        </main>
      </div>
      <TokenModal open={tokenModalOpen} onClose={() => setTokenModalOpen(false)} />
    </div>
  );
}
