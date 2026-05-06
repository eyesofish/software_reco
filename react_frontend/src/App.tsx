import { useState } from "react";
import ChatStream from "./components/ChatStream";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
}

function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);

  return (
    <div style={{ maxWidth: 720, margin: "0 auto", padding: 24 }}>
      <h1>Software Reco</h1>
      <ChatStream
        messages={messages}
        onMessagesChange={setMessages}
        sessionId={sessionId}
        onSessionIdChange={setSessionId}
      />
    </div>
  );
}

export default App;
