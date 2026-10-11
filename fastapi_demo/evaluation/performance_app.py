"""Uvicorn factory for isolated evaluation; never imported by the normal app."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


def create_app():
    runtime_value = os.environ.get("PERFORMANCE_RUNTIME_DIR")
    if not runtime_value:
        raise RuntimeError("Evaluation server requires an isolated PERFORMANCE_RUNTIME_DIR")
    runtime = Path(runtime_value)
    runtime.mkdir(parents=True, exist_ok=True)
    offline = os.environ.get("PERFORMANCE_OFFLINE") == "1"
    if offline:
        from .offline_support import install_adapters

        install_adapters()
    from app.core.config import settings
    from software_recommend_system.ingestion.embedder import embed_texts
    from software_recommend_system.ingestion.indexer import index_embeddings

    started = time.perf_counter()
    manifest = json.loads(Path(os.environ["PERFORMANCE_MANIFEST"]).read_text(encoding="utf-8"))
    corpus = manifest["corpus"]
    vectors = embed_texts([doc["content"] for doc in corpus])
    if len(vectors) != len(corpus) or any(not vector for vector in vectors):
        raise RuntimeError("Evaluation corpus embedding failed")
    index_embeddings(
        [doc["id"] for doc in corpus],
        [doc["content"] for doc in corpus],
        [{"source": "performance", "doc_id": doc["id"], "source_doc_id": doc["id"]} for doc in corpus],
        vectors,
        sync_keyword=False,
    )
    if not offline and settings.ROUTER_ENABLE:
        from software_recommend_system.router import route_query

        decision = route_query("Hello, say hello in one sentence.")
        if decision.fallback_used:
            raise RuntimeError("Configured router is unavailable or returned a fallback")
    (runtime / "setup.json").write_text(
        json.dumps(
            {
                "setup_ms": (time.perf_counter() - started) * 1000,
                "corpus_count": len(corpus),
                "offline": offline,
            }
        ),
        encoding="utf-8",
    )

    # The fixed corpus is already indexed. Do not scan the user's ingest folder.
    from app.core import startup

    startup.run_startup_ingestion_if_needed = lambda: None
    from app.main import app

    return app
