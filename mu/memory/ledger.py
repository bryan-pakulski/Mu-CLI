"""Local-first SQLite event ledger for durable cross-session memory."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Sequence

from .models import (
    EGRESS_POLICIES,
    LIFECYCLES,
    MEMORY_KINDS,
    SCOPE_TYPES,
    MemoryItem,
    RecallReceipt,
    uuid7,
)

SCHEMA_VERSION = 1
_TOKEN_RE = re.compile(r"[\w./:@+-]{2,}", re.UNICODE)


class MemoryConflictError(RuntimeError):
    """Raised when an optimistic version check fails."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: Any, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _normalise_statement(value: str) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _content_hash(value: str) -> str:
    return hashlib.sha256(_normalise_statement(value).encode("utf-8")).hexdigest()


def _etag(memory_id: str, version: int, content_hash: str, lifecycle: str) -> str:
    raw = f"{memory_id}:{version}:{content_hash}:{lifecycle}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _merge_json_rows(left: Iterable[Any], right: Iterable[Any]) -> List[Any]:
    result: List[Any] = []
    seen: set[str] = set()
    for value in [*list(left or []), *list(right or [])]:
        marker = _json(value)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(value)
    return result


class SQLiteMemoryLedger:
    """Transactional durable-memory repository.

    Connections are intentionally short-lived.  WAL mode and a busy timeout
    make the ledger safe for simultaneous TUI, web-daemon and mobile API use
    without sharing sqlite connection objects between threads.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = str(Path(path).expanduser().resolve())
        self._schema_lock = threading.RLock()
        self._schema_ready = False
        self._fts_enabled = True

    def _prepare_path(self) -> None:
        parent = Path(self.path).parent
        parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(parent, 0o700)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        self._prepare_path()
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        if not self._schema_ready:
            self._ensure_schema(connection)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return connection

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        with self._schema_lock:
            if self._schema_ready:
                return
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS memory_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    statement TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    scope_type TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    scope_label TEXT NOT NULL DEFAULT '',
                    lifecycle TEXT NOT NULL DEFAULT 'active',
                    pinned INTEGER NOT NULL DEFAULT 0,
                    trust_origin TEXT NOT NULL DEFAULT 'model',
                    verification TEXT NOT NULL DEFAULT 'unverified',
                    confidence REAL NOT NULL DEFAULT 0.7,
                    sensitivity TEXT NOT NULL DEFAULT 'normal',
                    egress_policy TEXT NOT NULL DEFAULT 'any',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    source_refs_json TEXT NOT NULL DEFAULT '[]',
                    relations_json TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_recalled_at REAL,
                    recall_count INTEGER NOT NULL DEFAULT 0,
                    content_hash TEXT NOT NULL,
                    etag TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_memories_scope
                    ON memories(scope_type, scope_key, lifecycle, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_memories_hash
                    ON memories(scope_type, scope_key, content_hash);
                CREATE INDEX IF NOT EXISTS idx_memories_kind
                    ON memories(kind, lifecycle, updated_at DESC);

                CREATE TABLE IF NOT EXISTS memory_revisions (
                    memory_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    statement TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    PRIMARY KEY(memory_id, version),
                    FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS memory_events (
                    event_id TEXT PRIMARY KEY,
                    memory_id TEXT,
                    version INTEGER NOT NULL DEFAULT 0,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    device_id TEXT NOT NULL DEFAULT 'local',
                    before_hash TEXT NOT NULL DEFAULT '',
                    after_json TEXT NOT NULL DEFAULT '{}',
                    reason TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_events_item
                    ON memory_events(memory_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS memory_recall_receipts (
                    id TEXT PRIMARY KEY,
                    session_name TEXT NOT NULL DEFAULT '',
                    query_text TEXT NOT NULL,
                    scopes_json TEXT NOT NULL,
                    budget_tokens INTEGER NOT NULL,
                    token_count INTEGER NOT NULL,
                    included_json TEXT NOT NULL,
                    excluded_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_receipts_session
                    ON memory_recall_receipts(session_name, created_at DESC);

                CREATE TABLE IF NOT EXISTS memory_sync_tombstones (
                    memory_id TEXT PRIMARY KEY,
                    content_hash TEXT NOT NULL,
                    forgotten_at REAL NOT NULL
                );
                """)
            try:
                connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts "
                    "USING fts5(id UNINDEXED, statement, tags, tokenize='porter unicode61')"
                )
            except sqlite3.OperationalError:
                self._fts_enabled = False
            connection.execute(
                "INSERT INTO memory_meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )
            connection.commit()
            self._schema_ready = True

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _row_to_item(row: sqlite3.Row | None) -> MemoryItem | None:
        if row is None:
            return None
        return MemoryItem(
            id=str(row["id"]),
            version=int(row["version"]),
            statement=str(row["statement"] or ""),
            kind=str(row["kind"] or "observation"),
            scope_type=str(row["scope_type"]),
            scope_key=str(row["scope_key"]),
            scope_label=str(row["scope_label"] or ""),
            lifecycle=str(row["lifecycle"] or "active"),
            pinned=bool(row["pinned"]),
            trust_origin=str(row["trust_origin"] or "model"),
            verification=str(row["verification"] or "unverified"),
            confidence=float(row["confidence"] or 0.0),
            sensitivity=str(row["sensitivity"] or "normal"),
            egress_policy=str(row["egress_policy"] or "any"),
            tags=list(_loads(row["tags_json"], [])),
            source_refs=list(_loads(row["source_refs_json"], [])),
            relations=list(_loads(row["relations_json"], [])),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            last_recalled_at=(
                float(row["last_recalled_at"])
                if row["last_recalled_at"] is not None
                else None
            ),
            recall_count=int(row["recall_count"] or 0),
            content_hash=str(row["content_hash"] or ""),
            etag=str(row["etag"] or ""),
            metadata=dict(_loads(row["metadata_json"], {})),
        )

    def _sync_fts(self, connection: sqlite3.Connection, item: MemoryItem) -> None:
        if not self._fts_enabled:
            return
        connection.execute("DELETE FROM memory_fts WHERE id = ?", (item.id,))
        if item.lifecycle != "forgotten" and item.statement:
            connection.execute(
                "INSERT INTO memory_fts(id, statement, tags) VALUES (?, ?, ?)",
                (item.id, item.statement, " ".join(item.tags)),
            )

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        *,
        memory_id: str | None,
        version: int,
        event_type: str,
        actor: str,
        before_hash: str = "",
        after: Dict[str, Any] | None = None,
        reason: str = "",
        device_id: str = "local",
    ) -> str:
        event_id = uuid7()
        # Event payloads intentionally exclude statement/source content so a
        # later Forget can purge every content-bearing table completely.
        connection.execute(
            "INSERT INTO memory_events(event_id, memory_id, version, event_type, "
            "actor, device_id, before_hash, after_json, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                memory_id,
                int(version),
                event_type,
                actor,
                device_id,
                before_hash,
                _json(after or {}),
                reason,
                time.time(),
            ),
        )
        return event_id

    @staticmethod
    def _validate(
        *,
        statement: str,
        kind: str,
        scope_type: str,
        lifecycle: str,
        egress_policy: str,
    ) -> tuple[str, str, str, str, str]:
        statement = str(statement or "").strip()
        if not statement:
            raise ValueError("memory statement must not be empty")
        kind = str(kind or "observation").strip().lower()
        if kind not in MEMORY_KINDS:
            kind = "observation"
        scope_type = str(scope_type or "personal").strip().lower()
        if scope_type not in SCOPE_TYPES:
            raise ValueError(f"invalid memory scope {scope_type!r}")
        lifecycle = str(lifecycle or "active").strip().lower()
        if lifecycle not in LIFECYCLES:
            raise ValueError(f"invalid memory lifecycle {lifecycle!r}")
        egress_policy = str(egress_policy or "any").strip().lower()
        if egress_policy not in EGRESS_POLICIES:
            raise ValueError(f"invalid egress policy {egress_policy!r}")
        return statement, kind, scope_type, lifecycle, egress_policy

    def remember(
        self,
        *,
        statement: str,
        kind: str,
        scope_type: str,
        scope_key: str,
        scope_label: str = "",
        lifecycle: str = "active",
        pinned: bool = False,
        trust_origin: str = "model",
        verification: str = "unverified",
        confidence: float = 0.7,
        sensitivity: str = "normal",
        egress_policy: str = "any",
        tags: Sequence[str] | None = None,
        source_refs: Sequence[Dict[str, Any]] | None = None,
        relations: Sequence[Dict[str, Any]] | None = None,
        metadata: Dict[str, Any] | None = None,
        actor: str = "model",
        reason: str = "",
        supersedes_id: str = "",
    ) -> tuple[MemoryItem, bool]:
        statement, kind, scope_type, lifecycle, egress_policy = self._validate(
            statement=statement,
            kind=kind,
            scope_type=scope_type,
            lifecycle=lifecycle,
            egress_policy=egress_policy,
        )
        scope_key = str(scope_key or "").strip()
        if not scope_key:
            raise ValueError("memory scope key must not be empty")
        tags_clean = sorted(
            {str(tag).strip().lower() for tag in (tags or []) if str(tag).strip()}
        )
        now = time.time()
        digest = _content_hash(statement)

        with self._write() as connection:
            duplicate = connection.execute(
                "SELECT * FROM memories WHERE scope_type=? AND scope_key=? "
                "AND content_hash=? AND lifecycle!='forgotten' LIMIT 1",
                (scope_type, scope_key, digest),
            ).fetchone()
            if duplicate is not None:
                current = self._row_to_item(duplicate)
                assert current is not None
                merged_tags = sorted(set(current.tags) | set(tags_clean))
                merged_sources = _merge_json_rows(
                    current.source_refs, source_refs or []
                )
                version = current.version + 1
                item = MemoryItem(
                    **{
                        **current.__dict__,
                        "version": version,
                        "tags": merged_tags,
                        "source_refs": merged_sources,
                        "confidence": max(float(current.confidence), float(confidence)),
                        "updated_at": now,
                        "etag": _etag(current.id, version, digest, current.lifecycle),
                    }
                )
                connection.execute(
                    "UPDATE memories SET version=?, tags_json=?, source_refs_json=?, "
                    "confidence=?, updated_at=?, etag=? WHERE id=?",
                    (
                        item.version,
                        _json(item.tags),
                        _json(item.source_refs),
                        item.confidence,
                        now,
                        item.etag,
                        item.id,
                    ),
                )
                connection.execute(
                    "INSERT INTO memory_revisions(memory_id, version, statement, "
                    "snapshot_json, actor, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        item.id,
                        item.version,
                        item.statement,
                        _json(item.to_dict()),
                        actor,
                        reason or "matching memory reinforced",
                        now,
                    ),
                )
                self._sync_fts(connection, item)
                self._event(
                    connection,
                    memory_id=item.id,
                    version=item.version,
                    event_type="reinforced",
                    actor=actor,
                    before_hash=current.content_hash,
                    after={"scope": item.scope_type, "lifecycle": item.lifecycle},
                    reason=reason,
                )
                return item, False

            memory_id = uuid7()
            relation_rows = list(relations or [])
            if supersedes_id:
                relation_rows.append({"type": "supersedes", "target_id": supersedes_id})
            item = MemoryItem(
                id=memory_id,
                version=1,
                statement=statement,
                kind=kind,
                scope_type=scope_type,
                scope_key=scope_key,
                scope_label=str(scope_label or scope_key),
                lifecycle=lifecycle,
                pinned=bool(pinned),
                trust_origin=str(trust_origin or "model"),
                verification=str(verification or "unverified"),
                confidence=max(0.0, min(1.0, float(confidence))),
                sensitivity=str(sensitivity or "normal"),
                egress_policy=egress_policy,
                tags=tags_clean,
                source_refs=list(source_refs or []),
                relations=relation_rows,
                created_at=now,
                updated_at=now,
                content_hash=digest,
                etag=_etag(memory_id, 1, digest, lifecycle),
                metadata=dict(metadata or {}),
            )
            connection.execute(
                """INSERT INTO memories(
                    id, version, statement, kind, scope_type, scope_key, scope_label,
                    lifecycle, pinned, trust_origin, verification, confidence,
                    sensitivity, egress_policy, tags_json, source_refs_json,
                    relations_json, created_at, updated_at, last_recalled_at,
                    recall_count, content_hash, etag, metadata_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item.id,
                    item.version,
                    item.statement,
                    item.kind,
                    item.scope_type,
                    item.scope_key,
                    item.scope_label,
                    item.lifecycle,
                    int(item.pinned),
                    item.trust_origin,
                    item.verification,
                    item.confidence,
                    item.sensitivity,
                    item.egress_policy,
                    _json(item.tags),
                    _json(item.source_refs),
                    _json(item.relations),
                    item.created_at,
                    item.updated_at,
                    None,
                    0,
                    item.content_hash,
                    item.etag,
                    _json(item.metadata),
                ),
            )
            connection.execute(
                "INSERT INTO memory_revisions(memory_id, version, statement, snapshot_json, "
                "actor, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (item.id, 1, item.statement, _json(item.to_dict()), actor, reason, now),
            )
            self._sync_fts(connection, item)
            self._event(
                connection,
                memory_id=item.id,
                version=1,
                event_type="created",
                actor=actor,
                after={
                    "kind": item.kind,
                    "scope_type": item.scope_type,
                    "scope_key": item.scope_key,
                    "lifecycle": item.lifecycle,
                    "content_hash": item.content_hash,
                },
                reason=reason,
            )

            if supersedes_id:
                old_row = connection.execute(
                    "SELECT * FROM memories WHERE id=? AND lifecycle!='forgotten'",
                    (supersedes_id,),
                ).fetchone()
                old = self._row_to_item(old_row)
                if old is not None:
                    old_relations = _merge_json_rows(
                        old.relations,
                        [{"type": "superseded_by", "target_id": item.id}],
                    )
                    old_version = old.version + 1
                    old_etag = _etag(
                        old.id, old_version, old.content_hash, "superseded"
                    )
                    old_updated = MemoryItem(
                        **{
                            **old.__dict__,
                            "version": old_version,
                            "lifecycle": "superseded",
                            "relations": old_relations,
                            "updated_at": now,
                            "etag": old_etag,
                        }
                    )
                    connection.execute(
                        "UPDATE memories SET version=?, lifecycle='superseded', "
                        "relations_json=?, updated_at=?, etag=? WHERE id=?",
                        (
                            old_version,
                            _json(old_relations),
                            now,
                            old_etag,
                            old.id,
                        ),
                    )
                    connection.execute(
                        "INSERT INTO memory_revisions(memory_id, version, statement, "
                        "snapshot_json, actor, reason, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            old_updated.id,
                            old_updated.version,
                            old_updated.statement,
                            _json(old_updated.to_dict()),
                            actor,
                            reason or f"superseded by {item.id}",
                            now,
                        ),
                    )
                    self._sync_fts(connection, old_updated)
                    self._event(
                        connection,
                        memory_id=old.id,
                        version=old_version,
                        event_type="superseded",
                        actor=actor,
                        before_hash=old.content_hash,
                        after={"superseded_by": item.id},
                        reason=reason,
                    )
            return item, True

    def get(self, memory_id: str) -> MemoryItem | None:
        with self._read() as connection:
            return self._row_to_item(
                connection.execute(
                    "SELECT * FROM memories WHERE id=?", (memory_id,)
                ).fetchone()
            )

    def list(
        self,
        *,
        scopes: Sequence[tuple[str, str]] | None = None,
        lifecycle: str | Sequence[str] | None = None,
        kind: str | None = None,
        query: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> List[MemoryItem]:
        clauses = ["lifecycle!='forgotten'"]
        params: List[Any] = []
        if scopes:
            clauses.append(
                "("
                + " OR ".join("(scope_type=? AND scope_key=?)" for _ in scopes)
                + ")"
            )
            for scope_type, scope_key in scopes:
                params.extend([scope_type, scope_key])
        if lifecycle:
            states = [lifecycle] if isinstance(lifecycle, str) else list(lifecycle)
            states = [state for state in states if state in LIFECYCLES]
            if states:
                clauses.append("lifecycle IN (" + ",".join("?" for _ in states) + ")")
                params.extend(states)
        if kind:
            clauses.append("kind=?")
            params.append(kind)
        if query:
            clauses.append(
                "(statement LIKE ? OR tags_json LIKE ? OR scope_label LIKE ?)"
            )
            like = f"%{query}%"
            params.extend([like, like, like])
        params.extend([max(1, min(int(limit), 1000)), max(0, int(offset))])
        sql = (
            "SELECT * FROM memories WHERE "
            + " AND ".join(clauses)
            + " ORDER BY pinned DESC, updated_at DESC LIMIT ? OFFSET ?"
        )
        with self._read() as connection:
            return [
                item
                for row in connection.execute(sql, params).fetchall()
                if (item := self._row_to_item(row)) is not None
            ]

    def candidates(
        self,
        *,
        scopes: Sequence[tuple[str, str]],
        query: str,
        limit: int = 100,
    ) -> List[tuple[MemoryItem, float]]:
        if not scopes:
            return []
        scope_clause = " OR ".join("(m.scope_type=? AND m.scope_key=?)" for _ in scopes)
        scope_params: List[Any] = []
        for scope_type, scope_key in scopes:
            scope_params.extend([scope_type, scope_key])
        by_id: Dict[str, tuple[MemoryItem, float]] = {}
        terms = list(dict.fromkeys(_TOKEN_RE.findall(str(query or "").casefold())))[:24]

        with self._read() as connection:
            if terms and self._fts_enabled:
                match = " OR ".join(
                    '"' + term.replace('"', '""') + '"' for term in terms
                )
                try:
                    rows = connection.execute(
                        "SELECT m.*, bm25(memory_fts) AS fts_rank FROM memory_fts "
                        "JOIN memories m ON m.id=memory_fts.id WHERE memory_fts MATCH ? "
                        "AND m.lifecycle='active' AND (" + scope_clause + ") "
                        "ORDER BY fts_rank LIMIT ?",
                        [match, *scope_params, max(1, min(limit, 500))],
                    ).fetchall()
                    for row in rows:
                        item = self._row_to_item(row)
                        if item is None:
                            continue
                        rank = abs(float(row["fts_rank"] or 0.0))
                        by_id[item.id] = (item, 1.0 / (1.0 + rank))
                except sqlite3.OperationalError:
                    self._fts_enabled = False

            # Pinned items are candidates even without a lexical hit. Recent
            # records provide a bounded fallback when FTS is unavailable.
            fallback_rows = connection.execute(
                "SELECT m.* FROM memories m WHERE m.lifecycle='active' AND ("
                + scope_clause
                + ") ORDER BY m.pinned DESC, m.updated_at DESC LIMIT ?",
                [*scope_params, max(50, min(limit, 500))],
            ).fetchall()
            for row in fallback_rows:
                item = self._row_to_item(row)
                if item is None or item.id in by_id:
                    continue
                if item.pinned or not terms or not self._fts_enabled:
                    by_id[item.id] = (item, 0.0)
        return list(by_id.values())

    def revise(
        self,
        memory_id: str,
        changes: Dict[str, Any],
        *,
        expected_version: int | None = None,
        actor: str = "user",
        reason: str = "",
    ) -> MemoryItem:
        allowed = {
            "statement",
            "kind",
            "scope_type",
            "scope_key",
            "scope_label",
            "lifecycle",
            "pinned",
            "verification",
            "confidence",
            "sensitivity",
            "egress_policy",
            "tags",
            "source_refs",
            "relations",
            "metadata",
        }
        clean = {key: value for key, value in changes.items() if key in allowed}
        if not clean:
            existing = self.get(memory_id)
            if existing is None:
                raise KeyError(memory_id)
            return existing

        with self._write() as connection:
            row = connection.execute(
                "SELECT * FROM memories WHERE id=?", (memory_id,)
            ).fetchone()
            current = self._row_to_item(row)
            if current is None:
                raise KeyError(memory_id)
            if current.lifecycle == "forgotten":
                raise ValueError("forgotten memory cannot be revised")
            if expected_version is not None and current.version != int(
                expected_version
            ):
                raise MemoryConflictError(
                    f"memory {memory_id} is version {current.version}, expected {expected_version}"
                )

            values = dict(current.__dict__)
            mapping = {
                "tags": lambda value: sorted(
                    {str(v).strip().lower() for v in value if str(v).strip()}
                ),
                "source_refs": lambda value: list(value or []),
                "relations": lambda value: list(value or []),
                "metadata": lambda value: dict(value or {}),
                "pinned": bool,
                "confidence": lambda value: max(0.0, min(1.0, float(value))),
            }
            for key, value in clean.items():
                values[key] = mapping.get(key, lambda item: str(item).strip())(value)
            (
                values["statement"],
                values["kind"],
                values["scope_type"],
                values["lifecycle"],
                values["egress_policy"],
            ) = self._validate(
                statement=values["statement"],
                kind=values["kind"],
                scope_type=values["scope_type"],
                lifecycle=values["lifecycle"],
                egress_policy=values["egress_policy"],
            )
            values["version"] = current.version + 1
            values["updated_at"] = time.time()
            values["content_hash"] = _content_hash(values["statement"])
            values["etag"] = _etag(
                current.id,
                values["version"],
                values["content_hash"],
                values["lifecycle"],
            )
            item = MemoryItem(**values)
            connection.execute(
                """UPDATE memories SET version=?, statement=?, kind=?, scope_type=?,
                scope_key=?, scope_label=?, lifecycle=?, pinned=?, verification=?,
                confidence=?, sensitivity=?, egress_policy=?, tags_json=?, source_refs_json=?,
                relations_json=?, updated_at=?, content_hash=?, etag=?, metadata_json=? WHERE id=?""",
                (
                    item.version,
                    item.statement,
                    item.kind,
                    item.scope_type,
                    item.scope_key,
                    item.scope_label,
                    item.lifecycle,
                    int(item.pinned),
                    item.verification,
                    item.confidence,
                    item.sensitivity,
                    item.egress_policy,
                    _json(item.tags),
                    _json(item.source_refs),
                    _json(item.relations),
                    item.updated_at,
                    item.content_hash,
                    item.etag,
                    _json(item.metadata),
                    item.id,
                ),
            )
            connection.execute(
                "INSERT INTO memory_revisions(memory_id, version, statement, snapshot_json, "
                "actor, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    item.id,
                    item.version,
                    item.statement,
                    _json(item.to_dict()),
                    actor,
                    reason,
                    item.updated_at,
                ),
            )
            self._sync_fts(connection, item)
            self._event(
                connection,
                memory_id=item.id,
                version=item.version,
                event_type="revised",
                actor=actor,
                before_hash=current.content_hash,
                after={"changed_fields": sorted(clean), "lifecycle": item.lifecycle},
                reason=reason,
            )
            return item

    def action(
        self,
        memory_id: str,
        action: str,
        *,
        actor: str = "user",
        reason: str = "",
        expected_version: int | None = None,
    ) -> MemoryItem:
        action = str(action or "").strip().lower()
        if action == "forget":
            return self.forget(
                memory_id,
                actor=actor,
                reason=reason,
                expected_version=expected_version,
            )
        changes = {
            "pin": {"pinned": True},
            "unpin": {"pinned": False},
            "archive": {"lifecycle": "archived"},
            "restore": {"lifecycle": "active"},
            "mark_needs_review": {"lifecycle": "needs_review"},
        }.get(action)
        if changes is None:
            raise ValueError(f"unknown memory action {action!r}")
        return self.revise(
            memory_id,
            changes,
            expected_version=expected_version,
            actor=actor,
            reason=reason or action,
        )

    def forget(
        self,
        memory_id: str,
        *,
        actor: str = "user",
        reason: str = "",
        expected_version: int | None = None,
    ) -> MemoryItem:
        with self._write() as connection:
            row = connection.execute(
                "SELECT * FROM memories WHERE id=?", (memory_id,)
            ).fetchone()
            current = self._row_to_item(row)
            if current is None:
                raise KeyError(memory_id)
            if expected_version is not None and current.version != int(
                expected_version
            ):
                raise MemoryConflictError(
                    f"memory {memory_id} is version {current.version}, expected {expected_version}"
                )
            if current.lifecycle == "forgotten":
                return current
            now = time.time()
            version = current.version + 1
            tombstone_etag = _etag(
                current.id, version, current.content_hash, "forgotten"
            )
            if self._fts_enabled:
                connection.execute("DELETE FROM memory_fts WHERE id=?", (memory_id,))
            connection.execute(
                "DELETE FROM memory_revisions WHERE memory_id=?", (memory_id,)
            )
            # Recall receipts are an audit trail, but they must not become a
            # shadow copy of content the user explicitly forgot. Retain the
            # candidate/score/hash receipt and remove the copied memory body
            # and its derived metadata from every historical receipt.
            receipt_rows = connection.execute(
                "SELECT id, included_json, excluded_json FROM memory_recall_receipts"
            ).fetchall()
            for receipt_row in receipt_rows:
                changed = False
                payloads: Dict[str, Any] = {}
                for column in ("included_json", "excluded_json"):
                    candidates = list(_loads(receipt_row[column], []))
                    for candidate in candidates:
                        memory = candidate.get("memory", {})
                        if str(memory.get("id") or "") != memory_id:
                            continue
                        candidate["memory"] = {
                            "id": memory_id,
                            "version": version,
                            "lifecycle": "forgotten",
                            "content_hash": current.content_hash,
                        }
                        changed = True
                    payloads[column] = candidates
                if changed:
                    connection.execute(
                        "UPDATE memory_recall_receipts SET included_json=?, "
                        "excluded_json=? WHERE id=?",
                        (
                            _json(payloads["included_json"]),
                            _json(payloads["excluded_json"]),
                            receipt_row["id"],
                        ),
                    )
            connection.execute(
                """UPDATE memories SET version=?, statement='', kind='observation',
                lifecycle='forgotten', pinned=0, tags_json='[]', source_refs_json='[]',
                relations_json='[]', metadata_json='{}', updated_at=?, etag=? WHERE id=?""",
                (version, now, tombstone_etag, memory_id),
            )
            connection.execute(
                "INSERT INTO memory_sync_tombstones(memory_id, content_hash, forgotten_at) "
                "VALUES (?, ?, ?) ON CONFLICT(memory_id) DO UPDATE SET "
                "content_hash=excluded.content_hash, forgotten_at=excluded.forgotten_at",
                (memory_id, current.content_hash, now),
            )
            self._event(
                connection,
                memory_id=memory_id,
                version=version,
                event_type="forgotten",
                actor=actor,
                before_hash=current.content_hash,
                after={"content_purged": True},
                reason=reason,
            )
            forgotten = connection.execute(
                "SELECT * FROM memories WHERE id=?", (memory_id,)
            ).fetchone()
            item = self._row_to_item(forgotten)
            assert item is not None
            return item

    def record_recall(self, receipt: RecallReceipt, *, actor: str = "system") -> None:
        included_payload = [candidate.to_dict() for candidate in receipt.included]
        excluded_payload = [candidate.to_dict() for candidate in receipt.excluded]
        with self._write() as connection:
            connection.execute(
                "INSERT INTO memory_recall_receipts(id, session_name, query_text, scopes_json, "
                "budget_tokens, token_count, included_json, excluded_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    receipt.id,
                    receipt.session_name,
                    receipt.query,
                    _json(receipt.scopes),
                    receipt.budget_tokens,
                    receipt.token_count,
                    _json(included_payload),
                    _json(excluded_payload),
                    receipt.created_at,
                ),
            )
            for candidate in receipt.included:
                connection.execute(
                    "UPDATE memories SET recall_count=recall_count+1, last_recalled_at=? WHERE id=?",
                    (receipt.created_at, candidate.item.id),
                )
                self._event(
                    connection,
                    memory_id=candidate.item.id,
                    version=candidate.item.version,
                    event_type="recalled",
                    actor=actor,
                    after={
                        "receipt_id": receipt.id,
                        "session_name": receipt.session_name,
                        "score": round(candidate.score, 4),
                        "token_cost": candidate.token_cost,
                    },
                )

    def get_recall(
        self, receipt_id: str = "", *, session_name: str = ""
    ) -> Dict[str, Any] | None:
        with self._read() as connection:
            if receipt_id:
                where_session = " AND session_name=?" if session_name else ""
                params: List[Any] = [receipt_id]
                if session_name:
                    params.append(session_name)
                row = connection.execute(
                    "SELECT * FROM memory_recall_receipts WHERE id=?" + where_session,
                    params,
                ).fetchone()
                # TUI receipts deliberately display compact IDs. Accept a
                # unique prefix there while retaining canonical IDs in data.
                if row is None and len(str(receipt_id)) >= 6:
                    prefix_params: List[Any] = [f"{receipt_id}%"]
                    if session_name:
                        prefix_params.append(session_name)
                    matches = connection.execute(
                        "SELECT * FROM memory_recall_receipts WHERE id LIKE ?"
                        + where_session
                        + " ORDER BY created_at DESC LIMIT 2",
                        prefix_params,
                    ).fetchall()
                    row = matches[0] if len(matches) == 1 else None
            elif session_name:
                row = connection.execute(
                    "SELECT * FROM memory_recall_receipts WHERE session_name=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (session_name,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM memory_recall_receipts ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
            if row is None:
                return None
            return {
                "id": row["id"],
                "session_name": row["session_name"],
                "query": row["query_text"],
                "scopes": _loads(row["scopes_json"], []),
                "budget_tokens": row["budget_tokens"],
                "token_count": row["token_count"],
                "included": _loads(row["included_json"], []),
                "excluded": _loads(row["excluded_json"], []),
                "created_at": row["created_at"],
            }

    def events(
        self,
        *,
        memory_id: str = "",
        scopes: Sequence[tuple[str, str]] | None = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        sql = "SELECT e.* FROM memory_events e"
        params: List[Any] = []
        clauses: List[str] = []
        if scopes:
            sql += " JOIN memories m ON m.id=e.memory_id"
            clauses.append(
                "("
                + " OR ".join("(m.scope_type=? AND m.scope_key=?)" for _ in scopes)
                + ")"
            )
            for scope_type, scope_key in scopes:
                params.extend([scope_type, scope_key])
        if memory_id:
            clauses.append("e.memory_id=?")
            params.append(memory_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY e.created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        with self._read() as connection:
            return [
                {
                    "event_id": row["event_id"],
                    "memory_id": row["memory_id"],
                    "version": row["version"],
                    "type": row["event_type"],
                    "actor": row["actor"],
                    "device_id": row["device_id"],
                    "before_hash": row["before_hash"],
                    "after": _loads(row["after_json"], {}),
                    "reason": row["reason"],
                    "created_at": row["created_at"],
                }
                for row in connection.execute(sql, params).fetchall()
            ]

    def revisions(self, memory_id: str, *, limit: int = 100) -> List[Dict[str, Any]]:
        with self._read() as connection:
            rows = connection.execute(
                "SELECT memory_id, version, statement, snapshot_json, actor, reason, created_at "
                "FROM memory_revisions WHERE memory_id=? ORDER BY version DESC LIMIT ?",
                (memory_id, max(1, min(int(limit), 1000))),
            ).fetchall()
            return [
                {
                    "memory_id": row["memory_id"],
                    "version": row["version"],
                    "statement": row["statement"],
                    "snapshot": _loads(row["snapshot_json"], {}),
                    "actor": row["actor"],
                    "reason": row["reason"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    def graph(self, memory_id: str) -> Dict[str, Any]:
        item = self.get(memory_id)
        if item is None:
            raise KeyError(memory_id)
        target_ids = {
            str(relation.get("target_id") or "")
            for relation in item.relations
            if isinstance(relation, dict) and relation.get("target_id")
        }
        nodes = [item.to_dict()]
        for target_id in sorted(target_ids):
            target = self.get(target_id)
            if target is not None:
                nodes.append(target.to_dict())
        edges = [
            {
                "source": item.id,
                "target": relation.get("target_id"),
                "type": relation.get("type", "related_to"),
            }
            for relation in item.relations
            if isinstance(relation, dict) and relation.get("target_id")
        ]
        return {"center": item.id, "nodes": nodes, "edges": edges}

    def stats(
        self, *, scopes: Sequence[tuple[str, str]] | None = None
    ) -> Dict[str, Any]:
        clauses = ["lifecycle!='forgotten'"]
        params: List[Any] = []
        if scopes:
            clauses.append(
                "("
                + " OR ".join("(scope_type=? AND scope_key=?)" for _ in scopes)
                + ")"
            )
            for scope_type, scope_key in scopes:
                params.extend([scope_type, scope_key])
        where = " AND ".join(clauses)
        with self._read() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM memories WHERE {where}", params
            ).fetchone()[0]
            rows = connection.execute(
                f"SELECT lifecycle, COUNT(*) AS n FROM memories WHERE {where} GROUP BY lifecycle",
                params,
            ).fetchall()
            pinned = connection.execute(
                f"SELECT COUNT(*) FROM memories WHERE {where} AND pinned=1", params
            ).fetchone()[0]
        return {
            "total": int(total),
            "pinned": int(pinned),
            "by_lifecycle": {str(row["lifecycle"]): int(row["n"]) for row in rows},
            "database": self.path,
            "schema_version": SCHEMA_VERSION,
            "fts_enabled": self._fts_enabled,
        }


__all__ = ["MemoryConflictError", "SCHEMA_VERSION", "SQLiteMemoryLedger"]
