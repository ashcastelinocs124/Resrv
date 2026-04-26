import { useState } from "react";
import type { AnalyticsPeriod } from "../api/types";
import { useAnalytics } from "../hooks/useAnalytics";
import { SummaryCards } from "../components/analytics/SummaryCards";
import { AISummary } from "../components/analytics/AISummary";
import { AttendanceChart } from "../components/analytics/AttendanceChart";
import { MachineUtilization } from "../components/analytics/MachineUtilization";
import { PeakHours } from "../components/analytics/PeakHours";
import { MachineTable } from "../components/analytics/MachineTable";
import { AnalyticsChat } from "../components/analytics/AnalyticsChat";

const periods: { label: string; value: AnalyticsPeriod }[] = [
  { label: "Day", value: "day" },
  { label: "Week", value: "week" },
  { label: "Month", value: "month" },
];

export function Analytics() {
  const [period, setPeriod] = useState<AnalyticsPeriod>("week");
  const { data, error, loading, refresh } = useAnalytics(period);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {periods.map((p) => (
            <button
              key={p.value}
              onClick={() => setPeriod(p.value)}
              className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors cursor-pointer ${
                period === p.value
                  ? "bg-indigo-600 text-white"
                  : "bg-white text-gray-700 border border-gray-300 hover:bg-gray-50"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
        <button
          onClick={refresh}
          disabled={loading}
          className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 transition-colors cursor-pointer disabled:opacity-50"
        >
          {loading ? "Refreshing..." : "Refresh"}
        </button>
      </div>

      {loading && (
        <div className="flex justify-center py-20">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-gray-300 border-t-indigo-600" />
        </div>
      )}

      {error && !loading && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-center">
          <p className="font-medium text-red-800">Failed to load analytics</p>
          <p className="mt-1 text-sm text-red-600">{error}</p>
        </div>
      )}

      {data && !loading && (
        <>
          <SummaryCards summary={data.summary} />
          <AISummary machines={data.machines} />

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <AttendanceChart data={data.daily_breakdown} />
            <MachineUtilization machines={data.machines} />
          </div>

          <PeakHours machines={data.machines} />
          <MachineTable machines={data.machines} />
        </>
      )}

      <AnalyticsChat period={period} />
    </div>
  );
}
