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


def _configure_embedding_file_logging() -> None:
    project_root = Path(__file__).resolve().parents[2]
    runtime_dir = project_root / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    log_path = runtime_dir / "embedding.log"
    resolved_log_path = log_path.resolve()

    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if isinstance(handler, RotatingFileHandler):
            try:
                if Path(handler.baseFilename).resolve() == resolved_log_path:
                    return
            except Exception:
                continue

    file_handler = RotatingFileHandler(
        filename=resolved_log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )
    file_handler.addFilter(_EmbeddingLogFilter())
    root_logger.addHandler(file_handler)
    logger.info("embedding file logging enabled: %s", resolved_log_path)


async def startup_event_handler():
    """Startup hook for the FastAPI app."""
    _configure_embedding_file_logging()
    logger.info("Software Recommendation System API is starting up...")

    if not settings.OPENAI_API_KEY and not settings.DASHSCOPE_API_KEY:
        logger.warning("API keys are not configured; some features may not work.")

    try:
        run_startup_ingestion_if_needed()
    except Exception as exc:  # pragma: no cover - startup safeguard
        logger.exception("startup ingestion failed: %s", exc)

    logger.info("Software Recommendation System API startup complete.")
