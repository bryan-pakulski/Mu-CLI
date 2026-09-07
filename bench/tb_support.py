"""Helpers shared by the Terminal-Bench adapter and its local tests.

This module intentionally has no ``terminal_bench`` dependency.  Keeping the
source snapshot and trace parsing here lets the normal MuCLI test environment
exercise benchmark-critical behavior without installing Terminal-Bench.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any

_SOURCE_EXCLUDES = (
    "bench/artifacts/",
    "bench/results/",
)

# A benchmark of an in-progress worktree must include new runtime modules, not
# only files that have already been staged in Git.  Limit untracked inclusion
# to project runtime roots so local notes, credentials, and unrelated scratch
# files can never be copied into an arbitrary Terminal-Bench container.
_UNTRACKED_RUNTIME_ROOTS = (
    "config/",
    "mu/",
    "providers/",
    "utils/",
)
_UNTRACKED_SECRET_NAMES = {
    ".env",
    ".netrc",
    "credentials.json",
    "id_ed25519",
    "id_rsa",
    "secrets.json",
    "service-account.json",
}
_UNTRACKED_SECRET_SUFFIXES = (".key", ".p12", ".pfx", ".pem")

_TASK_TIMEOUT_RE = re.compile(
    r"^max_agent_timeout_sec:\s*([0-9]+(?:\.[0-9]+)?)\s*(?:#.*)?$",
    re.MULTILINE,
)


def _git(repo: Path, *args: str, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=text,
    )


def _safe_untracked_runtime_path(rel_text: str) -> bool:
    """Whether an untracked, non-ignored file is safe runtime source."""

    posix = PurePosixPath(rel_text)
    if not any(rel_text.startswith(prefix) for prefix in _UNTRACKED_RUNTIME_ROOTS):
        return False
    if any(
        part in {"__pycache__", ".pytest_cache", ".mypy_cache"} for part in posix.parts
    ):
        return False
    name = posix.name.lower()
    if name in _UNTRACKED_SECRET_NAMES or name.startswith(".env."):
        return False
    return not name.endswith(_UNTRACKED_SECRET_SUFFIXES)


def _source_paths(repo: Path) -> tuple[list[Path], list[Path]]:
    """Return tracked files plus narrowly scoped untracked runtime source."""

    tracked_raw = _git(repo, "ls-files", "--cached", "-z", text=False).stdout
    untracked_raw = _git(
        repo,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        text=False,
    ).stdout
    paths: list[Path] = []
    untracked: list[Path] = []
    tracked_items = {item for item in tracked_raw.split(b"\0") if item}
    items = [(item, False) for item in tracked_items]
    items.extend(
        (item, True)
        for item in untracked_raw.split(b"\0")
        if item and item not in tracked_items
    )
    for item, is_untracked in items:
        if not item:
            continue
        rel_text = os.fsdecode(item)
        posix = PurePosixPath(rel_text)
        if posix.is_absolute() or ".." in posix.parts:
            raise ValueError(f"unsafe tracked path: {rel_text!r}")
        if any(rel_text.startswith(prefix) for prefix in _SOURCE_EXCLUDES):
            continue
        if is_untracked and not _safe_untracked_runtime_path(rel_text):
            continue
        rel = Path(*posix.parts)
        target = repo / rel
        # Deleted tracked files should be absent from the worktree snapshot.
        if target.is_file() or target.is_symlink():
            paths.append(rel)
            if is_untracked:
                untracked.append(rel)
    key = lambda path: path.as_posix()
    return sorted(paths, key=key), sorted(untracked, key=key)


def _snapshot_metadata(
    repo: Path,
    paths: list[Path],
    untracked_paths: list[Path],
) -> dict[str, Any]:
    digest = hashlib.sha256()
    for rel in paths:
        target = repo / rel
        digest.update(rel.as_posix().encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(f"{target.lstat().st_mode & 0o7777:o}".encode("ascii"))
        digest.update(b"\0")
        if target.is_symlink():
            digest.update(b"symlink\0")
            digest.update(os.readlink(target).encode("utf-8", errors="surrogateescape"))
        else:
            digest.update(b"file\0")
            with target.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    digest.update(chunk)
        digest.update(b"\0")

    commit = _git(repo, "rev-parse", "HEAD").stdout.strip()
    dirty = bool(
        _git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip()
    )
    return {
        "schema": 1,
        "git_commit": commit,
        "tracked_worktree_dirty": dirty,
        "included_untracked_files": [path.as_posix() for path in untracked_paths],
        "included_untracked_count": len(untracked_paths),
        "source_sha256": digest.hexdigest(),
        "file_count": len(paths),
    }


def source_snapshot_metadata(repo: Path) -> dict[str, Any]:
    """Describe the tracked and safe-untracked runtime worktree snapshot."""

    repo = Path(repo).resolve()
    paths, untracked = _source_paths(repo)
    return _snapshot_metadata(repo, paths, untracked)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_archive_metadata(path: Path) -> dict[str, Any]:
    """Read source metadata and bind it to the exact archive bytes."""

    path = Path(path)
    with tarfile.open(path, "r:gz") as archive:
        raw = archive.extractfile(".mucli-benchmark-source.json")
        if raw is None:
            raise ValueError("source archive has no benchmark metadata")
        metadata = json.load(raw)
    if not isinstance(metadata, dict):
        raise ValueError("source archive benchmark metadata is invalid")
    return {
        **metadata,
        "archive_sha256": file_sha256(path),
        "archive_path": str(path.resolve()),
    }


def build_source_tarball(repo: Path, output: Path | None = None) -> Path:
    """Create a safe snapshot of the current runtime worktree.

    ``git archive HEAD`` silently omitted in-progress fixes.  This snapshot
    reads tracked files from the worktree and safe untracked files from runtime
    roots, so modified and newly added modules are benchmarked. Ignored files,
    arbitrary untracked files, and likely credentials remain excluded.
    """

    repo = repo.resolve()
    paths, untracked = _source_paths(repo)
    metadata = _snapshot_metadata(repo, paths, untracked)

    if output is None:
        handle = tempfile.NamedTemporaryFile(
            prefix="mucli-bench-", suffix=".tar.gz", delete=False
        )
        handle.close()
        output = Path(handle.name)
        temporary = output
    else:
        output = Path(output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            prefix=f".{output.name}.", suffix=".tmp", dir=output.parent, delete=False
        )
        handle.close()
        temporary = Path(handle.name)
    try:
        with tarfile.open(temporary, mode="w:gz") as archive:
            for rel in paths:
                archive.add(
                    repo / rel,
                    arcname=rel.as_posix(),
                    recursive=False,
                )
            payload = (json.dumps(metadata, sort_keys=True) + "\n").encode("utf-8")
            info = tarfile.TarInfo(".mucli-benchmark-source.json")
            info.size = len(payload)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(payload))
        # Refuse to freeze a mixed snapshot if source changed while the archive
        # was being assembled. A subsequent worktree edit is harmless because
        # every trial consumes the immutable archive, not live source.
        if source_snapshot_metadata(repo) != metadata:
            raise RuntimeError("MuCLI source changed while freezing benchmark archive")
        if temporary != output:
            temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return output


def read_trace_usage(logging_dir: Path | None) -> tuple[int, int]:
    """Return token totals from the newest usable MuCLI trace, if any.

    Completed traces expose authoritative cumulative totals in ``run_end`` or
    ``turn_end``.  A command killed exactly at its execution deadline may have
    neither, so retain a sum of its completed ``iter`` records as a fallback.
    A completed trace is still preferred over a newer incomplete trace; this
    avoids counting an abandoned startup attempt when a later run completed.
    """

    if logging_dir is None:
        return 0, 0
    logging_dir = Path(logging_dir)
    # TB 0.2.18 advertises /agent-logs but its core compose files only mount
    # /logs, whose host path is the sibling ``sessions`` directory. Newer task
    # definitions may mount the dedicated agent directory, so support both.
    trace_dirs = (
        logging_dir / "mucli" / "trace",
        logging_dir.parent / "sessions" / "mucli" / "trace",
    )
    candidates = sorted(
        (path for directory in trace_dirs for path in directory.glob("*.jsonl")),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    incomplete_fallback: tuple[int, int] | None = None
    for path in candidates:
        latest: tuple[int, int] | None = None
        iter_input = 0
        iter_output = 0
        iter_count = 0
        try:
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        event = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if event.get("type") == "run_end":
                        try:
                            latest = (
                                int(event.get("tokens_in") or 0),
                                int(event.get("tokens_out") or 0),
                            )
                        except (TypeError, ValueError):
                            continue
                    elif event.get("type") == "turn_end" and latest is None:
                        try:
                            latest = (
                                int(event.get("total_in") or 0),
                                int(event.get("total_out") or 0),
                            )
                        except (TypeError, ValueError):
                            continue
                    elif event.get("type") == "iter":
                        tokens = event.get("tokens")
                        if not isinstance(tokens, dict):
                            continue
                        try:
                            iter_input += int(tokens.get("in") or 0)
                            iter_output += int(tokens.get("out") or 0)
                            iter_count += 1
                        except (TypeError, ValueError):
                            continue
        except OSError:
            continue
        if latest is not None:
            return latest
        if incomplete_fallback is None and iter_count:
            incomplete_fallback = (iter_input, iter_output)
    return incomplete_fallback or (0, 0)


def read_task_execution_timeout(task_yaml: Path) -> float:
    """Read the task's native agent budget without adding a YAML dependency."""

    try:
        text = Path(task_yaml).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read task config: {task_yaml}") from exc
    match = _TASK_TIMEOUT_RE.search(text)
    if match is None:
        raise ValueError(f"missing max_agent_timeout_sec in {task_yaml}")
    timeout = float(match.group(1))
    if timeout <= 0:
        raise ValueError(f"invalid max_agent_timeout_sec in {task_yaml}")
    return timeout


def normalize_ollama_cloud_model(model_name: str) -> str:
    """Return the model ID expected by Ollama's native CLI integrations.

    LiteLLM identifies Ollama Cloud models as ``ollama/<name>`` while
    ``ollama launch`` exposes the same hosted model as ``<name>:cloud``.
    Keeping this conversion in one place prevents comparator runners from
    silently selecting a different model.
    """

    value = (model_name or "").strip()
    if value.startswith("ollama/"):
        value = value.split("/", 1)[1]
    if not value:
        raise ValueError("an Ollama model name is required")
    return value if value.endswith(":cloud") else f"{value}:cloud"


def external_harness_environment(
    harness: str,
    model_name: str,
    api_key: str,
) -> dict[str, str]:
    """Build the direct-cloud equivalent of ``ollama launch`` configuration.

    The local Ollama relay normally emits these settings. Benchmark containers
    connect to Ollama Cloud directly so their networking does not depend on a
    host-only loopback listener. Secrets are returned only as environment
    values and are never included in provenance.
    """

    model = normalize_ollama_cloud_model(model_name)
    if not api_key:
        raise ValueError("OLLAMA_API_KEY is required")
    if harness == "opencode":
        config = {
            "$schema": "https://opencode.ai/config.json",
            "model": f"ollama/{model}",
            "provider": {
                "ollama": {
                    "models": {
                        model: {
                            "name": model,
                            "reasoning": True,
                            "modalities": {
                                "input": ["text", "image"],
                                "output": ["text"],
                            },
                            "limit": {"context": 1048576, "output": 1048576},
                            "variants": {
                                "high": {"disabled": True},
                                "low": {"disabled": True},
                                "medium": {"disabled": True},
                                "none": {"reasoningEffort": "none"},
                            },
                        }
                    },
                    "name": "Ollama",
                    "npm": "@ai-sdk/openai-compatible",
                    "options": {
                        "baseURL": "https://ollama.com/v1",
                        "apiKey": api_key,
                    },
                }
            },
        }
        return {"OPENCODE_CONFIG_CONTENT": json.dumps(config, separators=(",", ":"))}
    if harness == "claude-code":
        return {
            "ANTHROPIC_AUTH_TOKEN": api_key,
            "ANTHROPIC_API_KEY": "",
            "ANTHROPIC_BASE_URL": "https://ollama.com",
        }
    if harness == "pi":
        return {
            "OLLAMA_API_KEY": api_key,
            "PI_CODING_AGENT_DIR": "/logs/pi",
        }
    raise ValueError(f"unsupported external harness: {harness}")


def pi_ollama_models_config(model_name: str) -> dict[str, Any]:
    """Return Pi's secret-free Ollama Cloud model configuration."""

    model = normalize_ollama_cloud_model(model_name)
    return {
        "providers": {
            "ollama": {
                "api": "openai-completions",
                "apiKey": "$OLLAMA_API_KEY",
                "baseUrl": "https://ollama.com/v1",
                "models": [
                    {
                        "_launch": True,
                        "contextWindow": 1048576,
                        "id": model,
                        "input": ["text", "image"],
                        "reasoning": True,
                    }
                ],
            }
        }
    }


def read_external_cli_usage(harness: str, output: str) -> tuple[int, int]:
    """Extract cumulative request tokens from a controlled CLI JSONL stream."""

    input_tokens = 0
    output_tokens = 0
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        usage: Any = None
        if harness == "opencode" and event.get("type") == "step_finish":
            part = event.get("part")
            if isinstance(part, dict):
                usage = part.get("tokens")
        elif harness == "claude-code" and event.get("type") == "result":
            usage = event.get("usage")
        elif harness == "pi" and event.get("type") == "message_end":
            message = event.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        try:
            input_tokens += int(usage.get("input_tokens", usage.get("input", 0)) or 0)
            output_tokens += int(
                usage.get("output_tokens", usage.get("output", 0)) or 0
            )
        except (TypeError, ValueError):
            continue
    return input_tokens, output_tokens


def make_container_logs_host_cleanable(session) -> None:
    """Make bind-mounted benchmark logs readable and removable by the host.

    Terminal-Bench task containers can create files as a container-only user,
    including mode-0600 CLI state.  Normalizing the complete log mount during
    teardown keeps generated result trees disposable.  Cleanup is best-effort
    and must never change a benchmark outcome.
    """

    try:
        session.container.exec_run(
            ["sh", "-c", "chmod -R a+rwX /logs 2>/dev/null || true"]
        )
    except Exception:
        pass


def stop_mucli_process(
    session,
    *,
    polls_per_signal: int = 20,
    poll_interval: float = 0.1,
    sleep=time.sleep,
) -> None:
    """Stop a timed-out MuCLI process before Terminal-Bench starts grading."""

    try:
        session.send_keys(["C-c"], block=False, min_timeout_sec=0.0)
    except Exception:
        pass

    def is_running() -> bool:
        result = session.container.exec_run(
            ["sh", "-c", "pgrep -f '[/]opt/mucli/mucli.py' >/dev/null 2>&1"]
        )
        return result.exit_code == 0

    for signal_name in ("TERM", "KILL"):
        for _ in range(polls_per_signal):
            if not is_running():
                return
            sleep(poll_interval)
        session.container.exec_run(
            [
                "sh",
                "-c",
                f"pkill -{signal_name} -f '[/]opt/mucli/mucli.py' "
                "2>/dev/null || true",
            ]
        )


__all__ = [
    "build_source_tarball",
    "file_sha256",
    "external_harness_environment",
    "make_container_logs_host_cleanable",
    "normalize_ollama_cloud_model",
    "pi_ollama_models_config",
    "read_external_cli_usage",
    "read_task_execution_timeout",
    "read_trace_usage",
    "source_archive_metadata",
    "source_snapshot_metadata",
    "stop_mucli_process",
]
