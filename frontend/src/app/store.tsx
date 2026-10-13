import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, type Dashboard, type Status } from "../lib/api";

type Toast = { id: number; text: string; tone: "ok" | "attention" };

type Ctx = {
  status: Status | null;
  dash: Dashboard | null;
  error: string | null;
  loading: boolean;
  refresh: () => Promise<void>;
  toast: (text: string, tone?: Toast["tone"]) => void;
  toasts: Toast[];
  run: <T>(label: string, fn: () => Promise<T>) => Promise<T | undefined>;
  busy: string | null;
};

const LeashCtx = createContext<Ctx | null>(null);

export function LeashProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [dash, setDash] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const seq = useRef(0);

  const refresh = useCallback(async () => {
    try {
      const [s, d] = await Promise.all([api.status(), api.dashboard()]);
      setStatus(s);
      setDash(d);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not reach the Leash server.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(() => document.visibilityState === "visible" && refresh(), 5000);
    return () => clearInterval(t);
  }, [refresh]);

  const toast = useCallback((text: string, tone: Toast["tone"] = "ok") => {
    const id = ++seq.current;
    setToasts((t) => [...t, { id, text, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4800);
  }, []);

  const run = useCallback(
    async <T,>(label: string, fn: () => Promise<T>) => {
      setBusy(label);
      try {
        const out = await fn();
        await refresh();
        return out;
      } catch (e) {
        toast(e instanceof Error ? e.message : String(e), "attention");
        return undefined;
      } finally {
        setBusy(null);
      }
    },
    [refresh, toast],
  );

  return <LeashCtx.Provider value={{ status, dash, error, loading, refresh, toast, toasts, run, busy }}>{children}</LeashCtx.Provider>;
}

export function useLeash(): Ctx {
  const c = useContext(LeashCtx);
  if (!c) throw new Error("useLeash outside provider");
  return c;
}
