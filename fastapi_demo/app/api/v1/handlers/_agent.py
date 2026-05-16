"""Single shared LangGraph agent instance used by all v1 route handlers.

NOTE: create_rag_with_routing_agent() runs at import time. The caller
(main.py) must call configure_runtime_file_logging() before importing
this module so agent-construction logs are persisted.
"""

from __future__ import annotations

from software_recommend_system.rag_agent import create_rag_with_routing_agent

AGENT = create_rag_with_routing_agent()
