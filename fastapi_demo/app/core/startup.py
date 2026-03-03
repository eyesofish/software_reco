import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.api.v1.startup_ingest import run_startup_ingestion_if_needed
from app.core.config import settings

logger = logging.getLogger(__name__)


class _EmbeddingLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        logger_name = record.name or ""
        return (
            logger_name.startswith("software_recommend_system.ingestion.")
            or logger_name == "app.api.v1.startup_ingest"
            or logger_name == "app.core.startup"
        )


class _TavilyLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        logger_name = record.name or ""
        return logger_name == "software_recommend_system.tools"


class _LlmInvokeLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return "LLM_INVOKE_" in message or "AGENT_INVOKE_" in message


def _has_rotating_file_handler(root_logger: logging.Logger, resolved_log_path: Path) -> bool:
    for handler in root_logger.handlers:
        if isinstance(handler, RotatingFileHandler):
            try:
                if Path(handler.baseFilename).resolve() == resolved_log_path:
                    return True
            except Exception:
                continue
    return False


def _add_rotating_file_handler(
    log_path: Path,
    level: int = logging.INFO,
    log_filter: logging.Filter | None = None,
) -> None:
    resolved_log_path = log_path.resolve()
    root_logger = logging.getLogger()
    if _has_rotating_file_handler(root_logger, resolved_log_path):
        return

    file_handler = RotatingFileHandler(
        filename=resolved_log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )
    if log_filter is not None:
        file_handler.addFilter(log_filter)
    root_logger.addHandler(file_handler)


def configure_runtime_file_logging() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    runtime_dir = project_root / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    embedding_log_path = runtime_dir / "embedding.log"
    tavily_log_path = runtime_dir / "tavily_search.log"
    llm_invoke_log_path = runtime_dir / "llm_invoke.log"

    _add_rotating_file_handler(embedding_log_path, level=logging.INFO, log_filter=_EmbeddingLogFilter())
    _add_rotating_file_handler(tavily_log_path, level=logging.INFO, log_filter=_TavilyLogFilter())
    _add_rotating_file_handler(llm_invoke_log_path, level=logging.INFO, log_filter=_LlmInvokeLogFilter())

    # Ensure tools module emits INFO logs so Tavily success/failure traces are always written.
    logging.getLogger("software_recommend_system.tools").setLevel(logging.INFO)
    logging.getLogger("software_recommend_system.nodes").setLevel(logging.INFO)
    logging.getLogger("software_recommend_system.ingestion.embedder").setLevel(logging.INFO)
    logging.getLogger("app.api.v1.routes").setLevel(logging.INFO)

    logger.info(
        "runtime file logging enabled: embedding=%s tavily=%s llm_invoke=%s",
        embedding_log_path.resolve(),
        tavily_log_path.resolve(),
        llm_invoke_log_path.resolve(),
    )
    return runtime_dir


async def startup_event_handler():
    """Startup hook for the FastAPI app."""
    configure_runtime_file_logging()
    logger.info("Software Recommendation System API is starting up...")

    if not settings.OPENAI_API_KEY and not settings.DASHSCOPE_API_KEY:
        logger.warning("API keys are not configured; some features may not work.")

    try:
        run_startup_ingestion_if_needed()
    except Exception as exc:  # pragma: no cover - startup safeguard
        logger.exception("startup ingestion failed: %s", exc)

    logger.info("Software Recommendation System API startup complete.")
