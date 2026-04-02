export interface QueueEntry {
  id: number;
  user_id: number;
  machine_id: number;
  status: "waiting" | "serving" | "completed" | "cancelled" | "no_show";
  position: number;
  joined_at: string;
  serving_at: string | null;
  completed_at: string | null;
  reminded: number;
  job_successful: number | null;
  failure_notes: string | null;
  discord_id: string | null;
  discord_name: string | null;
}

export interface Machine {
  id: number;
  name: string;
  slug: string;
  status: "active" | "maintenance" | "offline";
  created_at: string;
}

export interface MachineQueue {
  machine_id: number;
  machine_name: string;
  machine_slug: string;
  machine_status: string;
  entries: QueueEntry[];
}
