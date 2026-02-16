from app.core.config import settings
from app.api.v1.startup_ingest import run_startup_ingestion_if_needed
import logging

logger = logging.getLogger(__name__)


async def startup_event_handler():
    """Startup hook for the FastAPI app."""
    logger.info("Software Recommendation System API is starting up...")

    if not settings.OPENAI_API_KEY and not settings.DASHSCOPE_API_KEY:
        logger.warning("API keys are not configured; some features may not work.")

    try:
        run_startup_ingestion_if_needed()
    except Exception as exc:  # pragma: no cover - startup safeguard
        logger.exception("startup ingestion failed: %s", exc)

    logger.info("Software Recommendation System API startup complete.")
