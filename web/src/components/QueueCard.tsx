import type { QueueEntry } from "../api/types";
import { StatusBadge } from "./StatusBadge";

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso + "Z").getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m`;
  return `${Math.floor(mins / 60)}h ${mins % 60}m`;
}

interface Props {
  entry: QueueEntry;
  onServe?: () => void;
  onComplete?: (success: boolean) => void;
  onBump?: () => void;
  onRemove?: () => void;
}

export function QueueCard({
  entry,
  onServe,
  onComplete,
  onBump,
  onRemove,
}: Props) {
  const borderColor =
    entry.status === "serving"
      ? "border-l-emerald-500"
      : entry.status === "waiting"
        ? "border-l-sky-400"
        : "border-l-gray-300";

  return (
    <div
      className={`rounded-lg border border-gray-200 bg-white p-3 shadow-sm border-l-4 ${borderColor}`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate font-medium text-gray-900 text-sm">
            {entry.discord_name ?? "Unknown"}
          </p>
          <p className="text-xs text-gray-500">
            #{entry.position} &middot; {timeAgo(entry.joined_at)}
          </p>
        </div>
        <StatusBadge status={entry.status} />
      </div>

      <div className="mt-2 flex flex-wrap gap-1.5">
        {entry.status === "waiting" && onServe && (
          <ActionBtn color="emerald" onClick={onServe}>
            Serve
          </ActionBtn>
        )}
        {entry.status === "waiting" && onBump && (
          <ActionBtn color="sky" onClick={onBump}>
            Bump
          </ActionBtn>
        )}
        {entry.status === "serving" && onComplete && (
          <>
            <ActionBtn color="emerald" onClick={() => onComplete(true)}>
              Done
            </ActionBtn>
            <ActionBtn color="amber" onClick={() => onComplete(false)}>
              Issue
            </ActionBtn>
          </>
        )}
        {(entry.status === "waiting" || entry.status === "serving") &&
          onRemove && (
            <ActionBtn color="red" onClick={onRemove}>
              Remove
            </ActionBtn>
          )}
      </div>
    </div>
  );
}

function ActionBtn({
  children,
  color,
  onClick,
}: {
  children: React.ReactNode;
  color: string;
  onClick: () => void;
}) {
  const styles: Record<string, string> = {
    emerald:
      "bg-emerald-50 text-emerald-700 hover:bg-emerald-100 border-emerald-200",
    sky: "bg-sky-50 text-sky-700 hover:bg-sky-100 border-sky-200",
    amber: "bg-amber-50 text-amber-700 hover:bg-amber-100 border-amber-200",
    red: "bg-red-50 text-red-700 hover:bg-red-100 border-red-200",
  };

  return (
    <button
      onClick={onClick}
      className={`rounded border px-2 py-0.5 text-xs font-medium transition-colors cursor-pointer ${styles[color] ?? ""}`}
    >
      {children}
    </button>
  );
}
