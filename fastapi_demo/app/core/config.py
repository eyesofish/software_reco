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

    ROUTER_ENABLE: bool = os.getenv("ROUTER_ENABLE", "true").lower() == "true"
    ROUTER_BASE_URL: str = os.getenv("ROUTER_BASE_URL", "http://127.0.0.1:18001/v1")
    ROUTER_API_KEY: str = os.getenv("ROUTER_API_KEY", "LOCAL_DUMMY_KEY")
    ROUTER_MODEL: str = os.getenv("ROUTER_MODEL", "qwen3-0.6b-instruct-router")
    ROUTER_TIMEOUT_SECONDS: float = float(os.getenv("ROUTER_TIMEOUT_SECONDS", "8"))
    ROUTER_CIRCUIT_BREAKER_SECONDS: float = float(os.getenv("ROUTER_CIRCUIT_BREAKER_SECONDS", "30"))
    ROUTER_CONFIDENCE_THRESHOLD: float = float(os.getenv("ROUTER_CONFIDENCE_THRESHOLD", "0.65"))
    ROUTER_FALLBACK_MODE: str = os.getenv("ROUTER_FALLBACK_MODE", "rag")

    CHROMA_DB_PATH: str = os.getenv("CHROMA_DB_PATH", "./chroma_db")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
    CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "800"))
    CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "120"))
    INGEST_PATH: str = os.getenv("INGEST_PATH", "./ingest_docs")
    IMAGE_MODEL: str = os.getenv("IMAGE_MODEL", "qwen-image-edit")
    IMAGE_SIZE: str = os.getenv("IMAGE_SIZE", "1024x1024")

    TOP_K: int = int(os.getenv("TOP_K", "4"))
    QUALITY_THRESHOLD: float = float(os.getenv("QUALITY_THRESHOLD", "0.6"))
    RECALL_ENABLE_VECTOR: bool = os.getenv("RECALL_ENABLE_VECTOR", "true").lower() == "true"
    RECALL_ENABLE_WEB: bool = os.getenv("RECALL_ENABLE_WEB", "true").lower() == "true"
    RECALL_ENABLE_KEYWORD: bool = os.getenv("RECALL_ENABLE_KEYWORD", "true").lower() == "true"
    RECALL_ENABLE_MEMORY: bool = os.getenv("RECALL_ENABLE_MEMORY", "true").lower() == "true"
    RECALL_VECTOR_TOP_K: int = max(1, int(os.getenv("RECALL_VECTOR_TOP_K", "20")))
    RECALL_WEB_TOP_K: int = max(1, int(os.getenv("RECALL_WEB_TOP_K", "3")))
    RECALL_KEYWORD_TOP_K: int = max(1, int(os.getenv("RECALL_KEYWORD_TOP_K", "15")))
    RECALL_KEYWORD_SCAN_LIMIT: int = max(10, int(os.getenv("RECALL_KEYWORD_SCAN_LIMIT", "2000")))
    KEYWORD_RECALL_CIRCUIT_BREAKER_SECONDS: float = float(
        os.getenv("KEYWORD_RECALL_CIRCUIT_BREAKER_SECONDS", "30")
    )
    RECALL_MEMORY_TOP_K: int = max(1, int(os.getenv("RECALL_MEMORY_TOP_K", "6")))
    QA_EVIDENCE_TOP_N: int = max(1, int(os.getenv("QA_EVIDENCE_TOP_N", "5")))
    RETRIEVED_DOC_IDS_FULL_LIMIT: int = max(1, int(os.getenv("RETRIEVED_DOC_IDS_FULL_LIMIT", "20")))
    RETRIEVAL_ENABLE_RERANK: bool = os.getenv("RETRIEVAL_ENABLE_RERANK", "false").lower() == "true"
    RERANK_FINAL_TOP_N: int = max(1, int(os.getenv("RERANK_FINAL_TOP_N", "5")))
    RERANK_MODEL_ENABLED: bool = os.getenv("RERANK_MODEL_ENABLED", "true").lower() == "true"
    RERANK_MODEL_NAME: str = os.getenv("RERANK_MODEL_NAME", "BAAI/bge-reranker-base")
    RERANK_MODEL_DEVICE: str = os.getenv("RERANK_MODEL_DEVICE", "cpu")
    RERANK_MODEL_LOCAL_FILES_ONLY: bool = os.getenv("RERANK_MODEL_LOCAL_FILES_ONLY", "false").lower() == "true"
    RERANK_MODEL_BATCH_SIZE: int = max(1, int(os.getenv("RERANK_MODEL_BATCH_SIZE", "8")))
    RERANK_MODEL_MAX_LENGTH: int = max(32, int(os.getenv("RERANK_MODEL_MAX_LENGTH", "512")))
    RERANK_LINEAR_WEIGHT_RETRIEVAL: float = float(os.getenv("RERANK_LINEAR_WEIGHT_RETRIEVAL", "0.35"))
    RERANK_LINEAR_WEIGHT_OVERLAP: float = float(os.getenv("RERANK_LINEAR_WEIGHT_OVERLAP", "0.25"))
    RERANK_LINEAR_WEIGHT_SOURCE: float = float(os.getenv("RERANK_LINEAR_WEIGHT_SOURCE", "0.10"))
    RERANK_LINEAR_WEIGHT_FRESHNESS: float = float(os.getenv("RERANK_LINEAR_WEIGHT_FRESHNESS", "0.10"))
    RERANK_LINEAR_WEIGHT_SKILL: float = float(os.getenv("RERANK_LINEAR_WEIGHT_SKILL", "0.10"))
    RERANK_LINEAR_WEIGHT_MEMORY: float = float(os.getenv("RERANK_LINEAR_WEIGHT_MEMORY", "0.10"))
    SKILL_ROUTER_ENABLE: bool = os.getenv("SKILL_ROUTER_ENABLE", "true").lower() == "true"
    SKILL_ROUTER_USE_LLM: bool = os.getenv("SKILL_ROUTER_USE_LLM", "false").lower() == "true"
    SKILL_ROUTER_RULE_THRESHOLD: float = float(os.getenv("SKILL_ROUTER_RULE_THRESHOLD", "0.25"))
    SKILL_ROUTER_CONFIDENCE_THRESHOLD: float = float(os.getenv("SKILL_ROUTER_CONFIDENCE_THRESHOLD", "0.55"))
    SKILL_ROUTER_FACT_QUESTION_BOOST: float = float(os.getenv("SKILL_ROUTER_FACT_QUESTION_BOOST", "0.72"))
    SKILL_ROUTER_BASE_URL: str = os.getenv("SKILL_ROUTER_BASE_URL", ROUTER_BASE_URL)
    SKILL_ROUTER_API_KEY: str = os.getenv("SKILL_ROUTER_API_KEY", ROUTER_API_KEY)
    SKILL_ROUTER_MODEL: str = os.getenv("SKILL_ROUTER_MODEL", ROUTER_MODEL)
    SKILL_ROUTER_TIMEOUT_SECONDS: float = float(os.getenv("SKILL_ROUTER_TIMEOUT_SECONDS", "8"))
    SKILL_ROUTER_FALLBACK_SKILL: str = os.getenv("SKILL_ROUTER_FALLBACK_SKILL", "generic_rag")
    PLANNER_ENABLE: bool = os.getenv("PLANNER_ENABLE", "true").lower() == "true"
    PLANNER_MAX_STEPS: int = max(1, int(os.getenv("PLANNER_MAX_STEPS", "4")))
    QA_PLAN_QUERY_MAX_CHARS: int = max(80, int(os.getenv("QA_PLAN_QUERY_MAX_CHARS", "220")))
    SUB_QUESTION_MIN_COUNT: int = max(1, int(os.getenv("SUB_QUESTION_MIN_COUNT", "2")))
    SUB_QUESTION_MAX_COUNT: int = max(SUB_QUESTION_MIN_COUNT, int(os.getenv("SUB_QUESTION_MAX_COUNT", "3")))
    QUERY_NORMALIZATION_USE_LLM: bool = os.getenv("QUERY_NORMALIZATION_USE_LLM", "true").lower() == "true"
    QUERY_NORMALIZATION_MAX_CHARS_FOR_LLM: int = max(0, int(os.getenv("QUERY_NORMALIZATION_MAX_CHARS_FOR_LLM", "320")))

    FEATURE_LAYERED_MEMORY: bool = os.getenv("FEATURE_LAYERED_MEMORY", "true").lower() == "true"
    MEMORY_ENABLE_WRITEBACK: bool = os.getenv("MEMORY_ENABLE_WRITEBACK", "true").lower() == "true"
    MEMORY_STORE_FILE: str = os.getenv("MEMORY_STORE_FILE", ".runtime/layered_memory_store.json")
    MEMORY_WORKING_TOP_K: int = max(0, int(os.getenv("MEMORY_WORKING_TOP_K", "6")))
    MEMORY_EPISODIC_TOP_K: int = max(0, int(os.getenv("MEMORY_EPISODIC_TOP_K", "6")))
    MEMORY_SEMANTIC_TOP_K: int = max(0, int(os.getenv("MEMORY_SEMANTIC_TOP_K", "4")))
    MEMORY_FACT_TOP_K: int = max(0, int(os.getenv("MEMORY_FACT_TOP_K", "6")))
    MEMORY_CONTEXT_MAX_ITEMS: int = max(1, int(os.getenv("MEMORY_CONTEXT_MAX_ITEMS", "20")))
    MEMORY_MAX_ITEMS_PER_LEVEL: int = max(20, int(os.getenv("MEMORY_MAX_ITEMS_PER_LEVEL", "200")))
    MEMORY_SEMANTIC_MIN_SALIENCE: float = float(os.getenv("MEMORY_SEMANTIC_MIN_SALIENCE", "0.45"))
    MEMORY_WRITEBACK_MIN_CHARS: int = max(20, int(os.getenv("MEMORY_WRITEBACK_MIN_CHARS", "24")))
    MEMORY_WRITEBACK_ENABLE_SEMANTIC: bool = (
        os.getenv("MEMORY_WRITEBACK_ENABLE_SEMANTIC", "true").lower() == "true"
    )

    TIMEOUT_BUDGET: int = int(os.getenv("TIMEOUT_BUDGET", "60"))
    MAX_ITERATIONS: int = int(os.getenv("MAX_ITERATIONS", "3"))

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]


settings = Settings()
