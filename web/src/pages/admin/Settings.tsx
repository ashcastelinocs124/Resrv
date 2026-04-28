import { useEffect, useState } from "react";
import { getSettings, patchSettings } from "../../api/admin";

const NUMERIC_KEYS = [
  "reminder_minutes",
  "grace_minutes",
  "queue_reset_hour",
  "agent_tick_seconds",
] as const;

const LABELS: Record<string, string> = {
  reminder_minutes: "Reminder after (minutes)",
  grace_minutes: "Grace period (minutes)",
  queue_reset_hour: "Daily reset hour (0–23)",
  agent_tick_seconds: "Agent tick interval (seconds)",
  public_mode: "Public mode (skip Illinois email check)",
  maintenance_banner: "Maintenance banner",
  data_analyst_enabled: "Enable data-analyst agent",
  data_analyst_visible_to_staff:
    "Visible to staff (uncheck = admin-only)",
};

export function AdminSettings() {
  const [values, setValues] = useState<Record<string, string>>({});
  const [initial, setInitial] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  async function refresh() {
    try {
      const data = await getSettings();
      setValues(data);
      setInitial(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  function update(key: string, value: string) {
    setValues({ ...values, [key]: value });
    setSaved(false);
  }

  const dirty = Object.keys(values).some((k) => values[k] !== initial[k]);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const diff: Record<string, string> = {};
      for (const k of Object.keys(values)) {
        if (values[k] !== initial[k]) diff[k] = values[k];
      }
      const updated = await patchSettings(diff);
      setValues(updated);
      setInitial(updated);
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold text-gray-900">Settings</h2>

      {error && (
        <div className="rounded-md bg-red-50 px-4 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <h3 className="text-sm font-semibold uppercase text-gray-500">
          Queue behavior
        </h3>
        <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
          {NUMERIC_KEYS.map((key) => (
            <label key={key} className="block text-sm">
              <span className="font-medium text-gray-700">{LABELS[key]}</span>
              <input
                type="number"
                value={values[key] ?? ""}
                onChange={(e) => update(key, e.target.value)}
                className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2"
              />
            </label>
          ))}
        </div>
      </section>

      <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <h3 className="text-sm font-semibold uppercase text-gray-500">
          Feature toggles
        </h3>
        <div className="mt-4 space-y-4">
          <label className="flex items-center gap-3 text-sm">
            <input
              type="checkbox"
              checked={values.public_mode === "true"}
              onChange={(e) =>
                update("public_mode", e.target.checked ? "true" : "false")
              }
              className="h-4 w-4"
            />
            <span className="font-medium text-gray-700">
              {LABELS.public_mode}
            </span>
          </label>
          <label className="block text-sm">
            <span className="font-medium text-gray-700">
              {LABELS.maintenance_banner}
            </span>
            <textarea
              value={values.maintenance_banner ?? ""}
              onChange={(e) => update("maintenance_banner", e.target.value)}
              rows={2}
              placeholder="Shown to all users when non-empty (e.g. “Planned maintenance 5–6pm”)"
              className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2"
            />
          </label>
        </div>
      </section>

      <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <h3 className="text-sm font-semibold uppercase text-gray-500">
          Data analyst agent
        </h3>
        <p className="mt-1 text-xs text-gray-500">
          Floating chart-builder. Pinned charts persist on the analytics page.
        </p>
        <div className="mt-4 space-y-3">
          <label className="flex items-center gap-3 text-sm">
            <input
              type="checkbox"
              checked={values.data_analyst_enabled === "true"}
              onChange={(e) =>
                update(
                  "data_analyst_enabled",
                  e.target.checked ? "true" : "false",
                )
              }
              className="h-4 w-4"
            />
            <span className="font-medium text-gray-700">
              {LABELS.data_analyst_enabled}
            </span>
          </label>
          <label className="flex items-center gap-3 text-sm">
            <input
              type="checkbox"
              checked={values.data_analyst_visible_to_staff === "true"}
              disabled={values.data_analyst_enabled !== "true"}
              onChange={(e) =>
                update(
                  "data_analyst_visible_to_staff",
                  e.target.checked ? "true" : "false",
                )
              }
              className="h-4 w-4 disabled:opacity-40"
            />
            <span
              className={`font-medium ${
                values.data_analyst_enabled === "true"
                  ? "text-gray-700"
                  : "text-gray-400"
              }`}
            >
              {LABELS.data_analyst_visible_to_staff}
            </span>
          </label>
        </div>
      </section>

      <div className="flex items-center gap-3">
        <button
          onClick={save}
          disabled={!dirty || saving}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:bg-indigo-300"
        >
          {saving ? "Saving…" : "Save changes"}
        </button>
        {saved && !dirty && (
          <span className="text-sm text-green-700">Saved.</span>
        )}
      </div>
    </div>
  );
}
