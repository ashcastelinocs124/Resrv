import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  deleteChatConversation,
  getChatConversation,
  listChatConversations,
  postChat,
} from "../../api/client";
import type {
  AnalyticsPeriod,
  ChatConversationDetail,
  ChatConversationSummary,
  ChatMessage,
} from "../../api/types";

interface Props {
  period: AnalyticsPeriod;
}

const SUGGESTIONS = [
  "Summarize this period",
  "Which machine had the most no-shows?",
  "Compare the two busiest days",
];

export function AnalyticsChat({ period }: Props) {
  const [open, setOpen] = useState(false);
  const [convs, setConvs] = useState<ChatConversationSummary[]>([]);
  const [active, setActive] = useState<ChatConversationDetail | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showList, setShowList] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  async function refreshList() {
    try {
      setConvs(await listChatConversations());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    if (open) refreshList();
  }, [open]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [active?.messages.length]);

  async function openConv(id: number) {
    setActive(await getChatConversation(id));
    setShowList(false);
  }

  function newConv() {
    setActive({ id: 0, title: "New chat", messages: [] });
    setShowList(false);
  }

  async function send(text: string) {
    const message = text.trim();
    if (!message || sending) return;
    setSending(true);
    setError(null);

    const optimistic: ChatMessage = {
      id: -Date.now(),
      conversation_id: active?.id ?? 0,
      role: "user",
      content: message,
      created_at: new Date().toISOString(),
    };
    setActive((prev) =>
      prev
        ? { ...prev, messages: [...prev.messages, optimistic] }
        : { id: 0, title: message.slice(0, 60), messages: [optimistic] }
    );
    setDraft("");

    try {
      const res = await postChat({
        conversation_id: active?.id || undefined,
        message,
        period,
      });
      const thread = await getChatConversation(res.conversation_id);
      setActive(thread);
      await refreshList();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setActive((prev) =>
        prev
          ? {
              ...prev,
              messages: prev.messages.filter((m) => m.id !== optimistic.id),
            }
          : null
      );
    } finally {
      setSending(false);
    }
  }

  async function handleDelete(id: number) {
    if (!confirm("Delete this conversation?")) return;
    await deleteChatConversation(id);
    if (active?.id === id) setActive(null);
    await refreshList();
  }

  return (
    <>
      {!open && (
        <button
          onClick={() => setOpen(true)}
          className="fixed bottom-6 right-6 z-40 rounded-full bg-indigo-600 px-4 py-3 text-sm font-semibold text-white shadow-lg hover:bg-indigo-700"
        >
          💬 Ask the data
        </button>
      )}

      {open && (
        <div
          className="fixed bottom-6 right-6 z-40 flex h-[560px] w-[380px] flex-col rounded-2xl border border-gray-200 bg-white shadow-2xl"
          onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
        >
          <div className="flex items-center justify-between border-b border-gray-100 px-4 py-3">
            <div>
              <div className="text-sm font-semibold text-gray-900">
                Analytics chat
              </div>
              <div className="text-xs text-gray-500">Scoped to: {period}</div>
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
                className="rounded bg-indigo-600 px-2 py-1 text-xs text-white hover:bg-indigo-700"
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
                  No conversations yet.
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
                  Ask anything about the analytics on this page.
                </p>
                <div className="flex flex-wrap gap-1">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      onClick={() => setDraft(s)}
                      className="rounded-full border border-gray-300 px-2 py-0.5 text-xs text-gray-700 hover:bg-gray-50"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {active?.messages.map((m) => (
              <div
                key={m.id}
                className={`flex ${
                  m.role === "user" ? "justify-end" : "justify-start"
                }`}
              >
                <div
                  className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
                    m.role === "user"
                      ? "bg-indigo-600 text-white"
                      : "bg-gray-100 text-gray-900"
                  }`}
                >
                  {m.role === "user" ? (
                    m.content
                  ) : (
                    <div className="prose prose-sm max-w-none">
                      <ReactMarkdown>{m.content}</ReactMarkdown>
                    </div>
                  )}
                </div>
              </div>
            ))}
            {sending && (
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
            <div className="flex gap-2">
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send(draft);
                  }
                }}
                placeholder="Ask a question…"
                disabled={sending}
                className="flex-1 rounded-lg border border-gray-300 px-3 py-2 text-sm disabled:bg-gray-100"
              />
              <button
                onClick={() => send(draft)}
                disabled={sending || !draft.trim()}
                className="rounded-lg bg-indigo-600 px-3 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:bg-indigo-300"
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
