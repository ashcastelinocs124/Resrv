import { useEffect, useState } from "react";
import { fetchHealth } from "../api/client";

export function ConnectionStatus() {
  const [connected, setConnected] = useState<boolean | null>(null);

  useEffect(() => {
    async function check() {
      try {
        await fetchHealth();
        setConnected(true);
      } catch {
        setConnected(false);
      }
    }
    check();
    const id = setInterval(check, 10000);
    return () => clearInterval(id);
  }, []);

  if (connected === null) return null;

  return (
    <div className="flex items-center gap-2 text-sm">
      <span
        className={`inline-block h-2.5 w-2.5 rounded-full ${connected ? "bg-emerald-500" : "bg-red-500 animate-pulse"}`}
      />
      <span className={connected ? "text-emerald-700" : "text-red-600"}>
        {connected ? "Connected to API" : "API unreachable"}
      </span>
    </div>
  );
}
