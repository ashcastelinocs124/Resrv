const colours: Record<string, string> = {
  active: "bg-emerald-100 text-emerald-800",
  maintenance: "bg-amber-100 text-amber-800",
  offline: "bg-red-100 text-red-800",
  waiting: "bg-sky-100 text-sky-800",
  serving: "bg-emerald-100 text-emerald-800",
  completed: "bg-gray-100 text-gray-500",
  cancelled: "bg-gray-100 text-gray-500",
  no_show: "bg-red-100 text-red-700",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${colours[status] ?? "bg-gray-100 text-gray-600"}`}
    >
      {status.replace("_", " ")}
    </span>
  );
}
