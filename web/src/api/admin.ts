import { request } from "./client";
import type { AdminCollege } from "./types";

export type AdminUnit = {
  id: number;
  machine_id: number;
  label: string;
  status: "active" | "maintenance";
  archived_at: string | null;
  created_at: string;
};

export type UnitSummary = {
  id: number;
  label: string;
  status: "active" | "maintenance";
};

export type AdminMachine = {
  id: number;
  name: string;
  slug: string;
  status: string;
  archived_at: string | null;
  created_at: string;
  embed_message_id?: string | null;
  units: UnitSummary[];
};

export const listMachines = (includeArchived = false) =>
  request<AdminMachine[]>(
    `/machines/${includeArchived ? "?include_archived=true" : ""}`
  );

export const createMachine = (name: string, slug: string) =>
  request<AdminMachine>(`/machines/`, {
    method: "POST",
    body: JSON.stringify({ name, slug }),
  });

export const patchMachine = (
  id: number,
  body: Partial<{ name: string; slug: string; status: string }>
) =>
  request<AdminMachine>(`/machines/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });

export const archiveMachine = (id: number) =>
  request<{ status: string }>(`/machines/${id}`, { method: "DELETE" });

export const purgeMachine = (id: number, confirm_slug: string) =>
  request<{ status: string; queue_entries: number; analytics_snapshots: number }>(
    `/machines/${id}?purge=true`,
    {
      method: "DELETE",
      body: JSON.stringify({ confirm_slug }),
    }
  );

export const restoreMachine = (id: number) =>
  request<AdminMachine>(`/machines/${id}/restore`, { method: "POST" });

// ── Machine Units ──

export const listUnits = (machineId: number, includeArchived = false) =>
  request<AdminUnit[]>(
    `/machines/${machineId}/units/${includeArchived ? "?include_archived=true" : ""}`
  );

export const createUnit = (machineId: number, label: string) =>
  request<AdminUnit>(`/machines/${machineId}/units/`, {
    method: "POST",
    body: JSON.stringify({ label }),
  });

export const patchUnit = (
  machineId: number,
  unitId: number,
  body: Partial<{ label: string; status: "active" | "maintenance" }>
) =>
  request<AdminUnit>(`/machines/${machineId}/units/${unitId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });

export const archiveUnit = (machineId: number, unitId: number) =>
  request<{ status: string }>(`/machines/${machineId}/units/${unitId}`, {
    method: "DELETE",
  });

export const purgeUnit = (
  machineId: number,
  unitId: number,
  confirm_label: string
) =>
  request<{ status: string }>(
    `/machines/${machineId}/units/${unitId}?purge=true`,
    { method: "DELETE", body: JSON.stringify({ confirm_label }) }
  );

export const restoreUnit = (machineId: number, unitId: number) =>
  request<AdminUnit>(`/machines/${machineId}/units/${unitId}/restore`, {
    method: "POST",
  });

// ── Staff ──

export type StaffRow = {
  id: number;
  username: string;
  role: "admin" | "staff";
  created_at: string;
};

export const listStaff = () => request<StaffRow[]>(`/staff/`);

export const createStaff = (
  username: string,
  password: string,
  role: "admin" | "staff"
) =>
  request<StaffRow>(`/staff/`, {
    method: "POST",
    body: JSON.stringify({ username, password, role }),
  });

export const patchStaff = (
  id: number,
  body: { role?: "admin" | "staff"; password?: string }
) =>
  request<StaffRow>(`/staff/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });

export const deleteStaff = (id: number) =>
  request<{ status: string }>(`/staff/${id}`, { method: "DELETE" });

// ── Settings ──

export const getSettings = () =>
  request<Record<string, string>>(`/settings/`);

export const patchSettings = (updates: Record<string, string>) =>
  request<Record<string, string>>(`/settings/`, {
    method: "PATCH",
    body: JSON.stringify(updates),
  });

export const getPublicSettings = () =>
  request<{ public_mode: string; maintenance_banner: string }>(
    `/public-settings/`
  );

// ── Colleges ──

export const listAllColleges = () =>
  request<AdminCollege[]>(`/colleges/?include_archived=true`);

export const createCollege = (name: string) =>
  request<AdminCollege>(`/colleges/`, {
    method: "POST",
    body: JSON.stringify({ name }),
  });

export const patchCollege = (id: number, name: string) =>
  request<AdminCollege>(`/colleges/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });

export const archiveCollege = (id: number) =>
  request<{ status: string }>(`/colleges/${id}`, { method: "DELETE" });

export const restoreCollege = (id: number) =>
  request<AdminCollege>(`/colleges/${id}/restore`, { method: "POST" });

export const purgeCollege = (id: number, confirm_name: string) =>
  request<{ status: string }>(`/colleges/${id}?purge=true`, {
    method: "DELETE",
    body: JSON.stringify({ confirm_name }),
  });
