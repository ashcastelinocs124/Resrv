import { useCallback, useEffect, useState } from "react";
import type { MachineQueue } from "../api/types";
import { fetchAllQueues } from "../api/client";

export function useQueues(pollMs = 3000) {
  const [queues, setQueues] = useState<MachineQueue[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const data = await fetchAllQueues();
      setQueues(data);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to fetch queues");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, pollMs);
    return () => clearInterval(id);
  }, [refresh, pollMs]);

  return { queues, error, loading, refresh };
}
