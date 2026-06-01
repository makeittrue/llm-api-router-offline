import {
  Activity,
  BarChart3,
  Bell,
  Cloud,
  KeyRound,
  ListTree,
  LogOut,
} from "lucide-react";
import { useEffect, useState } from "react";
import { checkHealth } from "@/api/client";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/Button";
import { cn } from "@/utils/format";
import type { TabId } from "@/types/api";

const navItems: Array<{ id: TabId; label: string; icon: typeof ListTree }> = [
  { id: "routes", label: "我的路由", icon: ListTree },
  { id: "providers", label: "全局服务商", icon: Cloud },
  { id: "logs", label: "调用日志", icon: Activity },
  { id: "charts", label: "用量统计", icon: BarChart3 },
  { id: "notifications", label: "通知设置", icon: Bell },
];

interface SidebarProps {
  activeTab: TabId;
  onTabChange: (tab: TabId) => void;
}

export function Sidebar({ activeTab, onTabChange }: SidebarProps) {
  return (
    <aside className="hidden w-64 shrink-0 border-r border-slate-200 bg-white lg:flex lg:flex-col">
      <div className="flex items-center gap-3 border-b border-slate-100 px-5 py-5">
        <img
          src="/static/imgs/20260516-002652.jpeg"
          alt="LLM API Router"
          className="h-10 w-10 rounded-lg object-cover ring-1 ring-slate-200"
        />
        <div>
          <p className="text-sm font-semibold text-slate-900">LLM API Router</p>
          <p className="text-xs text-slate-500">管理控制台</p>
        </div>
      </div>
      <nav className="flex-1 space-y-1 p-3">
        {navItems.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            onClick={() => onTabChange(id)}
            className={cn(
              "flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition",
              activeTab === id
                ? "bg-brand-50 text-brand-700"
                : "text-slate-600 hover:bg-slate-50 hover:text-slate-900",
            )}
          >
            <Icon className="h-4 w-4 shrink-0" />
            {label}
          </button>
        ))}
      </nav>
    </aside>
  );
}

interface HeaderProps {
  activeTab: TabId;
  onTabChange: (tab: TabId) => void;
  onShowToken: () => void;
}

export function Header({ activeTab, onTabChange, onShowToken }: HeaderProps) {
  const { username, logout } = useAuth();
  const [healthy, setHealthy] = useState(true);

  useEffect(() => {
    const run = async () => {
      setHealthy(await checkHealth());
    };
    run();
    const timer = window.setInterval(run, 30000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/90 backdrop-blur">
      <div className="flex flex-col gap-4 px-4 py-4 sm:px-6 lg:px-8">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
              控制台
            </p>
            <h1 className="text-xl font-semibold text-slate-900">
              {navItems.find((item) => item.id === activeTab)?.label}
            </h1>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium",
                healthy
                  ? "bg-emerald-50 text-emerald-700"
                  : "bg-rose-50 text-rose-700",
              )}
            >
              <span
                className={cn(
                  "h-2 w-2 rounded-full",
                  healthy ? "bg-emerald-500 animate-pulse" : "bg-rose-500",
                )}
              />
              {healthy ? "服务正常" : "服务异常"}
            </span>
            <span className="hidden text-sm text-slate-600 sm:inline">
              {username ? `欢迎，${username}` : ""}
            </span>
            <Button variant="secondary" size="sm" onClick={onShowToken}>
              <KeyRound className="h-4 w-4" />
              我的 Token
            </Button>
            <Button variant="ghost" size="sm" onClick={logout}>
              <LogOut className="h-4 w-4" />
              退出
            </Button>
          </div>
        </div>
        <div className="flex gap-2 overflow-x-auto lg:hidden">
          {navItems.map(({ id, label }) => (
            <button
              key={id}
              type="button"
              onClick={() => onTabChange(id)}
              className={cn(
                "whitespace-nowrap rounded-full px-3 py-1.5 text-sm font-medium",
                activeTab === id
                  ? "bg-brand-600 text-white"
                  : "bg-slate-100 text-slate-600",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
    </header>
  );
}
