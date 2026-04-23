import { useEffect, useState } from "react";
import { useAuth } from "../../auth/AuthContext";
import {
  archiveMachine,
  archiveUnit,
  createMachine,
  createUnit,
  listMachines,
  listUnits,
  patchMachine,
  patchUnit,
  purgeMachine,
  purgeUnit,
  restoreMachine,
  type AdminMachine,
  type AdminUnit,
} from "../../api/admin";

export function AdminMachines() {
  const { role } = useAuth();
  const isAdmin = role === "admin";
  const [machines, setMachines] = useState<AdminMachine[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newSlug, setNewSlug] = useState("");
  const [purgeTarget, setPurgeTarget] = useState<AdminMachine | null>(null);
  const [purgeTyped, setPurgeTyped] = useState("");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [unitsByMachine, setUnitsByMachine] = useState<Record<number, AdminUnit[]>>({});
  const [newUnitLabel, setNewUnitLabel] = useState<Record<number, string>>({});
  const [purgeUnitTarget, setPurgeUnitTarget] = useState<AdminUnit | null>(null);
  const [purgeUnitTyped, setPurgeUnitTyped] = useState("");

  async function refresh() {
    try {
      const rows = await listMachines(true);
      setMachines(rows);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await createMachine(newName, newSlug);
      setNewName("");
      setNewSlug("");
      setCreating(false);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleStatus(m: AdminMachine, status: string) {
    setError(null);
    try {
      await patchMachine(m.id, { status });
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleArchive(m: AdminMachine) {
    if (!confirm(`Archive "${m.name}"? History will be preserved.`)) return;
    setError(null);
    try {
      await archiveMachine(m.id);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleRestore(m: AdminMachine) {
    setError(null);
    try {
      await restoreMachine(m.id);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handlePurge() {
    if (!purgeTarget) return;
    setError(null);
    try {
      await purgeMachine(purgeTarget.id, purgeTyped);
      setPurgeTarget(null);
      setPurgeTyped("");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function loadUnits(machineId: number) {
    try {
      const rows = await listUnits(machineId);
      setUnitsByMachine((prev) => ({ ...prev, [machineId]: rows }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function toggleExpand(m: AdminMachine) {
    const next = new Set(expanded);
    if (next.has(m.id)) {
      next.delete(m.id);
    } else {
      next.add(m.id);
      if (!unitsByMachine[m.id]) await loadUnits(m.id);
    }
    setExpanded(next);
  }

  async function handleAddUnit(m: AdminMachine) {
    const label = (newUnitLabel[m.id] ?? "").trim();
    if (!label) return;
    setError(null);
    try {
      await createUnit(m.id, label);
      setNewUnitLabel((prev) => ({ ...prev, [m.id]: "" }));
      await loadUnits(m.id);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleUnitStatus(u: AdminUnit) {
    const next = u.status === "active" ? "maintenance" : "active";
    setError(null);
    try {
      await patchUnit(u.machine_id, u.id, { status: next });
      await loadUnits(u.machine_id);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleUnitRename(u: AdminUnit) {
    const label = window.prompt("New label:", u.label);
    if (!label || label === u.label) return;
    setError(null);
    try {
      await patchUnit(u.machine_id, u.id, { label });
      await loadUnits(u.machine_id);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleUnitArchive(u: AdminUnit) {
    if (!confirm(`Archive unit "${u.label}"?`)) return;
    setError(null);
    try {
      await archiveUnit(u.machine_id, u.id);
      await loadUnits(u.machine_id);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleUnitPurge() {
    if (!purgeUnitTarget) return;
    setError(null);
    try {
      await purgeUnit(purgeUnitTarget.machine_id, purgeUnitTarget.id, purgeUnitTyped);
      const mid = purgeUnitTarget.machine_id;
      setPurgeUnitTarget(null);
      setPurgeUnitTyped("");
      await loadUnits(mid);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const active = machines.filter((m) => !m.archived_at);
  const archived = machines.filter((m) => m.archived_at);

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold text-gray-900">Machines</h2>
        {!creating && (
          <button
            onClick={() => setCreating(true)}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700"
          >
            Add machine
          </button>
        )}
      </div>

      {error && (
        <div className="rounded-md bg-red-50 px-4 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      {creating && (
        <form
          onSubmit={handleCreate}
          className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm"
        >
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <input
              placeholder="Name (e.g. Vinyl Cutter)"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              required
              className="rounded-lg border border-gray-300 px-3 py-2 text-sm"
            />
            <input
              placeholder="slug (e.g. vinyl-cutter)"
              value={newSlug}
              onChange={(e) => setNewSlug(e.target.value)}
              pattern="^[a-z0-9]+(-[a-z0-9]+)*$"
              required
              className="rounded-lg border border-gray-300 px-3 py-2 text-sm font-mono"
            />
          </div>
          <div className="mt-3 flex gap-2">
            <button
              type="submit"
              className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700"
            >
              Create
            </button>
            <button
              type="button"
              onClick={() => setCreating(false)}
              className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      <section>
        <h3 className="mb-3 text-sm font-semibold uppercase text-gray-500">
          Active ({active.length})
        </h3>
        <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-2 text-left text-xs font-semibold uppercase text-gray-500">
                  Name
                </th>
                <th className="px-4 py-2 text-left text-xs font-semibold uppercase text-gray-500">
                  Slug
                </th>
                <th className="px-4 py-2 text-left text-xs font-semibold uppercase text-gray-500">
                  Status
                </th>
                <th className="px-4 py-2 text-right text-xs font-semibold uppercase text-gray-500">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {active.map((m) => (
                <>
                  <tr key={m.id}>
                    <td className="px-4 py-3 text-sm font-medium text-gray-900">
                      <button
                        type="button"
                        onClick={() => toggleExpand(m)}
                        className="mr-2 text-gray-400 hover:text-gray-700"
                        aria-label="Toggle units"
                      >
                        {expanded.has(m.id) ? "▾" : "▸"}
                      </button>
                      {m.name}
                      <span className="ml-2 text-xs text-gray-400">
                        ({m.units?.length ?? 0} unit{(m.units?.length ?? 0) === 1 ? "" : "s"})
                      </span>
                    </td>
                    <td className="px-4 py-3 text-sm font-mono text-gray-600">
                      {m.slug}
                    </td>
                    <td className="px-4 py-3">
                      <select
                        value={m.status}
                        onChange={(e) => handleStatus(m, e.target.value)}
                        className="rounded border border-gray-300 px-2 py-1 text-sm"
                      >
                        <option value="active">active</option>
                        <option value="maintenance">maintenance</option>
                        <option value="offline">offline</option>
                      </select>
                    </td>
                    <td className="px-4 py-3 text-right">
                      {isAdmin && (
                        <div className="flex justify-end gap-2">
                          <button
                            onClick={() => handleArchive(m)}
                            className="rounded-lg border border-gray-300 px-3 py-1 text-sm font-medium text-gray-700 hover:bg-gray-50"
                          >
                            Archive
                          </button>
                          <button
                            onClick={() => setPurgeTarget(m)}
                            className="rounded-lg border border-red-300 px-3 py-1 text-sm font-medium text-red-700 hover:bg-red-50"
                          >
                            Delete…
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                  {expanded.has(m.id) && (
                    <tr className="bg-gray-50">
                      <td colSpan={4} className="px-4 py-3">
                        <div className="space-y-2">
                          <div className="text-xs font-semibold uppercase text-gray-500">
                            Units
                          </div>
                          {(unitsByMachine[m.id] ?? []).length === 0 ? (
                            <div className="text-sm text-gray-500">No units yet.</div>
                          ) : (
                            <ul className="divide-y divide-gray-200 rounded-lg border border-gray-200 bg-white">
                              {(unitsByMachine[m.id] ?? []).map((u) => (
                                <li
                                  key={u.id}
                                  className="flex items-center justify-between gap-4 px-3 py-2"
                                >
                                  <div className="flex items-center gap-3">
                                    <span className="text-sm font-medium text-gray-900">
                                      {u.label}
                                    </span>
                                    <span
                                      className={
                                        "rounded px-2 py-0.5 text-xs font-medium " +
                                        (u.status === "active"
                                          ? "bg-green-100 text-green-800"
                                          : "bg-gray-200 text-gray-700")
                                      }
                                    >
                                      {u.status}
                                    </span>
                                  </div>
                                  <div className="flex gap-2">
                                    <button
                                      onClick={() => handleUnitRename(u)}
                                      className="rounded border border-gray-300 px-2 py-0.5 text-xs text-gray-700 hover:bg-gray-50"
                                    >
                                      Rename
                                    </button>
                                    <button
                                      onClick={() => handleUnitStatus(u)}
                                      className="rounded border border-gray-300 px-2 py-0.5 text-xs text-gray-700 hover:bg-gray-50"
                                    >
                                      {u.status === "active" ? "Maintenance" : "Activate"}
                                    </button>
                                    {isAdmin && (
                                      <>
                                        <button
                                          onClick={() => handleUnitArchive(u)}
                                          className="rounded border border-gray-300 px-2 py-0.5 text-xs text-gray-700 hover:bg-gray-50"
                                        >
                                          Archive
                                        </button>
                                        <button
                                          onClick={() => setPurgeUnitTarget(u)}
                                          className="rounded border border-red-300 px-2 py-0.5 text-xs text-red-700 hover:bg-red-50"
                                        >
                                          Delete…
                                        </button>
                                      </>
                                    )}
                                  </div>
                                </li>
                              ))}
                            </ul>
                          )}
                          <div className="flex gap-2 pt-1">
                            <input
                              placeholder="New unit label (e.g. Prusa MK4)"
                              value={newUnitLabel[m.id] ?? ""}
                              onChange={(e) =>
                                setNewUnitLabel((prev) => ({
                                  ...prev,
                                  [m.id]: e.target.value,
                                }))
                              }
                              className="flex-1 rounded-lg border border-gray-300 px-3 py-1.5 text-sm"
                            />
                            <button
                              onClick={() => handleAddUnit(m)}
                              className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-indigo-700"
                            >
                              Add unit
                            </button>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              ))}
              {active.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-4 py-6 text-center text-sm text-gray-500">
                    No active machines.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {archived.length > 0 && (
        <section>
          <h3 className="mb-3 text-sm font-semibold uppercase text-gray-500">
            Archived ({archived.length})
          </h3>
          <div className="overflow-hidden rounded-xl border border-gray-200 bg-gray-50 shadow-sm">
            <table className="min-w-full divide-y divide-gray-200">
              <tbody className="divide-y divide-gray-100">
                {archived.map((m) => (
                  <tr key={m.id}>
                    <td className="px-4 py-3 text-sm text-gray-600">{m.name}</td>
                    <td className="px-4 py-3 text-sm font-mono text-gray-500">
                      {m.slug}
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-400">
                      archived {m.archived_at}
                    </td>
                    <td className="px-4 py-3 text-right">
                      {isAdmin && (
                        <button
                          onClick={() => handleRestore(m)}
                          className="rounded-lg border border-indigo-300 px-3 py-1 text-sm font-medium text-indigo-700 hover:bg-indigo-50"
                        >
                          Restore
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {purgeUnitTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4">
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
            <h3 className="text-lg font-semibold text-red-700">
              Permanently delete unit “{purgeUnitTarget.label}”
            </h3>
            <p className="mt-2 text-sm text-gray-600">
              This cannot be undone. Historical queue entries will keep their
              rows but lose their unit reference.
            </p>
            <p className="mt-3 text-sm text-gray-700">
              Type the label{" "}
              <code className="rounded bg-gray-100 px-1 font-mono">
                {purgeUnitTarget.label}
              </code>{" "}
              to confirm:
            </p>
            <input
              value={purgeUnitTyped}
              onChange={(e) => setPurgeUnitTyped(e.target.value)}
              className="mt-2 block w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm"
            />
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => {
                  setPurgeUnitTarget(null);
                  setPurgeUnitTyped("");
                }}
                className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                onClick={handleUnitPurge}
                disabled={purgeUnitTyped !== purgeUnitTarget.label}
                className="rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-700 disabled:bg-red-300"
              >
                Delete permanently
              </button>
            </div>
          </div>
        </div>
      )}

      {purgeTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4">
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
            <h3 className="text-lg font-semibold text-red-700">
              Permanently delete “{purgeTarget.name}”
            </h3>
            <p className="mt-2 text-sm text-gray-600">
              This cannot be undone. All queue history and analytics for this
              machine will be destroyed.
            </p>
            <p className="mt-3 text-sm text-gray-700">
              Type the slug <code className="rounded bg-gray-100 px-1 font-mono">{purgeTarget.slug}</code> to confirm:
            </p>
            <input
              value={purgeTyped}
              onChange={(e) => setPurgeTyped(e.target.value)}
              className="mt-2 block w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm"
            />
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => {
                  setPurgeTarget(null);
                  setPurgeTyped("");
                }}
                className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                onClick={handlePurge}
                disabled={purgeTyped !== purgeTarget.slug}
                className="rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-700 disabled:bg-red-300"
              >
                Delete permanently
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
