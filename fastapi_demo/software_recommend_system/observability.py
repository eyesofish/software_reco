from collections.abc import Callable
from typing import Any

try:  # pragma: no cover - optional runtime dependency
    from langsmith import traceable as _langsmith_traceable
    from langsmith.wrappers import wrap_openai as _langsmith_wrap_openai
except Exception:  # pragma: no cover - degrade safely when langsmith is unavailable
    _langsmith_traceable = None
    _langsmith_wrap_openai = None


def wrap_openai(client: Any) -> Any:
    """Wrap OpenAI client with LangSmith tracing when available."""
    if _langsmith_wrap_openai is None:
        return client
    return _langsmith_wrap_openai(client)


def traceable(*args: Any, **kwargs: Any) -> Callable[..., Any]:
    """LangSmith traceable decorator with no-op fallback."""
    if _langsmith_traceable is not None:
        return _langsmith_traceable(*args, **kwargs)

    if args and len(args) == 1 and callable(args[0]) and not kwargs:
        return args[0]

    def _decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return func

    return _decorator

