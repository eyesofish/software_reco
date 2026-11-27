import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.status import HTTP_422_UNPROCESSABLE_ENTITY

from app.api.v1.session_store import session_rows
from app.core.config import settings
from app.core.startup import configure_runtime_file_logging, startup_event_handler
from software_recommend_system.memory_store import memory_rows
from software_recommend_system.performance import TimingMiddleware

# Configure file handlers before importing routes so module-import-time Tavily logs are persisted.
configure_runtime_file_logging()

from app.api.v1.routes import router as v1_router  # noqa: E402  (must follow configure_runtime_file_logging)
from app.api.v1.routing_api import router as routing_router  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.API_KEY:
        raise RuntimeError("API_KEY is required; run scripts/init_auth_demo.py and load .runtime/auth-demo.env")
    session_rows()
    if settings.FEATURE_LAYERED_MEMORY:
        memory_rows()
    await startup_event_handler()
    yield


limiter = Limiter(key_func=get_remote_address, default_limits=[settings.RATE_LIMIT_RECOMMEND])

app = FastAPI(
    title="Software Recommendation System API",
    description="FastAPI wrapper for the software recommendation system.",
    version="1.0.0",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

logger = logging.getLogger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["X-API-Key", "Content-Type", "Authorization", "X-Request-Id"],
    expose_headers=["X-Request-Id"],
)

app.add_middleware(TimingMiddleware)

app.include_router(v1_router, prefix="/api/v1")
app.include_router(routing_router, prefix="/api/v1")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning(
        "validation error method=%s path=%s error_types=%s",
        request.method,
        request.url.path,
        [error["type"] for error in exc.errors()],
    )
    return JSONResponse(
        status_code=HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors()},
    )


@app.get("/")
async def root():
    return {"message": "Welcome to Software Recommendation System API"}


@app.get("/health")
async def health_check():
    return {"status": "healthy"}
