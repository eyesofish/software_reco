from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import json
import os
from uuid import uuid4

import httpx

app = FastAPI()
LLM_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:18000/v1").rstrip("/")
LLM_URL = f"{LLM_BASE_URL}/chat/completions"
LLM_MODEL = os.getenv("LLM_MODEL", "qwen2.5-7b-instruct")


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _build_messages(payload: dict) -> list[dict]:
    messages = payload.get("messages")
    if isinstance(messages, list) and messages:
        return messages

    query = str(payload.get("query") or "").strip()
    if query:
        return [{"role": "user", "content": query}]

    return [{"role": "user", "content": "Hello"}]


async def _stream_llm_sse(session_id: str, messages: list[dict]):
    accumulated_parts: list[str] = []
    request_payload = {
        "model": LLM_MODEL,
        "stream": True,
        "messages": messages,
    }

    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                LLM_URL,
                json=request_payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        continue

                    data = line[len("data:") :].strip()
                    if not data:
                        continue
                    if data == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    try:
                        delta = chunk["choices"][0]["delta"]["content"]
                    except (KeyError, IndexError, TypeError):
                        continue

                    if not isinstance(delta, str) or not delta:
                        continue

                    accumulated_parts.append(delta)
                    yield _sse(
                        "token",
                        {
                            "type": "token",
                            "session_id": session_id,
                            "delta": delta,
                        },
                    )
    except Exception as exc:
        yield _sse(
            "error",
            {
                "type": "error",
                "session_id": session_id,
                "message": f"LLM streaming failed: {exc}",
            },
        )
        return

    yield _sse(
        "final",
        {
            "type": "final",
            "status": "success",
            "session_id": session_id,
            "final_answer": "".join(accumulated_parts),
        },
    )


@app.get("/api/chat/stream")
async def chat_stream():
    """
    关键：media_type 必须是 text/event-stream 或 text/plain。
    不要返回 dict，不要返回 list。
    """
    return StreamingResponse(
        _stream_llm_sse(
            session_id=uuid4().hex,
            messages=[{"role": "user", "content": "Hello"}],
        ),
        media_type="text/event-stream",
    )


@app.post("/api/v1/recommend/stream")
async def recommend_stream(request: Request):
    """
    Compatibility stream endpoint for Spring chat proxy.
    Returns SSE token/final events expected by downstream parser.
    """
    payload = await request.json()
    session_id = str(payload.get("session_id") or uuid4().hex)
    return StreamingResponse(
        _stream_llm_sse(session_id=session_id, messages=_build_messages(payload)),
        media_type="text/event-stream",
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
