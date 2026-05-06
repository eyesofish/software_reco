import { useState, useRef, useCallback } from "react";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
}

interface Props {
  messages: Message[];
  onMessagesChange: (msgs: Message[]) => void;
  sessionId: string | null;
  onSessionIdChange: (id: string) => void;
}

interface SseEvent {
  type: string;
  delta?: string;
  message?: string;
  final_answer?: string;
  session_id?: string;
  awaiting_human_confirmation?: boolean;
  pending_sub_questions?: string[];
}

export default function ChatStream({ messages, onMessagesChange, sessionId, onSessionIdChange }: Props) {
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [pendingQuestions, setPendingQuestions] = useState<string[] | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || streaming) return;

    const userMsg: Message = { id: crypto.randomUUID(), role: "user" as const, content: text };
    const assistantMsg: Message = { id: crypto.randomUUID(), role: "assistant" as const, content: "" };
    onMessagesChange([...messages, userMsg, assistantMsg]);
    setInput("");
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const resp = await fetch("/proxy/chat/stream", {
        method: "GET",
        headers: { Accept: "text/event-stream" },
        signal: controller.signal,
      });

      if (!resp.body) throw new Error("no response body");
      const reader = resp.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          const jsonStr = line.slice(5).trim();
          if (!jsonStr) continue;
          try {
            const evt: SseEvent = JSON.parse(jsonStr);
            handleEvent(evt, assistantMsg.id);
          } catch {
            // skip unparseable lines
          }
        }
      }
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      onMessagesChange(
        messages.concat(userMsg, {
          id: assistantMsg.id,
          role: "assistant",
          content: "Streaming error. Please try again.",
        }),
      );
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  }, [input, streaming, messages, onMessagesChange]);

  function handleEvent(evt: SseEvent, assistantId: string) {
    switch (evt.type) {
      case "token":
        if (evt.delta) {
          onMessagesChange(
            messages.map((m) => (m.id === assistantId ? { ...m, content: m.content + evt.delta! } : m)),
          );
        }
        break;
      case "final":
        if (evt.session_id) onSessionIdChange(evt.session_id);
        break;
      case "awaiting_confirmation":
        setPendingQuestions(evt.pending_sub_questions ?? null);
        break;
      case "error":
        onMessagesChange(
          messages.map((m) =>
            m.id === assistantId && !m.content ? { ...m, content: `Error: ${evt.message}` } : m,
          ),
        );
        break;
    }
  }

  function handleConfirm(confirmed: boolean) {
    if (!confirmed) {
      setPendingQuestions(null);
      return;
    }
    // TODO: send confirm request to backend
    setPendingQuestions(null);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  return (
    <div>
      <div style={{ border: "1px solid #ccc", borderRadius: 8, padding: 16, minHeight: 300, marginBottom: 16 }}>
        {messages.map((m) => (
          <div key={m.id} style={{ marginBottom: 12 }}>
            <strong>{m.role === "user" ? "You" : "Assistant"}:</strong>
            <div style={{ whiteSpace: "pre-wrap" }}>{m.content || (streaming && m.role === "assistant" ? "..." : "")}</div>
          </div>
        ))}
      </div>

      {pendingQuestions && (
        <div style={{ background: "#f0f4ff", padding: 12, borderRadius: 8, marginBottom: 16 }}>
          <strong>Confirm sub-questions:</strong>
          <ul>
            {pendingQuestions.map((q, i) => (
              <li key={i}>{q}</li>
            ))}
          </ul>
          <button onClick={() => handleConfirm(true)}>Confirm</button>
          <button onClick={() => handleConfirm(false)}>Edit</button>
        </div>
      )}

      <div style={{ display: "flex", gap: 8 }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Type your query..."
          disabled={streaming}
          style={{ flex: 1, padding: 8, borderRadius: 4, border: "1px solid #ccc" }}
        />
        <button onClick={handleSend} disabled={streaming} style={{ padding: "8px 16px" }}>
          Send
        </button>
      </div>
    </div>
  );
}
