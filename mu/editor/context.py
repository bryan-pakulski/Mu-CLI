"""Bounded, ignore-aware workspace context for editor requests."""

from __future__ import annotations

import json
import fnmatch
from pathlib import Path
import re
import subprocess
import threading
import time

from mu.security.secret_paths import is_denied_path, redact_secrets

MAX_FILE_BYTES = 200_000
MAX_BUFFER_CHARS = 500_000


def background_eligible(name: str) -> bool:
    """Generated/vendor bundles are opt-in context, still available via read."""
    path = Path(name)
    return not (
        set(path.parts)
        & {"vendor", "node_modules", "dist", "build", "coverage", "generated", ".cache"}
        or path.name.endswith((".min.js", ".min.css", ".map", ".lock"))
        or path.name in {"package-lock.json", "yarn.lock", "pnpm-lock.yaml"}
    )


def scrub(value: str) -> str:
    return redact_secrets(value)[0]


class Workspace:
    def __init__(self, root: str):
        self.root = Path(root).expanduser().resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Workspace must be a directory")
        self._files: list[str] = []
        self._refreshed = 0.0
        self._ignore_stamp = None
        self._ignore_patterns: list[str] = []
        self.overlays: dict[str, str] = {}

    def path(self, name: str) -> Path:
        if not isinstance(name, str) or not name:
            raise ValueError("A workspace file path is required")
        raw = self.root / name
        path = raw.resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Path is outside the editor workspace")
        # is_denied_path checks both original and resolved basenames itself.
        if is_denied_path(str(raw))[0] or any(
            part in {".git", ".ssh", ".aws", ".gnupg"}
            for part in path.relative_to(self.root).parts
        ):
            raise ValueError("Secret or internal path is excluded")
        relatives = {path.relative_to(self.root).as_posix()}
        if raw.is_relative_to(self.root):
            relatives.add(raw.relative_to(self.root).as_posix())
        if self.ignore_file.is_file():
            stamp = self.ignore_file.stat().st_mtime_ns
            if stamp != self._ignore_stamp:
                with self.ignore_file.open() as handle:
                    self._ignore_patterns = [
                        line.strip().rstrip("/")
                        for line in handle.read(32000).splitlines()
                        if line.strip() and not line.strip().startswith("#")
                    ]
                self._ignore_stamp = stamp
            for pattern in self._ignore_patterns:
                if any(
                    fnmatch.fnmatch(relative, pattern)
                    or relative.startswith(pattern + "/")
                    or any(
                        fnmatch.fnmatch(part, pattern) for part in Path(relative).parts
                    )
                    for relative in relatives
                ):
                    raise ValueError("Path is excluded by .mucliignore")
        return path

    @property
    def ignore_file(self) -> Path:
        return self.root / ".mucliignore"

    def files(self) -> list[str]:
        if time.monotonic() - self._refreshed < 10:
            return self._files
        command = [
            "rg",
            "--files",
            "--hidden",
            "-0",
            "-g",
            "!.git",
            "-g",
            "!node_modules",
            "-g",
            "!.venv",
            "-g",
            "!__pycache__",
        ]
        if self.ignore_file.is_file():
            command += ["--ignore-file", str(self.ignore_file)]
        # File list output is capped even in very large workspaces.
        with subprocess.Popen(
            command, cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        ) as proc:
            timer = threading.Timer(5, proc.kill)
            timer.start()
            try:
                raw = proc.stdout.read(512_000)
            finally:
                timer.cancel()
                proc.kill()
                proc.wait()
        names = raw.split(b"\0")[:-1]
        self._files = []
        for item in names[:4000]:
            name = item.decode("utf-8", errors="replace")
            try:
                self.path(name)
            except (ValueError, OSError):
                continue
            self._files.append(name)
        self._refreshed = time.monotonic()
        return self._files

    def read(self, name: str, start: int = 1, count: int = 80) -> dict:
        path = self.path(name)
        relative = path.relative_to(self.root).as_posix()
        if relative in self.overlays:
            raw = self.overlays[relative].encode()
        else:
            if path.stat().st_size > MAX_FILE_BYTES or not path.is_file():
                raise ValueError("File exceeds editor context size limit")
            with path.open("rb") as handle:
                raw = handle.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError("File exceeds editor context size limit")
        if b"\0" in raw:
            raise ValueError("Binary files are excluded")
        lines = raw.decode("utf-8", errors="replace").splitlines()
        start = max(1, int(start))
        count = max(1, min(120, int(count)))
        return {
            "path": str(path.relative_to(self.root)),
            "line": start,
            "end_line": min(len(lines), start + count - 1),
            "text": scrub(
                "\n".join(
                    f"{i + 1}: {lines[i]}"
                    for i in range(start - 1, min(len(lines), start - 1 + count))
                )
            )[:12000],
        }

    def search(self, query: str, limit: int = 12) -> list[dict]:
        tokens = set(re.findall(r"[\w]+", query.lower())) - {
            "the",
            "how",
            "does",
            "this",
            "that",
            "with",
            "from",
            "what",
            "where",
            "and",
            "for",
            "explain",
            "code",
            "cursor",
            "its",
            "relevant",
            "decisions",
            "design",
            "dependencies",
            "callers",
            "please",
            "can",
            "you",
            "about",
            "are",
            "a",
            "an",
        }
        tokens = {token for token in tokens if len(token) > 1}
        if not tokens:
            return []
        results = []
        remaining = 12_000_000
        for name in dict.fromkeys([*self.overlays, *self.files()]):
            if (
                not background_eligible(name)
                and name.lower() not in query.lower()
                and name not in self.overlays
            ):
                continue
            try:
                path = self.path(name)
                if name in self.overlays:
                    raw = self.overlays[name].encode()
                else:
                    if path.stat().st_size > MAX_FILE_BYTES or not path.is_file():
                        continue
                    with path.open("rb") as handle:
                        raw = handle.read(MAX_FILE_BYTES + 1)
                remaining -= len(raw)
                if remaining < 0:
                    break
                if len(raw) > MAX_FILE_BYTES:
                    continue
                if b"\0" in raw:
                    continue
                lines = raw.decode("utf-8", errors="replace").splitlines()
                best = (0, 0)
                for index, line in enumerate(lines):
                    words = set(re.findall(r"[\w]+", line.lower()))
                    score = len(tokens & words)
                    if score > best[0]:
                        best = (score, index)
                if best[0]:
                    start = max(0, best[1] - 3)
                    results.append(
                        {
                            "path": name,
                            "line": best[1] + 1,
                            "score": best[0],
                            "text": scrub(
                                "\n".join(
                                    f"{i + 1}: {lines[i]}"
                                    for i in range(start, min(len(lines), start + 12))
                                )
                            )[:1800],
                        }
                    )
            except (OSError, ValueError):
                continue
        return sorted(results, key=lambda item: (-item["score"], item["path"]))[:limit]

    def snapshot(self, params: dict) -> dict:
        self.overlays = {}
        path = self.path(params.get("path", ""))
        text = params.get("text", "")
        if not isinstance(text, str) or len(text) > MAX_BUFFER_CHARS:
            raise ValueError("Buffer exceeds editor context size limit")
        if "\0" in text:
            raise ValueError("Binary buffers are excluded")
        row, col = params.get("row", 0), params.get("col", 0)
        lines = text.split("\n")
        if (
            not isinstance(row, int)
            or not isinstance(col, int)
            or not 0 <= row < len(lines)
        ):
            raise ValueError("Invalid cursor position")
        # Neovim columns are byte offsets, not Python character offsets.
        line_bytes = lines[row].encode("utf-8")
        if not 0 <= col <= len(line_bytes):
            raise ValueError("Invalid cursor column")
        try:
            before = line_bytes[:col].decode("utf-8")
            after = line_bytes[col:].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Cursor splits a UTF-8 character") from exc
        prefix = "\n".join(lines[:row] + [before])
        suffix = "\n".join([after] + lines[row + 1 :])
        if scrub(prefix) + scrub(suffix) != scrub(text):
            raise ValueError(
                "Cursor intersects secret material; editor context excluded"
            )
        self.overlays[path.relative_to(self.root).as_posix()] = text
        related = []
        remaining = 4_000_000
        for buf in params.get("buffers", [])[:12]:
            try:
                other = self.path(buf["path"])
                value = buf["text"]
                if (
                    not isinstance(value, str)
                    or "\0" in value
                    or len(value) > MAX_BUFFER_CHARS
                ):
                    continue
                remaining -= len(value)
                if remaining < 0:
                    break
                self.overlays[other.relative_to(self.root).as_posix()] = value
                related.append(
                    {
                        "path": str(other.relative_to(self.root)),
                        "text": scrub(value)[:3000],
                    }
                )
            except (KeyError, ValueError):
                continue
        return {
            "path": str(path.relative_to(self.root)),
            "filetype": str(params.get("filetype", ""))[:80],
            "row": row,
            "prefix": scrub(prefix)[-12000:],
            "suffix": scrub(suffix)[:4000],
            "buffers": related,
            "diagnostics": scrub(json.dumps(params.get("diagnostics", [])[:20]))[:4000],
        }
