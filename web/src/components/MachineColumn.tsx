import type { MachineQueue } from "../api/types";
import { QueueCard } from "./QueueCard";
import { StatusBadge } from "./StatusBadge";
import {
  serveEntry,
  completeEntry,
  bumpEntry,
  leaveEntry,
  patchMachineStatus,
} from "../api/client";

const headerColors: Record<string, string> = {
  active: "bg-emerald-600",
  maintenance: "bg-amber-500",
  offline: "bg-red-600",
};

interface Props {
  queue: MachineQueue;
  onRefresh: () => void;
}

export function MachineColumn({ queue, onRefresh }: Props) {
  const serving = queue.entries.filter((e) => e.status === "serving");
  const waiting = queue.entries.filter((e) => e.status === "waiting");

  async function act(fn: () => Promise<unknown>) {
    try {
      await fn();
      onRefresh();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Action failed");
    }
  }

  async function togglePause() {
    const next =
      queue.machine_status === "active" ? "maintenance" : "active";
    await act(() => patchMachineStatus(queue.machine_id, next));
  }

  return (
    <div className="flex flex-col rounded-xl border border-gray-200 bg-gray-50 shadow-sm overflow-hidden min-w-[280px] max-w-[340px] w-full">
      {/* Header */}
      <div
        className={`${headerColors[queue.machine_status] ?? "bg-gray-500"} px-4 py-3 text-white`}
      >
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-base">{queue.machine_name}</h2>
          <StatusBadge status={queue.machine_status} />
        </div>
        <div className="mt-1 flex items-center gap-3 text-sm text-white/80">
          <span>{waiting.length} waiting</span>
          <span>&middot;</span>
          <span>{serving.length} serving</span>
        </div>
      </div>

      {/* Pause / Resume */}
      <div className="px-3 pt-2">
        <button
          onClick={togglePause}
          className={`w-full rounded-md border px-3 py-1.5 text-xs font-medium transition-colors cursor-pointer ${
            queue.machine_status === "active"
              ? "border-amber-300 bg-amber-50 text-amber-700 hover:bg-amber-100"
              : "border-emerald-300 bg-emerald-50 text-emerald-700 hover:bg-emerald-100"
          }`}
        >
          {queue.machine_status === "active" ? "Pause Machine" : "Resume Machine"}
        </button>
      </div>

      {/* Queue entries */}
      <div className="flex flex-col gap-2 p-3 overflow-y-auto max-h-[60vh]">
        {serving.map((entry) => (
          <QueueCard
            key={entry.id}
            entry={entry}
            onComplete={(ok) => act(() => completeEntry(entry.id, ok))}
            onRemove={() => act(() => leaveEntry(entry.id))}
          />
        ))}

        {waiting.length > 0 && serving.length > 0 && (
          <div className="border-t border-dashed border-gray-300 my-1" />
        )}

        {waiting.map((entry) => (
          <QueueCard
            key={entry.id}
            entry={entry}
            onServe={() => act(() => serveEntry(entry.id))}
            onBump={() => act(() => bumpEntry(entry.id))}
            onRemove={() => act(() => leaveEntry(entry.id))}
          />
        ))}

        {queue.entries.length === 0 && (
          <p className="py-8 text-center text-sm text-gray-400">
            Queue is empty
          </p>
        )}
      </div>
    </div>
  );
}
