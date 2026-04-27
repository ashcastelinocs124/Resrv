import type {
  AnalyticsResponse,
  ChatConversationDetail,
  ChatConversationSummary,
  ChatModelsResponse,
  ChatPostRequest,
  ChatPostResponse,
  CollegeSummary,
  Machine,
  MachineQueue,
  QueueEntry,
  TodayResponse,
} from "./types";

const BASE = "/api";
const TOKEN_KEY = "reserv.auth.token";

export function getAuthToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setAuthToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export async function request<T>(
  path: string,
  opts?: RequestInit
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((opts?.headers as Record<string, string>) ?? {}),
  };
  const token = getAuthToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${BASE}${path}`, { ...opts, headers });
  if (res.status === 401) {
    setAuthToken(null);
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function exportAnalytics(
  format: "csv" | "pdf",
  period: string,
  collegeId: number | null,
  machineId: number | null,
): Promise<void> {
  const qs = new URLSearchParams({ format, period });
  if (collegeId != null) qs.set("college_id", String(collegeId));
  if (machineId != null) qs.set("machine_id", String(machineId));
  const headers: Record<string, string> = {};
  const token = getAuthToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${BASE}/analytics/export?${qs.toString()}`, {
    headers,
  });
  if (res.status === 401) {
    setAuthToken(null);
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  const blob = await res.blob();
  const disp = res.headers.get("content-disposition") ?? "";
  const m = disp.match(/filename="([^"]+)"/);
  const filename = m?.[1] ?? `reserv-analytics.${format}`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// -- Auth --

export const login = (username: string, password: string) =>
  request<{ token: string; username: string; role: "admin" | "staff" }>(
    "/auth/login",
    {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }
  );

export const fetchMe = () =>
  request<{ username: string; staff_id: number; role: "admin" | "staff" }>(
    "/auth/me"
  );

// -- Machines --

export const fetchMachines = () => request<Machine[]>("/machines/");

export const patchMachineStatus = (id: number, status: string) =>
  request<Machine>(`/machines/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });

// -- Queues --

export const fetchAllQueues = () => request<MachineQueue[]>("/queue/");

export const fetchMachineQueue = (machineId: number) =>
  request<QueueEntry[]>(`/queue/${machineId}`);

export const serveEntry = (entryId: number) =>
  request<QueueEntry>(`/queue/${entryId}/serve`, { method: "POST" });

export const leaveEntry = (entryId: number) =>
  request<QueueEntry>(`/queue/${entryId}/leave`, { method: "POST" });

export const completeEntry = (
  entryId: number,
  jobSuccessful: boolean,
  failureNotes?: string
) =>
  request<QueueEntry>(`/queue/${entryId}/complete`, {
    method: "POST",
    body: JSON.stringify({
      job_successful: jobSuccessful,
      failure_notes: failureNotes ?? null,
    }),
  });

export const bumpEntry = (entryId: number) =>
  request<QueueEntry>(`/queue/${entryId}/bump`, { method: "POST" });

// -- Colleges (public) --

export const listColleges = () => request<CollegeSummary[]>("/colleges/");

// -- Health --

export const fetchHealth = () => request<{ status: string }>("/health");

// -- Analytics --

export const fetchAnalytics = (params?: {
  period?: string;
  start_date?: string;
  end_date?: string;
  college_id?: number | null;
}) => {
  const qs = new URLSearchParams();
  if (params?.period) qs.set("period", params.period);
  if (params?.start_date) qs.set("start_date", params.start_date);
  if (params?.end_date) qs.set("end_date", params.end_date);
  if (params?.college_id != null) qs.set("college_id", String(params.college_id));
  const query = qs.toString();
  return request<AnalyticsResponse>(
    `/analytics/${query ? `?${query}` : ""}`
  );
};

export const fetchMachineAnalytics = (
  machineId: number,
  params?: {
    period?: string;
    start_date?: string;
    end_date?: string;
    college_id?: number | null;
  }
) => {
  const qs = new URLSearchParams();
  if (params?.period) qs.set("period", params.period);
  if (params?.start_date) qs.set("start_date", params.start_date);
  if (params?.end_date) qs.set("end_date", params.end_date);
  if (params?.college_id != null) qs.set("college_id", String(params.college_id));
  const query = qs.toString();
  return request<AnalyticsResponse>(
    `/analytics/${machineId}${query ? `?${query}` : ""}`
  );
};

export const fetchTodayStats = () =>
  request<TodayResponse>("/analytics/today");

// -- Analytics chatbot --

export const postChat = (body: ChatPostRequest) =>
  request<ChatPostResponse>("/analytics/chat", {
    method: "POST",
    body: JSON.stringify(body),
  });

/**
 * Stream chat replies as SSE.
 *
 * The server emits `data: <json>` frames separated by blank lines:
 *   - {type:"meta", conversation_id}
 *   - {type:"delta", content}        (zero or more)
 *   - {type:"done", message_id}
 *   - {type:"error", detail}
 *
 * Calls handlers as events arrive. Returns when the stream closes.
 */
export async function postChatStream(
  body: ChatPostRequest,
  handlers: {
    onMeta?: (conversationId: number) => void;
    onDelta?: (content: string) => void;
    onDone?: (messageId: number) => void;
    onError?: (detail: string) => void;
  }
): Promise<void> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };
  const token = getAuthToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${BASE}/analytics/chat/stream`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (res.status === 401) {
    setAuthToken(null);
    throw new Error("Unauthorized");
  }
  if (!res.ok || !res.body) {
    const errBody = await res.json().catch(() => ({}));
    throw new Error(errBody.detail || `HTTP ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by blank lines (\n\n).
    let idx;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      try {
        const evt = JSON.parse(line.slice(6));
        if (evt.type === "meta") handlers.onMeta?.(evt.conversation_id);
        else if (evt.type === "delta") handlers.onDelta?.(evt.content);
        else if (evt.type === "done") handlers.onDone?.(evt.message_id);
        else if (evt.type === "error") handlers.onError?.(evt.detail);
      } catch {
        // skip malformed frames
      }
    }
  }
}

export const listChatConversations = () =>
  request<ChatConversationSummary[]>("/analytics/chat/conversations");

export const getChatConversation = (id: number) =>
  request<ChatConversationDetail>(`/analytics/chat/conversations/${id}`);

export const deleteChatConversation = (id: number) =>
  request<{ status: string }>(`/analytics/chat/conversations/${id}`, {
    method: "DELETE",
  });

export const listChatModels = () =>
  request<ChatModelsResponse>("/analytics/chat/models");
