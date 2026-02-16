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
    EMBEDDING_BASE_URL = os.getenv(
        "EMBEDDING_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

    # Vector store + embedding config
    CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", "./chroma_db")
    EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
    CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "800"))
    CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "120"))

    # Drawing config
    IMAGE_MODEL = os.getenv("IMAGE_MODEL", "qwen-image-edit")
    IMAGE_SIZE = os.getenv("IMAGE_SIZE", "1024x1024")

    # Search config
    TOP_K = int(os.getenv("TOP_K", "5"))
    QUALITY_THRESHOLD = float(os.getenv("QUALITY_THRESHOLD", "0.6"))

    # Runtime config
    TIMEOUT_BUDGET = int(os.getenv("TIMEOUT_BUDGET", "60"))
    MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "3"))


settings = Settings()
