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
  unit_id: number | null;
  discord_id: string | null;
  discord_name: string | null;
}

export type UnitStatus = "active" | "maintenance";

export interface MachineUnit {
  id: number;
  machine_id: number;
  label: string;
  status: UnitStatus;
  archived_at: string | null;
  created_at: string;
}

export interface UnitSummary {
  id: number;
  label: string;
  status: UnitStatus;
}

export interface Machine {
  id: number;
  name: string;
  slug: string;
  status: "active" | "maintenance" | "offline";
  created_at: string;
  archived_at?: string | null;
  units: UnitSummary[];
}

export interface MachineQueue {
  machine_id: number;
  machine_name: string;
  machine_slug: string;
  machine_status: string;
  entries: QueueEntry[];
  units: UnitSummary[];
}

// ── Analytics ───────────────────────────────────────────────────────────

export interface MachineStat {
  machine_id: number;
  machine_name: string;
  total_jobs: number;
  completed_jobs: number;
  unique_users: number;
  avg_wait_mins: number | null;
  avg_serve_mins: number | null;
  no_show_count: number;
  cancelled_count: number;
  failure_count: number;
  peak_hour: number | null;
  ai_summary: string | null;
}

export interface DailyBreakdown {
  date: string;
  total_jobs: number;
  completed_jobs: number;
}

export interface AnalyticsSummary {
  total_jobs: number;
  completed_jobs: number;
  unique_users: number;
  avg_wait_mins: number | null;
  avg_serve_mins: number | null;
  no_show_count: number;
  cancelled_count: number;
  failure_count: number;
}

export interface AnalyticsResponse {
  period: string;
  start_date: string;
  end_date: string;
  summary: AnalyticsSummary;
  machines: MachineStat[];
  daily_breakdown: DailyBreakdown[];
}

export interface TodayResponse {
  date: string;
  machines: MachineStat[];
}

export type AnalyticsPeriod = "day" | "week" | "month";

// ── Analytics chatbot ───────────────────────────────────────────────────

export interface ChatMessage {
  id: number;
  conversation_id: number;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  created_at: string;
}

export interface ChatConversationSummary {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ChatConversationDetail {
  id: number;
  title: string;
  messages: ChatMessage[];
}

export interface ChatPostRequest {
  conversation_id?: number;
  message: string;
  period?: AnalyticsPeriod;
  start_date?: string;
  end_date?: string;
  model?: string;
}

export interface ChatModelOption {
  id: string;
  label: string;
}

export interface ChatModelsResponse {
  default: string;
  models: ChatModelOption[];
}

export interface ChatPostResponse {
  conversation_id: number;
  message: ChatMessage;
}

// ── Colleges ────────────────────────────────────────────────────────────

export interface CollegeSummary {
  id: number;
  name: string;
}

export interface AdminCollege {
  id: number;
  name: string;
  archived_at: string | null;
}
