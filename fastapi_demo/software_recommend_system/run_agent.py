import argparse
import asyncio
import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

if __package__:
    from .rag_agent import create_rag_with_routing_agent  # 使用带路由的版本
    from .state import AgentState
    from .tools import get_all_tools  # 引入新的工具系统
    from .utils import initialize_vector_store  # 使用新创建的utils模块
else:
    import os
    import sys

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from software_recommend_system.rag_agent import create_rag_with_routing_agent
    from software_recommend_system.state import AgentState
    from software_recommend_system.tools import get_all_tools
    from software_recommend_system.utils import initialize_vector_store

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def _configure_runtime_file_logging() -> Path:
    runtime_dir = Path(__file__).resolve().parents[1] / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    log_path = runtime_dir / "llm_invoke.log"

    root_logger = logging.getLogger()
    resolved_log_path = log_path.resolve()
    for handler in root_logger.handlers:
        if isinstance(handler, RotatingFileHandler):
            try:
                if Path(handler.baseFilename).resolve() == resolved_log_path:
                    return log_path
            except Exception:
                continue

    file_handler = RotatingFileHandler(
        filename=resolved_log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )
    root_logger.addHandler(file_handler)
    return log_path


_RUNTIME_LLM_LOG_PATH = _configure_runtime_file_logging()
logger.info("run_agent runtime file logging enabled: llm=%s", _RUNTIME_LLM_LOG_PATH.resolve())

def parse_arguments():
    parser = argparse.ArgumentParser(description="RAG Agent for Software Recommendation")
    parser.add_argument("--query", type=str, help="Input query for the agent")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--timeout", type=int, default=60, help="Timeout in seconds (default: 60)")
    parser.add_argument("--max-iterations", type=int, default=3, help="Max iterations (default: 3)")
    parser.add_argument("--init-db", action="store_true", help="Initialize vector database with sample data")
    return parser.parse_args()

def _get_value(result: Any, key: str, default: Any = None) -> Any:
    if isinstance(result, dict):
        return result.get(key, default)
    return getattr(result, key, default)

def _print_result(result: Any, debug: bool) -> None:
    final_answer = _get_value(result, "final_answer", "")
    print("\næœ€ç»ˆç­”æ¡ˆ:")
    print(final_answer)

    if debug:
        print("\nè°ƒè¯•ä¿¡æ¯:")
        print(f"- æ¨¡å¼: {_get_value(result, 'mode', '')}")
        print(f"- è¿­ä»£æ¬¡æ•°: {_get_value(result, 'iteration_count', 0)}")
        print(f"- è¦†ç›–çŽ‡: {_get_value(result, 'coverage', 0)}")
        candidates = _get_value(result, "candidates", [])
        print(f"- å€™é€‰æ–¹æ¡ˆæ•°: {len(candidates) if candidates else 0}")

        tools = get_all_tools()
        print(f"- å¯ç”¨å·¥å…·æ•°: {len(tools)}")
        for i, tool in enumerate(tools):
            print(f"  {i+1}. {tool.__name__ if hasattr(tool, '__name__') else str(tool)}")

def _update_state_from_result(state: AgentState, result: Any) -> AgentState:
    if isinstance(result, AgentState):
        return result
    if isinstance(result, dict):
        for key, value in result.items():
            setattr(state, key, value)
    return state

async def run_agent(agent, state: AgentState):
    # 加载环境变量

    # 创建代理实例 - 使用带路由的版本

    # 初始化状态

    # 执行代理
    logger.info(f"Starting agent with query: {state.user_query}")
    logger.info(
        "AGENT_INVOKE_START mode=async_cli graph_input_type=%s",
        type(state).__name__,
    )
    started_at = time.perf_counter()
    try:
        result = await agent.ainvoke(state)
    except Exception:
        logger.exception("AGENT_INVOKE_FAILED mode=async_cli graph_input_type=%s", type(state).__name__)
        raise
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    logger.info("AGENT_INVOKE_DONE mode=async_cli elapsed_ms=%d", elapsed_ms)

    return result

def main():
    args = parse_arguments()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # 初始化向量数据库（如果需要）
    if args.init_db:
        logger.info("Initializing vector database with sample data...")
        sample_docs = [
            {
                "content": (
                    "Redis is an in-memory data structure store, used as a distributed, in-memory key–value "
                    "database, cache and message broker, with optional durability. Redis provides data structures "
                    "such as strings, hashes, lists, sets, sorted sets with range queries, bitmaps, hyperloglogs, "
                    "geospatial indexes, and streams."
                ),
                "metadata": {
                    "source": "redis.io",
                    "published_date": "2023-01-15",
                    "author": "Redis Team",
                    "url": "https://redis.io/",
                    "source_ranking": 9.0,
                    "tags": ["cache", "database", "key-value"]
                }
            },
            {
                "content": (
                    "Memcached is a general-purpose distributed memory caching system. It is often used to speed "
                    "up dynamic database-driven websites by caching data and objects in RAM to reduce the number "
                    "of times an external data source must be read."
                ),
                "metadata": {
                    "source": "memcached.org",
                    "published_date": "2022-11-20",
                    "author": "Memcached Team",
                    "url": "https://memcached.org/",
                    "source_ranking": 8.0,
                    "tags": ["cache", "performance"]
                }
            },
            {
                "content": (
                    "Ehcache is an open-source, standards-based cache used to boost performance, offload your "
                    "database and simplify scalability. Ehcache offers analysis and reporting, enabling you to "
                    "monitor cache activity and performance."
                ),
                "metadata": {
                    "source": "ehcache.org",
                    "published_date": "2023-03-10",
                    "author": "Terracotta Team",
                    "url": "https://www.ehcache.org/",
                    "source_ranking": 7.5,
                    "tags": ["cache", "java", "spring"]
                }
            },
            {
                "content": (
                    "Spring Boot is an open-source Java-based framework used to create stand-alone, "
                    "production-grade Spring applications with minimum configurations. It simplifies the "
                    "development process by providing default configurations."
                ),
                "metadata": {
                    "source": "spring.io",
                    "published_date": "2023-02-01",
                    "author": "Pivotal Team",
                    "url": "https://spring.io/projects/spring-boot",
                    "source_ranking": 9.5,
                    "tags": ["framework", "java", "spring"]
                }
            },
            {
                "content": (
                    "Hibernate is an object-relational mapping tool for the Java programming language. It provides "
                    "a framework for mapping an object-oriented domain model to a relational database and offers "
                    "data query and retrieval facilities."
                ),
                "metadata": {
                    "source": "hibernate.org",
                    "published_date": "2023-01-20",
                    "author": "Hibernate Team",
                    "url": "https://hibernate.org/",
                    "source_ranking": 8.5,
                    "tags": ["orm", "java", "database"]
                }
            }
        ]

        success = initialize_vector_store(sample_docs)
        if success:
            logger.info("Vector database initialized successfully")
        else:
            logger.error("Failed to initialize vector database")
            return

    load_dotenv()
    agent = create_rag_with_routing_agent()

    if args.query:
        state = AgentState(
            user_query=args.query,
            timeout_budget=args.timeout,
            max_iterations=args.max_iterations,
            start_time=time.time()
        )
        try:
            result = asyncio.run(run_agent(agent, state))
            _print_result(result, args.debug)
        except KeyboardInterrupt:
            logger.info("程序被用户中断")
        except Exception as e:
            logger.error(f"执行过程中出现错误: {str(e)}")
    else:
        state = AgentState(
            timeout_budget=args.timeout,
            max_iterations=args.max_iterations,
            start_time=time.time()
        )
        while True:
            user_input = input("请输入您的查询: ").strip()
            if not user_input:
                continue
            if user_input.lower() in {"quit", "exit"}:
                print("退出系统。再见！")
                break
            state.user_query = user_input
            try:
                result = asyncio.run(run_agent(agent, state))
                state = _update_state_from_result(state, result)
                _print_result(result, args.debug)
            except KeyboardInterrupt:
                logger.info("程序被用户中断")
                break
            except Exception as e:
                logger.error(f"执行过程中出现错误: {str(e)}")
if __name__ == "__main__":
    main()
