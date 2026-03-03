import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.status import HTTP_422_UNPROCESSABLE_ENTITY

from app.core.config import settings
from app.core.startup import configure_runtime_file_logging, startup_event_handler

# Configure file handlers before importing routes so module-import-time Tavily logs are persisted.
configure_runtime_file_logging()

from app.api.v1.routes import router as v1_router

app = FastAPI(
    title="Software Recommendation System API",
    description="FastAPI wrapper for the software recommendation system.",
    version="1.0.0",
)

logger = logging.getLogger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.on_event("startup")(startup_event_handler)
app.include_router(v1_router, prefix="/api/v1")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    body = await request.body()
    body_text = body.decode("utf-8", errors="replace")
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
