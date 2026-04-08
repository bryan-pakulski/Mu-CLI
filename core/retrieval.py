import os
import re
import time
import math
import subprocess
from dataclasses import dataclass, field
from collections import Counter
from typing import Any


TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SYMBOL_RE = re.compile(
    r"^\s*(?:def|class|function|const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)


def _tokenize(text: str) -> list[str]:
    tokens = []
    for token in TOKEN_RE.findall(str(text or "")):
        lowered = token.lower()
        tokens.append(lowered)
        if "_" in lowered:
            tokens.extend([part for part in lowered.split("_") if part])
    return tokens


@dataclass
class IndexedDocument:
    path: str
    mtime: float
    size: int
    symbols: set[str] = field(default_factory=set)
    token_counts: Counter = field(default_factory=Counter)
    snippet: str = ""


class SemanticCodeIndex:
    """Lightweight semantic index over workspace files.

    Scoring combines lexical overlap, symbol overlap, recency, and git-diff boost.
    """

    def __init__(self):
        self.documents: dict[str, IndexedDocument] = {}
        self._last_refresh_latency_ms: float = 0.0
        self.workspace_root: str = os.getcwd()

    @property
    def last_refresh_latency_ms(self) -> float:
        return self._last_refresh_latency_ms

    def build(self, folder_context) -> None:
        self.workspace_root = self._workspace_root(folder_context=folder_context)
        changed = self._collect_changed_files(folder_context, include_deleted=True)
        for path in changed["deleted"]:
            self.documents.pop(path, None)
        for path in changed["upsert"]:
            doc = self._index_file(path)
            if doc:
                self.documents[path] = doc
        self._last_refresh_latency_ms = changed["latency_ms"]

    def refresh_incremental(self, folder_context) -> None:
        self.build(folder_context)

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        filters = filters or {}
        query_tokens = _tokenize(query)
        query_counts = Counter(query_tokens)
        query_symbols = {token for token in query_tokens if token in set(query_tokens)}
        changed_paths = self._git_changed_paths(self.workspace_root)
        candidates = []
        now = time.time()

        path_prefix = str(filters.get("path_prefix", "") or "").strip()
        extensions = {
            str(ext).lower() for ext in (filters.get("extensions") or []) if str(ext).strip()
        }

        for doc in self.documents.values():
            if path_prefix and path_prefix not in doc.path:
                continue
            if extensions and os.path.splitext(doc.path)[1].lower() not in extensions:
                continue

            lexical = 0.0
            if query_counts:
                lexical_hits = sum(
                    min(doc.token_counts.get(token, 0), count)
                    for token, count in query_counts.items()
                )
                lexical = lexical_hits / max(1, sum(query_counts.values()))

            symbol_overlap = 0.0
            if query_symbols:
                symbol_overlap = len(doc.symbols.intersection(query_symbols)) / max(1, len(query_symbols))

            recency = 1.0 / (1.0 + max(0.0, (now - doc.mtime) / 3600.0))
            git_boost = 0.3 if doc.path in changed_paths else 0.0
            score = (0.65 * lexical) + (0.20 * symbol_overlap) + (0.15 * recency) + git_boost

            candidates.append(
                {
                    "path": doc.path,
                    "score": round(score, 6),
                    "snippet": doc.snippet,
                    "symbols": sorted(doc.symbols)[:12],
                    "features": {
                        "lexical": round(lexical, 4),
                        "symbol_overlap": round(symbol_overlap, 4),
                        "recency": round(recency, 4),
                        "git_boost": round(git_boost, 4),
                    },
                }
            )

        ranked = sorted(candidates, key=lambda item: item["score"], reverse=True)[: max(1, int(top_k or 5))]
        return {
            "query": query,
            "top_k": max(1, int(top_k or 5)),
            "count": len(ranked),
            "latency_ms": round(self._last_refresh_latency_ms, 3),
            "results": ranked,
        }

    def _collect_changed_files(self, folder_context, include_deleted: bool = True) -> dict[str, Any]:
        start = time.perf_counter()
        tracked = set()
        deleted = []

        for folder in getattr(folder_context, "folders", []) or []:
            for root, dirs, files in os.walk(folder):
                dirs[:] = [d for d in dirs if not folder_context.is_ignored(os.path.join(root, d))]
                for filename in files:
                    full_path = os.path.join(root, filename)
                    if folder_context.is_ignored(full_path):
                        continue
                    tracked.add(full_path)

        known_paths = set(self.documents.keys())
        if include_deleted:
            deleted = sorted(known_paths - tracked)
        upsert = []
        for path in tracked:
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            existing = self.documents.get(path)
            size = os.path.getsize(path)
            if (
                not existing
                or not math.isclose(existing.mtime, mtime)
                or int(existing.size) != int(size)
            ):
                upsert.append(path)

        latency_ms = (time.perf_counter() - start) * 1000.0
        return {"upsert": sorted(upsert), "deleted": deleted, "latency_ms": latency_ms}

    def _index_file(self, path: str) -> IndexedDocument | None:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                content = handle.read()
        except Exception:
            return None

        symbols = {match.group(1) for match in SYMBOL_RE.finditer(content)}
        tokens = Counter(_tokenize(content))
        lines = [line.rstrip() for line in content.splitlines() if line.strip()]
        snippet = "\n".join(lines[:12])[:1200]
        try:
            mtime = os.path.getmtime(path)
            size = os.path.getsize(path)
        except OSError:
            mtime = time.time()
            size = len(content.encode("utf-8", errors="ignore"))
        return IndexedDocument(
            path=path,
            mtime=mtime,
            size=size,
            symbols=symbols,
            token_counts=tokens,
            snippet=snippet,
        )

    def _workspace_root(self, folder_context=None, filters: dict[str, Any] | None = None) -> str:
        if filters and filters.get("_workspace_root"):
            return str(filters["_workspace_root"])
        if folder_context and getattr(folder_context, "folders", None):
            return os.path.abspath(folder_context.folders[0])
        return os.getcwd()

    def _git_changed_paths(self, workspace_root: str) -> set[str]:
        root = os.path.abspath(workspace_root)
        try:
            result = subprocess.run(
                ["git", "-C", root, "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=1.0,
            )
        except Exception:
            return set()
        if result.returncode != 0:
            return set()
        changed = set()
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            rel = line[3:].strip()
            if rel:
                changed.add(os.path.abspath(os.path.join(root, rel)))
        return changed
