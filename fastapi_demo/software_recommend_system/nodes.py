from typing import Dict, Any, List
from .state import AgentState
from .document_schema import Document, Metadata
from .config import settings
import openai
import os
import time
import re
import json
import ast
from datetime import datetime
import logging
from langgraph.func import task
from langgraph.types import interrupt

# 导入新的工具系统
from .retriever import retrieve, rerank_documents
from .tools import pre_drawing_tool, draw_image_tool
from .logging_utils import elapsed_ms, error_fields, log_event, log_exception, new_trace_id, text_preview
from .observability import traceable, wrap_openai
from .router import route_query

logger = logging.getLogger(__name__)


def _llm_invoke_start(scene: str, model: str, request_hint: str = "") -> tuple[str, float]:
    trace_id = new_trace_id("llm")
    log_event(
        logger,
        logging.INFO,
        "llm.invoke.start",
        component="llm",
        trace_id=trace_id,
        scene=scene,
        model=model,
        request_hint=request_hint,
    )
    return trace_id, time.perf_counter()


def _llm_invoke_done(scene: str, model: str, trace_id: str, started_at: float, response_obj: Any) -> None:
    choices = _get_field(response_obj, "choices", []) or []
    log_event(
        logger,
        logging.INFO,
        "llm.invoke.done",
        component="llm",
        trace_id=trace_id,
        scene=scene,
        model=model,
        elapsed_ms=elapsed_ms(started_at),
        choices=len(choices),
    )


def _llm_invoke_failed(
    scene: str,
    model: str,
    trace_id: str,
    exc: Exception,
    started_at: float | None = None,
    with_stack: bool = True,
) -> None:
    fields: Dict[str, Any] = {
        "component": "llm",
        "trace_id": trace_id,
        "scene": scene,
        "model": model,
    }
    if started_at is not None:
        fields["elapsed_ms"] = elapsed_ms(started_at)
    fields.update(error_fields(exc))
    if with_stack:
        log_exception(logger, "llm.invoke.fail", **fields)
    else:
        log_event(logger, logging.ERROR, "llm.invoke.fail", **fields)

def _get_field(obj: Any, field: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(field, default)
    return getattr(obj, field, default)

def _set_field(obj: Any, field: str, value: Any) -> None:
    if isinstance(obj, dict):
        obj[field] = value
    else:
        setattr(obj, field, value)


def _truncate_text(text: str, max_chars: int) -> str:
    value = str(text or "")
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    return value[-max_chars:]


def _rough_messages_tokens(messages: List[Dict[str, str]]) -> int:
    # Conservative rough estimator for small-context local models.
    # We intentionally over-estimate to avoid context overflow.
    total = 0
    for item in messages:
        total += 6  # per-message structural overhead
        total += len(str(item.get("content", "") or ""))
    return total + 2


def _prepare_messages_for_small_context(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Shrink message window to reduce context overflow risk on 2k-token local models."""
    max_messages = max(2, int(os.getenv("CHAT_MAX_MESSAGES", "12")))
    max_input_tokens = max(256, int(os.getenv("CHAT_MAX_INPUT_TOKENS", "1700")))
    max_system_chars = max(256, int(os.getenv("CHAT_MAX_SYSTEM_CHARS", "1200")))
    max_message_chars = max(128, int(os.getenv("CHAT_MAX_MESSAGE_CHARS", "800")))

    normalized: List[Dict[str, str]] = []
    for raw in messages:
        role = str(_get_field(raw, "role", "user") or "user").strip() or "user"
        content = str(_get_field(raw, "content", "") or "")
        limit = max_system_chars if role == "system" else max_message_chars
        normalized.append({"role": role, "content": _truncate_text(content, limit)})

    if len(normalized) > max_messages:
        if normalized and normalized[0].get("role") == "system":
            normalized = [normalized[0]] + normalized[-(max_messages - 1):]
        else:
            normalized = normalized[-max_messages:]

    while len(normalized) > 1 and _rough_messages_tokens(normalized) > max_input_tokens:
        if normalized[0].get("role") == "system" and len(normalized) > 2:
            # Keep system prompt and newest turns; drop oldest non-system turn.
            normalized.pop(1)
        else:
            normalized.pop(0)

    if normalized and _rough_messages_tokens(normalized) > max_input_tokens:
        target_idx = 1 if normalized[0].get("role") == "system" and len(normalized) > 1 else 0
        current = normalized[target_idx]["content"]
        overflow = _rough_messages_tokens(normalized) - max_input_tokens
        keep_chars = max(32, len(current) - overflow - 16)
        normalized[target_idx]["content"] = _truncate_text(current, keep_chars)

    return normalized


def _merge_doc_ids(existing: List[str], extra: List[str]) -> List[str]:
    merged: List[str] = []
    seen = set()
    for value in list(existing or []) + list(extra or []):
        doc_id = str(value or "").strip()
        if not doc_id or doc_id in seen:
            continue
        seen.add(doc_id)
        merged.append(doc_id)
    return merged


def _extract_doc_id(doc: Document, fallback_prefix: str, index: int) -> str:
    metadata = _get_field(doc, "metadata", {}) or {}
    doc_id = str(_get_field(metadata, "doc_id", "") or "").strip()
    if doc_id:
        return doc_id
    doc_url = str(_get_field(metadata, "url", "") or "").strip()
    if doc_url:
        return doc_url
    return f"{fallback_prefix}_{index}"


def _build_retrieval_record(
    *,
    subquery_id: str,
    subquery: str,
    docs: List[Document],
) -> Dict[str, Any]:
    retrieved_doc_ids: List[str] = []
    retrieved_contexts: List[str] = []
    for index, doc in enumerate(docs, start=1):
        doc_id = _extract_doc_id(doc, fallback_prefix=subquery_id, index=index)
        if doc_id not in retrieved_doc_ids:
            retrieved_doc_ids.append(doc_id)

        content = str(_get_field(doc, "content", "") or "").strip().replace("\n", " ")
        if content:
            retrieved_contexts.append(content[:280] + "..." if len(content) > 280 else content)

    return {
        "subquery_id": subquery_id,
        "subquery": str(subquery or ""),
        "retrieved_doc_ids": retrieved_doc_ids,
        "retrieved_contexts": retrieved_contexts[:8],
    }

# OpenAI client factory with explicit auth/base_url wiring.
def _get_openai_client() -> openai.OpenAI:
    api_key = settings.DASHSCOPE_API_KEY or settings.OPENAI_API_KEY
    base_url = settings.OPENAI_BASE_URL or None
    return wrap_openai(openai.OpenAI(api_key=api_key, base_url=base_url))

# 分流词表
TECH_TERMS = {
    "frameworks": [
        # Java / JVM
        "Spring", "Spring Boot", "Spring MVC", "Spring Cloud",
        "Hibernate", "Struts", "Play Framework", "Micronaut", "Quarkus",
        # JavaScript / TypeScript
        "React", "React Native", "Next.js", "Remix", "Gatsby",
        "Angular", "AngularJS",
        "Vue", "Nuxt.js", "Svelte", "SvelteKit",
        # Python
        "Django", "Flask", "FastAPI", "Tornado", "Sanic",
        "Pyramid", "Bottle",
        # Node.js / Backend JS
        "Express", "NestJS", "Koa", "Hapi",
        # PHP
        "Laravel", "Symfony", "CodeIgniter", "Yii", "Zend Framework",
        # Ruby
        "Ruby on Rails", "Sinatra",
        # .NET
        "ASP.NET", "ASP.NET Core", "Blazor",
        # Go
        "Gin", "Beego", "Echo", "Fiber",
        # Mobile
        "Flutter", "Cordova", "Ionic", "NativeScript",
        # Desktop / Cross-platform
        "Electron", "Qt", "GTK", "WPF", "Swing",
        # Others
        "Meteor", "Phoenix", "Elixir Phoenix", "Playwright Test Runner",
        "Strapi", "KeystoneJS"
    ],

    "technologies": [
        "MVC", "MVVM", "MVP",
        "ORM", "Object Relational Mapping",
        "CI/CD", "continuous integration", "continuous delivery", "continuous deployment",
        "microservices", "microservice architecture",
        "containerization", "container orchestration",
        "service mesh", "sidecar",
        "kubernetes operator",
        "cache", "caching", "in-memory cache",
        "database", "relational database", "NoSQL database",
        "API", "REST", "RESTful API", "GraphQL", "gRPC",
        "WebSocket", "SSE", "Server-Sent Events",
        "RPC", "thrift",
        "message passing", "event streaming",
        "batch processing", "stream processing",
        "serverless", "FaaS", "Function as a Service",
        "PaaS", "SaaS", "IaaS",
        "SOA", "service oriented architecture",
        "monolith", "monolithic architecture",
        "service discovery",
        "load balancing",
        "rate limiting",
        "circuit breaker",
        "event sourcing",
        "CQRS",
        "DDD", "domain driven design",
        "logging", "tracing", "monitoring", "observability",
        "A/B testing", "feature flag",
        "blue-green deployment", "canary release",
        "reverse proxy", "API gateway",
        "edge computing", "CDN",
        "web security", "OWASP",
        "multi-tenant", "multi tenancy",
        "event-driven architecture",
        "polyglot persistence",
        "eventual consistency",
        "ACID", "BASE",
        "idempotency",
        "saga pattern",
        "data sharding", "partitioning",
        "leader election", "consensus"
    ],

    "languages": [
        # 主流语言
        "Python", "JavaScript", "TypeScript", "Java", "C#", "C", "C++",
        "Go", "Golang", "Rust", "PHP", "Ruby", "Swift", "Kotlin", "Scala",
        # 函数式 / 脚本
        "Haskell", "Elixir", "Erlang", "F#", "Clojure",
        "Lua", "Perl", "R", "Julia", "MATLAB",
        # 低层 / 系统
        "Assembly", "x86 assembly", "ARM assembly",
        # Web / 标记
        "HTML", "CSS", "Sass", "Less",
        # Query / Data
        "SQL", "PL/SQL", "T-SQL",
        # Shell
        "Bash", "Shell", "PowerShell",
        # Other
        "Objective-C", "Dart", "Groovy", "VB.NET"
    ],

    "tools": [
        # VCS
        "git", "GitHub", "GitLab", "Bitbucket", "SVN",
        # CI/CD
        "jenkins", "GitHub Actions", "GitLab CI", "CircleCI", "Travis CI",
        "TeamCity", "Bamboo",
        # IaC / 配置管理
        "terraform", "ansible", "puppet", "chef", "cloudformation",
        "pulumi",
        # 监控 / 日志
        "prometheus", "grafana",
        "elasticsearch", "logstash", "kibana", "ELK stack", "EFK stack",
        "datadog", "new relic", "splunk", "jaeger", "zipkin", "opentelemetry",
        # 容器 / 集群
        "docker", "docker compose", "kubernetes", "k8s", "minikube",
        "helm", "ArgoCD", "FluxCD", "istio", "linkerd", "envoy",
        # 测试
        "JUnit", "TestNG", "Mockito", "Jest", "Mocha", "Chai",
        "Cypress", "Playwright", "Selenium", "Puppeteer",
        "pytest", "unittest", "nose",
        "Postman", "Insomnia", "Newman",
        # 构建
        "Maven", "Gradle", "Ant", "npm", "yarn", "pnpm",
        "webpack", "rollup", "parcel", "esbuild", "vite",
        # 协作
        "Jira", "Confluence", "Trello", "Asana",
        # 其他开发工具
        "VS Code", "IntelliJ IDEA", "PyCharm", "WebStorm", "Eclipse",
        "Xcode", "Android Studio",
        "Fiddler", "Charles Proxy",
        "Swagger", "OpenAPI"
    ],

    "architecture": [
        "distributed system", "distributed systems",
        "high availability", "HA",
        "scalability", "horizontal scaling", "vertical scaling",
        "load balancing", "global load balancing",
        "service discovery", "service registry",
        "event-driven", "event-driven architecture",
        "CQRS", "command query responsibility segregation",
        "DDD", "domain driven design",
        "monolith", "monolithic",
        "SOA", "service oriented architecture",
        "microservices", "microservice architecture",
        "hexagonal architecture", "clean architecture", "onion architecture",
        "layered architecture",
        "peer to peer", "P2P",
        "master slave", "leader follower",
        "multi-tier architecture", "three-tier architecture",
        "event sourcing",
        "message driven",
        "lambda architecture", "kappa architecture",
        "data lakehouse", "data mesh",
        "multi region", "multi AZ",
        "CQRS + event sourcing"
    ],

    "data": [
        # 数据库
        "MySQL", "PostgreSQL", "MariaDB", "Oracle", "SQL Server",
        "SQLite", "CockroachDB",
        "MongoDB", "Cassandra", "HBase", "DynamoDB",
        "Redis", "Memcached",
        "Neo4j", "JanusGraph", "ArangoDB", "TigerGraph",
        "InfluxDB", "TimescaleDB", "ClickHouse",
        "Elasticsearch", "OpenSearch",
        "NoSQL", "SQL",
        "columnar database", "time series database",
        # 大数据
        "Hadoop", "HDFS", "YARN",
        "Spark", "Flink", "Storm", "Samza",
        "Kafka Streams", "KSQL",
        "Hive", "Pig",
        # ETL / DW
        "data warehouse", "data lake", "data lakehouse",
        "ETL", "ELT", "data pipeline",
        "Airflow", "Luigi", "Dagster",
        # 向量数据库 / 检索
        "vector database", "embedding",
        "FAISS", "Milvus", "Pinecone", "Weaviate", "Qdrant",
        "Annoy", "HNSW",
        # BI / 分析
        "Tableau", "Power BI", "Looker", "Superset",
        "OLAP", "OLTP",
        "data modeling", "star schema", "snowflake schema"
    ],

    "ai": [
        "LLM", "large language model",
        "RAG", "retrieval augmented generation",
        "agent", "multi-agent", "AI agent",
        "prompt", "prompt engineering",
        "fine-tuning", "LoRA", "adapter tuning",
        "embedding model", "text embedding",
        "LangChain", "LangGraph",
        "OpenAI", "ChatGPT", "GPT-4", "GPT-4.1",
        "Hugging Face", "Transformers",
        "transformer", "attention mechanism",
        "BERT", "RoBERTa", "T5", "LLaMA",
        # 传统 ML
        "machine learning", "deep learning",
        "supervised learning", "unsupervised learning", "reinforcement learning",
        "classification", "regression", "clustering",
        "neural network", "CNN", "RNN", "LSTM", "GAN",
        # AI 框架
        "PyTorch", "TensorFlow", "Keras", "JAX",
        "scikit-learn", "XGBoost", "LightGBM", "CatBoost",
        # MLOps
        "MLOps", "model serving", "feature store",
        "Kubeflow", "MLflow", "SageMaker",
        "Ray", "Horovod",
        # 其他
        "computer vision", "NLP", "speech recognition"
    ],

    "backend": [
        "message queue", "message broker",
        "Kafka", "RabbitMQ", "ActiveMQ", "RocketMQ", "SQS", "Pub/Sub",
        "Redis", "distributed lock",
        "rate limiting", "throttling",
        "idempotency",
        "transaction", "distributed transaction",
        "two phase commit", "2PC",
        "eventual consistency", "strong consistency",
        "CAP theorem", "Paxos", "Raft", "consensus algorithm",
        "session management",
        "authentication", "authorization",
        "JWT", "OAuth", "OpenID Connect",
        "RBAC", "ABAC",
        "file storage", "object storage", "blob storage",
        "email service", "notification service",
        "payment integration", "webhook",
        "cron job", "scheduled task",
        "API gateway", "reverse proxy",
        "Nginx", "HAProxy", "Traefik"
    ],

    "devops": [
        "DevOps", "SRE", "site reliability engineering",
        "AWS", "GCP", "Azure",
        "EC2", "S3", "RDS", "Lambda", "ECS", "EKS",
        "GKE", "Cloud Run", "Cloud Functions",
        "AKS", "App Service",
        "CI", "CD", "CI/CD",
        "GitHub Actions", "GitLab CI", "jenkins",
        "Helm", "ArgoCD", "FluxCD",
        "docker", "kubernetes",
        "infrastructure as code", "IaC",
        "terraform", "ansible", "puppet", "chef", "cloudformation",
        "logging", "tracing", "monitoring", "observability",
        "prometheus", "grafana", "ELK stack", "EFK stack",
        "blue-green deployment", "canary deployment",
        "on-call", "incident management",
        "auto scaling", "autoscaling group",
        "service mesh",
        "config management",
        "secret management", "vault"
    ],

    "non_functional": [
        "performance", "latency", "throughput",
        "concurrency", "parallelism",
        "high QPS", "QPS", "RPS",
        "fault tolerance", "reliability", "resilience",
        "availability", "SLA", "SLO", "SLI",
        "scalability", "elasticity",
        "security", "authentication", "authorization",
        "confidentiality", "integrity", "availability",
        "input validation", "encryption", "TLS", "SSL",
        "audit log", "compliance",
        "usability", "accessibility",
        "maintainability", "testability",
        "observability", "monitorability",
        "cost optimization"
    ],

    # 新增：前端相关
    "frontend": [
        "HTML", "CSS", "JavaScript", "TypeScript",
        "React", "Angular", "Vue", "Svelte",
        "Next.js", "Nuxt.js", "Gatsby",
        "Redux", "MobX", "Recoil", "Zustand",
        "Webpack", "Vite", "Rollup", "Parcel",
        "Sass", "Less", "Tailwind CSS",
        "Bootstrap", "Material UI", "Ant Design",
        "responsive design", "SPA", "PWA",
        "Web Components", "Shadow DOM",
        "DOM", "virtual DOM"
    ],

    # 新增：移动开发
    "mobile": [
        "Android", "iOS",
        "Kotlin", "Swift", "Objective-C", "Java",
        "Android Studio", "Xcode",
        "Flutter", "React Native",
        "Cordova", "Ionic", "NativeScript",
        "mobile SDK", "push notification",
        "App Store", "Google Play"
    ],

    # 新增：测试相关
    "testing": [
        "unit test", "integration test", "system test",
        "end to end test", "E2E test",
        "regression test", "performance test", "load test", "stress test",
        "smoke test", "sanity test",
        "TDD", "test driven development",
        "BDD", "behavior driven development",
        "JUnit", "TestNG", "pytest", "Jest", "Mocha", "Cypress",
        "Selenium", "Playwright", "Puppeteer",
        "coverage", "code coverage",
        "mock", "stub", "spy"
    ],

    # 新增：安全
    "security": [
        "encryption", "decryption",
        "symmetric encryption", "asymmetric encryption",
        "hashing", "HMAC",
        "TLS", "SSL", "HTTPS",
        "OAuth", "OAuth2", "OpenID Connect",
        "SAML", "JWT",
        "CSRF", "XSS", "SQL injection", "clickjacking",
        "WAF", "web application firewall",
        "IAM", "identity and access management",
        "SSO", "single sign on",
        "key management", "KMS",
        "zero trust", "least privilege",
        "penetration testing", "vulnerability scanning",
        "OWASP Top 10"
    ],

    # 新增：方法论 / 流程
    "methodology": [
        "Agile", "Scrum", "Kanban",
        "Waterfall",
        "XP", "extreme programming",
        "pair programming", "code review",
        "CI/CD", "DevOps",
        "user story", "story point",
        "backlog", "sprint", "retrospective",
        "design review", "architecture review",
        "RFC", "ADR", "technical specification"
    ],

    # 新增：设计模式
    "design_patterns": [
        "singleton", "factory", "abstract factory",
        "builder", "prototype",
        "adapter", "bridge", "composite", "decorator", "facade",
        "flyweight", "proxy",
        "chain of responsibility",
        "command", "interpreter", "iterator",
        "mediator", "memento", "observer",
        "state", "strategy", "template method",
        "visitor",
        "repository pattern", "unit of work",
        "dependency injection", "inversion of control"
    ],

    # 新增：基础 CS 概念
    "cs_core": [
        "data structure", "algorithm",
        "array", "linked list", "stack", "queue",
        "hash table", "heap", "priority queue",
        "tree", "binary tree", "bst",
        "segment tree", "fenwick tree",
        "graph", "bfs", "dfs", "dijkstra", "shortest path",
        "sorting", "searching", "dynamic programming",
        "time complexity", "space complexity", "big o notation",
        "process", "thread", "coroutine",
        "deadlock", "race condition", "mutex", "semaphore",
        "virtual memory", "paging", "cache",
        "CPU", "GPU", "I/O",
        "network", "TCP", "UDP", "HTTP", "HTTP/2", "HTTP/3",
        "DNS", "CDN", "load balancer",
        "RPC", "gRPC",
        "operating system", "kernel", "syscall",
        "distributed system"
    ]
}


DRAWING_KEYWORDS = [
    "画图", "画一个", "生成图", "生成图表", "绘图", "绘制", "图表",
    "柱状图", "折线图", "饼图",
    "chart", "plot", "bar chart", "line chart", "pie chart"
]

def _is_drawing_request(text: str) -> bool:
    if not text:
        return False
    text_lower = text.lower()
    for keyword in DRAWING_KEYWORDS:
        if keyword in text or keyword in text_lower:
            return True
    return False

KNOWN_FACTS_MARKER = "[Known User Facts]"
ALL_TECH_TERMS = tuple(
    str(term).strip()
    for group in TECH_TERMS.values()
    for term in group
    if str(term).strip()
)
SHORT_AMBIGUOUS_TECH_TERMS = {"go", "ci", "cd"}
CHAT_FIRST_PATTERNS = (
    re.compile(r"^\s*(你好|您好|嗨|哈喽|早上好|下午好|晚上好)\s*[!！。?？]?\s*$"),
    re.compile(r"^\s*(谢谢|多谢|再见|拜拜)\s*[!！。]?\s*$"),
    re.compile(r"(?i)^\s*(hi|hello|hey|good morning|good afternoon|good evening)\b"),
    re.compile(r"(?i)^\s*(thanks|thank you|bye|goodbye)\b"),
    re.compile(r"(我是谁|我叫什么|你记得我吗|你还记得我|你是谁|你能做什么)"),
    re.compile(r"(?i)\b(who am i|what is my name|what's my name|whats my name|who are you)\b"),
    re.compile(r"^\s*(?:我叫|我是)\s*[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_\-]{0,31}\s*$"),
    re.compile(r"(?i)^\s*(?:my name is|i am|i'm)\s+[A-Za-z][A-Za-z\-' ]{0,40}\s*$"),
)
RAG_INTENT_PATTERNS = (
    re.compile(
        r"(?i)\b(recommend|comparison|compare|versus|vs|architecture|design|implement|"
        r"build|deploy|optimize|tech stack|framework|database|vector database|"
        r"rag|llm|agent|langgraph|langchain)\b"
    ),
    re.compile(r"(推荐|对比|比较|选型|架构|方案|实现|部署|优化|技术栈|数据库|向量库|检索|召回)"),
)

def _extract_current_user_query(raw_query: str) -> str:
    query = (raw_query or "").strip()
    if not query:
        return ""
    marker_index = query.find(KNOWN_FACTS_MARKER)
    if marker_index >= 0:
        return query[:marker_index].strip()
    return query

def _contains_tech_term(query_lower: str, term: str) -> bool:
    normalized_term = (term or "").strip().lower()
    if not normalized_term:
        return False
    if re.search(r"[a-z0-9]", normalized_term):
        boundary_pattern = rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])"
        return re.search(boundary_pattern, query_lower) is not None
    return normalized_term in query_lower

def _collect_tech_hits(query: str) -> List[str]:
    lowered = (query or "").lower()
    if not lowered:
        return []
    hits: List[str] = []
    for term in ALL_TECH_TERMS:
        if _contains_tech_term(lowered, term):
            hits.append(term)
    return hits

def _is_chat_first_query(query: str) -> bool:
    normalized = (query or "").strip()
    if not normalized:
        return True
    return any(pattern.search(normalized) for pattern in CHAT_FIRST_PATTERNS)

def _has_rag_intent(query: str) -> bool:
    normalized = (query or "").strip()
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in RAG_INTENT_PATTERNS)


def _heuristic_route_mode(query: str) -> tuple[str, List[str]]:
    if _is_drawing_request(query):
        return "direct", []
    if _is_chat_first_query(query) and not _has_rag_intent(query):
        return "direct", []

    tech_hits = _collect_tech_hits(query)
    has_rag_intent = _has_rag_intent(query)
    if has_rag_intent:
        return "rag", tech_hits
    if len(tech_hits) >= 2:
        return "rag", tech_hits
    if len(tech_hits) == 1 and tech_hits[0].lower() not in SHORT_AMBIGUOUS_TECH_TERMS:
        return "rag", tech_hits
    return "direct", tech_hits

def entry_node(state: AgentState) -> Dict[str, Any]:
    """用户输入节点"""
    logger.info(f"接收用户查询: {state.user_query[:50]}...")
    
    # 初始化时间戳
    messages = list(_get_field(state, "messages", []) or [])
    messages.append({"role": "user", "content": state.user_query})

    return {
        "start_time": time.time(),
        "messages": messages
    }

@traceable(name="routing_decision")
def routing_node(state: AgentState) -> Dict[str, Any]:
    """Routing node: decide between rag/direct/hitl."""
    user_query = _get_field(state, "user_query", "")
    routing_query = _extract_current_user_query(user_query)
    started_at = time.perf_counter()
    confidence = 0.0
    reason = "router_disabled_heuristic"
    fallback_used = False
    tech_hits: List[str] = []

    if settings.ROUTER_ENABLE:
        decision = route_query(
            routing_query,
            context_hint=_get_field(state, "normalized_query", "") or None,
        )
        mode = decision.mode
        confidence = decision.confidence
        reason = decision.reason
        fallback_used = decision.fallback_used
    else:
        mode, tech_hits = _heuristic_route_mode(routing_query)

    log_event(
        logger,
        logging.INFO,
        "routing_decision",
        query=text_preview(routing_query, 200),
        mode=mode,
        confidence=round(confidence, 4),
        fallback_used=fallback_used,
        latency_ms=elapsed_ms(started_at),
        reason=text_preview(reason, 160),
        router_enabled=settings.ROUTER_ENABLE,
    )
    if tech_hits:
        logger.debug("routing heuristic tech_hits=%s", tech_hits[:8])
    return {"mode": mode}
@task
def normalize_query_with_llm(model: str, prompt: str, query: str):
    client = _get_openai_client()
    trace_id, started_at = _llm_invoke_start(
        scene="normalize_query",
        model=model,
        request_hint=f"query_len={len(query or '')}",
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": query},
            ],
            temperature=0.2,
        )
    except Exception as exc:
        _llm_invoke_failed(
            scene="normalize_query",
            model=model,
            trace_id=trace_id,
            exc=exc,
            started_at=started_at,
            with_stack=True,
        )
        raise
    _llm_invoke_done("normalize_query", model, trace_id, started_at, response)
    return response
    
@traceable(name="query_normalization")
def query_normalization_node(state: AgentState) -> Dict[str, Any]:
    """查询规范化节点"""
    user_query = _get_field(state, "user_query", "")
    logger.info(f"规范化查询: {user_query}")

    normalized_queries = []
    prompt = "\n".join([
        'You are a software engineering query normalization assistant.',
        '',
        'Your task is:',
        'Convert a user\'s natural-language question into one or more standardized, engineering-oriented, searchable queries.',
        'Make them as close as possible to technical keywords in the following categories:',
        '- Software architecture / Distributed systems',
        '- Backend engineering / Middleware',
        '- Databases / Vector databases / Data engineering',
        '- AI / LLM / RAG / Agent',
        '- DevOps / Cloud / Non-functional requirements',
        '- Common programming languages and frameworks',
        '',
        'Transformation rules:',
        '1. Remove emotional, goal-oriented, or vague wording (e.g., "I want", "any expert recommendations", "best", "very strong").',
        '2. Replace colloquial expressions with explicit technical concepts (e.g., "handle lots of users" -> "high concurrency").',
        '3. If multiple technical concerns exist, split them into multiple normalized sub-queries.',
        '4. Do not introduce new requirements not mentioned by the user.',
        '5. Do not provide solutions or recommendations.',
        '',
        'Output format requirements:',
        '- Output JSON only',
        '- Use the normalized_queries field',
        '- Each query should look like a search-engine query or technical document title',
        '',
        'Example:',
        'User input:',
        '"I want to build a RAG system that can handle many simultaneous users. Any good approach?"',
        '',
        'Output:',
        '{',
        '  "normalized_queries": [',
        '    "RAG system architecture for high concurrency",',
        '    "vector database selection for RAG",',
        '    "distributed system design for AI services"',
        '  ]',
        '}',
    ])

    try:
        future = normalize_query_with_llm(model=settings.LLM_MODEL, prompt=prompt, query=user_query)
        response = future.result()
        content = ""
        if hasattr(response, "choices") and response.choices:
            first_choice = response.choices[0]
            if isinstance(first_choice, dict):
                message = first_choice.get("message") or {}
                content = message.get("content") or ""
            else:
                message = getattr(first_choice, "message", None)
                content = getattr(message, "content", "") if message else ""

        parsed = json.loads(content) if content else {}
        if isinstance(parsed, dict):
            normalized_queries = parsed.get("normalized_queries", [])
        elif isinstance(parsed, list):
            normalized_queries = parsed

        if not isinstance(normalized_queries, list):
            raise ValueError("LLM返回格式不是列表")

        normalized_queries = [str(item).strip() for item in normalized_queries if str(item).strip()]
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "llm.output.parse.fail",
            component="llm",
            scene="normalize_query",
            model=settings.LLM_MODEL,
            query=text_preview(user_query),
            **error_fields(exc),
        )

    normalized_query = "\n".join(normalized_queries) if normalized_queries else user_query.strip()

    constraints = {"language": "", "scenario": "", "preference": ""}

    if "python" in user_query.lower():
        constraints["language"] = "Python"
    elif "java" in user_query.lower():
        constraints["language"] = "Java"
    elif "javascript" in user_query.lower() or "js" in user_query.lower():
        constraints["language"] = "JavaScript"
    elif "go" in user_query.lower():
        constraints["language"] = "Go"
    elif "rust" in user_query.lower():
        constraints["language"] = "Rust"

    if "web" in user_query.lower() or "前端" in user_query.lower():
        constraints["scenario"] = "Web开发"
    elif "mobile" in user_query.lower() or "移动端" in user_query.lower():
        constraints["scenario"] = "移动开发"
    elif "ml" in user_query.lower() or "机器学习" in user_query.lower():
        constraints["scenario"] = "机器学习"
    elif "数据" in user_query.lower() or "database" in user_query.lower():
        constraints["scenario"] = "数据处理"

    if "轻量" in user_query.lower() or "lightweight" in user_query.lower():
        constraints["preference"] = "轻量级"
    elif "企业" in user_query.lower() or "enterprise" in user_query.lower():
        constraints["preference"] = "企业级"
    elif "开源" in user_query.lower() or "open source" in user_query.lower():
        constraints["preference"] = "开源"

    return {
        "normalized_query": normalized_query,
        "constraints": constraints
    }
@task
def sub_question_generation_with_llm(model: str, prompt: str, query: str):
    client = _get_openai_client()
    trace_id, started_at = _llm_invoke_start(
        scene="sub_question_generation",
        model=model,
        request_hint=f"query_len={len(query or '')}",
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": query},
            ],
            temperature=0.3,
        )
    except Exception as exc:
        _llm_invoke_failed(
            scene="sub_question_generation",
            model=model,
            trace_id=trace_id,
            exc=exc,
            started_at=started_at,
            with_stack=True,
        )
        raise
    _llm_invoke_done("sub_question_generation", model, trace_id, started_at, response)
    return response
@traceable(name="task_decomposition")
def sub_question_generation_node(state: AgentState) -> Dict[str, Any]:
    """Sub-question generation node."""
    normalized_query = _get_field(state, "normalized_query", "")
    constraints = _get_field(state, "constraints", {})
    min_sub_questions = max(1, int(getattr(settings, "SUB_QUESTION_MIN_COUNT", 2)))
    max_sub_questions = max(min_sub_questions, int(getattr(settings, "SUB_QUESTION_MAX_COUNT", 3)))

    logger.info(f"Generating sub-questions for query: {normalized_query}")

    sub_questions = []
    prompt = "\n".join(
        [
            "You are a requirements analysis assistant for a software recommendation system.",
            f"Generate {min_sub_questions} to {max_sub_questions} concise and non-overlapping sub-questions.",
            "Focus only on the most important dimensions: core problem/users, domain+stack constraints, and key non-functional/data requirements.",
            f"Return only a JSON array (preferred length {min_sub_questions}-{max_sub_questions}) or a JSON object containing the sub_questions field.",
            "Do not output any other text.",
        ]
    )
    try:
        future = sub_question_generation_with_llm(
            model=settings.LLM_MODEL,
            prompt=prompt,
            query=normalized_query,
        )
        response = future.result()

        content = ""
        if hasattr(response, "choices") and response.choices:
            first_choice = response.choices[0]
            if isinstance(first_choice, dict):
                message = first_choice.get("message") or {}
                content = message.get("content") or ""
            else:
                message = getattr(first_choice, "message", None)
                content = getattr(message, "content", "") if message else ""

        parsed = json.loads(content) if content else []
        sub_questions = _normalize_sub_questions(parsed)
        if not sub_questions:
            raise ValueError("LLM response format is not a sub-question list")
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "llm.output.parse.fail",
            component="llm",
            scene="sub_question_generation",
            model=settings.LLM_MODEL,
            query=text_preview(normalized_query),
            **error_fields(exc),
        )

    if not sub_questions:
        sub_questions = [normalized_query]
        if " and " in normalized_query.lower():
            parts = re.split(r'\band\b', normalized_query, flags=re.IGNORECASE)
            if len(parts) > 1:
                sub_questions = [part.strip() for part in parts if part.strip()]
    sub_questions = _enforce_sub_question_count(
        sub_questions,
        normalized_query,
        min_count=min_sub_questions,
        max_count=max_sub_questions,
    )

    logger.warning(
        "SUBQ_READY count=%d target=%d~%d preview=%s",
        len(sub_questions),
        min_sub_questions,
        max_sub_questions,
        sub_questions[:3],
    )

    return {
        "sub_questions": sub_questions,
        "pending_sub_questions": sub_questions,
        "awaiting_human_confirmation": True,
        "human_confirmation_done": False,
    }


def _normalize_sub_questions(raw: Any) -> List[str]:
    cleaned: List[str] = []
    seen = set()

    def append_unique(value: str) -> None:
        text = str(value or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        cleaned.append(text)

    def try_parse_structured_text(text: str) -> Any:
        stripped = text.strip()
        if not stripped or stripped[0] not in {"[", "{"}:
            return None
        try:
            return json.loads(stripped)
        except Exception:
            pass
        try:
            return ast.literal_eval(stripped)
        except Exception:
            return None

    def collect(value: Any) -> None:
        if value is None:
            return

        if isinstance(value, str):
            text = value.strip()
            if not text:
                return
            parsed = try_parse_structured_text(text)
            if parsed is not None:
                collect(parsed)
                return
            append_unique(text)
            return

        if isinstance(value, dict):
            preferred: List[Any] = []
            for key in ("sub_questions", "pending_sub_questions"):
                if key in value:
                    preferred.append(value.get(key))
            if preferred:
                for item in preferred:
                    collect(item)
                return
            for nested in value.values():
                collect(nested)
            return

        if isinstance(value, (list, tuple, set)):
            for item in value:
                collect(item)
            return

        text = str(value).strip()
        if text:
            append_unique(text)

    collect(raw)
    return cleaned


def _enforce_sub_question_count(
    sub_questions: List[str],
    normalized_query: str,
    *,
    min_count: int,
    max_count: int,
) -> List[str]:
    result = _normalize_sub_questions(sub_questions)

    if not result and str(normalized_query or "").strip():
        result = [str(normalized_query).strip()]

    if len(result) < min_count and str(normalized_query or "").strip():
        parts = re.split(
            r"\band\b|\bor\b|with|以及|并且|和|，|,|；|;",
            normalized_query,
            flags=re.IGNORECASE,
        )
        for part in parts:
            candidate = str(part or "").strip()
            if not candidate or candidate in result:
                continue
            result.append(candidate)
            if len(result) >= min_count:
                break

    if len(result) < min_count:
        fallback_templates = [
            "What technology stack and runtime constraints should this solution satisfy?",
            "What are the key non-functional requirements such as performance, scalability, and reliability?",
            "What data and integration requirements should be considered?",
        ]
        for template in fallback_templates:
            if template in result:
                continue
            result.append(template)
            if len(result) >= min_count:
                break

    if len(result) > max_count:
        result = result[:max_count]

    return result


@traceable(name="hitl_confirmation")
def human_confirmation_node(state: AgentState) -> Dict[str, Any]:
    """
    Human-in-the-loop 阻塞节点：
    1) 首次到达时通过 interrupt 暂停；
    2) 外部 resume 后拿到用户确认内容；
    3) 写回最终 sub_questions，继续后续检索流程。
    """
    original_sub_questions = _normalize_sub_questions(_get_field(state, "sub_questions", []))
    if not original_sub_questions:
        fallback_query = str(_get_field(state, "normalized_query", "")).strip()
        original_sub_questions = [fallback_query] if fallback_query else []

    hitl_policy = str(_get_field(state, "hitl_policy", "human") or "human").strip().lower()
    if hitl_policy not in {"human", "auto_confirm", "oracle_edit"}:
        hitl_policy = "human"
    oracle_edits = _normalize_sub_questions(_get_field(state, "oracle_edits", []))

    # Guard against unexpected re-entry after a successful confirm within the same graph run.
    if bool(_get_field(state, "human_confirmation_done", False)):
        logger.warning(
            "HITL_BYPASS_ALREADY_CONFIRMED subq_count=%d",
            len(original_sub_questions),
        )
        return {
            "awaiting_human_confirmation": False,
            "pending_sub_questions": [],
        }

    if hitl_policy == "auto_confirm":
        logger.warning(
            "HITL_AUTO_CONFIRM subq_count=%d",
            len(original_sub_questions),
        )
        return {
            "sub_questions": original_sub_questions,
            "awaiting_human_confirmation": False,
            "pending_sub_questions": [],
            "human_feedback": "",
            "human_confirmation_done": True,
            "hitl": {
                "policy": "auto_confirm",
                "decision": "confirm",
                "edited_subqueries": [],
            },
        }

    if hitl_policy == "oracle_edit":
        final_sub_questions = oracle_edits if oracle_edits else original_sub_questions
        decision = "edit" if oracle_edits else "confirm"
        logger.warning(
            "HITL_ORACLE_EDIT decision=%s edited_count=%d final_count=%d",
            decision,
            len(oracle_edits),
            len(final_sub_questions),
        )
        return {
            "sub_questions": final_sub_questions,
            "awaiting_human_confirmation": False,
            "pending_sub_questions": [],
            "human_feedback": "",
            "human_confirmation_done": True,
            "hitl": {
                "policy": "oracle_edit",
                "decision": decision,
                "edited_subqueries": oracle_edits,
            },
        }

    request_payload = {
        "type": "human_confirmation",
        "sub_questions": original_sub_questions,
    }

    logger.warning(
        "HITL_INTERRUPT_ENTER subq_count=%d preview=%s",
        len(original_sub_questions),
        original_sub_questions[:3],
    )

    try:
        human_input = interrupt(request_payload)
    except RuntimeError as exc:
        # Never auto-confirm when interrupt context is missing; fail fast for caller-side handling.
        if "Called get_config outside of a runnable context" not in str(exc):
            raise
        logger.error(
            "Interrupt called without LangGraph runnable context; refusing auto-confirm."
        )
        raise RuntimeError(
            "Interrupt requires an active LangGraph runnable context."
        ) from exc

    action = str(_get_field(human_input, "action", "confirm")).strip().lower()
    if action not in {"confirm", "edit"}:
        action = "confirm"

    edited_sub_questions = _normalize_sub_questions(_get_field(human_input, "sub_questions", []))
    comment = str(_get_field(human_input, "comment", "")).strip()
    final_sub_questions = edited_sub_questions if action == "edit" and edited_sub_questions else original_sub_questions

    logger.warning(
        "HITL_RESUMED action=%s edited_count=%d final_count=%d",
        action,
        len(edited_sub_questions),
        len(final_sub_questions),
    )

    return {
        "sub_questions": final_sub_questions,
        "awaiting_human_confirmation": False,
        "pending_sub_questions": [],
        "human_feedback": comment,
        "human_confirmation_done": True,
        "hitl": {
            "policy": "human",
            "decision": action,
            "edited_subqueries": edited_sub_questions if action == "edit" else [],
        },
    }


@traceable(name="retrieve_multi_subquery")
def retrieve_node(state: AgentState) -> Dict[str, Any]:
    """Retrieve node: global merge -> dedup -> rerank -> top3 evidence."""
    MAX_EVIDENCE = 3
    RERANK_TOP_N = 5
    sub_questions = _get_field(state, "sub_questions", [])
    retrieval_records = list(_get_field(state, "retrieval_records", []) or [])
    iteration_count = _get_field(state, "iteration_count", 0)
    trace_id = new_trace_id("search")
    global_query = str(_get_field(state, "user_query", "") or "").strip()
    if not global_query:
        global_query = " ".join(
            str(item or "").strip()
            for item in sub_questions
            if str(item or "").strip()
        )
    all_docs: List[Document] = []

    log_event(
        logger,
        logging.INFO,
        "search.retrieve.start",
        component="search",
        trace_id=trace_id,
        scene="retrieve_node",
        iteration=iteration_count,
        sub_question_count=len(sub_questions),
        global_top_n=MAX_EVIDENCE,
    )

    for index, question in enumerate(sub_questions, start=1):
        question_started_at = time.perf_counter()
        question_hint = text_preview(question)
        log_event(
            logger,
            logging.INFO,
            "search.retrieve.question.start",
            component="search",
            trace_id=trace_id,
            scene="retrieve_node",
            iteration=iteration_count,
            question_index=index,
            question_count=len(sub_questions),
            query=question_hint,
            top_k=settings.TOP_K,
        )
        search_results = retrieve(question, top_k=settings.TOP_K)
        all_docs.extend(search_results)
        subquery_id = f"sq_{index}"
        retrieval_record = _build_retrieval_record(
            subquery_id=subquery_id,
            subquery=question,
            docs=search_results,
        )
        retrieval_records.append(retrieval_record)
        log_event(
            logger,
            logging.INFO,
            "search.retrieve.question.done",
            component="search",
            trace_id=trace_id,
            scene="retrieve_node",
            iteration=iteration_count,
            question_index=index,
            question_count=len(sub_questions),
            query=question_hint,
            docs=len(search_results),
            cumulative_docs=len(all_docs),
            elapsed_ms=elapsed_ms(question_started_at),
        )

    seen_doc_ids: set[str] = set()
    unique_docs: List[Document] = []
    for doc_index, doc in enumerate(all_docs, start=1):
        metadata = _get_field(doc, "metadata", {}) or {}
        doc_id = str(_get_field(metadata, "doc_id", "") or "").strip()
        if not doc_id:
            doc_id = _extract_doc_id(doc, fallback_prefix="merged", index=doc_index)
            if isinstance(metadata, dict):
                metadata["doc_id"] = doc_id
            else:
                _set_field(metadata, "doc_id", doc_id)
        if doc_id in seen_doc_ids:
            continue
        seen_doc_ids.add(doc_id)
        unique_docs.append(doc)

    ranked_docs = rerank_documents(query=global_query, docs=unique_docs, top_n=RERANK_TOP_N)
    final_docs = ranked_docs[:MAX_EVIDENCE]
    quality_score = min(0.9, 0.5 + (len(final_docs) * 0.1))

    retrieved_doc_ids: List[str] = []
    evidence: List[Dict[str, Any]] = []
    for doc_index, doc in enumerate(final_docs, start=1):
        doc_id = _extract_doc_id(doc, fallback_prefix="global", index=doc_index)
        if doc_id not in retrieved_doc_ids:
            retrieved_doc_ids.append(doc_id)
        metadata = _get_field(doc, "metadata", {}) or {}
        search_result = {
            "content": doc.content,
            "score": doc.score,
            "doc_id": doc_id,
            "source": str(_get_field(metadata, "source", "") or ""),
            "retrieval_source": str(_get_field(metadata, "retrieval_source", "") or ""),
        }
        evidence.append(
            {
                "question": global_query,
                "documents": [doc],
                "search_results": [search_result],
                "quality_score": quality_score,
            }
        )

    log_event(
        logger,
        logging.INFO,
        "search.retrieve.done",
        component="search",
        trace_id=trace_id,
        scene="retrieve_node",
        iteration=iteration_count,
        total_collected_docs=len(all_docs),
        deduped_docs=len(unique_docs),
        ranked_docs=len(ranked_docs),
        evidence_items=len(evidence),
        total_evidence_docs=len(final_docs),
        total_retrieved_doc_ids=len(retrieved_doc_ids),
    )
    return {
        "evidence": evidence,
        "retrieval_records": retrieval_records,
        "retrieved_doc_ids": retrieved_doc_ids,
    }


def evidence_collection_node(state: AgentState) -> Dict[str, Any]:
    """Evidence collection node: persist retrieved evidence and advance iteration."""
    evidence = _get_field(state, "evidence", [])
    iteration_count = _get_field(state, "iteration_count", 0)

    logger.warning(
        "EVIDENCE_COLLECTION_ENTER iteration=%d evidence_count=%d",
        iteration_count,
        len(evidence),
    )
    return {
        "evidence": evidence,
        "iteration_count": iteration_count + 1
    }

def evidence_evaluation_node(state: AgentState) -> Dict[str, Any]:
    """证据评估节点"""
    evidence = _get_field(state, "evidence", [])

    logger.info(f"评估 {len(evidence)} 个证据项")
    
    # 在实际应用中，这里会使用 LLM 来评估证据的质量
    # 简化实现：根据文档数量和分数评估
    
    for item in evidence:
        # 重新计算质量分数，结合相关性、时效性和权威性
        docs = _get_field(item, "documents", [])
        if docs:
            avg_score = sum(doc.score for doc in docs) / len(docs) if docs else 0
            # 简化的质量分数计算，实际应用中会更复杂
            _set_field(item, "quality_score", min(1.0, avg_score + 0.1))
    
    return {
        "evidence": evidence
    }

def candidate_generation_node(state: AgentState) -> Dict[str, Any]:
    """候选方案生成节点"""
    evidence = _get_field(state, "evidence", [])
    candidates = _get_field(state, "candidates", [])

    logger.info(f"基于 {len(evidence)} 个证据项生成候选方案")

    if not evidence:
        return {"candidates": candidates}

    def _truncate(text: str, max_len: int = 600) -> str:
        if not text:
            return ""
        return text if len(text) <= max_len else text[:max_len] + "..."

    evidence_payload = []
    for item in evidence:
        docs = _get_field(item, "documents", [])
        doc_summaries = []
        for doc in docs[:3]:
            content = _get_field(doc, "content", "")
            metadata = _get_field(doc, "metadata", {})
            source = _get_field(metadata, "source", "")
            doc_summaries.append({
                "source": source,
                "content": _truncate(content, 600),
                "score": _get_field(doc, "score", 0.0)
            })
        evidence_payload.append({
            "question": _get_field(item, "question", ""),
            "quality_score": _get_field(item, "quality_score", 0.0),
            "documents": doc_summaries
        })

    llm_candidates = []
    try:
        client = _get_openai_client()
        trace_id, started_at = _llm_invoke_start(
            scene="candidate_generation",
            model=settings.LLM_MODEL,
            request_hint=f"evidence_items={len(evidence_payload)}",
        )
        response = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a software recommendation assistant. Generate a list of candidate solutions based on the provided evidence. "
                        "Return only a JSON array or a JSON object containing the candidates field. Do not output any other text. "
                        "Each candidate must include: solution, rationale, pros, cons, relevance_score (0-1)."
                    )
                },
                {"role": "user", "content": json.dumps(evidence_payload, ensure_ascii=False)}
            ],
            temperature=0.4
        )
        _llm_invoke_done("candidate_generation", settings.LLM_MODEL, trace_id, started_at, response)

        content = ""
        if hasattr(response, "choices") and response.choices:
            first_choice = response.choices[0]
            if isinstance(first_choice, dict):
                message = first_choice.get("message") or {}
                content = message.get("content") or ""
            else:
                message = getattr(first_choice, "message", None)
                content = getattr(message, "content", "") if message else ""

        parsed = json.loads(content) if content else []
        if isinstance(parsed, dict):
            parsed = parsed.get("candidates", [])

        if not isinstance(parsed, list):
            raise ValueError("LLM返回格式不是候选列表")

        for item in parsed:
            if not isinstance(item, dict):
                continue
            solution = str(item.get("solution", "")).strip()
            if not solution:
                continue
            rationale = str(item.get("rationale", "")).strip()
            pros = item.get("pros") if isinstance(item.get("pros"), list) else []
            cons = item.get("cons") if isinstance(item.get("cons"), list) else []
            score = item.get("relevance_score", 0.0)
            try:
                score = float(score)
            except (TypeError, ValueError):
                score = 0.0
            score = max(0.0, min(1.0, score))

            llm_candidates.append({
                "solution": solution,
                "rationale": rationale,
                "pros": pros,
                "cons": cons,
                "relevance_score": score
            })
    except Exception as exc:
        _llm_invoke_failed(
            scene="candidate_generation",
            model=settings.LLM_MODEL,
            trace_id=trace_id if "trace_id" in locals() else new_trace_id("llm"),
            exc=exc,
            started_at=started_at if "started_at" in locals() else None,
            with_stack=True,
        )

    if llm_candidates:
        candidates.extend(llm_candidates)
    else:
        for item in evidence:
            docs = _get_field(item, "documents", [])
            if docs:
                first_doc = docs[0]
                solution_text = first_doc.content[:200] + "..." if len(first_doc.content) > 200 else first_doc.content

                relevance_score = _get_field(item, "quality_score", 0.5)

                candidate_solution = {
                    "solution": solution_text,
                    "rationale": f"根据文档来源 {first_doc.metadata.source} 推荐",
                    "pros": ["相关性强", "来源可靠"] if relevance_score > 0.7 else ["有一定参考价值"],
                    "cons": ["信息可能不够全面"] if relevance_score < 0.8 else [],
                    "relevance_score": relevance_score
                }

                candidates.append(candidate_solution)

    candidates.sort(key=lambda x: _get_field(x, "relevance_score", 0.0), reverse=True)

    return {
        "candidates": candidates
    }

def coverage_check_node(state: AgentState) -> Dict[str, Any]:
    """覆盖率检查节点"""
    sub_questions = _get_field(state, "sub_questions", [])
    evidence = _get_field(state, "evidence", [])
    candidates = _get_field(state, "candidates", [])
    coverage_threshold = _get_field(state, "coverage_threshold", 0.8)
    max_iterations = _get_field(state, "max_iterations", 3)
    iteration_count = _get_field(state, "iteration_count", 0)
    
    logger.info(f"检查覆盖率: {len(evidence)}/{len(sub_questions)}")
    
    # 计算覆盖率：已解答的子问题数量 / 总子问题数量
    total_questions = len(sub_questions)
    covered_questions = len([item for item in evidence if _get_field(item, "documents", [])])
    covered_questions = min(covered_questions, total_questions)

    coverage = covered_questions / total_questions if total_questions > 0 else 0
    
    # 检查是否需要继续细化
    needs_refinement = (
        coverage < coverage_threshold and 
        iteration_count < max_iterations and
        len(candidates) == 0  # 如果没有生成任何候选方案，也需要继续
    )
    
    logger.info(f"覆盖率: {coverage:.2f}, 阈值: {coverage_threshold}, 需要细化: {needs_refinement}")
    
    return {
        "coverage": coverage,
        "needs_refinement": needs_refinement
    }

@traceable(name="final_generation_rag")
def answer_generation_node(state: AgentState) -> Dict[str, Any]:
    """答案生成节点（RAG模式）"""
    user_query = _get_field(state, "user_query", "")
    candidates = _get_field(state, "candidates", [])
    evidence = _get_field(state, "evidence", [])

    logger.warning(
        "ANSWER_GENERATION_ENTER candidate_count=%d evidence_count=%d",
        len(candidates),
        len(evidence),
    )
    logger.info(f"生成最终答案，基于 {len(candidates)} 个候选方案")
    
    if candidates:
        # 选择得分最高的候选方案
        best_candidate = candidates[0]
        
        # 构造最终答案
        final_answer = f"根据您的查询 '{user_query}'，我推荐:\n\n"
        final_answer += f"方案: {_get_field(best_candidate, 'solution', '')}\n"
        final_answer += f"理由: {_get_field(best_candidate, 'rationale', '')}\n"
        pros = _get_field(best_candidate, 'pros', [])
        cons = _get_field(best_candidate, 'cons', [])
        final_answer += f"优点: {', '.join(pros) if pros else '无'}\n"
        if cons:
            final_answer += f"缺点: {', '.join(cons)}\n"
    else:
        final_answer = f"抱歉，基于现有知识库，我无法为您的查询 '{user_query}' 找到合适的软件推荐方案。"
    
    messages = list(_get_field(state, "messages", []) or [])
    messages.append({"role": "assistant", "content": final_answer})

    return {
        "final_answer": final_answer,
        "messages": messages,
        "retrieval_records": list(_get_field(state, "retrieval_records", []) or []),
        "retrieved_doc_ids": list(_get_field(state, "retrieved_doc_ids", []) or []),
        "hitl": dict(_get_field(state, "hitl", {}) or {}),
    }

@traceable(name="final_generation_chat")
def chat_answer_generation_node(state: AgentState) -> Dict[str, Any]:
    """Answer generation node for direct/chat branch (retrieval optional)."""
    user_query = _get_field(state, "user_query", "")
    retrieval_query = _extract_current_user_query(user_query) or user_query
    mode = str(_get_field(state, "mode", "") or "").strip().lower()

    messages = list(_get_field(state, "messages", []) or [])
    if not messages:
        messages = [{"role": "user", "content": user_query}]

    retrieval_records = list(_get_field(state, "retrieval_records", []) or [])
    retrieved_doc_ids = list(_get_field(state, "retrieved_doc_ids", []) or [])
    retrieved_docs: List[Document] = []
    if mode != "direct":
        retrieval_trace_id = new_trace_id("search")
        log_event(
            logger,
            logging.INFO,
            "search.chat_retrieval.start",
            component="search",
            trace_id=retrieval_trace_id,
            scene="chat_answer_generation",
            query=text_preview(retrieval_query),
            top_k=settings.TOP_K,
        )

        retrieval_started_at = time.perf_counter()
        try:
            retrieved_docs = retrieve(retrieval_query, top_k=settings.TOP_K)
        except Exception as exc:
            log_event(
                logger,
                logging.WARNING,
                "search.chat_retrieval.fail",
                component="search",
                trace_id=retrieval_trace_id,
                scene="chat_answer_generation",
                query=text_preview(retrieval_query),
                elapsed_ms=elapsed_ms(retrieval_started_at),
                **error_fields(exc),
            )
            retrieved_docs = []

        log_event(
            logger,
            logging.INFO,
            "search.chat_retrieval.done",
            component="search",
            trace_id=retrieval_trace_id,
            scene="chat_answer_generation",
            query=text_preview(retrieval_query),
            docs=len(retrieved_docs),
            elapsed_ms=elapsed_ms(retrieval_started_at),
        )
        chat_record = _build_retrieval_record(
            subquery_id="chat_0",
            subquery=retrieval_query,
            docs=retrieved_docs,
        )
        retrieval_records.append(chat_record)
        retrieved_doc_ids = _merge_doc_ids(
            retrieved_doc_ids,
            chat_record.get("retrieved_doc_ids", []),
        )
    else:
        log_event(
            logger,
            logging.INFO,
            "search.chat_retrieval.skipped",
            component="search",
            scene="chat_answer_generation",
            query=text_preview(retrieval_query),
            reason="mode_direct",
        )

    def _format_retrieved_context(docs: List[Document]) -> str:
        lines: List[str] = []
        for idx, doc in enumerate(docs[:4], start=1):
            content = str(_get_field(doc, "content", "")).strip().replace("\n", " ")
            if len(content) > 400:
                content = content[:400] + "..."
            metadata = _get_field(doc, "metadata", {})
            source = str(_get_field(metadata, "source", "")).strip() or "unknown"
            url = str(_get_field(metadata, "url", "")).strip()
            score = _get_field(doc, "score", 0.0)
            try:
                score_text = f"{float(score):.4f}"
            except Exception:
                score_text = str(score)
            header = f"[{idx}] source={source} score={score_text}"
            if url:
                header += f" url={url}"
            lines.append(header)
            lines.append(content)
        return "\n".join(lines).strip()

    retrieved_context = _format_retrieved_context(retrieved_docs)
    llm_messages = list(messages)
    if retrieved_context:
        system_prompt = "\n".join(
            [
                "Use the retrieved context as the primary source of truth.",
"If the answer can be reasonably inferred from the retrieved context, answer based on it.",
"Only say 'I don't know' if the retrieved context is clearly irrelevant.",
                "",
                "[Retrieved Context]",
                retrieved_context,
            ]
        )
        llm_messages = [{"role": "system", "content": system_prompt}] + llm_messages

    prepared_messages = _prepare_messages_for_small_context(llm_messages)
    max_output_tokens = max(16, int(os.getenv("CHAT_MAX_OUTPUT_TOKENS", "256")))
    log_event(
        logger,
        logging.INFO,
        "llm.context.trim",
        component="llm",
        scene="chat_answer_generation",
        before_messages=len(llm_messages),
        after_messages=len(prepared_messages),
        before_tokens_rough=_rough_messages_tokens(llm_messages),
        after_tokens_rough=_rough_messages_tokens(prepared_messages),
        max_output_tokens=max_output_tokens,
    )

    try:
        client = _get_openai_client()
        trace_id, started_at = _llm_invoke_start(
            scene="chat_answer_generation",
            model=settings.LLM_MODEL,
            request_hint=f"messages={len(prepared_messages)}",
        )
        response = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=prepared_messages,
            temperature=0.7,
            max_tokens=max_output_tokens,
        )
        _llm_invoke_done("chat_answer_generation", settings.LLM_MODEL, trace_id, started_at, response)

        final_answer = ""
        if hasattr(response, "choices") and response.choices:
            first_choice = response.choices[0]
            if isinstance(first_choice, dict):
                message = first_choice.get("message") or {}
                final_answer = message.get("content") or ""
            else:
                message = getattr(first_choice, "message", None)
                final_answer = getattr(message, "content", "") if message else ""

        if not final_answer:
            final_answer = "抱歉，暂时无法获取LLM回复，请稍后再试。"
    except Exception as exc:
        _llm_invoke_failed(
            scene="chat_answer_generation",
            model=settings.LLM_MODEL,
            trace_id=trace_id if "trace_id" in locals() else new_trace_id("llm"),
            exc=exc,
            started_at=started_at if "started_at" in locals() else None,
            with_stack=True,
        )
        final_answer = "抱歉，暂时无法获取LLM回复，请稍后再试。"

    messages.append({"role": "assistant", "content": final_answer})

    return {
        "final_answer": final_answer,
        "messages": messages,
        "retrieval_records": retrieval_records,
        "retrieved_doc_ids": retrieved_doc_ids,
        "hitl": dict(_get_field(state, "hitl", {}) or {}),
    }

def pre_drawing_node(state: AgentState) -> Dict[str, Any]:
    """画图前处理节点"""
    user_query = _get_field(state, "user_query", "")
    structured_params = pre_drawing_tool(user_query)
    return {"drawing_params": structured_params}

def draw_image_node(state: AgentState) -> Dict[str, Any]:
    """画图执行节点"""
    structured_params = _get_field(state, "drawing_params", "")
    image_url = draw_image_tool(structured_params)
    final_answer = f"图像已生成: {image_url}"
    return {"image_result": image_url, "final_answer": final_answer}
