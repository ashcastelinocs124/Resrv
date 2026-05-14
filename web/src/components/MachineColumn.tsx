import type { MachineQueue } from "../api/types";
import { QueueCard } from "./QueueCard";
import { StatusBadge } from "./StatusBadge";
import {
  serveEntry,
  completeEntry,
  bumpEntry,
  leaveEntry,
  patchMachineStatus,
  undoRemoval,
} from "../api/client";

const headerGradients: Record<string, string> = {
  active: "from-[#13294B] to-[#1e3a5f]",
  maintenance: "from-amber-500 to-orange-500",
  offline: "from-gray-500 to-gray-600",
};

interface Props {
  queue: MachineQueue;
  onRefresh: () => void;
}

export function MachineColumn({ queue, onRefresh }: Props) {
  const serving = queue.entries.filter((e) => e.status === "serving");
  const waiting = queue.entries.filter((e) => e.status === "waiting");

  const servingByUnit = new Map<number, string>();
  for (const e of serving) {
    const label = e.full_name ?? e.discord_name;
    if (e.unit_id != null && label) {
      servingByUnit.set(e.unit_id, label);
    }
  }
  const showUnitStrip =
    queue.units &&
    queue.units.length > 0 &&
    !(queue.units.length === 1 && queue.units[0].label === "Main");

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
    <div className="flex flex-col rounded-2xl border border-gray-200/60 bg-white shadow-md hover:shadow-lg transition-shadow duration-300 overflow-hidden min-w-[280px] max-w-[340px] w-full">
      {/* Header */}
      <div
        className={`bg-gradient-to-br ${headerGradients[queue.machine_status] ?? "from-gray-500 to-gray-600"} px-4 py-4 text-white`}
      >
        <div className="flex items-center justify-between">
          <h2 className="font-bold text-lg tracking-tight">{queue.machine_name}</h2>
          <StatusBadge status={queue.machine_status} />
        </div>
        <div className="mt-2 flex items-center gap-4 text-sm text-white/80">
          <span className="flex items-center gap-1">
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
            {waiting.length} waiting
          </span>
          <span className="flex items-center gap-1">
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
            {serving.length} serving
          </span>
        </div>
      </div>

      {/* Units chip strip */}
      {showUnitStrip && (
        <div className="flex flex-wrap gap-1.5 px-4 pt-3">
          {queue.units.map((u) => {
            const servingName = servingByUnit.get(u.id);
            const cls =
              u.status === "maintenance"
                ? "bg-gray-100 text-gray-500 border-gray-200"
                : servingName
                  ? "bg-[#13294B]/10 text-[#13294B] border-[#13294B]/20"
                  : "bg-[#E84A27]/10 text-[#E84A27] border-[#E84A27]/20";
            return (
              <span
                key={u.id}
                className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${cls}`}
                title={
                  u.status === "maintenance"
                    ? "maintenance"
                    : servingName
                      ? `in use by ${servingName}`
                      : "available"
                }
              >
                {u.label}
                {servingName ? ` — ${servingName}` : ""}
                {u.status === "maintenance" ? " — maint" : ""}
              </span>
            );
          })}
        </div>
      )}

      {/* Actions */}
      <div className="px-4 pt-3 flex gap-2">
        <button
          onClick={togglePause}
          className={`flex-1 rounded-xl border px-3 py-2 text-xs font-semibold transition-all duration-200 cursor-pointer ${
            queue.machine_status === "active"
              ? "border-amber-200 bg-amber-50 text-amber-700 hover:bg-amber-100 hover:shadow-sm"
              : "border-[#E84A27]/30 bg-[#E84A27]/10 text-[#E84A27] hover:bg-[#E84A27]/20 hover:shadow-sm"
          }`}
        >
          {queue.machine_status === "active" ? "Pause" : "Resume"}
        </button>
        <button
          onClick={() => act(() => undoRemoval(queue.machine_id))}
          className="flex-1 rounded-xl border border-[#13294B]/20 bg-[#13294B]/5 text-[#13294B] hover:bg-[#13294B]/10 hover:shadow-sm px-3 py-2 text-xs font-semibold transition-all duration-200 cursor-pointer"
        >
          Undo Remove
        </button>
      </div>

      {/* Queue entries */}
      <div className="flex flex-col gap-2 p-4 overflow-y-auto max-h-[60vh]">
        {serving.map((entry) => (
          <QueueCard
            key={entry.id}
            entry={entry}
            onComplete={(ok) => act(() => completeEntry(entry.id, ok))}
            onRemove={() => act(() => leaveEntry(entry.id))}
          />
        ))}

        {waiting.length > 0 && serving.length > 0 && (
          <div className="border-t border-dashed border-gray-200 my-1" />
        )}

        {waiting.map((entry, i) => (
          <QueueCard
            key={entry.id}
            entry={entry}
            displayPosition={i + 1}
            onServe={() => act(() => serveEntry(entry.id))}
            onBump={() => act(() => bumpEntry(entry.id))}
            onRemove={() => act(() => leaveEntry(entry.id))}
          />
        ))}

        {queue.entries.length === 0 && (
          <p className="py-8 text-center text-sm text-gray-400 italic">
            Queue is empty
          </p>
        )}
      </div>
    </div>
  );
}
