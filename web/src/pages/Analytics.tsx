import { useEffect, useState } from "react";
import type {
  AnalyticsPeriod,
  CollegeSummary,
  FeatureFlags,
} from "../api/types";
import { exportAnalytics, fetchFeatures, listColleges } from "../api/client";
import { useAnalytics } from "../hooks/useAnalytics";
import { SummaryCards } from "../components/analytics/SummaryCards";
import { AISummary } from "../components/analytics/AISummary";
import { AttendanceChart } from "../components/analytics/AttendanceChart";
import { MachineUtilization } from "../components/analytics/MachineUtilization";
import { CollegeUtilization } from "../components/analytics/CollegeUtilization";
import { PeakHours } from "../components/analytics/PeakHours";
import { MachineTable } from "../components/analytics/MachineTable";
import { AnalyticsChat } from "../components/analytics/AnalyticsChat";
import { AnalystAgent } from "../components/analytics/AnalystAgent";
import { CustomCharts } from "../components/analytics/CustomCharts";

const periods: { label: string; value: AnalyticsPeriod }[] = [
  { label: "Day", value: "day" },
  { label: "Week", value: "week" },
  { label: "Month", value: "month" },
];

export function Analytics() {
  const [period, setPeriod] = useState<AnalyticsPeriod>("week");
  const [selectedCollegeId, setSelectedCollegeId] = useState<number | null>(
    null,
  );
  const [colleges, setColleges] = useState<CollegeSummary[]>([]);
  const [features, setFeatures] = useState<FeatureFlags | null>(null);
  const { data, error, loading, refresh } = useAnalytics(
    period,
    selectedCollegeId,
  );

  useEffect(() => {
    listColleges()
      .then(setColleges)
      .catch(() => setColleges([]));
    fetchFeatures()
      .then(setFeatures)
      .catch(() => setFeatures(null));
  }, []);

  const selectedCollege = colleges.find((c) => c.id === selectedCollegeId);
  const hasFilters = selectedCollegeId != null;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
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

        <div className="flex items-center gap-2">
          <label
            htmlFor="college-filter"
            className="text-sm font-medium text-gray-600"
          >
            College:
          </label>
          <select
            id="college-filter"
            value={selectedCollegeId ?? ""}
            onChange={(e) =>
              setSelectedCollegeId(
                e.target.value === "" ? null : Number(e.target.value),
              )
            }
            className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 cursor-pointer"
          >
            <option value="">All colleges</option>
            {colleges.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>

          <button
            onClick={refresh}
            disabled={loading}
            className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 transition-colors cursor-pointer disabled:opacity-50"
          >
            {loading ? "Refreshing..." : "Refresh"}
          </button>

          <button
            onClick={() =>
              exportAnalytics("csv", period, selectedCollegeId, null).catch(
                (e) => alert(`Export failed: ${e.message ?? e}`),
              )
            }
            className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 transition-colors cursor-pointer"
          >
            Export CSV
          </button>
          <button
            onClick={() =>
              exportAnalytics("pdf", period, selectedCollegeId, null).catch(
                (e) => alert(`Export failed: ${e.message ?? e}`),
              )
            }
            className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 transition-colors cursor-pointer"
          >
            Export PDF
          </button>
        </div>
      </div>

      {hasFilters && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs uppercase tracking-wide text-gray-500">
            Filters:
          </span>
          {selectedCollegeId != null && (
            <button
              onClick={() => setSelectedCollegeId(null)}
              className="inline-flex items-center gap-1.5 rounded-full bg-violet-100 px-3 py-1 text-xs font-medium text-violet-800 hover:bg-violet-200 transition-colors cursor-pointer"
            >
              College:{" "}
              {selectedCollege?.name ?? `#${selectedCollegeId}`}
              <span aria-hidden className="text-violet-600">
                ×
              </span>
              <span className="sr-only">Clear college filter</span>
            </button>
          )}
        </div>
      )}

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

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <CollegeUtilization
              colleges={data.colleges}
              onSelect={(id) => setSelectedCollegeId(id)}
            />
            <PeakHours machines={data.machines} />
          </div>

          <MachineTable machines={data.machines} />
          <CustomCharts />
        </>
      )}

      <AnalyticsChat period={period} />
      {features?.data_analyst_visible && <AnalystAgent />}
    </div>
  );
}
