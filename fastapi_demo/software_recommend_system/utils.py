from typing import List, Dict, Any
from .document_schema import Document, Metadata
from .config import settings
import chromadb
import openai
import logging

logger = logging.getLogger(__name__)

def _get_openai_client() -> openai.OpenAI:
    api_key = settings.DASHSCOPE_API_KEY or settings.OPENAI_API_KEY
    base_url = settings.OPENAI_BASE_URL or None
    return openai.OpenAI(api_key=api_key, base_url=base_url)


def _embed_texts(texts: List[str]) -> List[List[float]]:
    client = _get_openai_client()
    response = client.embeddings.create(
        model=settings.EMBEDDING_MODEL,
        input=texts,
    )
    return [item.embedding for item in response.data]

def initialize_vector_store(documents: List[Dict[str, Any]]) -> bool:
    """
    初始化向量数据库，将文档数据存入向量库
    """
    try:
        # 初始化向量数据库客户端
        client = chromadb.PersistentClient(path=settings.CHROMA_DB_PATH)
        
        # 获取或创建集合
        collection = client.get_or_create_collection("software_recommendations")
        
        # 准备数据
        ids = [str(i) for i in range(len(documents))]
        texts = [doc["content"] for doc in documents]
        metadatas = [doc.get("metadata", {}) for doc in documents]
        
        # 计算嵌入向量
        embeddings = _embed_texts(texts)
        
        # 添加到集合中
        collection.add(
            ids=ids,
            documents=texts,
            metadatas=metadatas,
            embeddings=embeddings
        )
        
        logger.info(f"成功添加 {len(documents)} 个文档到向量数据库")
        return True
    except Exception as e:
        logger.error(f"初始化向量数据库失败: {str(e)}")
        return False
