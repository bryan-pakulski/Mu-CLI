"""Rubric checks for benchmark scoring.

Each `Rubric` evaluates against a completed `BenchmarkRun` and returns
a `RubricResult`. Concrete rubrics cover the common patterns:

  * `FileContains`     — workspace file contains a substring
  * `FileRegex`        — workspace file matches a regex
  * `FileNotContains`  — anti-pattern: file does NOT contain text
  * `CommandSucceeds`  — running a shell command in the workspace exits 0
  * `MaxToolCalls`     — agent stayed under a tool-call budget
  * `MaxSeconds`       — agent stayed under a wall-clock budget
  * `ResponseContains` — final assistant text contains a substring
  * `ResponseMatches`  — final assistant text matches a regex

Each rubric carries a `weight` (default 1.0). The benchmark's total
score is `sum(passed_weights) / sum(all_weights)`.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .spec import BenchmarkRun


@dataclass
class RubricResult:
    name: str
    passed: bool
    weight: float
    score: float  # 0.0 or `weight` for boolean; can be partial for graded
    message: str = ""


class Rubric(ABC):
    """Base class. Subclasses set `name` and `weight` and implement `evaluate`."""

    weight: float = 1.0

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @abstractmethod
    def evaluate(self, run: "BenchmarkRun") -> RubricResult:
        raise NotImplementedError

    # ------------------------------------------------------------ helpers

    def _result(self, *, passed: bool, message: str = "", score: float = None) -> RubricResult:
        if score is None:
            score = self.weight if passed else 0.0
        return RubricResult(
            name=self.name,
            passed=passed,
            weight=self.weight,
            score=score,
            message=message,
        )

    def _read_workspace_file(self, run: "BenchmarkRun", relative_path: str) -> str:
        """Read a file relative to the run's workspace. Returns empty string if missing."""
        full_path = os.path.join(run.workspace_path, relative_path)
        try:
            with open(full_path, "r", encoding="utf-8") as fh:
                return fh.read()
        except (OSError, UnicodeDecodeError):
            return ""


# --------------------------------------------------------------- file content


class FileContains(Rubric):
    """Workspace file (relative path) contains `needle` as a substring."""

    def __init__(self, file: str, needle: str, *, weight: float = 1.0):
        self.file = file
        self.needle = needle
        self.weight = weight

    @property
    def name(self) -> str:
        return f"FileContains({self.file!r}, {self.needle!r})"

    def evaluate(self, run):
        content = self._read_workspace_file(run, self.file)
        if not content:
            return self._result(passed=False, message=f"file not found or empty: {self.file}")
        if self.needle in content:
            return self._result(passed=True, message="found")
        return self._result(passed=False, message=f"substring not found")


class FileNotContains(Rubric):
    """Anti-pattern: workspace file does NOT contain `needle`."""

    def __init__(self, file: str, needle: str, *, weight: float = 1.0):
        self.file = file
        self.needle = needle
        self.weight = weight

    @property
    def name(self) -> str:
        return f"FileNotContains({self.file!r}, {self.needle!r})"

    def evaluate(self, run):
        content = self._read_workspace_file(run, self.file)
        if self.needle in content:
            return self._result(passed=False, message="anti-pattern present")
        return self._result(passed=True, message="anti-pattern absent")


class FileRegex(Rubric):
    """Workspace file matches `pattern` (regex, re.MULTILINE)."""

    def __init__(self, file: str, pattern: str, *, weight: float = 1.0):
        self.file = file
        self.pattern = pattern
        self._compiled = re.compile(pattern, re.MULTILINE)
        self.weight = weight

    @property
    def name(self) -> str:
        return f"FileRegex({self.file!r}, {self.pattern!r})"

    def evaluate(self, run):
        content = self._read_workspace_file(run, self.file)
        if not content:
            return self._result(passed=False, message=f"file not found or empty: {self.file}")
        if self._compiled.search(content):
            return self._result(passed=True, message="regex matched")
        return self._result(passed=False, message="regex did not match")


# --------------------------------------------------------------- commands


class CommandSucceeds(Rubric):
    """Run a shell command in the workspace; pass iff exit code is 0.

    The command runs with `cwd=run.workspace_path`. Use this for tests:

        CommandSucceeds("pytest test_calc.py -q", timeout=30)
    """

    def __init__(self, command: str, *, weight: float = 1.0, timeout: float = 60.0):
        self.command = command
        self.weight = weight
        self.timeout = timeout

    @property
    def name(self) -> str:
        return f"CommandSucceeds({self.command!r})"

    def evaluate(self, run):
        if not run.workspace_path:
            return self._result(passed=False, message="no workspace_path")
        try:
            proc = subprocess.run(
                self.command,
                shell=True,
                cwd=run.workspace_path,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return self._result(passed=False, message=f"timeout after {self.timeout}s")
        except Exception as exc:  # pragma: no cover — defensive
            return self._result(passed=False, message=f"exec failed: {exc}")
        if proc.returncode == 0:
            return self._result(passed=True, message=f"exit 0 ({len(proc.stdout)} stdout chars)")
        # Trim stderr to a useful preview.
        stderr_preview = (proc.stderr or "").strip().splitlines()
        tail = "\n".join(stderr_preview[-5:])
        return self._result(
            passed=False,
            message=f"exit {proc.returncode}; stderr tail:\n{tail[:400]}",
        )


# --------------------------------------------------------------- budgets


class MaxToolCalls(Rubric):
    """Pass iff `run.tool_call_count <= limit`.

    Lower budgets are stricter — set the weight low and the limit
    aggressively if you only care about efficiency as a soft signal.
    """

    def __init__(self, limit: int, *, weight: float = 0.5):
        self.limit = int(limit)
        self.weight = weight

    @property
    def name(self) -> str:
        return f"MaxToolCalls({self.limit})"

    def evaluate(self, run):
        actual = run.tool_call_count
        if actual <= self.limit:
            return self._result(passed=True, message=f"{actual} <= {self.limit}")
        return self._result(passed=False, message=f"{actual} > {self.limit}")


class MaxSeconds(Rubric):
    """Pass iff wall-clock elapsed <= limit. Useful for tracking regressions
    when model providers slow down or when prompts bloat."""

    def __init__(self, limit: float, *, weight: float = 0.5):
        self.limit = float(limit)
        self.weight = weight

    @property
    def name(self) -> str:
        return f"MaxSeconds({self.limit})"

    def evaluate(self, run):
        if run.elapsed_seconds <= self.limit:
            return self._result(
                passed=True, message=f"{run.elapsed_seconds:.1f}s <= {self.limit}s"
            )
        return self._result(
            passed=False, message=f"{run.elapsed_seconds:.1f}s > {self.limit}s"
        )


# --------------------------------------------------------------- response text


class ResponseContains(Rubric):
    """Final assistant text contains `needle` (substring, case-insensitive)."""

    def __init__(self, needle: str, *, weight: float = 1.0, case_sensitive: bool = False):
        self.needle = needle
        self.weight = weight
        self.case_sensitive = case_sensitive

    @property
    def name(self) -> str:
        return f"ResponseContains({self.needle!r})"

    def evaluate(self, run):
        haystack = run.final_response or ""
        if not self.case_sensitive:
            haystack = haystack.lower()
            needle = self.needle.lower()
        else:
            needle = self.needle
        if needle in haystack:
            return self._result(passed=True, message="found")
        return self._result(passed=False, message="not found")


class ResponseMatches(Rubric):
    """Final assistant text matches `pattern` (regex)."""

    def __init__(self, pattern: str, *, weight: float = 1.0):
        self.pattern = pattern
        self._compiled = re.compile(pattern, re.MULTILINE | re.IGNORECASE)
        self.weight = weight

    @property
    def name(self) -> str:
        return f"ResponseMatches({self.pattern!r})"

    def evaluate(self, run):
        if self._compiled.search(run.final_response or ""):
            return self._result(passed=True, message="regex matched")
        return self._result(passed=False, message="regex did not match")


__all__ = [
    "CommandSucceeds",
    "FileContains",
    "FileNotContains",
    "FileRegex",
    "MaxSeconds",
    "MaxToolCalls",
    "ResponseContains",
    "ResponseMatches",
    "Rubric",
    "RubricResult",
]
