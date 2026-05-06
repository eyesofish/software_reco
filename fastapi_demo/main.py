import json
import logging
from typing import Optional
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.core.config import settings

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Software Reco SSE Proxy",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id"],
)

LLM_BASE_URL = settings.OPENAI_BASE_URL.rstrip("/")
LLM_URL = f"{LLM_BASE_URL}/chat/completions"


class StreamRequest(BaseModel):
    session_id: Optional[str] = Field(default=None, max_length=128)
    messages: Optional[list[dict[str, str]]] = Field(default=None, max_length=100)
    query: Optional[str] = Field(default=None, max_length=5000)


class ErrorDetail(BaseModel):
    error: dict[str, object]


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _build_messages(payload: StreamRequest) -> list[dict[str, str]]:
    if payload.messages:
        return payload.messages
    query = (payload.query or "").strip()
    if query:
        return [{"role": "user", "content": query}]
    return [{"role": "user", "content": "Hello"}]


async def _stream_llm_sse(session_id: str, messages: list[dict[str, str]]):
    accumulated_parts: list[str] = []
    request_payload = {
        "model": settings.LLM_MODEL,
        "stream": True,
        "messages": messages,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
            async with client.stream("POST", LLM_URL, json=request_payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if not data or data == "[DONE]":
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
                    yield _sse("token", {
                        "type": "token",
                        "session_id": session_id,
                        "delta": delta,
                    })
    except httpx.HTTPStatusError as exc:
        logger.error("llm upstream error: %s %s", exc.response.status_code, exc.response.text[:500])
        yield _sse("error", {
            "type": "error",
            "session_id": session_id,
            "message": f"LLM upstream returned {exc.response.status_code}",
        })
    except Exception as exc:
        logger.exception("llm streaming failed: session_id=%s", session_id)
        yield _sse("error", {
            "type": "error",
            "session_id": session_id,
            "message": "LLM streaming failed",
        })

    yield _sse("final", {
        "type": "final",
        "status": "success",
        "session_id": session_id,
        "final_answer": "".join(accumulated_parts),
    })


@app.get("/api/chat/stream")
async def chat_stream():
    return StreamingResponse(
        _stream_llm_sse(
            session_id=uuid4().hex,
            messages=[{"role": "user", "content": "Hello"}],
        ),
        media_type="text/event-stream",
    )


@app.post("/api/v1/recommend/stream")
async def recommend_stream(payload: StreamRequest):
    session_id = payload.session_id or uuid4().hex
    return StreamingResponse(
        _stream_llm_sse(session_id=session_id, messages=_build_messages(payload)),
        media_type="text/event-stream",
    )


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": f"http/{exc.status_code}",
                "message": exc.detail,
            }
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.SERVER_HOST, port=settings.SERVER_PORT)
