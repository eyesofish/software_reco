import ast
import json
import logging
import os
import re
import time
from typing import Any

from langgraph.func import task
from langgraph.types import interrupt

from .config import settings
from .document_schema import Document
from .evidence_evaluator import evaluate_evidence
from .llm_governance import governed_chat_completion
from .llm_utils import (
    _build_retrieval_record,
    _build_retrieved_images,
    _extract_doc_id,
    _get_field,
    _get_openai_client,
    _llm_invoke_done,
    _llm_invoke_failed,
    _llm_invoke_start,
    _merge_doc_ids,
    _merge_retrieved_images,
    _prepare_messages_for_small_context,
    _rough_messages_tokens,
    _set_field,
    _stream_chat_completion_text,
)
from .logging_utils import elapsed_ms, error_fields, log_event, new_trace_id, text_preview
from .observability import traceable
from .planner import build_execution_plan
from .retriever import rerank_documents, retrieve
from .router import route_query
from .skill_router import route_skill
from .state import AgentState, CandidateSolution
from .tools import draw_image_tool, pre_drawing_tool

logger = logging.getLogger(__name__)

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
    return any(keyword in text or keyword in text_lower for keyword in DRAWING_KEYWORDS)

def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

KNOWN_FACTS_MARKER = "[Known User Facts]"
SPRING_CONTEXT_QUERY_PATTERN = re.compile(
    r"(?is)^\s*current user input:\s*(?P<input>.*?)(?:\n\s*known user facts:\s*|\n\s*recent conversation history:\s*|$)"
)
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

    # Spring may enrich query into a block that starts with:
    # "Current user input:\n<question>\n..."
    context_match = SPRING_CONTEXT_QUERY_PATTERN.match(query)
    if context_match:
        extracted = str(context_match.group("input") or "").strip()
        if extracted:
            return extracted

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

def _collect_tech_hits(query: str) -> list[str]:
    lowered = (query or "").lower()
    if not lowered:
        return []
    hits: list[str] = []
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


def _heuristic_route_mode(query: str) -> tuple[str, list[str]]:
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

def entry_node(state: AgentState) -> dict[str, Any]:
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
def routing_node(state: AgentState) -> dict[str, Any]:
    """Routing node: decide between rag/direct/hitl."""
    user_query = _get_field(state, "user_query", "")
    routing_query = _extract_current_user_query(user_query)
    started_at = time.perf_counter()
    confidence = 0.0
    reason = "router_disabled_heuristic"
    fallback_used = False
    tech_hits: list[str] = []

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


def _normalize_plan_steps(raw_steps: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_steps, list):
        return []
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(raw_steps, start=1):
        if isinstance(item, dict):
            step_id = str(item.get("step_id", "") or f"step_{index}").strip() or f"step_{index}"
            objective = str(item.get("objective", "") or "").strip()
            action = str(item.get("action", "retrieve") or "retrieve").strip() or "retrieve"
            query = str(item.get("query", "") or "").strip()
            retrieval_profile = str(item.get("retrieval_profile", "balanced") or "balanced").strip() or "balanced"
            required = bool(item.get("required", True))
        else:
            step_id = f"step_{index}"
            objective = str(item or "").strip()
            action = "retrieve"
            query = objective
            retrieval_profile = "balanced"
            required = True
        if not objective and not query:
            continue
        normalized.append(
            {
                "step_id": step_id,
                "objective": objective or query,
                "action": action,
                "query": query or objective,
                "retrieval_profile": retrieval_profile,
                "required": required,
            }
        )
    return normalized


@traceable(name="skill_routing")
def skill_routing_node(state: AgentState) -> dict[str, Any]:
    query = str(_get_field(state, "user_query", "") or "")
    normalized_query = str(_get_field(state, "normalized_query", "") or "")
    started_at = time.perf_counter()

    result = route_skill(
        query,
        normalized_query=normalized_query,
    )
    log_event(
        logger,
        logging.INFO,
        "skill_routing_decision",
        component="routing",
        selected_skill=result.selected_skill,
        confidence=round(result.confidence, 4),
        fallback_used=result.fallback_used,
        reason=text_preview(result.reason, 160),
        latency_ms=elapsed_ms(started_at),
    )
    return {
        "selected_skill": result.selected_skill,
        "skill_candidates": result.candidates,
        "skill_router_reason": result.reason,
    }


@traceable(name="plan_generation")
def planning_node(state: AgentState) -> dict[str, Any]:
    if not bool(getattr(settings, "PLANNER_ENABLE", True)):
        return {
            "plan": {},
            "plan_steps": [],
            "planner_reason": "planner_disabled",
        }

    selected_skill = str(_get_field(state, "selected_skill", "") or "").strip() or "generic_rag"
    user_query = str(_get_field(state, "user_query", "") or "")
    normalized_query = str(_get_field(state, "normalized_query", "") or "")
    constraints = _get_field(state, "constraints", {}) or {}

    started_at = time.perf_counter()
    plan = build_execution_plan(
        selected_skill=selected_skill,
        user_query=user_query,
        normalized_query=normalized_query,
        constraints=constraints,
    )
    plan_steps = _normalize_plan_steps(plan.model_dump().get("steps", []))
    planner_reason = str(plan.planner_reason or "").strip() or "template_plan"

    log_event(
        logger,
        logging.INFO,
        "planner_ready",
        component="planner",
        selected_skill=selected_skill,
        steps=len(plan_steps),
        reason=text_preview(planner_reason, 200),
        latency_ms=elapsed_ms(started_at),
    )
    return {
        "plan": plan.model_dump(),
        "plan_steps": plan_steps,
        "planner_reason": planner_reason,
    }


@task
def normalize_query_with_llm(model: str, prompt: str, query: str):
    client = _get_openai_client()
    trace_id, started_at = _llm_invoke_start(
        scene="normalize_query",
        model=model,
        request_hint=f"query_len={len(query or '')}",
    )
    try:
        response = governed_chat_completion(
            client=client,
            scene="normalize_query",
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
def query_normalization_node(state: AgentState) -> dict[str, Any]:
    """查询规范化节点"""
    user_query = _extract_current_user_query(_get_field(state, "user_query", ""))
    logger.info(f"规范化查询: {user_query}")

    normalized_queries = []
    prompt = "\n".join([
        'You are a software engineering query normalization assistant.',
        '',
        'Your task is:',
        (
            "Convert a user's natural-language question into one or more standardized, "
            "engineering-oriented, searchable queries."
        ),
        'Make them as close as possible to technical keywords in the following categories:',
        '- Software architecture / Distributed systems',
        '- Backend engineering / Middleware',
        '- Databases / Vector databases / Data engineering',
        '- AI / LLM / RAG / Agent',
        '- DevOps / Cloud / Non-functional requirements',
        '- Common programming languages and frameworks',
        '',
        'Transformation rules:',
        (
            '1. Remove emotional, goal-oriented, or vague wording (e.g., "I want", '
            '"any expert recommendations", "best", "very strong").'
        ),
        (
            '2. Replace colloquial expressions with explicit technical concepts (e.g., "handle lots of users" -> '
            '"high concurrency").'
        ),
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

    use_llm_normalization = bool(getattr(settings, "QUERY_NORMALIZATION_USE_LLM", True))
    max_chars_for_llm = max(0, int(getattr(settings, "QUERY_NORMALIZATION_MAX_CHARS_FOR_LLM", 320)))
    if max_chars_for_llm > 0 and len(str(user_query or "")) > max_chars_for_llm:
        use_llm_normalization = False

    if use_llm_normalization:
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
        response = governed_chat_completion(
            client=client,
            scene="sub_question_generation",
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


@task
def retrieve_with_search(
    query: str,
    top_k: int,
    session_id: str = "",
    selected_skill: str = "",
    memory_context: list[dict[str, Any]] | None = None,
    query_image_candidates: list[Document] | None = None,
):
    return retrieve(
        query,
        top_k=top_k,
        session_id=session_id or None,
        selected_skill=selected_skill or None,
        memory_context=memory_context,
        query_image_candidates=query_image_candidates,
    )


@task
def candidate_generation_with_llm(
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
):
    client = _get_openai_client()
    return governed_chat_completion(
        client=client,
        scene="candidate_generation",
        model=model,
        messages=messages,
        temperature=temperature,
    )


@task
def chat_answer_with_llm(
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
):
    client = _get_openai_client()
    return governed_chat_completion(
        client=client,
        scene="chat_answer",
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )


@task
def draw_image_with_tool(structured_params: str):
    return draw_image_tool(structured_params)


@traceable(name="task_decomposition")
def sub_question_generation_node(state: AgentState) -> dict[str, Any]:
    """Sub-question generation node."""
    normalized_query = _get_field(state, "normalized_query", "")
    selected_skill = str(_get_field(state, "selected_skill", "") or "").strip()
    plan_steps = _normalize_plan_steps(_get_field(state, "plan_steps", []) or [])
    planner_reason = str(_get_field(state, "planner_reason", "") or "").strip()
    min_sub_questions = max(1, int(getattr(settings, "SUB_QUESTION_MIN_COUNT", 2)))
    max_sub_questions = max(min_sub_questions, int(getattr(settings, "SUB_QUESTION_MAX_COUNT", 3)))

    normalized_query_text = str(normalized_query or "").strip().lower()
    factoid_prefix_match = re.match(
        r"^\s*(what|how|why|when|where|which|who|can|could|should|is|are|does|do)\b",
        normalized_query_text,
    )
    looks_like_question = (
        ("?" in normalized_query_text)
        or ("？" in normalized_query_text)
        or (factoid_prefix_match is not None)
        or normalized_query_text.startswith(("什么", "如何", "怎么", "为何", "为什么", "是否", "哪个", "哪种", "怎样"))
    )
    is_fact_qa = selected_skill == "quick_fact_qa" or (
        selected_skill == "generic_rag" and looks_like_question
    )
    if is_fact_qa:
        min_sub_questions = 1
        max_sub_questions = min(max_sub_questions, 2)

    logger.info(f"Generating sub-questions for query: {normalized_query}")

    sub_questions = []
    planned_seed_questions = []
    for step in plan_steps:
        candidate = str(step.get("query", "") or step.get("objective", "")).strip()
        if candidate and candidate not in planned_seed_questions:
            planned_seed_questions.append(candidate)
    planned_seed_questions = planned_seed_questions[:max_sub_questions]

    # For fact QA, planner seeds are already concise retrieval intents.
    # Bypass an extra LLM decomposition round to reduce latency and parse instability.
    if is_fact_qa and planned_seed_questions:
        sub_questions = _enforce_sub_question_count(
            planned_seed_questions,
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

    planner_hint_lines: list[str] = []
    if selected_skill:
        planner_hint_lines.append(f"Selected skill: {selected_skill}")
    if planner_reason:
        planner_hint_lines.append(f"Planner reason: {planner_reason}")
    if planned_seed_questions:
        planner_hint_lines.append("Planner seed sub-questions:")
        planner_hint_lines.extend([f"- {item}" for item in planned_seed_questions])

    if is_fact_qa:
        prompt = "\n".join(
            [
                "You are a technical QA decomposition assistant.",
                f"Generate {min_sub_questions} to {max_sub_questions} concise, non-overlapping factual sub-questions.",
                "Focus on direct answerability from product documentation and official troubleshooting guidance.",
                "Do not ask for business strategy, recommendation framing, or generic requirement analysis.",
                (
                    f"Return only a JSON array (preferred length {min_sub_questions}-{max_sub_questions}) "
                    "or a JSON object containing the sub_questions field."
                ),
                "Do not output any other text.",
                "",
                "\n".join(planner_hint_lines) if planner_hint_lines else "",
            ]
        )
    else:
        prompt = "\n".join(
            [
                "You are a requirements analysis assistant for a software recommendation system.",
                f"Generate {min_sub_questions} to {max_sub_questions} concise and non-overlapping sub-questions.",
                (
                    "Focus only on the most important dimensions: core problem/users, "
                    "domain+stack constraints, and key non-functional/data requirements."
                ),
                (
                    f"Return only a JSON array (preferred length {min_sub_questions}-{max_sub_questions}) "
                    "or a JSON object containing the sub_questions field."
                ),
                "Do not output any other text.",
                "",
                "\n".join(planner_hint_lines) if planner_hint_lines else "",
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
        sub_questions = list(planned_seed_questions) if planned_seed_questions else [normalized_query]
        if " and " in normalized_query.lower():
            parts = re.split(r'\band\b', normalized_query, flags=re.IGNORECASE)
            if len(parts) > 1:
                sub_questions.extend([part.strip() for part in parts if part.strip()])
    sub_questions = _normalize_sub_questions(sub_questions)
    if not sub_questions:
        sub_questions = [normalized_query]
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


def _normalize_sub_questions(raw: Any) -> list[str]:
    cleaned: list[str] = []
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
            preferred: list[Any] = []
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
    sub_questions: list[str],
    normalized_query: str,
    *,
    min_count: int,
    max_count: int,
) -> list[str]:
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
def human_confirmation_node(state: AgentState) -> dict[str, Any]:
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
def retrieve_node(state: AgentState) -> dict[str, Any]:
    """Retrieve node: global merge -> dedup -> rerank -> top3 evidence."""
    BASE_MAX_EVIDENCE = 3
    retrieved_doc_ids_full_limit = max(1, int(getattr(settings, "RETRIEVED_DOC_IDS_FULL_LIMIT", 20)))
    sub_questions = _get_field(state, "sub_questions", [])
    retrieval_records = list(_get_field(state, "retrieval_records", []) or [])
    iteration_count = _get_field(state, "iteration_count", 0)
    session_id = str(_get_field(state, "session_id", "") or "").strip()
    selected_skill = str(_get_field(state, "selected_skill", "") or "").strip()
    max_evidence = BASE_MAX_EVIDENCE
    if selected_skill in {"quick_fact_qa", "generic_rag"}:
        max_evidence = max(BASE_MAX_EVIDENCE, int(getattr(settings, "QA_EVIDENCE_TOP_N", 5)))
    memory_context = list(_get_field(state, "memory_context", []) or [])
    query_image_candidates = list(
        _get_field(state, "query_image_candidates", []) or []
    )
    trace_id = new_trace_id("search")
    global_query = str(_get_field(state, "user_query", "") or "").strip()
    if not global_query:
        global_query = " ".join(
            str(item or "").strip()
            for item in sub_questions
            if str(item or "").strip()
        )
    all_docs: list[Document] = []

    log_event(
        logger,
        logging.INFO,
        "search.retrieve.start",
        component="search",
        trace_id=trace_id,
        scene="retrieve_node",
        iteration=iteration_count,
        sub_question_count=len(sub_questions),
        global_top_n=max_evidence,
        selected_skill=selected_skill,
        has_memory_context=bool(memory_context),
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
        search_results_future = retrieve_with_search(
            question,
            top_k=settings.TOP_K,
            session_id=session_id,
            selected_skill=selected_skill,
            memory_context=memory_context,
            query_image_candidates=query_image_candidates,
        )
        search_results = search_results_future.result()
        all_docs.extend(search_results)
        subquery_id = f"sq_{index}"
        retrieval_record = _build_retrieval_record(
            subquery_id=subquery_id,
            subquery=question,
            docs=search_results,
        )
        retrieval_record["selected_skill"] = selected_skill
        retrieval_record["skill_used"] = selected_skill
        rerank_mode = ""
        for candidate in search_results:
            metadata = _get_field(candidate, "metadata", {}) or {}
            rerank_features = _get_field(metadata, "rerank_features", {}) or {}
            mode = str(_get_field(rerank_features, "mode", "") or "").strip()
            if mode:
                rerank_mode = mode
                break
        retrieval_record["rerank_mode"] = rerank_mode or "unknown"
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
    unique_docs: list[Document] = []
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

    rerank_top_n = max(max_evidence, min(len(unique_docs), retrieved_doc_ids_full_limit))
    ranked_docs = rerank_documents(
        query=global_query,
        docs=unique_docs,
        top_n=rerank_top_n,
        selected_skill=selected_skill,
        session_id=session_id,
    )
    final_docs = ranked_docs[:max_evidence]
    retrieved_images = _build_retrieved_images(final_docs)
    quality_score = min(0.9, 0.5 + (len(final_docs) * 0.1))

    retrieved_doc_ids: list[str] = []
    retrieved_doc_ids_full: list[str] = []
    memory_doc_ids: list[str] = []
    for doc_index, doc in enumerate(ranked_docs, start=1):
        doc_id = _extract_doc_id(doc, fallback_prefix="ranked", index=doc_index)
        if doc_id not in retrieved_doc_ids_full:
            retrieved_doc_ids_full.append(doc_id)
        if len(retrieved_doc_ids_full) >= retrieved_doc_ids_full_limit:
            break

    evidence: list[dict[str, Any]] = []
    for doc_index, doc in enumerate(final_docs, start=1):
        doc_id = _extract_doc_id(doc, fallback_prefix="global", index=doc_index)
        if doc_id not in retrieved_doc_ids:
            retrieved_doc_ids.append(doc_id)
        metadata = _get_field(doc, "metadata", {}) or {}
        retrieval_source = str(_get_field(metadata, "retrieval_source", "") or "").strip().lower()
        channel = str(_get_field(metadata, "channel", "") or "").strip().lower()
        if (retrieval_source == "memory" or channel == "memory") and doc_id not in memory_doc_ids:
            memory_doc_ids.append(doc_id)
        search_result = {
            "content": doc.content,
            "score": doc.score,
            "doc_id": doc_id,
            "source": str(_get_field(metadata, "source", "") or ""),
            "retrieval_source": retrieval_source,
            "channel": channel,
            "modality": str(_get_field(metadata, "modality", "") or ""),
            "asset_path": str(_get_field(metadata, "asset_path", "") or ""),
            "asset_url": str(_get_field(metadata, "asset_url", "") or ""),
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
        total_retrieved_doc_ids_full=len(retrieved_doc_ids_full),
        memory_doc_ids=len(memory_doc_ids),
        selected_skill=selected_skill,
    )
    return {
        "evidence": evidence,
        "retrieval_records": retrieval_records,
        "retrieved_doc_ids": retrieved_doc_ids,
        "retrieved_doc_ids_full": retrieved_doc_ids_full,
        "retrieved_images": retrieved_images,
        "memory_doc_ids": memory_doc_ids,
    }


def evidence_collection_node(state: AgentState) -> dict[str, Any]:
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

def evidence_evaluation_node(state: AgentState) -> dict[str, Any]:
    """Score each evidence item for how well it supports the question.

    Feeds `coverage_check_node`, so the score has to mean something. Uses the
    LLM judge when `EVIDENCE_EVAL_USE_LLM` is on and falls back to the
    deterministic heuristic otherwise or on any judge failure.
    """
    evidence = list(_get_field(state, "evidence", []) or [])
    if not evidence:
        return {"evidence": evidence}

    question = str(_get_field(state, "user_query", "") or "").strip()
    if not question:
        question = str(_get_field(state, "normalized_query", "") or "").strip()

    scores, mode = evaluate_evidence(question, evidence)
    for item, score in zip(evidence, scores, strict=True):
        _set_field(item, "quality_score", score)

    log_event(
        logger,
        logging.INFO,
        "rag.evidence.evaluated",
        component="rag",
        mode=mode,
        items=len(evidence),
        min_score=round(min(scores), 4) if scores else 0.0,
        max_score=round(max(scores), 4) if scores else 0.0,
        mean_score=round(sum(scores) / len(scores), 4) if scores else 0.0,
    )

    return {
        "evidence": evidence
    }

def candidate_generation_node(state: AgentState) -> dict[str, Any]:
    """Generate candidate solutions from evidence and normalize to CandidateSolution."""
    evidence = list(_get_field(state, "evidence", []) or [])
    raw_candidates = list(_get_field(state, "candidates", []) or [])
    selected_skill = str(_get_field(state, "selected_skill", "") or "").strip()

    logger.info("candidate generation: evidence_count=%d", len(evidence))

    def _to_candidate_solution(raw: Any) -> CandidateSolution | None:
        if isinstance(raw, CandidateSolution):
            return raw
        if not isinstance(raw, dict):
            return None

        solution = str(raw.get("solution", "") or "").strip()
        if not solution:
            return None
        rationale = str(raw.get("rationale", "") or "").strip()
        pros_raw = raw.get("pros") if isinstance(raw.get("pros"), list) else []
        cons_raw = raw.get("cons") if isinstance(raw.get("cons"), list) else []
        pros = [str(item).strip() for item in pros_raw if str(item).strip()]
        cons = [str(item).strip() for item in cons_raw if str(item).strip()]
        try:
            score = float(raw.get("relevance_score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        score = max(0.0, min(1.0, score))

        return CandidateSolution(
            solution=solution,
            rationale=rationale,
            pros=pros,
            cons=cons,
            relevance_score=score,
        )

    candidates: list[CandidateSolution] = []
    for item in raw_candidates:
        parsed = _to_candidate_solution(item if isinstance(item, dict) else _get_field(item, "__dict__", {}))
        if parsed is not None:
            candidates.append(parsed)

    if not evidence:
        return {"candidates": candidates}

    def _truncate(content: str, max_len: int = 600) -> str:
        value = str(content or "")
        if len(value) <= max_len:
            return value
        return value[:max_len] + "..."

    evidence_payload = []
    for item in evidence:
        docs = _get_field(item, "documents", [])
        doc_summaries = []
        for doc in docs[:3]:
            metadata = _get_field(doc, "metadata", {}) or {}
            doc_summaries.append(
                {
                    "source": _get_field(metadata, "source", ""),
                    "content": _truncate(_get_field(doc, "content", ""), 600),
                    "score": _get_field(doc, "score", 0.0),
                }
            )
        evidence_payload.append(
            {
                "question": _get_field(item, "question", ""),
                "quality_score": _get_field(item, "quality_score", 0.0),
                "documents": doc_summaries,
            }
        )

    llm_candidates: list[CandidateSolution] = []
    should_skip_llm = selected_skill == "quick_fact_qa"
    if not should_skip_llm:
        try:
            trace_id, started_at = _llm_invoke_start(
                scene="candidate_generation",
                model=settings.LLM_MODEL,
                request_hint=f"evidence_items={len(evidence_payload)}",
            )
            response_future = candidate_generation_with_llm(
                model=settings.LLM_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a software recommendation assistant. "
                            "Generate candidate solutions based on evidence. "
                            "Return JSON array or object{candidates:[...]}. Each candidate includes: "
                            "solution, rationale, pros, cons, relevance_score(0-1)."
                        ),
                    },
                    {"role": "user", "content": json.dumps(evidence_payload, ensure_ascii=False)},
                ],
                temperature=0.4,
            )
            response = response_future.result()
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
                raise ValueError("candidate_generation_non_list")

            for raw in parsed:
                item = _to_candidate_solution(raw)
                if item is not None:
                    llm_candidates.append(item)
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
            if not docs:
                continue
            first_doc = docs[0]
            solution_text = str(_get_field(first_doc, "content", "") or "").strip()
            if len(solution_text) > 240:
                solution_text = solution_text[:240] + "..."

            score = _get_field(item, "quality_score", 0.5)
            try:
                score = float(score)
            except (TypeError, ValueError):
                score = 0.5
            score = max(0.0, min(1.0, score))
            source = _get_field(_get_field(first_doc, "metadata", {}) or {}, "source", "unknown")

            candidates.append(
                CandidateSolution(
                    solution=solution_text or "No concise solution text from evidence.",
                    rationale=f"Based on evidence source {source}",
                    pros=["Relevant to question", "Grounded on retrieved evidence"],
                    cons=[] if score >= 0.75 else ["Evidence may be incomplete"],
                    relevance_score=score,
                )
            )

    # Deduplicate by solution while keeping best score.
    best_by_solution: dict[str, CandidateSolution] = {}
    for item in candidates:
        key = item.solution.strip()
        if not key:
            continue
        existing = best_by_solution.get(key)
        if existing is None or item.relevance_score > existing.relevance_score:
            best_by_solution[key] = item

    normalized_candidates = sorted(
        best_by_solution.values(),
        key=lambda x: float(x.relevance_score),
        reverse=True,
    )

    return {"candidates": normalized_candidates}

def coverage_check_node(state: AgentState) -> dict[str, Any]:
    """Gate for the retrieval refinement loop.

    A sub-question counts as covered only when its evidence carries documents
    *and* clears ``QUALITY_THRESHOLD``. Counting merely non-empty recall would
    let a single low-quality hit satisfy the gate and terminate the loop.

    Refinement stops as soon as an iteration fails to improve coverage: the loop
    re-runs retrieval over the same sub-questions, so a non-improving pass cannot
    become productive later and would only burn latency and tokens.
    """
    sub_questions = _get_field(state, "sub_questions", [])
    evidence = _get_field(state, "evidence", [])
    coverage_threshold = _safe_float(_get_field(state, "coverage_threshold", 0.8), 0.8)
    max_iterations = int(_safe_float(_get_field(state, "max_iterations", 3), 3))
    iteration_count = int(_safe_float(_get_field(state, "iteration_count", 0), 0))
    previous_coverage = _safe_float(_get_field(state, "previous_coverage", 0.0), 0.0)
    quality_threshold = _safe_float(getattr(settings, "QUALITY_THRESHOLD", 0.6), 0.6)

    total_questions = len(sub_questions)
    covered_questions = 0
    for item in evidence:
        if not _get_field(item, "documents", []):
            continue
        if _safe_float(_get_field(item, "quality_score", 0.0), 0.0) < quality_threshold:
            continue
        covered_questions += 1
    covered_questions = min(covered_questions, total_questions)

    coverage = covered_questions / total_questions if total_questions > 0 else 0.0

    below_threshold = coverage < coverage_threshold
    has_iterations_left = iteration_count < max_iterations
    made_progress = iteration_count <= 1 or coverage > previous_coverage
    needs_refinement = below_threshold and has_iterations_left and made_progress

    log_event(
        logger,
        logging.INFO,
        "rag.coverage.check",
        component="rag",
        coverage=round(coverage, 4),
        previous_coverage=round(previous_coverage, 4),
        coverage_threshold=coverage_threshold,
        quality_threshold=quality_threshold,
        covered_questions=covered_questions,
        total_questions=total_questions,
        iteration=iteration_count,
        max_iterations=max_iterations,
        needs_refinement=needs_refinement,
        stop_reason=(
            ""
            if needs_refinement
            else (
                "coverage_met"
                if not below_threshold
                else "max_iterations" if not has_iterations_left else "no_progress"
            )
        ),
    )

    return {
        "coverage": coverage,
        "previous_coverage": coverage,
        "needs_refinement": needs_refinement,
    }

@traceable(name="final_generation_rag")
def answer_generation_node(state: AgentState) -> dict[str, Any]:
    """Answer generation node (RAG mode)."""
    user_query = str(_get_field(state, "user_query", "") or "")
    candidates = list(_get_field(state, "candidates", []) or [])
    evidence = list(_get_field(state, "evidence", []) or [])
    retrieval_records = list(_get_field(state, "retrieval_records", []) or [])
    selected_skill = str(_get_field(state, "selected_skill", "") or "").strip()

    logger.warning(
        "ANSWER_GENERATION_ENTER candidate_count=%d evidence_count=%d selected_skill=%s",
        len(candidates),
        len(evidence),
        selected_skill,
    )

    def _qa_evidence_context(
        evidence_items: list[dict[str, Any]],
        records: list[dict[str, Any]],
        *,
        max_docs: int = 8,
        max_chars: int = 520,
    ) -> str:
        blocks: list[str] = []
        seen_keys: set[str] = set()

        def _append_block(header: str, content: str) -> None:
            normalized = str(content or "").strip().replace("\n", " ")
            if not normalized:
                return
            if len(normalized) > max_chars:
                normalized = normalized[:max_chars] + "..."
            dedup_key = f"{header}|{normalized[:120]}"
            if dedup_key in seen_keys:
                return
            seen_keys.add(dedup_key)
            blocks.append(f"[{len(blocks) + 1}] {header}\n{normalized}")

        for entry in evidence_items:
            docs = _get_field(entry, "documents", []) or []
            for index, doc in enumerate(docs, start=1):
                if len(blocks) >= max_docs:
                    break
                metadata = _get_field(doc, "metadata", {}) or {}
                source = str(_get_field(metadata, "source", "") or "").strip() or "unknown"
                doc_id = _extract_doc_id(doc, fallback_prefix="evidence", index=index)
                score = _get_field(doc, "score", 0.0)
                try:
                    score_text = f"{float(score):.4f}"
                except Exception:
                    score_text = str(score)
                _append_block(
                    header=f"doc_id={doc_id} source={source} score={score_text}",
                    content=str(_get_field(doc, "content", "") or ""),
                )
            if len(blocks) >= max_docs:
                break

        if len(blocks) < max_docs:
            for record in records:
                subquery = str(_get_field(record, "subquery", "") or "").strip()
                snippets = _get_field(record, "retrieved_contexts", []) or []
                for snippet in snippets:
                    if len(blocks) >= max_docs:
                        break
                    header = f"subquery={subquery}" if subquery else "subquery=unknown"
                    _append_block(header=header, content=str(snippet or ""))
                if len(blocks) >= max_docs:
                    break

        return "\n\n".join(blocks).strip()

    final_answer = ""
    if selected_skill in {"quick_fact_qa", "generic_rag"}:
        context = _qa_evidence_context(evidence, retrieval_records)
        if context:
            try:
                final_answer = _stream_chat_completion_text(
                    scene="qa_answer_generation",
                    model=settings.LLM_MODEL,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a technical support QA assistant. "
                                "Use only the provided evidence. "
                                "Provide a direct, concrete answer in 2-6 sentences. "
                                "If steps are required, provide short numbered steps. "
                                "Include concrete product names, versions, commands, or settings when present. "
                                "Avoid generic recommendation language and avoid speculative claims."
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"Question:\n{user_query}\n\nEvidence:\n{context}",
                        },
                    ],
                    temperature=0.0,
                    stream_node="rag_answer_generation",
                )
            except Exception:
                final_answer = ""
        if not final_answer and context:
            first_block = context.split("\n\n")[0] if context else ""
            final_answer = f"Based on retrieved evidence, the likely answer is:\n{first_block}"

    if not final_answer:
        if candidates:
            best_candidate = candidates[0]
            final_answer = f"Answer to '{user_query}':\n\n"
            final_answer += f"Solution: {_get_field(best_candidate, 'solution', '')}\n"
            final_answer += f"Reasoning: {_get_field(best_candidate, 'rationale', '')}\n"
            pros = _get_field(best_candidate, "pros", [])
            cons = _get_field(best_candidate, "cons", [])
            final_answer += f"Pros: {', '.join(pros) if pros else 'N/A'}\n"
            if cons:
                final_answer += f"Cons: {', '.join(cons)}\n"
        else:
            final_answer = f"Insufficient evidence to answer '{user_query}' confidently."

    messages = list(_get_field(state, "messages", []) or [])
    messages.append({"role": "assistant", "content": final_answer})

    return {
        "final_answer": final_answer,
        "messages": messages,
        "retrieval_records": list(_get_field(state, "retrieval_records", []) or []),
        "retrieved_doc_ids": list(_get_field(state, "retrieved_doc_ids", []) or []),
        "retrieved_doc_ids_full": list(_get_field(state, "retrieved_doc_ids_full", []) or []),
        "retrieved_images": list(_get_field(state, "retrieved_images", []) or []),
        "hitl": dict(_get_field(state, "hitl", {}) or {}),
    }

@traceable(name="final_generation_chat")
def chat_answer_generation_node(state: AgentState) -> dict[str, Any]:
    """Answer generation node for direct/chat branch (retrieval optional)."""
    user_query = _get_field(state, "user_query", "")
    retrieval_query = _extract_current_user_query(user_query) or user_query
    mode = str(_get_field(state, "mode", "") or "").strip().lower()
    session_id = str(_get_field(state, "session_id", "") or "").strip()
    selected_skill = str(_get_field(state, "selected_skill", "") or "").strip()
    memory_context = list(_get_field(state, "memory_context", []) or [])
    query_image_candidates = list(
        _get_field(state, "query_image_candidates", []) or []
    )

    messages = list(_get_field(state, "messages", []) or [])
    if not messages:
        messages = [{"role": "user", "content": user_query}]

    retrieval_records = list(_get_field(state, "retrieval_records", []) or [])
    retrieved_doc_ids = list(_get_field(state, "retrieved_doc_ids", []) or [])
    retrieved_doc_ids_full = list(_get_field(state, "retrieved_doc_ids_full", []) or [])
    retrieved_images = list(_get_field(state, "retrieved_images", []) or [])
    retrieved_docs: list[Document] = []
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
            retrieved_docs_future = retrieve_with_search(
                retrieval_query,
                top_k=settings.TOP_K,
                session_id=session_id,
                selected_skill=selected_skill,
                memory_context=memory_context,
                query_image_candidates=query_image_candidates,
            )
            retrieved_docs = retrieved_docs_future.result()
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
        chat_record["selected_skill"] = selected_skill
        retrieval_records.append(chat_record)
        retrieved_doc_ids = _merge_doc_ids(
            retrieved_doc_ids,
            chat_record.get("retrieved_doc_ids", []),
        )
        retrieved_doc_ids_full = _merge_doc_ids(
            retrieved_doc_ids_full,
            chat_record.get("retrieved_doc_ids", []),
        )
        retrieved_images = _merge_retrieved_images(
            retrieved_images,
            chat_record.get("retrieved_images", []),
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

    def _format_retrieved_context(docs: list[Document]) -> str:
        lines: list[str] = []
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
        final_answer = _stream_chat_completion_text(
            scene="chat_answer_generation",
            model=settings.LLM_MODEL,
            messages=prepared_messages,
            temperature=0.7,
            max_tokens=max_output_tokens,
            stream_node="chat_answer_generation",
        )

        if not final_answer:
            final_answer = "抱歉，暂时无法获取LLM回复，请稍后再试。"
    except Exception:
        final_answer = "抱歉，暂时无法获取LLM回复，请稍后再试。"

    messages.append({"role": "assistant", "content": final_answer})

    return {
        "final_answer": final_answer,
        "messages": messages,
        "retrieval_records": retrieval_records,
        "retrieved_doc_ids": retrieved_doc_ids,
        "retrieved_doc_ids_full": retrieved_doc_ids_full,
        "retrieved_images": retrieved_images,
        "hitl": dict(_get_field(state, "hitl", {}) or {}),
    }

def pre_drawing_node(state: AgentState) -> dict[str, Any]:
    """画图前处理节点"""
    user_query = _get_field(state, "user_query", "")
    structured_params = pre_drawing_tool(user_query)
    return {"drawing_params": structured_params}

def draw_image_node(state: AgentState) -> dict[str, Any]:
    """画图执行节点"""
    structured_params = _get_field(state, "drawing_params", "")
    image_url_future = draw_image_with_tool(structured_params)
    image_url = image_url_future.result()
    final_answer = f"图像已生成: {image_url}"
    return {"image_result": image_url, "final_answer": final_answer}
