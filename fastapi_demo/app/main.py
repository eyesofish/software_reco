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

from app.core.config import settings
from app.core.startup import configure_runtime_file_logging, startup_event_handler

# Configure file handlers before importing routes so module-import-time Tavily logs are persisted.
configure_runtime_file_logging()

from app.api.v1.routes import router as v1_router  # noqa: E402  (must follow configure_runtime_file_logging)


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    allow_headers=["X-API-Key", "Content-Type", "Authorization"],
)

app.include_router(v1_router, prefix="/api/v1")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    body = await request.body()
    if b"data:image/" in body:
        body_text = f"<{len(body)} bytes; image data redacted>"
    else:
        body_text = body.decode("utf-8", errors="replace")[:2000]
    logger.warning(
        "validation error: method=%s path=%s content_type=%s body=%r errors=%s",
        request.method,
        request.url.path,
        request.headers.get("content-type"),
        body_text,
        exc.errors(),
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
