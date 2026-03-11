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
    ROUTER_CIRCUIT_BREAKER_SECONDS = float(os.getenv("ROUTER_CIRCUIT_BREAKER_SECONDS", "30"))
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
    RECALL_ENABLE_VECTOR = os.getenv("RECALL_ENABLE_VECTOR", "true").lower() == "true"
    RECALL_ENABLE_WEB = os.getenv("RECALL_ENABLE_WEB", "true").lower() == "true"
    RECALL_ENABLE_KEYWORD = os.getenv("RECALL_ENABLE_KEYWORD", "true").lower() == "true"
    RECALL_ENABLE_MEMORY = os.getenv("RECALL_ENABLE_MEMORY", "true").lower() == "true"
    RECALL_VECTOR_TOP_K = max(1, int(os.getenv("RECALL_VECTOR_TOP_K", "20")))
    RECALL_WEB_TOP_K = max(1, int(os.getenv("RECALL_WEB_TOP_K", "3")))
    RECALL_KEYWORD_TOP_K = max(1, int(os.getenv("RECALL_KEYWORD_TOP_K", "15")))
    RECALL_KEYWORD_SCAN_LIMIT = max(10, int(os.getenv("RECALL_KEYWORD_SCAN_LIMIT", "2000")))
    KEYWORD_RECALL_CIRCUIT_BREAKER_SECONDS = float(os.getenv("KEYWORD_RECALL_CIRCUIT_BREAKER_SECONDS", "30"))
    RECALL_MEMORY_TOP_K = max(1, int(os.getenv("RECALL_MEMORY_TOP_K", "6")))
    QA_EVIDENCE_TOP_N = max(1, int(os.getenv("QA_EVIDENCE_TOP_N", "5")))
    RETRIEVED_DOC_IDS_FULL_LIMIT = max(1, int(os.getenv("RETRIEVED_DOC_IDS_FULL_LIMIT", "20")))
    RETRIEVAL_ENABLE_RERANK = os.getenv("RETRIEVAL_ENABLE_RERANK", "false").lower() == "true"
    RERANK_FINAL_TOP_N = max(1, int(os.getenv("RERANK_FINAL_TOP_N", "5")))
    RERANK_MODEL_ENABLED = os.getenv("RERANK_MODEL_ENABLED", "true").lower() == "true"
    RERANK_MODEL_NAME = os.getenv("RERANK_MODEL_NAME", "BAAI/bge-reranker-base")
    RERANK_MODEL_DEVICE = os.getenv("RERANK_MODEL_DEVICE", "cpu")
    RERANK_MODEL_LOCAL_FILES_ONLY = os.getenv("RERANK_MODEL_LOCAL_FILES_ONLY", "false").lower() == "true"
    RERANK_MODEL_BATCH_SIZE = max(1, int(os.getenv("RERANK_MODEL_BATCH_SIZE", "8")))
    RERANK_MODEL_MAX_LENGTH = max(32, int(os.getenv("RERANK_MODEL_MAX_LENGTH", "512")))
    RERANK_LINEAR_WEIGHT_RETRIEVAL = float(os.getenv("RERANK_LINEAR_WEIGHT_RETRIEVAL", "0.35"))
    RERANK_LINEAR_WEIGHT_OVERLAP = float(os.getenv("RERANK_LINEAR_WEIGHT_OVERLAP", "0.25"))
    RERANK_LINEAR_WEIGHT_SOURCE = float(os.getenv("RERANK_LINEAR_WEIGHT_SOURCE", "0.10"))
    RERANK_LINEAR_WEIGHT_FRESHNESS = float(os.getenv("RERANK_LINEAR_WEIGHT_FRESHNESS", "0.10"))
    RERANK_LINEAR_WEIGHT_SKILL = float(os.getenv("RERANK_LINEAR_WEIGHT_SKILL", "0.10"))
    RERANK_LINEAR_WEIGHT_MEMORY = float(os.getenv("RERANK_LINEAR_WEIGHT_MEMORY", "0.10"))

    # Skill router + planner config
    SKILL_ROUTER_ENABLE = os.getenv("SKILL_ROUTER_ENABLE", "true").lower() == "true"
    SKILL_ROUTER_USE_LLM = os.getenv("SKILL_ROUTER_USE_LLM", "false").lower() == "true"
    SKILL_ROUTER_RULE_THRESHOLD = float(os.getenv("SKILL_ROUTER_RULE_THRESHOLD", "0.25"))
    SKILL_ROUTER_CONFIDENCE_THRESHOLD = float(os.getenv("SKILL_ROUTER_CONFIDENCE_THRESHOLD", "0.55"))
    SKILL_ROUTER_FACT_QUESTION_BOOST = float(os.getenv("SKILL_ROUTER_FACT_QUESTION_BOOST", "0.72"))
    SKILL_ROUTER_BASE_URL = os.getenv("SKILL_ROUTER_BASE_URL", ROUTER_BASE_URL)
    SKILL_ROUTER_API_KEY = os.getenv("SKILL_ROUTER_API_KEY", ROUTER_API_KEY)
    SKILL_ROUTER_MODEL = os.getenv("SKILL_ROUTER_MODEL", ROUTER_MODEL)
    SKILL_ROUTER_TIMEOUT_SECONDS = float(os.getenv("SKILL_ROUTER_TIMEOUT_SECONDS", "8"))
    SKILL_ROUTER_FALLBACK_SKILL = os.getenv("SKILL_ROUTER_FALLBACK_SKILL", "generic_rag")
    PLANNER_ENABLE = os.getenv("PLANNER_ENABLE", "true").lower() == "true"
    PLANNER_MAX_STEPS = max(1, int(os.getenv("PLANNER_MAX_STEPS", "4")))
    QA_PLAN_QUERY_MAX_CHARS = max(80, int(os.getenv("QA_PLAN_QUERY_MAX_CHARS", "220")))

    SUB_QUESTION_MIN_COUNT = max(1, int(os.getenv("SUB_QUESTION_MIN_COUNT", "2")))
    SUB_QUESTION_MAX_COUNT = max(SUB_QUESTION_MIN_COUNT, int(os.getenv("SUB_QUESTION_MAX_COUNT", "3")))
    QUERY_NORMALIZATION_USE_LLM = os.getenv("QUERY_NORMALIZATION_USE_LLM", "true").lower() == "true"
    QUERY_NORMALIZATION_MAX_CHARS_FOR_LLM = max(0, int(os.getenv("QUERY_NORMALIZATION_MAX_CHARS_FOR_LLM", "320")))

    # Layered memory config
    FEATURE_LAYERED_MEMORY = os.getenv("FEATURE_LAYERED_MEMORY", "true").lower() == "true"
    MEMORY_ENABLE_WRITEBACK = os.getenv("MEMORY_ENABLE_WRITEBACK", "true").lower() == "true"
    MEMORY_STORE_FILE = os.getenv("MEMORY_STORE_FILE", ".runtime/layered_memory_store.json")
    MEMORY_WORKING_TOP_K = max(0, int(os.getenv("MEMORY_WORKING_TOP_K", "6")))
    MEMORY_EPISODIC_TOP_K = max(0, int(os.getenv("MEMORY_EPISODIC_TOP_K", "6")))
    MEMORY_SEMANTIC_TOP_K = max(0, int(os.getenv("MEMORY_SEMANTIC_TOP_K", "4")))
    MEMORY_FACT_TOP_K = max(0, int(os.getenv("MEMORY_FACT_TOP_K", "6")))
    MEMORY_CONTEXT_MAX_ITEMS = max(1, int(os.getenv("MEMORY_CONTEXT_MAX_ITEMS", "20")))
    MEMORY_MAX_ITEMS_PER_LEVEL = max(20, int(os.getenv("MEMORY_MAX_ITEMS_PER_LEVEL", "200")))
    MEMORY_SEMANTIC_MIN_SALIENCE = float(os.getenv("MEMORY_SEMANTIC_MIN_SALIENCE", "0.45"))
    MEMORY_WRITEBACK_MIN_CHARS = max(20, int(os.getenv("MEMORY_WRITEBACK_MIN_CHARS", "24")))
    MEMORY_WRITEBACK_ENABLE_SEMANTIC = os.getenv("MEMORY_WRITEBACK_ENABLE_SEMANTIC", "true").lower() == "true"

    # Runtime config
    TIMEOUT_BUDGET = int(os.getenv("TIMEOUT_BUDGET", "60"))
    MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "3"))


settings = Settings()
