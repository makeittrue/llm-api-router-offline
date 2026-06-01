import {
  Coins,
  History,
  Route,
  Server,
  Sparkles,
} from "lucide-react";
import type { DashboardStats } from "@/types/api";

const cards = [
  {
    key: "routeCount" as const,
    label: "我的路由数",
    icon: Route,
    tone: "text-brand-600 bg-brand-50",
  },
  {
    key: "providerCount" as const,
    label: "全局服务商",
    icon: Server,
    tone: "text-emerald-600 bg-emerald-50",
  },
  {
    key: "totalCalls" as const,
    label: "总调用次数",
    icon: History,
    tone: "text-violet-600 bg-violet-50",
  },
  {
    key: "totalTokens" as const,
    label: "总 Token 消耗",
    icon: Sparkles,
    tone: "text-amber-600 bg-amber-50",
  },
  {
    key: "totalCost" as const,
    label: "总费用",
    icon: Coins,
    tone: "text-orange-600 bg-orange-50",
  },
];

export function StatCards({ stats }: { stats: DashboardStats }) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
      {cards.map(({ key, label, icon: Icon, tone }) => (
        <div key={key} className="panel p-5">
          <div className="flex items-center gap-4">
            <div className={`rounded-xl p-3 ${tone}`}>
              <Icon className="h-5 w-5" />
            </div>
            <div>
              <p className="text-sm text-slate-500">{label}</p>
              <p className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">
                {key === "totalTokens"
                  ? stats.totalTokens
                  : key === "totalCost"
                    ? stats.totalCost
                    : stats[key]}
              </p>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
