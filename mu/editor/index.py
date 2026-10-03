"""Incremental, bounded workspace retrieval. No model calls on the lookup path."""

from __future__ import annotations

from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time

from .context import (
    MAX_BUFFER_CHARS,
    MAX_FILE_BYTES,
    Workspace,
    scrub,
    background_eligible,
)

WORDS = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
DEFINITION = re.compile(
    r"^\s*(?:(?:export|default|pub|async|public|private|protected|static)\s+)*"
    r"(?:class|interface|type|enum|struct|trait|fn|func|function|def)\s+(\w+)"
)
IMPORT = re.compile(
    r"^\s*(?:from\s+\S+\s+import\b|import\b|use\s+|#include\b|require\s*\()"
)
STOP = set(
    "the and for with from import def class return self this true false none const let var function if else in is not as str int bool print".split()
)


def identifiers(text):
    return set(WORDS.findall(text)) - STOP


def structure(text: str, row: int) -> tuple[str, str]:
    """Keep imports and enclosing declarations even for incomplete syntax."""
    lines = text.split("\n")
    imports, stack = [], []
    for number, line in enumerate(lines[: row + 1]):
        if IMPORT.match(line) and sum(map(len, imports)) < 1800:
            imports.append(line)
        if not line.strip() or line.lstrip().startswith(("#", "//")):
            continue
        indent = len(line) - len(line.lstrip())
        # At the cursor an empty body or a partial member access is normal.
        if number < row:
            while stack and indent <= stack[-1][0]:
                stack.pop()
        if DEFINITION.match(line):
            declaration = [line]
            depth = line.count("(") - line.count(")")
            for following in lines[number + 1 : number + 9]:
                if depth <= 0:
                    break
                declaration.append(following)
                depth += following.count("(") - following.count(")")
            stack.append((indent, "\n".join(declaration)))
    return (
        scrub("\n".join(imports))[:1800],
        scrub("\n".join(s[1] for s in stack))[-1800:],
    )


@dataclass(frozen=True)
class Chunk:
    line: int
    text: str
    tokens: frozenset
    symbols: frozenset


@dataclass(frozen=True)
class Document:
    text: str
    stamp: tuple
    chunks: tuple[Chunk, ...]


def document(text: str, stamp=()) -> Document:
    text = scrub(text)
    lines = text.splitlines()
    starts = set(range(0, len(lines), 24))
    starts.update(
        max(0, i - 1) for i, line in enumerate(lines) if DEFINITION.match(line)
    )
    chunks = []
    for start in sorted(starts):
        code = "\n".join(lines[start : start + 40])[:2400]
        if not code.strip():
            continue
        symbols = frozenset(
            m.group(1) for line in code.splitlines() if (m := DEFINITION.match(line))
        )
        chunks.append(Chunk(start + 1, code, frozenset(identifiers(code)), symbols))
    return Document(text, stamp, tuple(chunks))


def ignore_stamp(root: Path, name: str):
    result = []
    parent = (root / name).parent
    while parent.is_relative_to(root):
        for filename in (".gitignore", ".ignore", ".mucliignore"):
            try:
                stat = (parent / filename).stat()
                result.append((str(parent / filename), stat.st_mtime_ns, stat.st_size))
            except OSError:
                pass
        if parent == root:
            break
        parent = parent.parent
    try:
        stat = (root / ".git/info/exclude").stat()
        result.append(("git-exclude", stat.st_mtime_ns, stat.st_size))
    except OSError:
        pass
    return tuple(result)


class WorkspaceIndex:
    """Disk refresh runs separately; queries read an immutable published index."""

    def __init__(self, root: str, cache_dir: Path | None = None):
        self.workspace = Workspace(root)
        self.documents = {}
        self.postings = {}
        self.view = ({}, {})
        self.module_paths = {}
        self.ready = threading.Event()
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.revision = 0
        self.limited = False
        self.error = None
        self.refresh_ms = 0
        self.cache_dir = cache_dir
        self._refresh_lock = threading.Lock()

    def start(self):
        if self.thread is not None:
            return
        self.thread = threading.Thread(
            target=self._work, name="mucli-editor-index", daemon=True
        )
        self.thread.start()

    def close(self):
        self.stop.set()
        self.wake.set()

    def _work(self):
        while not self.stop.is_set():
            try:
                self.refresh()
            except (OSError, ValueError, sqlite3.Error) as exc:
                self.error = scrub(str(exc))[:200]
                self.ready.set()
            self.wake.wait(10)
            self.wake.clear()

    def _cache(self):
        if self.cache_dir is None:
            return None
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            name = hashlib.sha256(str(self.workspace.root).encode()).hexdigest()[:24]
            connection = sqlite3.connect(
                str(self.cache_dir / (name + ".sqlite3")), timeout=1
            )
            connection.execute("PRAGMA secure_delete=ON")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, stamp TEXT, text TEXT)"
            )
            return connection
        except (OSError, sqlite3.Error):
            return None  # A read-only home must not disable editor completion.

    def refresh(self):
        with self._refresh_lock:
            started = time.monotonic()
            ws = self.workspace
            ws._refreshed = 0
            discovered = ws.files()
            discovery_limited = len(discovered) >= 4000
            names = [name for name in discovered if background_eligible(name)]
            modules = defaultdict(list)
            for name in names:
                stem = name.rsplit(".", 1)[0]
                stems = [stem]
                if stem.rsplit("/", 1)[-1] in {"__init__", "index", "mod"}:
                    stems.append(stem.rsplit("/", 1)[0])
                for value in stems:
                    parts = value.split("/")
                    for i in range(len(parts)):
                        modules["/".join(parts[i:])].append(name)
            self.module_paths = dict(modules)
            updated, total = {}, 0
            connection = self._cache()
            try:
                cached = (
                    dict(
                        (p, (s, t))
                        for p, s, t in connection.execute(
                            "SELECT path, stamp, text FROM files"
                        )
                    )
                    if connection and not self.documents
                    else {}
                )
                for name in names:
                    if self.stop.is_set():
                        return
                    try:
                        path = ws.path(name)
                        stat = path.stat()
                        if not path.is_file() or stat.st_size > MAX_FILE_BYTES:
                            continue
                        total += stat.st_size
                        if total > 24_000_000:
                            break
                        stamp = (
                            stat.st_mtime_ns,
                            stat.st_size,
                            stat.st_ino,
                            ignore_stamp(ws.root, name),
                        )
                        previous = self.documents.get(name)
                        if previous and previous.stamp == stamp:
                            updated[name] = previous
                            continue
                        saved = cached.get(name)
                        if saved and saved[0] == repr(stamp):
                            text = saved[1]
                        else:
                            with path.open("rb") as handle:
                                raw = handle.read(MAX_FILE_BYTES + 1)
                            if b"\0" in raw or len(raw) > MAX_FILE_BYTES:
                                continue
                            text = raw.decode("utf-8", errors="replace")
                        updated[name] = document(text, stamp)
                        if connection:
                            connection.execute(
                                "INSERT OR REPLACE INTO files VALUES (?, ?, ?)",
                                (name, repr(stamp), updated[name].text),
                            )
                    except (OSError, ValueError):
                        continue
                if connection:
                    for name in (
                        cached.keys() | self.documents.keys()
                    ) - updated.keys():
                        connection.execute("DELETE FROM files WHERE path = ?", (name,))
                    connection.commit()
            finally:
                if connection:
                    connection.close()
            changed = {k: v.stamp for k, v in updated.items()} != {
                k: v.stamp for k, v in self.documents.items()
            }
            if changed:
                postings = defaultdict(list)
                for name, doc in updated.items():
                    for i, chunk in enumerate(doc.chunks):
                        for token in chunk.tokens:
                            postings[token].append((name, i))
                self.documents, self.postings = updated, dict(postings)
                self.view = (self.documents, self.postings)
            self.revision += int(changed)
            self.limited = total > 24_000_000 or discovery_limited
            self.refresh_ms = round((time.monotonic() - started) * 1000, 1)
            self.error = None
            self.ready.set()

    def status(self):
        return {
            "ready": self.ready.is_set(),
            "files": len(self.documents),
            "limited": self.limited,
            "revision": self.revision,
            "refresh_ms": self.refresh_ms,
            "error": self.error,
        }

    def retrieve(
        self,
        ws: Workspace,
        snapshot: dict,
        overlays: dict[str, Document],
        recent=(),
        limit=6,
        budget=6000,
        query=None,
    ):
        if self.thread is not None and not self.ready.is_set():
            self.ready.wait(0.025)
        docs, postings = self.view
        path = snapshot["path"]
        focused = query is not None
        query = (
            query
            if focused
            else snapshot["prefix"][-1400:] + "\n" + snapshot.get("scope", "")
        )
        tokens = identifiers(query)
        imports = snapshot.get("imports", "")
        # Match Python module paths and relative JS/TS imports without guessing an API.
        modules = re.findall(
            r"(?:from|import)\s+([.\w/]+)|[\"']([.\w/-]+)[\"']", imports
        )
        hints = {
            part.replace(".", "/").strip("/")
            for pair in modules
            for part in pair
            if part
        }
        imported = {name for hint in hints for name in self.module_paths.get(hint, ())}
        # On a cold start the file list often arrives before full indexing. Read
        # only known, ignore-filtered imports (at most six) while indexing continues.
        cold = {}
        for name in sorted(imported - docs.keys())[:6]:
            try:
                target = ws.path(name)
                if target.stat().st_size <= MAX_FILE_BYTES:
                    with target.open("rb") as handle:
                        raw = handle.read(MAX_FILE_BYTES + 1)
                    if len(raw) <= MAX_FILE_BYTES and b"\0" not in raw:
                        cold[name] = document(raw.decode("utf-8", errors="replace"))
            except (OSError, ValueError):
                continue
        docs = dict(docs, **cold) if cold else docs
        candidates = set()
        for token in sorted(tokens, key=lambda t: len(postings.get(t, ())))[:32]:
            candidates.update(postings.get(token, ())[:300])
        for name in imported:
            if name in docs:
                candidates.update((name, i) for i in range(len(docs[name].chunks)))
        for name, doc in overlays.items():
            candidates.difference_update(
                (name, i) for i in range(len(docs.get(name, doc).chunks))
            )
            candidates.update((name, i) for i in range(len(doc.chunks)))
        ranking = []
        weights = {
            token: 1 + math.log(1 + len(docs) / (1 + len(postings.get(token, ()))))
            for token in tokens
        }
        for name, i in candidates:
            if name != path and name not in overlays and not background_eligible(name):
                continue
            if focused and imported and name not in imported and name != path:
                # An explicit import binding is stronger evidence than another
                # file defining a coincidentally identical symbol.
                continue
            doc = overlays.get(name) or docs.get(name)
            if not doc or i >= len(doc.chunks):
                continue
            chunk = doc.chunks[i]
            matches = tokens & chunk.tokens
            if not matches:
                continue
            if focused and name not in imported and not (tokens & chunk.symbols):
                continue
            if name == path:
                if (
                    chunk.line <= snapshot["row"] + 1 <= chunk.line + 40
                    or not tokens & chunk.symbols
                ):
                    continue
            score = sum(weights[t] for t in matches)
            score += 8 * len(tokens & chunk.symbols) + (8 if name in imported else 0)
            score += 2 if name in recent else 0
            score /= 1 + len(chunk.tokens) / 200
            if name != path and name not in imported:
                parts = set(Path(name).parts)
                if parts & {
                    "bench",
                    "tests",
                    "test",
                    "documentation",
                    "docs",
                } and not parts & set(Path(path).parts):
                    score *= 0.35
            ranking.append((score, name, chunk))
        result, ranges = [], defaultdict(list)
        for score, name, chunk in sorted(
            ranking, key=lambda x: (-x[0], x[1], x[2].line)
        ):
            if len(ranges[name]) >= 2 or any(
                abs(chunk.line - line) < 30 for line in ranges[name]
            ):
                continue
            try:
                target = ws.path(
                    name
                )  # Recheck exclusions even when the index predates them.
                if name not in overlays:
                    doc = docs[name]
                    stat = target.stat()
                    if doc.stamp and doc.stamp[3] != ignore_stamp(ws.root, name):
                        self.wake.set()
                        continue
                    if doc.stamp and doc.stamp[:3] != (
                        stat.st_mtime_ns,
                        stat.st_size,
                        stat.st_ino,
                    ):
                        self.wake.set()
                        if stat.st_size > MAX_FILE_BYTES:
                            continue
                        with target.open("rb") as handle:
                            raw = handle.read(MAX_FILE_BYTES + 1)
                        if b"\0" in raw or len(raw) > MAX_FILE_BYTES:
                            continue
                        fresh = document(raw.decode("utf-8", errors="replace"))
                        chunk = max(
                            fresh.chunks,
                            key=lambda c: len(tokens & c.tokens)
                            + 8 * len(tokens & c.symbols),
                            default=None,
                        )
                        if chunk is None or not tokens & chunk.tokens:
                            continue
            except (OSError, ValueError):
                continue
            if any(abs(chunk.line - line) < 30 for line in ranges[name]):
                continue
            if budget < 100:
                break
            text = scrub(chunk.text)[: min(2000, budget)]
            result.append(
                {
                    "path": name,
                    "line": chunk.line,
                    "text": text,
                    "reason": (
                        "import/definition"
                        if name in imported or tokens & chunk.symbols
                        else "related code"
                    ),
                    "unsaved": name in overlays,
                }
            )
            ranges[name].append(chunk.line)
            budget -= len(text)
            if len(result) >= limit:
                break
        return result


class EditorState:
    """Versioned overlays and bounded edit history shared by the two lanes."""

    def __init__(self, root):
        self.root = root
        self.lock = threading.RLock()
        self.buffers = OrderedDict()
        self.edits = []
        self.revision = 0

    def update(self, params):
        ws = Workspace(self.root)
        name = ws.path(params.get("path", "")).relative_to(ws.root).as_posix()
        with self.lock:
            if params.get("closed"):
                self.buffers.pop(name, None)
                self.edits = [edit for edit in self.edits if edit["path"] != name]
                self.revision += 1
                return
            text, version = params.get("text"), params.get("version")
            if (
                not isinstance(text, str)
                or len(text) > MAX_BUFFER_CHARS
                or "\0" in text
            ):
                raise ValueError("Invalid or oversized editor buffer")
            if not isinstance(version, int):
                raise ValueError("Buffer version must be an integer")
            previous = self.buffers.get(name)
            if previous and version < previous[0]:
                return
            if previous and previous[0] == version and previous[1] == text:
                return
            if previous:
                before, after = previous[1].splitlines(), text.splitlines()
                first = next(
                    (
                        i
                        for i, pair in enumerate(zip(before, after))
                        if pair[0] != pair[1]
                    ),
                    min(len(before), len(after)),
                )
                self.edits.append(
                    {
                        "path": name,
                        "line": first + 1,
                        "before": scrub("\n".join(before[first : first + 4]))[:500],
                        "after": scrub("\n".join(after[first : first + 4]))[:500],
                    }
                )
                self.edits = self.edits[-8:]
            self.buffers[name] = (version, text)
            self.buffers.move_to_end(name)
            while len(self.buffers) > 16:
                self.buffers.popitem(last=False)
            self.revision += 1

    def hydrate(self, params):
        ws = Workspace(self.root)
        result = dict(params)
        result["buffers"] = [dict(item) for item in params.get("buffers", [])[:12]]
        with self.lock:
            accepted = []
            for item in [result, *result.get("buffers", [])]:
                try:
                    name = ws.path(item.get("path", "")).relative_to(ws.root).as_posix()
                except (ValueError, OSError):
                    if item is result:
                        raise
                    continue
                if "text" in item:
                    accepted.append(item)
                    continue
                saved = self.buffers.get(name)
                if saved is None or saved[0] != item.get("version"):
                    if item is not result:
                        continue
                    raise ValueError(
                        "Editor buffer version is stale; synchronize again"
                    )
                item["text"] = saved[1]
                accepted.append(item)
            result["buffers"] = [item for item in accepted if item is not result]
            allowed = {
                ws.path(item["path"]).relative_to(ws.root).as_posix()
                for item in accepted
            }
            result["recent_edits"] = [
                dict(edit) for edit in self.edits if edit["path"] in allowed
            ]
        return result


def default_cache_dir():
    return (
        Path(os.environ.get("MUCLI_HOME", str(Path.home() / ".mucli")))
        / "editor"
        / "index"
    )
