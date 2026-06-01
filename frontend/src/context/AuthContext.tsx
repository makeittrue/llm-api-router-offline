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

interface AuthContextValue {
  token: string | null;
  username: string | null;
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

  const logout = useCallback(() => {
    localStorage.removeItem("token");
    localStorage.removeItem("username");
    setToken(null);
    setUsername(null);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(logout);
  }, [logout]);

  const login = useCallback(async (name: string, password: string) => {
    const data = await apiLogin(name, password);
    localStorage.setItem("token", data.access_token);
    localStorage.setItem("username", name);
    setToken(data.access_token);
    setUsername(name);
  }, []);

  const register = useCallback(async (name: string, password: string) => {
    const data = await apiRegister(name, password);
    localStorage.setItem("token", data.access_token);
    localStorage.setItem("username", name);
    setToken(data.access_token);
    setUsername(name);
  }, []);

  const updateToken = useCallback((nextToken: string) => {
    localStorage.setItem("token", nextToken);
    setToken(nextToken);
  }, []);

  const value = useMemo(
    () => ({
      token,
      username,
      isAuthenticated: Boolean(token),
      login,
      register,
      logout,
      updateToken,
    }),
    [token, username, login, register, logout, updateToken],
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
