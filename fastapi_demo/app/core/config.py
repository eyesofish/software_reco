from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- server ----
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8000
    DEBUG: bool = False
    ALLOWED_ORIGINS: str = "http://localhost,http://localhost:3000"

    # ---- auth ----
    # If empty, /api/v1/* endpoints accept any caller (dev mode).
    # In production, set both to non-empty random strings (>=32 chars).
    API_KEY: str = ""
    ADMIN_API_KEY: str = ""

    # ---- rate limiting ----
    RATE_LIMIT_RECOMMEND: str = "30/minute"

    # ---- LLM ----
    LLM_MODEL: str = "qwen-flash"
    OPENAI_API_KEY: str = ""
    DASHSCOPE_API_KEY: str = ""
    BASE_URL: str = ""
    OPENAI_BASE_URL: str = ""
    EMBEDDING_PROVIDER: str = "dashscope"
    EMBEDDING_ENABLE_FALLBACK: bool = True
    EMBEDDING_FALLBACK_PROVIDER: str = "dashscope"
    EMBEDDING_TIMEOUT_SECONDS: float = 15.0
    EMBEDDING_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    EMBEDDING_LOCAL_BASE_URL: str = ""
    EMBEDDING_LOCAL_API_KEY: str = ""
    EMBEDDING_LOCAL_MODEL: str = "BAAI/bge-small-zh"
    EMBEDDING_LOCAL_FILES_ONLY: bool = True
    TAVILY_API_KEY: str = ""

    # ---- router ----
    ROUTER_ENABLE: bool = True
    ROUTER_BASE_URL: str = "http://127.0.0.1:18001/v1"
    ROUTER_API_KEY: str = "LOCAL_DUMMY_KEY"
    ROUTER_MODEL: str = "qwen3-0.6b-instruct-router"
    ROUTER_TIMEOUT_SECONDS: float = 8.0
    ROUTER_CIRCUIT_BREAKER_SECONDS: float = 30.0
    ROUTER_CONFIDENCE_THRESHOLD: float = 0.65
    ROUTER_FALLBACK_MODE: str = "rag"

    # ---- vector / chunking ----
    CHROMA_DB_PATH: str = "./chroma_db"
    EMBEDDING_MODEL: str = "Qwen/Qwen3-Embedding-0.6B"
    CHUNK_SIZE: int = 800
    CHUNK_OVERLAP: int = 120
    ENABLE_PARENT_CHILD_CHUNKING: bool = False
    PARENT_CHUNK_SIZE: int = 1600
    PARENT_CHUNK_OVERLAP: int = 200
    CHILD_CHUNK_SIZE: int = 400
    CHILD_CHUNK_OVERLAP: int = 80
    PARENT_COLLECTION_NAME: str = "software_recommendations_parent"
    INGEST_PATH: str = "./ingest_docs"
    IMAGE_MODEL: str = "qwen-image-edit"
    IMAGE_SIZE: str = "1024x1024"
    VISION_MODEL: str = "qwen-vl-max"
    VISION_BASE_URL: str = ""
    VISION_API_KEY: str = ""
    VISION_TIMEOUT_SECONDS: float = 30.0
    VISION_CAPTION_MAX_TOKENS: int = 500
    MULTIMODAL_MAX_IMAGES: int = 4
    MULTIMODAL_MAX_IMAGE_BYTES: int = 5 * 1024 * 1024
    MULTIMODAL_MAX_TOTAL_IMAGE_BYTES: int = 12 * 1024 * 1024
    MULTIMODAL_MAX_IMAGE_PIXELS: int = 16_777_216
    IMAGE_EMBEDDING_MODEL: str = "clip-ViT-B-32"
    IMAGE_EMBEDDING_REVISION: str = ""
    IMAGE_EMBEDDING_DEVICE: str = "cpu"
    IMAGE_EMBEDDING_LOCAL_FILES_ONLY: bool = True
    IMAGE_VECTOR_COLLECTION_NAME: str = "software_recommendations_image"

    # ---- retrieval ----
    TOP_K: int = 4
    QUALITY_THRESHOLD: float = 0.6
    RECALL_ENABLE_VECTOR: bool = True
    RECALL_ENABLE_IMAGE_VECTOR: bool = False
    RECALL_ENABLE_WEB: bool = True
    RECALL_ENABLE_KEYWORD: bool = True
    RECALL_ENABLE_MEMORY: bool = True
    RECALL_VECTOR_TOP_K: int = 20
    RECALL_IMAGE_VECTOR_TOP_K: int = 12
    RECALL_WEB_TOP_K: int = 3
    RECALL_KEYWORD_TOP_K: int = 15
    RECALL_KEYWORD_SCAN_LIMIT: int = 2000
    KEYWORD_RECALL_CIRCUIT_BREAKER_SECONDS: float = 30.0
    RECALL_MEMORY_TOP_K: int = 6
    # Wall-clock budget for the parallel recall fan-out. Channels still running
    # when it expires are abandoned and contribute no documents. 0 disables it.
    RECALL_CHANNEL_TIMEOUT_SECONDS: float = 20.0
    MULTIMODAL_RRF_K: int = 60
    MULTIMODAL_TEXT_VECTOR_WEIGHT: float = 1.0
    MULTIMODAL_IMAGE_VECTOR_WEIGHT: float = 1.0
    QA_EVIDENCE_TOP_N: int = 5
    RETRIEVED_DOC_IDS_FULL_LIMIT: int = 20
    RETRIEVAL_ENABLE_RERANK: bool = False
    RERANK_FINAL_TOP_N: int = 5
    RERANK_MODEL_ENABLED: bool = True
    RERANK_MODEL_NAME: str = "BAAI/bge-reranker-base"
    RERANK_MODEL_DEVICE: str = "cpu"
    RERANK_MODEL_LOCAL_FILES_ONLY: bool = False
    RERANK_MODEL_BATCH_SIZE: int = 8
    RERANK_MODEL_MAX_LENGTH: int = 512
    RERANK_LINEAR_WEIGHT_RETRIEVAL: float = 0.35
    RERANK_LINEAR_WEIGHT_OVERLAP: float = 0.25
    RERANK_LINEAR_WEIGHT_SOURCE: float = 0.10
    RERANK_LINEAR_WEIGHT_FRESHNESS: float = 0.10
    RERANK_LINEAR_WEIGHT_SKILL: float = 0.10
    RERANK_LINEAR_WEIGHT_MEMORY: float = 0.10

    # ---- skill router / planner ----
    SKILL_ROUTER_ENABLE: bool = True
    SKILL_ROUTER_USE_LLM: bool = False
    SKILL_ROUTER_RULE_THRESHOLD: float = 0.25
    SKILL_ROUTER_CONFIDENCE_THRESHOLD: float = 0.55
    SKILL_ROUTER_FACT_QUESTION_BOOST: float = 0.72
    SKILL_ROUTER_BASE_URL: str = ""
    SKILL_ROUTER_API_KEY: str = ""
    SKILL_ROUTER_MODEL: str = ""
    SKILL_ROUTER_TIMEOUT_SECONDS: float = 8.0
    SKILL_ROUTER_FALLBACK_SKILL: str = "generic_rag"
    PLANNER_ENABLE: bool = True
    PLANNER_MAX_STEPS: int = 4
    QA_PLAN_QUERY_MAX_CHARS: int = 220
    SUB_QUESTION_MIN_COUNT: int = 2
    SUB_QUESTION_MAX_COUNT: int = 3
    QUERY_NORMALIZATION_USE_LLM: bool = True
    QUERY_NORMALIZATION_MAX_CHARS_FOR_LLM: int = 320

    # ---- layered memory ----
    FEATURE_LAYERED_MEMORY: bool = True
    MEMORY_ENABLE_WRITEBACK: bool = True
    MEMORY_STORE_FILE: str = ".runtime/layered_memory_store.json"
    MEMORY_WORKING_TOP_K: int = 6
    MEMORY_EPISODIC_TOP_K: int = 6
    MEMORY_SEMANTIC_TOP_K: int = 4
    MEMORY_FACT_TOP_K: int = 6
    MEMORY_CONTEXT_MAX_ITEMS: int = 20
    MEMORY_MAX_ITEMS_PER_LEVEL: int = 200
    MEMORY_SEMANTIC_MIN_SALIENCE: float = 0.45
    MEMORY_WRITEBACK_MIN_CHARS: int = 24
    MEMORY_WRITEBACK_ENABLE_SEMANTIC: bool = True

    # ---- langsmith ----
    LANGSMITH_API_KEY: str = ""
    LANGSMITH_TRACING: bool = False
    LANGSMITH_PROJECT: str = "software-reco-rag-eval"

    # ---- runtime ----
    TIMEOUT_BUDGET: int = 60
    MAX_ITERATIONS: int = 3
    SESSION_STATE_FILE: str = ".runtime/fastapi_session_state.json"
    SESSION_MAX_MESSAGES: int = 30

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]


settings = Settings()
