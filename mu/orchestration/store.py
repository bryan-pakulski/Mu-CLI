"""Durable orchestration state: ``$MUCLI_HOME/orchestration/orch.sqlite3`` (WAL) + ``blobs/``.

One short connection per operation, ``BEGIN IMMEDIATE`` for compound writes, so the GUI process
(scheduler, node endpoint) and CLI processes can share the database. Schema-versioned like
``JobStore``. The ``changes`` table is the cross-process change log the GUI bridges into its
``ChangeBus`` (scopes ``node``, ``tool``, ``run``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets as _secrets
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from .models import Execution, Node, Run, RunbookVersion, ToolVersion
from .node.canonical import canonical, sha256_hex

SCHEMA_VERSION = 2
_SHA = re.compile(r"^[0-9a-f]{64}$")


def orchestration_home() -> str:
    root = os.path.expanduser(os.environ.get("MUCLI_HOME", "~/.mucli/"))
    return os.path.join(root, "orchestration")


def _j(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def new_id(prefix: str, nbytes: int = 6) -> str:
    return f"{prefix}_{_secrets.token_hex(nbytes)}"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS nodes(
    id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', labels TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending', kind TEXT NOT NULL DEFAULT 'host', canary INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL, accepted_at REAL, last_seen REAL, agent_version TEXT NOT NULL DEFAULT '',
    credential_hash TEXT, pending_credential_hash TEXT, credential_rotated_at REAL,
    facts TEXT NOT NULL DEFAULT '{}', facts_at REAL, policy TEXT NOT NULL DEFAULT '{}',
    address TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS join_tokens(
    token_hash TEXT PRIMARY KEY, created_at REAL NOT NULL, expires_at REAL NOT NULL, used_at REAL,
    node_id TEXT, labels TEXT NOT NULL DEFAULT '{}', canary INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS groups(
    name TEXT PRIMARY KEY, selector TEXT NOT NULL, description TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS tools(
    id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tool_versions(
    tool_id TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL,
    manifest TEXT NOT NULL, manifest_digest TEXT NOT NULL DEFAULT '', blobs TEXT NOT NULL DEFAULT '{}',
    signature TEXT NOT NULL DEFAULT '', signed_with TEXT NOT NULL DEFAULT '', key_id TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL, updated_at REAL NOT NULL, created_by TEXT NOT NULL DEFAULT '',
    published_at REAL, stage TEXT NOT NULL DEFAULT 'intent', session_name TEXT NOT NULL DEFAULT '',
    intent TEXT NOT NULL DEFAULT '', lint TEXT NOT NULL DEFAULT '{}', review TEXT NOT NULL DEFAULT '{}',
    origin TEXT NOT NULL DEFAULT 'studio',
    PRIMARY KEY(tool_id, version));
CREATE TABLE IF NOT EXISTS runbooks(
    id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS runbook_versions(
    runbook_id TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL, spec TEXT NOT NULL,
    digest TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL,
    created_by TEXT NOT NULL DEFAULT '', published_at REAL, origin TEXT NOT NULL DEFAULT 'manual',
    review TEXT NOT NULL DEFAULT '{}', source TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(runbook_id, version));
CREATE TABLE IF NOT EXISTS runs(
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, target_id TEXT NOT NULL, target_version INTEGER NOT NULL,
    params TEXT NOT NULL DEFAULT '{}', selector TEXT NOT NULL DEFAULT '{}', nodes TEXT NOT NULL DEFAULT '[]',
    excluded TEXT NOT NULL DEFAULT '[]', strategy TEXT NOT NULL DEFAULT '{}', check_mode INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL, proposed_by TEXT NOT NULL DEFAULT '', approved_by TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL, approved_at REAL, started_at REAL, finished_at REAL,
    decision TEXT NOT NULL DEFAULT '{}', plan TEXT NOT NULL DEFAULT '{}', reason TEXT NOT NULL DEFAULT '',
    paused_reason TEXT NOT NULL DEFAULT '', parent_run TEXT NOT NULL DEFAULT '',
    schedule_id TEXT NOT NULL DEFAULT '', batch INTEGER NOT NULL DEFAULT 0, batch_finished_at REAL,
    summary TEXT NOT NULL DEFAULT '{}', title TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS runs_state ON runs(state);
CREATE TABLE IF NOT EXISTS executions(
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, node_id TEXT NOT NULL, tool_id TEXT NOT NULL,
    version INTEGER NOT NULL, step INTEGER NOT NULL DEFAULT 0, step_name TEXT NOT NULL DEFAULT '',
    batch INTEGER NOT NULL DEFAULT 0, params TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, check_mode INTEGER NOT NULL DEFAULT 0,
    idempotent INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, dispatched_at REAL, started_at REAL,
    finished_at REAL, exit_code INTEGER, output TEXT, output_errors TEXT NOT NULL DEFAULT '[]',
    log TEXT NOT NULL DEFAULT '', log_truncated INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '',
    duration_s REAL, output_digest TEXT NOT NULL DEFAULT '', nonce TEXT NOT NULL DEFAULT '',
    envelope TEXT NOT NULL DEFAULT '{}', cancel_requested INTEGER NOT NULL DEFAULT 0,
    rollback_of TEXT NOT NULL DEFAULT '', redactions INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS executions_run ON executions(run_id);
CREATE INDEX IF NOT EXISTS executions_node_state ON executions(node_id, state);
CREATE TABLE IF NOT EXISTS audit(
    seq INTEGER PRIMARY KEY, at REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '', digest TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}',
    prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS changes(
    seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, scope TEXT NOT NULL, id TEXT NOT NULL,
    parts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS access_policy(
    version INTEGER PRIMARY KEY, rules TEXT NOT NULL, created_at REAL NOT NULL, created_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS schedules(
    id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, spec TEXT NOT NULL, target TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1, last_fired_at REAL, next_at REAL, created_at REAL NOT NULL,
    state TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS secrets(
    name TEXT PRIMARY KEY, nonce BLOB NOT NULL, ciphertext BLOB NOT NULL, description TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS trusted_keys(
    key_id TEXT PRIMARY KEY, public_key TEXT NOT NULL, label TEXT NOT NULL DEFAULT '', added_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS overlay_peers(
    name TEXT PRIMARY KEY, node_id TEXT NOT NULL DEFAULT '', address TEXT NOT NULL, public_key TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'node', created_at REAL NOT NULL);
"""

GENESIS = "0" * 64

# Columns added after schema 1, applied to existing stores by ``initialize`` (``ALTER TABLE``).
_MIGRATIONS = {
    2: [("runbook_versions", "review", "TEXT NOT NULL DEFAULT '{}'"),
        ("runbook_versions", "source", "TEXT NOT NULL DEFAULT ''")],
}


class OrchestrationStore:
    def __init__(self, root: Optional[str] = None, *, clock=time.time):
        self.root = os.path.abspath(os.path.expanduser(root or orchestration_home()))
        self.path = os.path.join(self.root, "orch.sqlite3")
        self.blob_dir = os.path.join(self.root, "blobs")
        self.clock = clock
        os.makedirs(self.blob_dir, mode=0o700, exist_ok=True)
        os.chmod(self.root, 0o700)
        self._init_lock = threading.Lock()
        self.initialize()

    # ------------------------------------------------------------ plumbing
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    def initialize(self) -> None:
        with self._init_lock, self.conn() as conn:
            conn.executescript(_SCHEMA)
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None:
                conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
            elif int(row["value"]) > SCHEMA_VERSION:
                raise RuntimeError(f"orchestration store schema {row['value']} is newer than this mucli")
            elif int(row["value"]) < SCHEMA_VERSION:
                self._migrate(conn, int(row["value"]))
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _migrate(self, conn: sqlite3.Connection, current: int) -> None:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for version in range(current + 1, SCHEMA_VERSION + 1):
                for table, column, decl in _MIGRATIONS.get(version, []):
                    have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                    if column not in have:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            conn.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    def schema_version(self) -> int:
        with self.conn() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return int(row["value"]) if row else 0

    def now(self) -> float:
        return float(self.clock())

    # ------------------------------------------------------------ blobs
    def put_blob(self, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        path = os.path.join(self.blob_dir, sha)
        if not os.path.exists(path):
            fd, tmp = tempfile.mkstemp(dir=self.blob_dir)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        return sha

    def get_blob(self, sha: str) -> Optional[bytes]:
        sha = str(sha or "").replace("sha256:", "")
        if not _SHA.match(sha):
            return None
        try:
            with open(os.path.join(self.blob_dir, sha), "rb") as fh:
                data = fh.read()
        except FileNotFoundError:
            return None
        return data if hashlib.sha256(data).hexdigest() == sha else None

    # ------------------------------------------------------------ change log + audit
    def change(self, scope: str, id: str, parts: Iterable[str], conn: Optional[sqlite3.Connection] = None) -> None:
        sql = "INSERT INTO changes(at, scope, id, parts) VALUES(?,?,?,?)"
        args = (self.now(), scope, str(id or ""), _j(sorted(set(parts))))
        if conn is not None:
            conn.execute(sql, args)
        else:
            with self.conn() as c:
                c.execute(sql, args)

    def changes_since(self, seq: int, limit: int = 500) -> List[Dict[str, Any]]:
        with self.conn() as conn:
            rows = conn.execute("SELECT * FROM changes WHERE seq > ? ORDER BY seq LIMIT ?", (int(seq), limit)).fetchall()
        return [{"seq": r["seq"], "scope": r["scope"], "id": r["id"], "parts": json.loads(r["parts"]),
                 "at": r["at"]} for r in rows]

    def last_change_seq(self) -> int:
        with self.conn() as conn:
            row = conn.execute("SELECT MAX(seq) AS s FROM changes").fetchone()
        return int(row["s"] or 0)

    def audit(self, actor: str, action: str, subject: str = "", payload: Optional[Dict[str, Any]] = None,
              conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
        """Append to the hash-chained audit log (see ``audit.py`` for verification)."""
        from .audit import entry_hash
        body = dict(payload or {})
        pd = "sha256:" + sha256_hex(canonical(body))

        def _append(c: sqlite3.Connection) -> Dict[str, Any]:
            last = c.execute("SELECT seq, hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
            seq = (last["seq"] + 1) if last else 1
            prev = last["hash"] if last else GENESIS
            at = self.now()
            h = entry_hash(seq, at, actor, action, subject, pd, prev)
            c.execute("INSERT INTO audit(seq, at, actor, action, subject, digest, payload, prev_hash, hash)"
                      " VALUES(?,?,?,?,?,?,?,?,?)", (seq, at, actor, action, subject, pd, _j(body), prev, h))
            return {"seq": seq, "at": at, "actor": actor, "action": action, "subject": subject, "hash": h}

        if conn is not None:
            return _append(conn)
        with self.tx() as c:
            return _append(c)

    def audit_entries(self, *, after: int = 0, limit: int = 500, actor: str = "", action: str = "",
                      subject: str = "") -> List[Dict[str, Any]]:
        sql, args = "SELECT * FROM audit WHERE seq > ?", [int(after)]
        if actor:
            sql += " AND actor LIKE ?"
            args.append(actor.replace("*", "%"))
        if action:
            sql += " AND action LIKE ?"
            args.append(action.replace("*", "%"))
        if subject:
            sql += " AND subject LIKE ?"
            args.append(f"%{subject}%")
        sql += " ORDER BY seq LIMIT ?"
        args.append(int(limit))
        with self.conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(r, payload=json.loads(r["payload"] or "{}")) for r in rows]

    def audit_recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self.conn() as conn:
            rows = conn.execute("SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r, payload=json.loads(r["payload"] or "{}")) for r in rows]

    # ------------------------------------------------------------ settings
    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value: Any, conn: Optional[sqlite3.Connection] = None) -> None:
        sql = ("INSERT INTO settings(key, value, updated_at) VALUES(?,?,?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at")
        if conn is not None:
            conn.execute(sql, (key, _j(value), self.now()))
        else:
            with self.conn() as c:
                c.execute(sql, (key, _j(value), self.now()))

    # ------------------------------------------------------------ nodes
    def insert_node(self, node: Node, credential_hash: str, conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO nodes(id, name, labels, status, kind, canary, created_at, agent_version, credential_hash,"
            " facts, facts_at, policy, address, last_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (node.id, node.name, _j(node.labels), node.status, node.kind, int(node.canary), node.created_at,
             node.agent_version, credential_hash, _j(node.facts), node.facts_at, _j(node.policy), node.address,
             node.last_seen))

    def get_node(self, node_id: str) -> Optional[Node]:
        with self.conn() as conn:
            row = conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        return Node.from_row(row) if row else None

    def node_credentials(self, node_id: str) -> Optional[Tuple[str, str, str]]:
        with self.conn() as conn:
            row = conn.execute("SELECT status, credential_hash, pending_credential_hash FROM nodes WHERE id=?",
                               (node_id,)).fetchone()
        if not row:
            return None
        return row["status"], row["credential_hash"] or "", row["pending_credential_hash"] or ""

    def list_nodes(self, *, statuses: Optional[Iterable[str]] = None) -> List[Node]:
        sql, args = "SELECT * FROM nodes", []
        if statuses:
            statuses = list(statuses)
            sql += f" WHERE status IN ({','.join('?' * len(statuses))})"
            args = statuses
        with self.conn() as conn:
            rows = conn.execute(sql + " ORDER BY name, id", args).fetchall()
        return [Node.from_row(r) for r in rows]

    def update_node(self, node_id: str, conn: Optional[sqlite3.Connection] = None, **fields: Any) -> None:
        if not fields:
            return
        cols, args = [], []
        for key, value in fields.items():
            if key in ("labels", "facts", "policy"):
                value = _j(value)
            elif key == "canary":
                value = int(bool(value))
            cols.append(f"{key}=?")
            args.append(value)
        args.append(node_id)
        sql = f"UPDATE nodes SET {', '.join(cols)} WHERE id=?"
        if conn is not None:
            conn.execute(sql, args)
        else:
            with self.conn() as c:
                c.execute(sql, args)

    # ------------------------------------------------------------ groups
    def list_groups(self) -> List[Dict[str, Any]]:
        with self.conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM groups ORDER BY name").fetchall()]

    def put_group(self, name: str, selector: str, description: str = "", conn=None) -> None:
        sql = ("INSERT INTO groups(name, selector, description) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET"
               " selector=excluded.selector, description=excluded.description")
        if conn is not None:
            conn.execute(sql, (name, selector, description))
        else:
            with self.conn() as c:
                c.execute(sql, (name, selector, description))

    def delete_group(self, name: str) -> bool:
        with self.conn() as conn:
            return conn.execute("DELETE FROM groups WHERE name=?", (name,)).rowcount > 0

    # ------------------------------------------------------------ tools
    def get_version(self, tool_id: str, version: int) -> Optional[ToolVersion]:
        with self.conn() as conn:
            row = conn.execute("SELECT * FROM tool_versions WHERE tool_id=? AND version=?",
                               (tool_id, int(version))).fetchone()
        return ToolVersion.from_row(row) if row else None

    def list_versions(self, tool_id: Optional[str] = None, *, statuses: Optional[Iterable[str]] = None
                      ) -> List[ToolVersion]:
        sql, args = "SELECT * FROM tool_versions WHERE 1=1", []
        if tool_id:
            sql += " AND tool_id=?"
            args.append(tool_id)
        if statuses:
            statuses = list(statuses)
            sql += f" AND status IN ({','.join('?' * len(statuses))})"
            args += statuses
        with self.conn() as conn:
            rows = conn.execute(sql + " ORDER BY tool_id, version", args).fetchall()
        return [ToolVersion.from_row(r) for r in rows]

    def latest_version(self, tool_id: str, *, published_only: bool = False) -> Optional[ToolVersion]:
        statuses = ["published"] if published_only else None
        versions = self.list_versions(tool_id, statuses=statuses)
        return versions[-1] if versions else None

    def next_version_number(self, tool_id: str, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT MAX(version) AS v FROM tool_versions WHERE tool_id=?", (tool_id,)).fetchone()
        return int(row["v"] or 0) + 1

    def insert_version(self, tv: ToolVersion, conn: sqlite3.Connection) -> None:
        now = self.now()
        conn.execute("INSERT INTO tools(id, title, category, created_at, updated_at) VALUES(?,?,?,?,?)"
                     " ON CONFLICT(id) DO UPDATE SET title=excluded.title, category=excluded.category,"
                     " updated_at=excluded.updated_at",
                     (tv.tool_id, str(tv.manifest.get("title") or ""), str(tv.manifest.get("category") or ""),
                      now, now))
        conn.execute(
            "INSERT INTO tool_versions(tool_id, version, status, manifest, manifest_digest, blobs, signature,"
            " signed_with, key_id, created_at, updated_at, created_by, published_at, stage, session_name, intent,"
            " lint, review, origin) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tv.tool_id, tv.version, tv.status, _j(tv.manifest), tv.manifest_digest, _j(tv.blobs), tv.signature,
             tv.signed_with, tv.key_id, tv.created_at or now, tv.updated_at or now, tv.created_by,
             tv.published_at, tv.stage, tv.session_name, tv.intent, _j(tv.lint), _j(tv.review), tv.origin))

    def update_version(self, tool_id: str, version: int, conn: sqlite3.Connection, **fields: Any) -> None:
        cols, args = [], []
        for key, value in fields.items():
            if key in ("manifest", "blobs", "lint", "review"):
                value = _j(value)
            cols.append(f"{key}=?")
            args.append(value)
        cols.append("updated_at=?")
        args += [self.now(), tool_id, int(version)]
        conn.execute(f"UPDATE tool_versions SET {', '.join(cols)} WHERE tool_id=? AND version=?", args)

    def tool_ids(self) -> List[str]:
        with self.conn() as conn:
            return [r["id"] for r in conn.execute("SELECT id FROM tools ORDER BY id").fetchall()]

    # ------------------------------------------------------------ runbooks
    def get_runbook_version(self, runbook_id: str, version: int) -> Optional[RunbookVersion]:
        with self.conn() as conn:
            row = conn.execute("SELECT * FROM runbook_versions WHERE runbook_id=? AND version=?",
                               (runbook_id, int(version))).fetchone()
        return RunbookVersion.from_row(row) if row else None

    def list_runbook_versions(self, runbook_id: Optional[str] = None, *,
                              statuses: Optional[Iterable[str]] = None) -> List[RunbookVersion]:
        sql, args = "SELECT * FROM runbook_versions WHERE 1=1", []
        if runbook_id:
            sql += " AND runbook_id=?"
            args.append(runbook_id)
        if statuses:
            statuses = list(statuses)
            sql += f" AND status IN ({','.join('?' * len(statuses))})"
            args += statuses
        with self.conn() as conn:
            rows = conn.execute(sql + " ORDER BY runbook_id, version", args).fetchall()
        return [RunbookVersion.from_row(r) for r in rows]

    def insert_runbook_version(self, rb: RunbookVersion, conn: sqlite3.Connection) -> None:
        now = self.now()
        conn.execute("INSERT INTO runbooks(id, title, created_at, updated_at) VALUES(?,?,?,?) ON CONFLICT(id)"
                     " DO UPDATE SET title=excluded.title, updated_at=excluded.updated_at",
                     (rb.runbook_id, str(rb.spec.get("title") or ""), now, now))
        conn.execute("INSERT INTO runbook_versions(runbook_id, version, status, spec, digest, created_at,"
                     " updated_at, created_by, published_at, origin, review, source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (rb.runbook_id, rb.version, rb.status, _j(rb.spec), rb.digest, now, now, rb.created_by,
                      rb.published_at, rb.origin, _j(rb.review), rb.source))

    def update_runbook_version(self, runbook_id: str, version: int, conn: sqlite3.Connection, **fields) -> None:
        cols, args = [], []
        for key, value in fields.items():
            if key in ("spec", "review"):
                value = _j(value)
            cols.append(f"{key}=?")
            args.append(value)
        cols.append("updated_at=?")
        args += [self.now(), runbook_id, int(version)]
        conn.execute(f"UPDATE runbook_versions SET {', '.join(cols)} WHERE runbook_id=? AND version=?", args)

    def next_runbook_version(self, runbook_id: str, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT MAX(version) AS v FROM runbook_versions WHERE runbook_id=?",
                           (runbook_id,)).fetchone()
        return int(row["v"] or 0) + 1

    # ------------------------------------------------------------ runs
    _RUN_JSON = ("params", "selector", "nodes", "excluded", "strategy", "decision", "plan", "summary")

    def insert_run(self, run: Run, conn: sqlite3.Connection) -> None:
        data = run.to_dict()
        for key in self._RUN_JSON:
            data[key] = _j(data[key])
        data["check_mode"] = int(run.check_mode)
        cols = list(data)
        conn.execute(f"INSERT INTO runs({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                     [data[c] for c in cols])

    def get_run(self, run_id: str, conn: Optional[sqlite3.Connection] = None) -> Optional[Run]:
        if conn is not None:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        else:
            with self.conn() as c:
                row = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return Run.from_row(row) if row else None

    def list_runs(self, *, states: Optional[Iterable[str]] = None, limit: int = 200,
                  kind: Optional[str] = None, target_id: Optional[str] = None) -> List[Run]:
        sql, args = "SELECT * FROM runs WHERE 1=1", []
        if states:
            states = list(states)
            sql += f" AND state IN ({','.join('?' * len(states))})"
            args += states
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        if target_id:
            sql += " AND target_id=?"
            args.append(target_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(int(limit))
        with self.conn() as conn:
            return [Run.from_row(r) for r in conn.execute(sql, args).fetchall()]

    def update_run(self, run_id: str, conn: sqlite3.Connection, **fields: Any) -> None:
        cols, args = [], []
        for key, value in fields.items():
            if key in self._RUN_JSON:
                value = _j(value)
            elif key == "check_mode":
                value = int(bool(value))
            cols.append(f"{key}=?")
            args.append(value)
        args.append(run_id)
        conn.execute(f"UPDATE runs SET {', '.join(cols)} WHERE id=?", args)

    # ------------------------------------------------------------ executions
    _EXEC_JSON = ("params", "output", "output_errors", "envelope")
    _EXEC_BOOL = ("check_mode", "idempotent", "cancel_requested", "log_truncated")

    def insert_execution(self, ex: Execution, conn: sqlite3.Connection) -> None:
        data = ex.to_dict()
        for key in self._EXEC_JSON:
            data[key] = _j(data[key])
        for key in self._EXEC_BOOL:
            data[key] = int(bool(data[key]))
        cols = list(data)
        conn.execute(f"INSERT INTO executions({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                     [data[c] for c in cols])

    def get_execution(self, exec_id: str, conn: Optional[sqlite3.Connection] = None) -> Optional[Execution]:
        if conn is not None:
            row = conn.execute("SELECT * FROM executions WHERE id=?", (exec_id,)).fetchone()
        else:
            with self.conn() as c:
                row = c.execute("SELECT * FROM executions WHERE id=?", (exec_id,)).fetchone()
        return Execution.from_row(row) if row else None

    def list_executions(self, *, run_id: Optional[str] = None, node_id: Optional[str] = None,
                        states: Optional[Iterable[str]] = None, limit: int = 5000,
                        conn: Optional[sqlite3.Connection] = None) -> List[Execution]:
        sql, args = "SELECT * FROM executions WHERE 1=1", []
        if run_id:
            sql += " AND run_id=?"
            args.append(run_id)
        if node_id:
            sql += " AND node_id=?"
            args.append(node_id)
        if states:
            states = list(states)
            sql += f" AND state IN ({','.join('?' * len(states))})"
            args += states
        sql += " ORDER BY created_at, step LIMIT ?"
        args.append(int(limit))
        if conn is not None:
            return [Execution.from_row(r) for r in conn.execute(sql, args).fetchall()]
        with self.conn() as c:
            return [Execution.from_row(r) for r in c.execute(sql, args).fetchall()]

    def update_execution(self, exec_id: str, conn: sqlite3.Connection, **fields: Any) -> None:
        cols, args = [], []
        for key, value in fields.items():
            if key in self._EXEC_JSON:
                value = _j(value)
            elif key in self._EXEC_BOOL:
                value = int(bool(value))
            cols.append(f"{key}=?")
            args.append(value)
        args.append(exec_id)
        conn.execute(f"UPDATE executions SET {', '.join(cols)} WHERE id=?", args)

    # ------------------------------------------------------------ access policy
    def access_rules(self) -> Tuple[int, List[Dict[str, Any]]]:
        with self.conn() as conn:
            row = conn.execute("SELECT * FROM access_policy ORDER BY version DESC LIMIT 1").fetchone()
        if not row:
            return 0, []
        return int(row["version"]), json.loads(row["rules"])

    def access_history(self) -> List[Dict[str, Any]]:
        with self.conn() as conn:
            rows = conn.execute("SELECT * FROM access_policy ORDER BY version DESC").fetchall()
        return [dict(r, rules=json.loads(r["rules"])) for r in rows]

    def save_access_rules(self, rules: List[Dict[str, Any]], actor: str, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT MAX(version) AS v FROM access_policy").fetchone()
        version = int(row["v"] or 0) + 1
        conn.execute("INSERT INTO access_policy(version, rules, created_at, created_by) VALUES(?,?,?,?)",
                     (version, _j(rules), self.now(), actor))
        return version

    # ------------------------------------------------------------ schedules
    def list_schedules(self) -> List[Dict[str, Any]]:
        with self.conn() as conn:
            rows = conn.execute("SELECT * FROM schedules ORDER BY name").fetchall()
        return [dict(r, spec=json.loads(r["spec"]), target=json.loads(r["target"]), state=json.loads(r["state"]),
                     enabled=bool(r["enabled"])) for r in rows]

    def get_schedule(self, schedule_id: str) -> Optional[Dict[str, Any]]:
        for item in self.list_schedules():
            if item["id"] == schedule_id:
                return item
        return None

    def put_schedule(self, item: Dict[str, Any], conn=None) -> None:
        sql = ("INSERT INTO schedules(id, name, kind, spec, target, enabled, last_fired_at, next_at, created_at, state)"
               " VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, kind=excluded.kind,"
               " spec=excluded.spec, target=excluded.target, enabled=excluded.enabled,"
               " last_fired_at=excluded.last_fired_at, next_at=excluded.next_at, state=excluded.state")
        args = (item["id"], item["name"], item["kind"], _j(item["spec"]), _j(item["target"]),
                int(bool(item.get("enabled", True))), item.get("last_fired_at"), item.get("next_at"),
                item.get("created_at") or self.now(), _j(item.get("state") or {}))
        if conn is not None:
            conn.execute(sql, args)
        else:
            with self.conn() as c:
                c.execute(sql, args)

    def delete_schedule(self, schedule_id: str) -> bool:
        with self.conn() as conn:
            return conn.execute("DELETE FROM schedules WHERE id=?", (schedule_id,)).rowcount > 0

    # ------------------------------------------------------------ trusted keys / overlay
    def trusted_keys(self) -> List[Dict[str, Any]]:
        with self.conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM trusted_keys ORDER BY added_at").fetchall()]

    def add_trusted_key(self, key_id: str, public_key: str, label: str) -> None:
        with self.conn() as conn:
            conn.execute("INSERT OR REPLACE INTO trusted_keys(key_id, public_key, label, added_at) VALUES(?,?,?,?)",
                         (key_id, public_key, label, self.now()))

    def remove_trusted_key(self, key_id: str) -> bool:
        with self.conn() as conn:
            return conn.execute("DELETE FROM trusted_keys WHERE key_id=?", (key_id,)).rowcount > 0

    def overlay_peers(self) -> List[Dict[str, Any]]:
        with self.conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM overlay_peers ORDER BY address").fetchall()]

    def put_overlay_peer(self, name: str, address: str, public_key: str, role: str = "node", node_id: str = "") -> None:
        with self.conn() as conn:
            conn.execute("INSERT OR REPLACE INTO overlay_peers(name, node_id, address, public_key, role, created_at)"
                         " VALUES(?,?,?,?,?,?)", (name, node_id, address, public_key, role, self.now()))
