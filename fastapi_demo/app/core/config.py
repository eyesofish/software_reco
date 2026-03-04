import os

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

load_dotenv()


class Settings(BaseSettings):
    SERVER_HOST: str = os.getenv("SERVER_HOST", "0.0.0.0")
    SERVER_PORT: int = int(os.getenv("SERVER_PORT", "8000"))
    DEBUG: bool = os.getenv("DEBUG", "False").lower() == "true"

    ALLOWED_ORIGINS: str = os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost,http://localhost:3000",
    )

    LLM_MODEL: str = os.getenv("LLM_MODEL", "qwen-flash")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")
    BASE_URL: str = os.getenv("BASE_URL", "")
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", BASE_URL)
    EMBEDDING_PROVIDER: str = os.getenv("EMBEDDING_PROVIDER", "dashscope")
    EMBEDDING_ENABLE_FALLBACK: bool = os.getenv("EMBEDDING_ENABLE_FALLBACK", "true").lower() == "true"
    EMBEDDING_FALLBACK_PROVIDER: str = os.getenv("EMBEDDING_FALLBACK_PROVIDER", "dashscope")
    EMBEDDING_TIMEOUT_SECONDS: float = float(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "15"))
    EMBEDDING_BASE_URL: str = os.getenv(
        "EMBEDDING_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    EMBEDDING_LOCAL_BASE_URL: str = os.getenv("EMBEDDING_LOCAL_BASE_URL", "")
    EMBEDDING_LOCAL_API_KEY: str = os.getenv("EMBEDDING_LOCAL_API_KEY", "")
    EMBEDDING_LOCAL_MODEL: str = os.getenv("EMBEDDING_LOCAL_MODEL", "BAAI/bge-small-zh")
    EMBEDDING_LOCAL_FILES_ONLY: bool = os.getenv("EMBEDDING_LOCAL_FILES_ONLY", "true").lower() == "true"
    TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")

    CHROMA_DB_PATH: str = os.getenv("CHROMA_DB_PATH", "./chroma_db")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
    CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "800"))
    CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "120"))
    INGEST_PATH: str = os.getenv("INGEST_PATH", "./ingest_docs")
    IMAGE_MODEL: str = os.getenv("IMAGE_MODEL", "qwen-image-edit")
    IMAGE_SIZE: str = os.getenv("IMAGE_SIZE", "1024x1024")

    TOP_K: int = int(os.getenv("TOP_K", "5"))
    QUALITY_THRESHOLD: float = float(os.getenv("QUALITY_THRESHOLD", "0.6"))

    TIMEOUT_BUDGET: int = int(os.getenv("TIMEOUT_BUDGET", "60"))
    MAX_ITERATIONS: int = int(os.getenv("MAX_ITERATIONS", "3"))

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]


settings = Settings()
