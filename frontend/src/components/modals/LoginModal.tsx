import { useState } from "react";
import { useAuth } from "@/context/AuthContext";
import { useToast } from "@/context/ToastContext";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";

export function LoginModal() {
  const { login, register } = useAuth();
  const { showToast } = useToast();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (mode: "login" | "register") => {
    if (!username.trim() || !password.trim()) {
      showToast("请输入用户名和密码", "error");
      return;
    }
    setLoading(true);
    try {
      if (mode === "login") {
        await login(username.trim(), password.trim());
        showToast("登录成功", "success");
      } else {
        await register(username.trim(), password.trim());
        showToast("注册成功", "success");
      }
    } catch (error) {
      showToast(error instanceof Error ? error.message : "操作失败", "error");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 p-4 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-8 shadow-2xl">
        <div className="mb-8 text-center">
          <img
            src="/static/imgs/20260516-002652.jpeg"
            alt="LLM API Router"
            className="mx-auto mb-4 h-14 w-14 rounded-2xl object-cover ring-1 ring-slate-200"
          />
          <h1 className="text-2xl font-semibold text-slate-900">LLM API Router</h1>
          <p className="mt-2 text-sm text-slate-500">登录或注册以管理你的私有路由</p>
        </div>
        <div className="space-y-4">
          <Input
            label="用户名"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            placeholder="请输入用户名"
            autoComplete="username"
          />
          <Input
            label="密码"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="请输入密码"
            autoComplete="current-password"
          />
        </div>
        <div className="mt-6 flex gap-3">
          <Button
            className="flex-1"
            disabled={loading}
            onClick={() => submit("login")}
          >
            {loading ? "处理中..." : "登录"}
          </Button>
          <Button
            className="flex-1"
            variant="secondary"
            disabled={loading}
            onClick={() => submit("register")}
          >
            注册
          </Button>
        </div>
      </div>
    </div>
  );
}
