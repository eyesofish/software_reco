from app.core.config import settings
from software_recommend_system.utils import initialize_vector_store


async def startup_event_handler():
    """Startup hook for the FastAPI app."""
    print("Software Recommendation System API is starting up...")

    if not settings.OPENAI_API_KEY and not settings.DASHSCOPE_API_KEY:
        print("Warning: API keys are not configured; some features may not work.")

    print("Software Recommendation System API startup complete.")
