import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { login as apiLogin, register as apiRegister, setUnauthorizedHandler } from "@/api/client";
import { getMe } from "@/api/services";

interface AuthContextValue {
  token: string | null;
  username: string | null;
  role: string | null;
  isAdmin: boolean;
  isAuthenticated: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string) => Promise<void>;
  logout: () => void;
  updateToken: (token: string) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(
    () => localStorage.getItem("token"),
  );
  const [username, setUsername] = useState<string | null>(
    () => localStorage.getItem("username"),
  );
  const [role, setRole] = useState<string | null>(
    () => localStorage.getItem("role"),
  );

  const logout = useCallback(() => {
    localStorage.removeItem("token");
    localStorage.removeItem("username");
    localStorage.removeItem("role");
    setToken(null);
    setUsername(null);
    setRole(null);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(logout);
  }, [logout]);

  // 页面刷新后，用已保存的 token 重新拉取用户信息（含角色），保证权限状态与数据库一致
  useEffect(() => {
    const storedToken = localStorage.getItem("token");
    if (!storedToken) return;
    let cancelled = false;
    getMe()
      .then((me) => {
        if (cancelled) return;
        setUsername(me.username);
        setRole(me.role);
        localStorage.setItem("username", me.username);
        localStorage.setItem("role", me.role);
      })
      // 401 已由 authFetch 的未授权处理器统一登出；其余错误（如网络抖动）保留现有会话
      .catch(() => {
        if (cancelled) return;
      });
    return () => {
      cancelled = true;
    };
  }, [logout]);

  const login = useCallback(async (name: string, password: string) => {
    const data = await apiLogin(name, password);
    localStorage.setItem("token", data.access_token);
    localStorage.setItem("username", name);
    localStorage.setItem("role", data.role ?? "user");
    setToken(data.access_token);
    setUsername(name);
    setRole(data.role ?? "user");
  }, []);

  const register = useCallback(async (name: string, password: string) => {
    const data = await apiRegister(name, password);
    localStorage.setItem("token", data.access_token);
    localStorage.setItem("username", name);
    localStorage.setItem("role", data.role ?? "user");
    setToken(data.access_token);
    setUsername(name);
    setRole(data.role ?? "user");
  }, []);

  const updateToken = useCallback((nextToken: string) => {
    localStorage.setItem("token", nextToken);
    setToken(nextToken);
  }, []);

  const value = useMemo(
    () => ({
      token,
      username,
      role,
      isAdmin: role === "admin",
      isAuthenticated: Boolean(token),
      login,
      register,
      logout,
      updateToken,
    }),
    [token, username, role, login, register, logout, updateToken],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return context;
}
