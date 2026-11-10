import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiError, api } from "@/services/api";
import type { Preferences, User } from "@/types";
import { setDisplayTimezone } from "@/utils";

interface AuthValue {
  user: User | null | undefined;
  setUser: (user: User) => void;
  logout: () => Promise<void>;
  prefs: Preferences | undefined;
  isDemo: boolean;
}
const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const { data, isPending } = useQuery<User | null>({
    queryKey: ["me"],
    queryFn: async () => {
      try {
        return await api.get<User>("/auth/me");
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
    staleTime: 60_000,
  });
  setDisplayTimezone(data?.preferences?.timezone);

  useEffect(() => {
    const expire = () => { queryClient.clear(); queryClient.setQueryData(["me"], null); };
    window.addEventListener("autopilot:unauthorized", expire);
    return () => window.removeEventListener("autopilot:unauthorized", expire);
  }, [queryClient]);

  const setUser = useCallback((user: User) => queryClient.setQueryData(["me"], user), [queryClient]);
  const logout = useCallback(async () => {
    await api.post("/auth/logout");
    queryClient.clear();
    queryClient.setQueryData(["me"], null);
  }, [queryClient]);

  const value = useMemo<AuthValue>(() => ({ user: isPending ? undefined : (data ?? null), setUser, logout, prefs: data?.preferences, isDemo: data?.preferences.integration_mode !== "live" }), [data, isPending, setUser, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth outside AuthProvider");
  return value;
}

type Theme = "light" | "dark" | "system";
const ThemeContext = createContext<{ theme: Theme; setTheme: (theme: Theme) => void }>({ theme: "system", setTheme: () => undefined });

function readTheme(): Theme {
  try {
    return (localStorage.getItem("autopilot-theme") as Theme) || "system";
  } catch {
    return "system";
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(readTheme);
  useEffect(() => {
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    const apply = () => document.documentElement.classList.toggle("dark", theme === "dark" || (theme === "system" && !!media?.matches));
    apply();
    media?.addEventListener("change", apply);
    return () => media?.removeEventListener("change", apply);
  }, [theme]);
  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    try { localStorage.setItem("autopilot-theme", next); } catch { /* storage may be unavailable */ }
  }, []);
  return <ThemeContext.Provider value={{ theme, setTheme }}>{children}</ThemeContext.Provider>;
}

export const useTheme = () => useContext(ThemeContext);
