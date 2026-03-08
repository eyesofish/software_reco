import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    # LLM config
    LLM_MODEL = os.getenv("LLM_MODEL", "qwen-flash")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
    BASE_URL = os.getenv("BASE_URL", "")
    OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", BASE_URL)
    EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "dashscope")
    EMBEDDING_ENABLE_FALLBACK = os.getenv("EMBEDDING_ENABLE_FALLBACK", "true").lower() == "true"
    EMBEDDING_FALLBACK_PROVIDER = os.getenv("EMBEDDING_FALLBACK_PROVIDER", "dashscope")
    EMBEDDING_TIMEOUT_SECONDS = float(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "15"))
    EMBEDDING_BASE_URL = os.getenv(
        "EMBEDDING_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    EMBEDDING_LOCAL_BASE_URL = os.getenv("EMBEDDING_LOCAL_BASE_URL", "")
    EMBEDDING_LOCAL_API_KEY = os.getenv("EMBEDDING_LOCAL_API_KEY", "")
    EMBEDDING_LOCAL_MODEL = os.getenv("EMBEDDING_LOCAL_MODEL", "BAAI/bge-small-zh")
    EMBEDDING_LOCAL_FILES_ONLY = os.getenv("EMBEDDING_LOCAL_FILES_ONLY", "true").lower() == "true"
    TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

    # Router config (OpenAI-compatible local service)
    ROUTER_ENABLE = os.getenv("ROUTER_ENABLE", "true").lower() == "true"
    ROUTER_BASE_URL = os.getenv("ROUTER_BASE_URL", "http://127.0.0.1:18001/v1")
    ROUTER_API_KEY = os.getenv("ROUTER_API_KEY", "LOCAL_DUMMY_KEY")
    ROUTER_MODEL = os.getenv("ROUTER_MODEL", "qwen3-0.6b-instruct-router")
    ROUTER_TIMEOUT_SECONDS = float(os.getenv("ROUTER_TIMEOUT_SECONDS", "8"))
    ROUTER_CONFIDENCE_THRESHOLD = float(os.getenv("ROUTER_CONFIDENCE_THRESHOLD", "0.65"))
    ROUTER_FALLBACK_MODE = os.getenv("ROUTER_FALLBACK_MODE", "rag")

    # Vector store + embedding config
    CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", "./chroma_db")
    EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
    CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "800"))
    CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "120"))

    # Drawing config
    IMAGE_MODEL = os.getenv("IMAGE_MODEL", "qwen-image-edit")
    IMAGE_SIZE = os.getenv("IMAGE_SIZE", "1024x1024")

    # Search config
    TOP_K = int(os.getenv("TOP_K", "4"))
    QUALITY_THRESHOLD = float(os.getenv("QUALITY_THRESHOLD", "0.6"))
    RETRIEVAL_ENABLE_RERANK = os.getenv("RETRIEVAL_ENABLE_RERANK", "false").lower() == "true"
    RERANK_MODEL_ENABLED = os.getenv("RERANK_MODEL_ENABLED", "true").lower() == "true"
    RERANK_MODEL_NAME = os.getenv("RERANK_MODEL_NAME", "BAAI/bge-reranker-base")
    RERANK_MODEL_DEVICE = os.getenv("RERANK_MODEL_DEVICE", "cpu")
    RERANK_MODEL_LOCAL_FILES_ONLY = os.getenv("RERANK_MODEL_LOCAL_FILES_ONLY", "false").lower() == "true"
    RERANK_MODEL_BATCH_SIZE = max(1, int(os.getenv("RERANK_MODEL_BATCH_SIZE", "8")))
    RERANK_MODEL_MAX_LENGTH = max(32, int(os.getenv("RERANK_MODEL_MAX_LENGTH", "512")))
    SUB_QUESTION_MIN_COUNT = max(1, int(os.getenv("SUB_QUESTION_MIN_COUNT", "2")))
    SUB_QUESTION_MAX_COUNT = max(SUB_QUESTION_MIN_COUNT, int(os.getenv("SUB_QUESTION_MAX_COUNT", "3")))

    # Runtime config
    TIMEOUT_BUDGET = int(os.getenv("TIMEOUT_BUDGET", "60"))
    MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "3"))


settings = Settings()
