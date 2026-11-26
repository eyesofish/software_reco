"""Store independent session snapshots with atomic read/modify/write transactions."""

import json
import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)
Table = Literal["agent_session_state", "agent_layered_memory"]
State = dict[str, Any]


def _unique_keys(pairs: list[tuple[str, Any]]) -> State:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate JSON key")
    return result


class SessionRows:
    def __init__(self, path: Path, legacy: Path, table: Table, normalize: Callable[[str, State], State]) -> None:
        if path.resolve() == legacy.resolve():
            raise ValueError("The SQLite database and legacy JSON must have different paths")
        self.path = path
        self.table = table
        self.normalize = normalize
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            db.execute(f"CREATE TABLE IF NOT EXISTS {table}(session_id TEXT PRIMARY KEY, state_json TEXT NOT NULL)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS session_imports(store TEXT PRIMARY KEY, session_count INTEGER NOT NULL)"
            )
            if db.execute("SELECT 1 FROM session_imports WHERE store=?", (table,)).fetchone():
                return
            count = self._import_legacy(db, legacy)
            db.execute("INSERT INTO session_imports VALUES (?, ?)", (table, count))
        logger.info("Session store ready store=%s imported_sessions=%d", table, count)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def _import_legacy(self, db: sqlite3.Connection, legacy: Path) -> int:
        if not legacy.exists():
            return 0
        try:
            raw = json.loads(legacy.read_text(encoding="utf-8"), object_pairs_hook=_unique_keys)
            if not isinstance(raw, dict):
                raise ValueError("expected a JSON object")
            for session_id, state in raw.items():
                if not session_id.strip() or not isinstance(state, dict):
                    raise ValueError("invalid session entry")
                for field in ("facts", "messages", "episodes", "semantics"):
                    expected = dict if field == "facts" else list
                    if field in state and not isinstance(state[field], expected):
                        raise ValueError("invalid session data")
                sid = session_id.strip()
                normalized = json.dumps(self.normalize(sid, state), ensure_ascii=False)
                db.execute(f"INSERT INTO {self.table} VALUES (?, ?)", (sid, normalized))
            imported = int(db.execute(f"SELECT COUNT(*) FROM {self.table}").fetchone()[0])
            if imported != len(raw):
                raise ValueError("session count mismatch")
        except (OSError, ValueError, sqlite3.IntegrityError) as exc:
            raise RuntimeError(f"Session migration failed for {legacy} ({type(exc).__name__})") from exc
        return imported

    def read(self, session_id: str) -> State | None:
        with closing(self._connect()) as db:
            row = db.execute(f"SELECT state_json FROM {self.table} WHERE session_id=?", (session_id,)).fetchone()
        return self._decode(row[0]) if row else None

    @staticmethod
    def _decode(encoded: str) -> State:
        state = json.loads(encoded)
        if not isinstance(state, dict):
            raise ValueError("Invalid stored session")
        return state

    @contextmanager
    def edit(self, session_id: str) -> Iterator[State]:
        sid = session_id.strip()
        if not sid:
            raise ValueError("session_id is required")
        with closing(self._connect()) as db, db:
            # Acquire the write reservation before reading, including across worker processes.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(f"SELECT state_json FROM {self.table} WHERE session_id=?", (sid,)).fetchone()
            state = self._decode(row[0]) if row else self.normalize(sid, {})
            yield state
            encoded = json.dumps(state, ensure_ascii=False)
            if row is None:
                db.execute(f"INSERT INTO {self.table} VALUES (?, ?)", (sid, encoded))
            elif row[0] != encoded:
                db.execute(f"UPDATE {self.table} SET state_json=? WHERE session_id=?", (encoded, sid))

    def delete(self, session_id: str) -> None:
        with closing(self._connect()) as db, db:
            db.execute(f"DELETE FROM {self.table} WHERE session_id=?", (session_id,))


@lru_cache(maxsize=8)
def open_session_rows(
    legacy: Path, database: str | None, table: Table, normalize: Callable[[str, State], State]
) -> SessionRows:
    path = Path(database) if database else legacy.with_suffix(".sqlite3")
    if not database and path == legacy:
        path = legacy.with_name(legacy.name + ".sqlite3")
    return SessionRows(path, legacy, table, normalize)
