import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import {
  fetchMe,
  getAuthToken,
  login as apiLogin,
  markOnboarded,
  setAuthToken,
} from "../api/client";

export type Role = "admin" | "staff";

type AuthState = {
  username: string | null;
  role: Role | null;
  onboardedAt: string | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  markOnboardedLocal: () => Promise<void>;
};

const AuthCtx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [username, setUsername] = useState<string | null>(null);
  const [role, setRole] = useState<Role | null>(null);
  const [onboardedAt, setOnboardedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = getAuthToken();
    if (!token) {
      setLoading(false);
      return;
    }
    fetchMe()
      .then((me) => {
        setUsername(me.username);
        setRole(me.role);
        setOnboardedAt(me.onboarded_at);
      })
      .catch(() => setAuthToken(null))
      .finally(() => setLoading(false));
  }, []);

  async function login(u: string, p: string) {
    const res = await apiLogin(u, p);
    setAuthToken(res.token);
    setUsername(res.username);
    setRole(res.role);
    // Pull onboarded_at on login so the tour gate sees fresh state.
    try {
      const me = await fetchMe();
      setOnboardedAt(me.onboarded_at);
    } catch {
      setOnboardedAt(null);
    }
  }

  function logout() {
    setAuthToken(null);
    setUsername(null);
    setRole(null);
    setOnboardedAt(null);
  }

  async function markOnboardedLocal() {
    try {
      await markOnboarded();
    } catch {
      /* network error — still flip locally so we don't loop the tour */
    }
    setOnboardedAt(new Date().toISOString());
  }

  return (
    <AuthCtx.Provider
      value={{
        username,
        role,
        onboardedAt,
        loading,
        login,
        logout,
        markOnboardedLocal,
      }}
    >
      {children}
    </AuthCtx.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
