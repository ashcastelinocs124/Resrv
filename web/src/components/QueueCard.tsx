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
  displayPosition?: number;
  onServe?: () => void;
  onComplete?: (success: boolean) => void;
  onBump?: () => void;
  onRemove?: () => void;
}

export function QueueCard({
  entry,
  displayPosition,
  onServe,
  onComplete,
  onBump,
  onRemove,
}: Props) {
  const borderColor =
    entry.status === "serving"
      ? "border-l-[#E84A27] bg-orange-50/30"
      : entry.status === "waiting"
        ? "border-l-[#13294B]"
        : "border-l-gray-300";

  return (
    <div
      className={`rounded-xl border border-gray-100 bg-white p-3.5 shadow-sm hover:shadow-md transition-all duration-200 border-l-4 ${borderColor}`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate font-semibold text-gray-900 text-sm">
            {entry.purpose === "training" && (
              <span className="inline-flex items-center rounded-md bg-[#E84A27]/10 text-[#E84A27] px-1.5 py-0.5 text-[10px] font-bold mr-1.5 uppercase tracking-wide">Train</span>
            )}
            {entry.full_name ?? entry.discord_name ?? "Unknown"}
          </p>
          <p className="text-xs text-gray-400 mt-0.5">
            {entry.status === "serving"
              ? "serving"
              : `#${displayPosition ?? entry.position}`}{" "}
            &middot; {timeAgo(entry.joined_at)}
          </p>
        </div>
        <StatusBadge status={entry.status} />
      </div>

      <div className="mt-2.5 flex flex-wrap gap-1.5">
        {entry.status === "waiting" && onServe && (
          <ActionBtn color="orange" onClick={onServe}>
            Serve
          </ActionBtn>
        )}
        {entry.status === "waiting" && onBump && (
          <ActionBtn color="navy" onClick={onBump}>
            Bump
          </ActionBtn>
        )}
        {entry.status === "serving" && onComplete && (
          <>
            <ActionBtn color="orange" onClick={() => onComplete(true)}>
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
    orange:
      "bg-[#E84A27]/10 text-[#E84A27] hover:bg-[#E84A27]/20 border-[#E84A27]/20",
    navy: "bg-[#13294B]/10 text-[#13294B] hover:bg-[#13294B]/20 border-[#13294B]/20",
    amber: "bg-amber-50 text-amber-700 hover:bg-amber-100 border-amber-200",
    red: "bg-red-50 text-red-700 hover:bg-red-100 border-red-200",
  };

  return (
    <button
      onClick={onClick}
      className={`rounded-lg border px-2.5 py-1 text-xs font-semibold transition-all duration-200 cursor-pointer hover:shadow-sm ${styles[color] ?? ""}`}
    >
      {children}
    </button>
  );
}
