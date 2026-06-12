import { Fragment, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { getLogs } from "@/api/services";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Select";
import {
  LoadingState,
  Table,
  TableShell,
  Td,
  Th,
} from "@/components/ui/DataTable";
import { useToast } from "@/context/ToastContext";
import type { CallLog } from "@/types/api";
import { formatCost, formatDateTime, formatHitRate, parseJsonDisplay } from "@/utils/format";

const PAGE_SIZE = 20;

interface LogsPageProps {
  modelOptions: string[];
}

export function LogsPage({ modelOptions }: LogsPageProps) {
  const { showToast } = useToast();
  const [logs, setLogs] = useState<CallLog[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [model, setModel] = useState("");
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const loadLogs = async (nextPage = page, nextModel = model) => {
    setLoading(true);
    try {
      const offset = nextPage * PAGE_SIZE;
      const data = await getLogs({
        limit: PAGE_SIZE,
        offset,
        model: nextModel || undefined,
      });
      const totalCount = typeof data.total === "number" ? data.total : data.data.length;
      if (nextPage > 0 && offset >= totalCount && totalCount > 0) {
        const lastPage = Math.max(0, Math.ceil(totalCount / PAGE_SIZE) - 1);
        setPage(lastPage);
        return loadLogs(lastPage, nextModel);
      }
      setLogs(data.data);
      setTotal(totalCount);
    } catch (error) {
      showToast(error instanceof Error ? error.message : "加载日志失败", "error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadLogs();
  }, [page]);

  const totalPages = total === 0 ? 1 : Math.ceil(total / PAGE_SIZE);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">调用日志</h2>
          <p className="mt-1 text-sm text-slate-500">点击行可展开请求、排查与计费详情。</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Select
            value={model}
            onChange={(event) => {
              setModel(event.target.value);
              setPage(0);
              loadLogs(0, event.target.value);
            }}
          >
            <option value="">所有模型</option>
            {modelOptions.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </Select>
          <Button variant="secondary" onClick={() => loadLogs()}>
            <RefreshCw className="h-4 w-4" />
            刷新
          </Button>
        </div>
      </div>

      {loading ? (
        <LoadingState />
      ) : (
        <>
          <TableShell>
            <Table>
              <thead className="bg-slate-50">
                <tr>
                  <Th>时间</Th>
                  <Th>模型</Th>
                  <Th>服务商</Th>
                  <Th>总 Token</Th>
                  <Th>缓存命中率</Th>
                  <Th>费用</Th>
                  <Th>耗时 (ms)</Th>
                  <Th>状态</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 bg-white">
                {logs.map((log) => (
                  <Fragment key={log.id}>
                    <tr
                      className="cursor-pointer hover:bg-slate-50/80"
                      onClick={() =>
                        setExpandedId((current) =>
                          current === log.id ? null : log.id,
                        )
                      }
                    >
                      <Td>{formatDateTime(log.created_at)}</Td>
                      <Td className="font-medium text-slate-900">{log.model}</Td>
                      <Td>{log.provider || "-"}</Td>
                      <Td>{log.total_tokens || 0}</Td>
                      <Td>{formatHitRate(log.cache_hit_rate)}</Td>
                      <Td>{formatCost(log.estimated_cost, log.billing_currency)}</Td>
                      <Td>{log.duration_ms || 0}</Td>
                      <Td>
                        <Badge variant={log.status === "success" ? "success" : "danger"}>
                          {log.status === "success" ? "成功" : "失败"}
                        </Badge>
                      </Td>
                    </tr>
                    {expandedId === log.id ? (
                      <tr key={`${log.id}-detail`} className="bg-slate-50">
                        <Td colSpan={7}>
                          <div className="grid grid-cols-1 gap-4 py-2 lg:grid-cols-3">
                            <DetailBlock
                              title="请求消息"
                              content={parseJsonDisplay(log.request_messages)}
                            />
                            <DetailBlock
                              title="排查信息"
                              content={parseJsonDisplay(log.log_meta)}
                            />
                            <div className="space-y-3">
                              <DetailBlock
                                title="计费信息"
                                content={parseJsonDisplay(log.billing_meta)}
                              />
                              <DetailBlock
                                title="错误信息"
                                content={log.error_message || "-"}
                              />
                            </div>
                          </div>
                        </Td>
                      </tr>
                    ) : null}
                  </Fragment>
                ))}
              </tbody>
            </Table>
          </TableShell>

          <div className="flex flex-col items-center justify-between gap-3 border-t border-slate-200 pt-4 text-sm text-slate-600 sm:flex-row">
            <span>
              {total === 0
                ? "共 0 条"
                : `共 ${total} 条 · 第 ${Math.min(page + 1, totalPages)} / ${totalPages} 页`}
            </span>
            <div className="flex gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={page <= 0 || total === 0}
                onClick={() => setPage((current) => Math.max(0, current - 1))}
              >
                上一页
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={total === 0 || (page + 1) * PAGE_SIZE >= total}
                onClick={() => setPage((current) => current + 1)}
              >
                下一页
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function DetailBlock({ title, content }: { title: string; content: string }) {
  return (
    <div>
      <h4 className="mb-2 text-sm font-semibold text-slate-700">{title}</h4>
      <pre className="max-h-64 overflow-auto rounded-lg bg-white p-3 text-xs text-slate-700 ring-1 ring-slate-200">
        {content}
      </pre>
    </div>
  );
}
