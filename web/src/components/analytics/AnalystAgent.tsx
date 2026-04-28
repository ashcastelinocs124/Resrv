import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  deleteAgentConversation,
  getAgentConversation,
  listAgentConversations,
  listAgentModels,
  pinChart,
  postAgentStream,
} from "../../api/client";
import type {
  AgentConversationDetail,
  AgentConversationSummary,
  AgentMessage,
  AgentModelOption,
  ChartSpec,
} from "../../api/types";
import { ChartFromSpec } from "./ChartFromSpec";

const SUGGESTIONS = [
  "Build a bar chart of jobs per machine this week",
  "Top 5 majors by completed jobs this month",
  "Compare last week vs this week — total jobs",
];

const MODEL_STORAGE_KEY = "reserv.agent.model";

export function AnalystAgent() {
  const [open, setOpen] = useState(false);
  const [convs, setConvs] = useState<AgentConversationSummary[]>([]);
  const [active, setActive] = useState<AgentConversationDetail | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showList, setShowList] = useState(false);
  const [toolStatus, setToolStatus] = useState<string | null>(null);
  const [models, setModels] = useState<AgentModelOption[]>([]);
  const [model, setModel] = useState<string>(
    () => localStorage.getItem(MODEL_STORAGE_KEY) ?? ""
  );
  const [pinningId, setPinningId] = useState<number | null>(null);
  const [pinTitle, setPinTitle] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  async function refreshList() {
    try {
      setConvs(await listAgentConversations());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    if (open) refreshList();
  }, [open]);

  useEffect(() => {
    if (!open || models.length > 0) return;
    listAgentModels()
      .then((res) => {
        setModels(res.models);
        if (!model) setModel(res.default);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [open, models.length, model]);

  function pickModel(id: string) {
    setModel(id);
    localStorage.setItem(MODEL_STORAGE_KEY, id);
  }

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [active?.messages.length]);

  async function openConv(id: number) {
    setActive(await getAgentConversation(id));
    setShowList(false);
  }

  function newConv() {
    setActive({ id: 0, title: "New analysis", messages: [] });
    setShowList(false);
  }

  async function send(text: string) {
    const message = text.trim();
    if (!message || sending) return;
    setSending(true);
    setError(null);
    setToolStatus(null);

    const optimisticUser: AgentMessage = {
      id: -Date.now(),
      role: "user",
      content: message,
      chart_spec: null,
      created_at: new Date().toISOString(),
    };
    const streamingAssistant: AgentMessage = {
      id: -(Date.now() + 1),
      role: "assistant",
      content: "",
      chart_spec: null,
      created_at: new Date().toISOString(),
    };
    setActive((prev) =>
      prev
        ? {
            ...prev,
            messages: [...prev.messages, optimisticUser, streamingAssistant],
          }
        : {
            id: 0,
            title: message.slice(0, 60),
            messages: [optimisticUser, streamingAssistant],
          }
    );
    setDraft("");

    let conversationId = active?.id || 0;
    try {
      await postAgentStream(
        {
          conversation_id: active?.id || undefined,
          message,
          model: model || undefined,
        },
        {
          onMeta: (cid) => {
            conversationId = cid;
          },
          onToolCall: (name) => {
            setToolStatus(`Calling ${name}…`);
          },
          onChart: (spec) => {
            setActive((prev) =>
              prev
                ? {
                    ...prev,
                    messages: prev.messages.map((m) =>
                      m.id === streamingAssistant.id
                        ? { ...m, chart_spec: spec }
                        : m
                    ),
                  }
                : prev
            );
          },
          onDelta: (piece) => {
            setActive((prev) =>
              prev
                ? {
                    ...prev,
                    messages: prev.messages.map((m) =>
                      m.id === streamingAssistant.id
                        ? { ...m, content: m.content + piece }
                        : m
                    ),
                  }
                : prev
            );
          },
          onError: (detail) => {
            throw new Error(detail);
          },
        }
      );
      setToolStatus(null);
      const thread = await getAgentConversation(conversationId);
      setActive(thread);
      await refreshList();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setActive((prev) =>
        prev
          ? {
              ...prev,
              messages: prev.messages.filter(
                (m) =>
                  m.id !== optimisticUser.id && m.id !== streamingAssistant.id
              ),
            }
          : null
      );
    } finally {
      setSending(false);
      setToolStatus(null);
    }
  }

  async function handleDelete(id: number) {
    if (!confirm("Delete this analysis?")) return;
    await deleteAgentConversation(id);
    if (active?.id === id) setActive(null);
    await refreshList();
  }

  function startPin(messageId: number, defaultTitle: string) {
    setPinningId(messageId);
    setPinTitle(defaultTitle.slice(0, 80));
  }

  async function confirmPin(spec: ChartSpec) {
    const title = pinTitle.trim() || spec.title || "Pinned chart";
    try {
      await pinChart(spec, title);
      setPinningId(null);
      setPinTitle("");
      window.dispatchEvent(new CustomEvent("reserv:pinned-charts-changed"));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <>
      {!open && (
        <button
          onClick={() => setOpen(true)}
          className="fixed bottom-24 right-6 z-40 rounded-full bg-violet-600 px-4 py-3 text-sm font-semibold text-white shadow-lg hover:bg-violet-700"
        >
          📊 Build a chart
        </button>
      )}

      {open && (
        <div
          className="fixed bottom-6 right-6 z-40 flex h-[80vh] max-h-[820px] w-[min(560px,calc(100vw-3rem))] flex-col rounded-2xl border border-gray-200 bg-white shadow-2xl"
          onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
        >
          <div className="flex items-center justify-between border-b border-gray-100 px-4 py-3">
            <div>
              <div className="text-sm font-semibold text-gray-900">
                Build a chart
              </div>
              <div className="text-xs text-gray-500">
                Tool-calling agent — answers from real data
              </div>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => setShowList((v) => !v)}
                className="rounded border border-gray-300 px-2 py-1 text-xs text-gray-600 hover:bg-gray-50"
                title="Conversations"
              >
                ☰
              </button>
              <button
                onClick={newConv}
                className="rounded bg-violet-600 px-2 py-1 text-xs text-white hover:bg-violet-700"
              >
                + New
              </button>
              <button
                onClick={() => setOpen(false)}
                className="rounded border border-gray-300 px-2 py-1 text-xs text-gray-600 hover:bg-gray-50"
              >
                ✕
              </button>
            </div>
          </div>

          {showList && (
            <div className="max-h-40 overflow-y-auto border-b border-gray-100 bg-gray-50 px-2 py-1">
              {convs.length === 0 && (
                <div className="px-2 py-2 text-xs text-gray-500">
                  No analyses yet.
                </div>
              )}
              {convs.map((c) => (
                <div
                  key={c.id}
                  className="flex items-center justify-between rounded px-2 py-1 text-sm hover:bg-white"
                >
                  <button
                    className="flex-1 truncate text-left text-gray-800"
                    onClick={() => openConv(c.id)}
                  >
                    {c.title}
                  </button>
                  <button
                    onClick={() => handleDelete(c.id)}
                    className="ml-2 text-xs text-red-600 hover:underline"
                  >
                    delete
                  </button>
                </div>
              ))}
            </div>
          )}

          <div
            ref={scrollRef}
            className="flex-1 space-y-3 overflow-y-auto px-3 py-3"
          >
            {(!active || active.messages.length === 0) && (
              <div className="space-y-2">
                <p className="text-sm text-gray-600">
                  Describe a chart — the agent will fetch real data and render
                  it.
                </p>
                <div className="flex flex-col gap-1">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      onClick={() => setDraft(s)}
                      className="rounded-md border border-gray-300 px-2 py-1 text-left text-xs text-gray-700 hover:bg-gray-50"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {active?.messages
              .filter((m) => m.role === "user" || m.role === "assistant")
              .map((m) => (
                <div
                  key={m.id}
                  className={`flex ${
                    m.role === "user" ? "justify-end" : "justify-start"
                  }`}
                >
                  <div
                    className={`max-w-[90%] rounded-lg px-3 py-2 text-sm ${
                      m.role === "user"
                        ? "bg-violet-600 text-white"
                        : "bg-gray-100 text-gray-900"
                    }`}
                  >
                    {m.role === "user" ? (
                      m.content
                    ) : (
                      <div className="space-y-2">
                        {m.content && (
                          <div className="prose prose-sm max-w-none">
                            <ReactMarkdown>{m.content}</ReactMarkdown>
                          </div>
                        )}
                        {m.chart_spec && (
                          <div className="rounded-md border border-gray-200 bg-white p-2">
                            <div className="mb-1 flex items-center justify-between">
                              <span className="text-xs font-medium text-gray-700">
                                {m.chart_spec.title}
                              </span>
                              {m.id > 0 && pinningId !== m.id && (
                                <button
                                  onClick={() =>
                                    startPin(m.id, m.chart_spec!.title)
                                  }
                                  className="rounded border border-gray-300 px-2 py-0.5 text-xs text-gray-700 hover:bg-gray-50"
                                >
                                  Pin
                                </button>
                              )}
                            </div>
                            <ChartFromSpec spec={m.chart_spec} height={220} />
                            {pinningId === m.id && (
                              <div className="mt-2 flex items-center gap-1.5">
                                <input
                                  value={pinTitle}
                                  onChange={(e) => setPinTitle(e.target.value)}
                                  placeholder="Title for the pinned chart"
                                  className="flex-1 rounded border border-gray-300 px-2 py-1 text-xs"
                                />
                                <button
                                  onClick={() => confirmPin(m.chart_spec!)}
                                  className="rounded bg-violet-600 px-2 py-1 text-xs font-medium text-white hover:bg-violet-700"
                                >
                                  Save
                                </button>
                                <button
                                  onClick={() => {
                                    setPinningId(null);
                                    setPinTitle("");
                                  }}
                                  className="rounded border border-gray-300 px-2 py-1 text-xs text-gray-700 hover:bg-gray-50"
                                >
                                  Cancel
                                </button>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            {sending && toolStatus && (
              <div className="flex justify-start">
                <div className="rounded-lg bg-gray-100 px-3 py-2 text-xs italic text-gray-500">
                  {toolStatus}
                </div>
              </div>
            )}
            {sending && !toolStatus && (
              <div className="flex justify-start">
                <div className="rounded-lg bg-gray-100 px-3 py-2 text-sm text-gray-500">
                  ···
                </div>
              </div>
            )}
            {error && (
              <div className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-700">
                {error}
              </div>
            )}
          </div>

          <div className="border-t border-gray-100 p-2">
            <div className="flex items-center gap-2">
              <select
                value={model}
                onChange={(e) => pickModel(e.target.value)}
                disabled={sending || models.length === 0}
                className="max-w-[40%] shrink rounded-lg border border-gray-300 bg-white px-2 py-2 text-xs text-gray-700 disabled:bg-gray-100"
                title="Model"
              >
                {models.length === 0 && <option>loading…</option>}
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))}
              </select>
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send(draft);
                  }
                }}
                placeholder="Describe a chart…"
                disabled={sending}
                className="flex-1 rounded-lg border border-gray-300 px-3 py-2 text-sm disabled:bg-gray-100"
              />
              <button
                onClick={() => send(draft)}
                disabled={sending || !draft.trim()}
                className="rounded-lg bg-violet-600 px-3 py-2 text-sm font-semibold text-white hover:bg-violet-700 disabled:bg-violet-300"
              >
                Send
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
