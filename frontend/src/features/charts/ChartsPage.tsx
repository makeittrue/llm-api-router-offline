import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  ArcElement,
  BarElement,
  CategoryScale,
  Chart as ChartJS,
  Legend,
  LinearScale,
  Tooltip,
} from "chart.js";
import { Bar, Pie } from "react-chartjs-2";
import { getLogSummary } from "@/api/services";
import { Select } from "@/components/ui/Select";
import {
  LoadingState,
  Table,
  TableShell,
  Td,
  Th,
} from "@/components/ui/DataTable";
import { useToast } from "@/context/ToastContext";
import type { LogSummaryItem } from "@/types/api";
import {
  formatCost,
  formatCurrencyTotals,
  formatHitRate,
  formatTokens,
  getMonthOptions,
} from "@/utils/format";

ChartJS.register(
  CategoryScale,
  LinearScale,
  BarElement,
  ArcElement,
  Tooltip,
  Legend,
);

interface ChartsPageProps {
  onStatsChange: (calls: number, tokens: number, costLabel: string) => void;
}

export function ChartsPage({ onStatsChange }: ChartsPageProps) {
  const { showToast } = useToast();
  const [summary, setSummary] = useState<LogSummaryItem[]>([]);
  const [month, setMonth] = useState("");
  const [loading, setLoading] = useState(true);
  const monthOptions = useMemo(() => getMonthOptions(), []);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        const data = await getLogSummary(month || undefined);
        setSummary(data.data);

        let totalCalls = 0;
        let totalTokens = 0;
        const currencyTotals: Record<string, number> = {};
        data.data.forEach((item) => {
          totalCalls += item.call_count;
          totalTokens += item.total_tokens || 0;
          if (item.billing_currency) {
            currencyTotals[item.billing_currency] =
              (currencyTotals[item.billing_currency] || 0) +
              Number(item.estimated_cost || 0);
          }
        });
        onStatsChange(
          totalCalls,
          totalTokens,
          formatCurrencyTotals(currencyTotals),
        );
      } catch (error) {
        showToast(error instanceof Error ? error.message : "加载统计失败", "error");
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [month, onStatsChange, showToast]);

  const labels = summary.map((item) => item.model);
  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { position: "right" as const } },
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">用量统计</h2>
          <p className="mt-1 text-sm text-slate-500">按模型查看调用次数、Token 与费用分布。</p>
        </div>
        <Select
          value={month}
          onChange={(event) => setMonth(event.target.value)}
          className="w-44"
        >
          <option value="">所有月份</option>
          {monthOptions.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </Select>
      </div>

      {loading ? (
        <LoadingState />
      ) : (
        <>
          <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
            <ChartCard title="各模型调用次数">
              <Bar
                options={{ ...chartOptions, plugins: { legend: { display: false } } }}
                data={{
                  labels,
                  datasets: [
                    {
                      label: "调用次数",
                      data: summary.map((item) => item.call_count),
                      backgroundColor: "rgba(79, 70, 229, 0.75)",
                      borderColor: "rgba(79, 70, 229, 1)",
                      borderWidth: 1,
                    },
                  ],
                }}
              />
            </ChartCard>
            <ChartCard title="各模型 Token 消耗">
              <Pie
                options={chartOptions}
                data={{
                  labels,
                  datasets: [
                    {
                      data: summary.map((item) => item.total_tokens || 0),
                      backgroundColor: [
                        "rgba(79, 70, 229, 0.75)",
                        "rgba(16, 185, 129, 0.75)",
                        "rgba(245, 158, 11, 0.75)",
                        "rgba(139, 92, 246, 0.75)",
                        "rgba(239, 68, 68, 0.75)",
                        "rgba(14, 165, 233, 0.75)",
                      ],
                      borderColor: "#fff",
                      borderWidth: 1,
                    },
                  ],
                }}
              />
            </ChartCard>
            <ChartCard title="各模型费用">
              <Bar
                options={{ ...chartOptions, plugins: { legend: { display: false } } }}
                data={{
                  labels,
                  datasets: [
                    {
                      label: "费用",
                      data: summary.map((item) => Number(item.estimated_cost || 0)),
                      backgroundColor: "rgba(245, 158, 11, 0.75)",
                      borderColor: "rgba(245, 158, 11, 1)",
                      borderWidth: 1,
                    },
                  ],
                }}
              />
            </ChartCard>
          </div>

          <div>
            <h3 className="mb-4 text-base font-semibold text-slate-900">详细统计</h3>
            <TableShell>
              <Table>
                <thead className="bg-slate-50">
                  <tr>
                    <Th>模型</Th>
                    <Th>调用次数</Th>
                    <Th>输入 Token</Th>
                    <Th>输出 Token</Th>
                    <Th>缓存命中 Token</Th>
                    <Th>缓存命中率</Th>
                    <Th>总 Token</Th>
                    <Th>费用</Th>
                    <Th>平均耗时 (ms)</Th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 bg-white">
                  {summary.map((item) => (
                    <tr key={item.model}>
                      <Td className="font-medium text-slate-900">{item.model}</Td>
                      <Td>{item.call_count}</Td>
                      <Td>{item.total_prompt_tokens || 0}</Td>
                      <Td>{item.total_completion_tokens || 0}</Td>
                      <Td>{item.cached_input_tokens || 0}</Td>
                      <Td>{formatHitRate(item.cache_hit_rate)}</Td>
                      <Td>{formatTokens(item.total_tokens || 0)}</Td>
                      <Td>{formatCost(item.estimated_cost, item.billing_currency)}</Td>
                      <Td>{Math.round(item.avg_duration_ms || 0)}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </TableShell>
          </div>
        </>
      )}
    </div>
  );
}

function ChartCard({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="panel p-5">
      <h3 className="mb-4 text-base font-semibold text-slate-900">{title}</h3>
      <div className="h-80">{children}</div>
    </div>
  );
}
