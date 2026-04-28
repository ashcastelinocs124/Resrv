import { useCallback, useEffect, useState } from "react";
import {
  listPinnedCharts,
  refreshPinnedChart,
  unpinChart,
} from "../../api/client";
import type { PinnedChart } from "../../api/types";
import { ChartFromSpec } from "./ChartFromSpec";

export function CustomCharts() {
  const [charts, setCharts] = useState<PinnedChart[]>([]);
  const [busy, setBusy] = useState<Record<number, boolean>>({});

  const reload = useCallback(() => {
    listPinnedCharts()
      .then(setCharts)
      .catch(() => setCharts([]));
  }, []);

  useEffect(() => {
    reload();
    const onChange = () => reload();
    window.addEventListener("reserv:pinned-charts-changed", onChange);
    return () =>
      window.removeEventListener("reserv:pinned-charts-changed", onChange);
  }, [reload]);

  if (charts.length === 0) return null;

  const handleRefresh = async (chart: PinnedChart) => {
    setBusy((b) => ({ ...b, [chart.id]: true }));
    try {
      const fresh = await refreshPinnedChart(chart.id);
      setCharts((cs) => cs.map((c) => (c.id === chart.id ? fresh : c)));
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      alert(`Refresh failed: ${msg}`);
    } finally {
      setBusy((b) => ({ ...b, [chart.id]: false }));
    }
  };

  const handleUnpin = async (chart: PinnedChart) => {
    if (!confirm(`Unpin "${chart.title}"? This can't be undone.`)) return;
    setBusy((b) => ({ ...b, [chart.id]: true }));
    try {
      await unpinChart(chart.id);
      setCharts((cs) => cs.filter((c) => c.id !== chart.id));
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      alert(`Unpin failed: ${msg}`);
      setBusy((b) => ({ ...b, [chart.id]: false }));
    }
  };

  return (
    <section className="space-y-3">
      <h2 className="text-sm font-medium text-gray-500">Custom charts</h2>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {charts.map((c) => (
          <div
            key={c.id}
            className="rounded-xl bg-white p-5 shadow-sm border border-gray-200"
          >
            <div className="flex items-start justify-between gap-3 mb-3">
              <div className="min-w-0">
                <h3 className="text-sm font-medium text-gray-900 truncate">
                  {c.title}
                </h3>
                {c.created_by_username && (
                  <p className="text-xs text-gray-500 mt-0.5">
                    Pinned by {c.created_by_username}
                  </p>
                )}
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                <button
                  onClick={() => handleRefresh(c)}
                  disabled={busy[c.id]}
                  className="rounded-md border border-gray-300 bg-white px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 cursor-pointer"
                >
                  {busy[c.id] ? "..." : "Refresh"}
                </button>
                <button
                  onClick={() => handleUnpin(c)}
                  disabled={busy[c.id]}
                  className="rounded-md border border-gray-300 bg-white px-2 py-1 text-xs font-medium text-rose-700 hover:bg-rose-50 disabled:opacity-50 cursor-pointer"
                >
                  Unpin
                </button>
              </div>
            </div>
            <ChartFromSpec spec={c.chart_spec} />
          </div>
        ))}
      </div>
    </section>
  );
}
