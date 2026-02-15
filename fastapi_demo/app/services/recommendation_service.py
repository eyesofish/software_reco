import time
from typing import Any, Dict

from software_recommend_system.rag_agent import create_rag_with_routing_agent
from software_recommend_system.state import AgentState
from software_recommend_system.utils import initialize_vector_store


class RecommendationService:
    def __init__(self) -> None:
        self.agent = None
        self._initialize_agent()

    def _initialize_agent(self) -> None:
        """Initialize the recommendation agent."""
        try:
            self.agent = create_rag_with_routing_agent()
        except Exception as exc:
            print(f"Failed to initialize agent: {exc}")
            raise

    async def get_recommendation(
        self,
        query: str,
        timeout: int = 60,
        max_iterations: int = 3,
    ) -> Dict[str, Any]:
        if not self.agent:
            raise RuntimeError("Agent not initialized")

        state = AgentState(
            user_query=query,
            timeout_budget=timeout,
            max_iterations=max_iterations,
            start_time=time.time(),
        )

        try:
            result = await self.agent.ainvoke(state)

            final_answer = getattr(result, "final_answer", result.get("final_answer", ""))
            candidates = getattr(result, "candidates", result.get("candidates", []))
            mode = getattr(result, "mode", result.get("mode", ""))
            iteration_count = getattr(
                result,
                "iteration_count",
                result.get("iteration_count", 0),
            )
            coverage = getattr(result, "coverage", result.get("coverage", 0.0))

            return {
                "status": "success",
                "final_answer": final_answer,
                "candidates": candidates,
                "mode": mode,
                "iteration_count": iteration_count,
                "coverage": coverage,
            }
        except Exception as exc:
            return {
                "status": "error",
                "error_message": str(exc),
                "final_answer": "",
                "candidates": [],
                "mode": "",
                "iteration_count": 0,
                "coverage": 0.0,
            }

    def initialize_database(self) -> bool:
        sample_docs = [
            {
                "content": (
                    "Redis is an in-memory data structure store, used as a distributed, "
                    "in-memory key-value database, cache and message broker, with optional "
                    "durability. Redis provides data structures such as strings, hashes, lists, "
                    "sets, sorted sets with range queries, bitmaps, hyperloglogs, geospatial "
                    "indexes, and streams."
                ),
                "metadata": {
                    "source": "redis.io",
                    "published_date": "2023-01-15",
                    "author": "Redis Team",
                    "url": "https://redis.io/",
                    "source_ranking": 9.0,
                    "tags": ["cache", "database", "key-value"],
                },
            },
            {
                "content": (
                    "Memcached is a general-purpose distributed memory caching system. It is "
                    "often used to speed up dynamic database-driven websites by caching data "
                    "and objects in RAM to reduce the number of times an external data source "
                    "must be read."
                ),
                "metadata": {
                    "source": "memcached.org",
                    "published_date": "2022-11-20",
                    "author": "Memcached Team",
                    "url": "https://memcached.org/",
                    "source_ranking": 8.0,
                    "tags": ["cache", "performance"],
                },
            },
            {
                "content": (
                    "Ehcache is an open-source, standards-based cache used to boost performance, "
                    "offload your database and simplify scalability. Ehcache offers analysis "
                    "and reporting, enabling you to monitor cache activity and performance."
                ),
                "metadata": {
                    "source": "ehcache.org",
                    "published_date": "2023-03-10",
                    "author": "Terracotta Team",
                    "url": "https://www.ehcache.org/",
                    "source_ranking": 7.5,
                    "tags": ["cache", "java", "spring"],
                },
            },
            {
                "content": (
                    "Spring Boot is an open-source Java-based framework used to create stand-alone, "
                    "production-grade Spring applications with minimum configurations. It simplifies "
                    "the development process by providing default configurations."
                ),
                "metadata": {
                    "source": "spring.io",
                    "published_date": "2023-02-01",
                    "author": "Pivotal Team",
                    "url": "https://spring.io/projects/spring-boot",
                    "source_ranking": 9.5,
                    "tags": ["framework", "java", "spring"],
                },
            },
            {
                "content": (
                    "Hibernate is an object-relational mapping tool for the Java programming language. "
                    "It provides a framework for mapping an object-oriented domain model to a relational "
                    "database and offers data query and retrieval facilities."
                ),
                "metadata": {
                    "source": "hibernate.org",
                    "published_date": "2023-01-20",
                    "author": "Hibernate Team",
                    "url": "https://hibernate.org/",
                    "source_ranking": 8.5,
                    "tags": ["orm", "java", "database"],
                },
            },
        ]

        return initialize_vector_store(sample_docs)
