"""Immutable KB revisions and atomically published incremental manifests.

A row's snapshot_id records its creation, not membership. Published manifests
select one immutable version per logical document, sharing unchanged rows.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import settings

LEGACY = "legacy"
VERSION_FIELDS = ("document_id", "version", "snapshot_id", "status", "chunk_id")
_CURRENT: contextvars.ContextVar[str | None] = contextvars.ContextVar("kb_snapshot", default=None)
logger = logging.getLogger(__name__)


class SnapshotUnavailable(ValueError):
    pass


class PublicationConflict(RuntimeError):
    pass


class ManifestStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            with self.connection() as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if {"snapshots", "members", "artifacts", "control"}.issubset(tables):
                    initialized = db.execute(
                        "SELECT COUNT(*) FROM control WHERE key IN ('active','adopted')"
                    ).fetchone()[0]
                    if initialized == 2:
                        return
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    id TEXT PRIMARY KEY, base_id TEXT, state TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS members (
                    snapshot_id TEXT NOT NULL, document_id TEXT NOT NULL, version TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id, document_id));
                CREATE TABLE IF NOT EXISTS artifacts (
                    snapshot_id TEXT NOT NULL, document_id TEXT NOT NULL, kind TEXT NOT NULL,
                    ready INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(snapshot_id,document_id,kind));
                CREATE TABLE IF NOT EXISTS control (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO snapshots VALUES (?,NULL,'published',?)", (LEGACY, time.time()))
            db.execute("INSERT OR IGNORE INTO control VALUES ('active',?)", (LEGACY,))
            db.execute("INSERT OR IGNORE INTO control VALUES ('adopted','0')")

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=60)
        try:
            db.execute("PRAGMA busy_timeout=60000")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def active(self) -> str:
        with self.connection() as db:
            return str(db.execute("SELECT value FROM control WHERE key='active'").fetchone()[0])

    def members(self, snapshot_id: str) -> dict[str, str] | None:
        with self.connection() as db:
            row = db.execute("SELECT state FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
            if row is None or row[0] != "published":
                raise SnapshotUnavailable(f"KB snapshot is not published: {snapshot_id}")
            adopted = db.execute("SELECT value FROM control WHERE key='adopted'").fetchone()[0]
            if snapshot_id == LEGACY and adopted == "0":
                return None
            return dict(db.execute("SELECT document_id,version FROM members WHERE snapshot_id=?", (snapshot_id,)))

    def adopt_legacy(self, importer: Callable[[], dict[str, str]]) -> None:
        """Serialize the one-time metadata adoption before any versioned writes.

        Failed adoption leaves the pointer at legacy; partially decorated legacy
        rows remain readable. Retrying adoption is idempotent.
        """
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT value FROM control WHERE key='adopted'").fetchone()[0] == "1":
                return
            members = importer()
            db.executemany(
                "INSERT OR REPLACE INTO members VALUES (?,?,?)",
                [(LEGACY, document, version) for document, version in members.items()],
            )
            db.execute("UPDATE control SET value='1' WHERE key='adopted'")

    def stage(
        self, updates: dict[str, str], required: dict[str, set[str]] | None = None, *, expected_base: str | None = None
    ) -> str:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT value FROM control WHERE key='adopted'").fetchone()[0] != "1":
                raise RuntimeError("Adopt existing KB rows before staging a publication")
            base = str(db.execute("SELECT value FROM control WHERE key='active'").fetchone()[0])
            if expected_base is not None and base != expected_base:
                raise PublicationConflict("KB changed while preparing revisions; retry publication")
            snapshot = uuid4().hex
            db.execute("INSERT INTO snapshots VALUES (?,?,'staged',?)", (snapshot, base, time.time()))
            db.execute(
                "INSERT INTO members SELECT ?,document_id,version FROM members WHERE snapshot_id=?", (snapshot, base)
            )
            db.executemany(
                "INSERT OR REPLACE INTO members VALUES (?,?,?)",
                [(snapshot, document, version) for document, version in updates.items()],
            )
            db.executemany(
                "INSERT INTO artifacts VALUES (?,?,?,0)",
                [(snapshot, doc, kind) for doc, kinds in (required or {}).items() for kind in kinds],
            )
            return snapshot

    def mark_ready(self, snapshot: str, document_id: str, kind: str) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE artifacts SET ready=1 WHERE snapshot_id=? AND document_id=? AND kind=?",
                (snapshot, document_id, kind),
            )

    def fail(self, snapshot: str) -> None:
        with self.connection() as db:
            db.execute("UPDATE snapshots SET state='failed' WHERE id=? AND state='staged'", (snapshot,))

    def publish(self, snapshot: str) -> str:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT base_id,state FROM snapshots WHERE id=?", (snapshot,)).fetchone()
            if row is None or row[1] != "staged":
                raise SnapshotUnavailable("Only staged snapshots can be published")
            if db.execute("SELECT 1 FROM artifacts WHERE snapshot_id=? AND ready=0 LIMIT 1", (snapshot,)).fetchone():
                raise RuntimeError("Required KB artifacts are not ready")
            active = db.execute("SELECT value FROM control WHERE key='active'").fetchone()[0]
            if active != row[0]:
                raise PublicationConflict("KB changed while staging; retry against the newly published snapshot")
            db.execute("UPDATE snapshots SET state='published' WHERE id=?", (snapshot,))
            db.execute("UPDATE control SET value=? WHERE key='active'", (snapshot,))
        logger.info("kb.snapshot.published snapshot_id=%s base_snapshot_id=%s", snapshot, row[0])
        return snapshot

    def rollback(self, snapshot: str) -> str:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM snapshots WHERE id=?", (snapshot,)).fetchone()
            if row is None or row[0] != "published":
                raise SnapshotUnavailable("Rollback target must be a published KB snapshot")
            db.execute("UPDATE control SET value=? WHERE key='active'", (snapshot,))
        return snapshot


def manifest_path() -> Path:
    configured = str(getattr(settings, "KB_MANIFEST_PATH", "") or "").strip()
    return Path(configured) if configured else Path(settings.CHROMA_DB_PATH) / "kb_manifest.sqlite3"


def get_store() -> ManifestStore:
    return ManifestStore(manifest_path())


def capture_snapshot() -> str:
    return get_store().active() if manifest_path().exists() else LEGACY


def current_snapshot_id() -> str | None:
    return _CURRENT.get()


def _members(snapshot: str) -> dict[str, str] | None:
    if not manifest_path().exists():
        if snapshot != LEGACY:
            raise SnapshotUnavailable(f"Unknown KB snapshot: {snapshot}")
        return None
    return get_store().members(snapshot)


def _selected(snapshot_id: str | None = None) -> str:
    snapshot = snapshot_id or current_snapshot_id()
    if snapshot:
        return snapshot
    # Compatibility for offline callers before the first publication. Production
    # request entrypoints must pin; silently following latest per channel is unsafe.
    if capture_snapshot() != LEGACY:
        raise SnapshotUnavailable("A published KB requires a request snapshot pin")
    return LEGACY


@contextmanager
def use_snapshot(snapshot_id: str) -> Iterator[str]:
    _members(snapshot_id)  # Validate before any operation; no fallback to latest.
    token = _CURRENT.set(snapshot_id)
    try:
        yield snapshot_id
    finally:
        _CURRENT.reset(token)


def rollback_snapshot(snapshot_id: str) -> str:
    return get_store().rollback(snapshot_id)


def document_identity(metadata: dict[str, Any], fallback: str = "") -> str:
    return str(
        metadata.get("document_id")
        or metadata.get("source_doc_id")
        or metadata.get("doc_id")
        or metadata.get("path")
        or fallback
    )


def visible(metadata: dict[str, Any], snapshot_id: str | None = None) -> bool:
    members = _members(_selected(snapshot_id))
    version = str(metadata.get("version") or "")
    if members is None:
        # During first adoption only original rows are eligible, never staged rows.
        return not version or version.startswith("legacy-")
    document = document_identity(metadata)
    return bool(document and members.get(document) == version and metadata.get("status") == "ready")


def chroma_filter(snapshot_id: str | None = None) -> dict[str, Any] | None:
    members = _members(_selected(snapshot_id))
    if members is None:
        return None
    terms = [
        {"$and": [{"document_id": {"$eq": doc}}, {"version": {"$eq": version}}]}
        for doc, version in sorted(members.items())
    ]
    membership = (
        terms[0] if len(terms) == 1 else {"$or": terms} if terms else {"document_id": {"$eq": "__empty_manifest__"}}
    )
    return {"$and": [{"status": {"$eq": "ready"}}, membership]}


def keyword_filter(snapshot_id: str | None = None) -> dict[str, Any] | None:
    members = _members(_selected(snapshot_id))
    if members is None:
        return None
    if not members:
        return {"match_none": {}}
    return {
        "bool": {
            "filter": [{"term": {"status": "ready"}}],
            "minimum_should_match": 1,
            "should": [
                {"bool": {"filter": [{"term": {"document_id": doc}}, {"term": {"version": version}}]}}
                for doc, version in sorted(members.items())
            ],
        }
    }


def revision(document: dict[str, Any], policy: dict[str, Any]) -> str:
    metadata = {
        key: value
        for key, value in dict(document.get("metadata") or {}).items()
        if key not in {*VERSION_FIELDS, "asset_path", "asset_url"}
    }
    payload = {
        "id": str(document["id"]),
        "content": document.get("content", ""),
        "blocks": document.get("blocks"),
        "metadata": metadata,
        "policy": policy,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def versioned_document(document: dict[str, Any], version: str, snapshot: str) -> dict[str, Any]:
    logical = str(document["id"])
    namespace = hashlib.sha256(logical.encode("utf-8")).hexdigest()
    return {
        **document,
        "id": f"kb:{namespace}:{version}",
        "metadata": {
            **dict(document.get("metadata") or {}),
            "source_doc_id": logical,
            "document_id": logical,
            "version": version,
            "snapshot_id": snapshot,
            "status": "ready",
        },
    }


def preserve_asset(data: bytes, filename: str, document_id: str, version: str, root: Path) -> str:
    """Store immutable bytes beneath the existing safe asset-serving root."""
    root = root.expanduser().resolve()
    asset_key = hashlib.sha256(f"{document_id}\0{version}".encode()).hexdigest()
    # Keep immutable asset paths short enough for default Windows filesystem limits.
    path = root / ".versions" / asset_key[:2] / f"{asset_key}{Path(filename).suffix.lower()}"
    if not path.resolve().is_relative_to(root):
        raise ValueError("Versioned asset escaped ingest root")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # Publish the complete file atomically without replacing another writer's revision.
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError("Immutable asset revision collision") from None
    finally:
        temporary.unlink(missing_ok=True)
    return path.relative_to(root).as_posix()
