import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ChartSpec } from "../../api/types";

const PIE_COLORS = [
  "#6366f1",
  "#10b981",
  "#f59e0b",
  "#ef4444",
  "#8b5cf6",
  "#06b6d4",
  "#ec4899",
  "#84cc16",
];

interface Props {
  spec: ChartSpec;
  height?: number;
}

export function ChartFromSpec({ spec, height = 260 }: Props) {
  const xField = spec.x.field;
  const yField = spec.y.field;
  const xLabel = spec.x.label ?? xField;
  const yLabel = spec.y.label ?? yField;
  const data = spec.data ?? [];

  if (data.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-gray-300 p-6 text-center text-xs text-gray-500">
        No data to render for {spec.title}.
      </div>
    );
  }

  if (spec.type === "table") {
    return (
      <div className="overflow-x-auto">
        <table className="min-w-full text-xs">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-3 py-2 text-left font-medium text-gray-700">
                {xLabel}
              </th>
              <th className="px-3 py-2 text-left font-medium text-gray-700">
                {yLabel}
              </th>
            </tr>
          </thead>
          <tbody>
            {data.map((row, i) => (
              <tr key={i} className="border-t border-gray-100">
                <td className="px-3 py-1.5 text-gray-800">
                  {String(row[xField] ?? "")}
                </td>
                <td className="px-3 py-1.5 text-gray-800">
                  {String(row[yField] ?? "")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  if (spec.type === "pie") {
    return (
      <ResponsiveContainer width="100%" height={height}>
        <PieChart>
          <Pie
            data={data}
            dataKey={yField}
            nameKey={xField}
            outerRadius={Math.min(110, height / 2 - 20)}
            label={({ name }: { name?: string }) => name ?? ""}
          >
            {data.map((_, i) => (
              <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
            ))}
          </Pie>
          <Tooltip />
          <Legend />
        </PieChart>
      </ResponsiveContainer>
    );
  }

  if (spec.type === "line") {
    return (
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
          <XAxis dataKey={xField} tick={{ fontSize: 12 }} />
          <YAxis tick={{ fontSize: 12 }} />
          <Tooltip />
          <Line
            type="monotone"
            dataKey={yField}
            stroke="#6366f1"
            strokeWidth={2}
            dot={{ r: 3 }}
          />
        </LineChart>
      </ResponsiveContainer>
    );
  }

  // Default: bar chart.
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
        <XAxis dataKey={xField} tick={{ fontSize: 12 }} />
        <YAxis tick={{ fontSize: 12 }} />
        <Tooltip />
        <Bar dataKey={yField} fill="#6366f1" />
      </BarChart>
    </ResponsiveContainer>
  );
}
