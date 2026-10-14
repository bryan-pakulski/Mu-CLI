"""Parallel tool dispatch.

When the model returns multiple tool calls in a single turn, current
production loops in this codebase execute them serially. This module
provides the building block for executing them concurrently while
preserving:

  * **Approval ordering** — approvals are still gathered serially (the
    user cannot review parallel prompts) BEFORE execution starts.
  * **Result ordering** — the returned list is in the same order as the
    input list, regardless of completion order.
  * **Concurrency bound** — a semaphore caps in-flight work; default 4.
  * **Collation ordering** — collated results are appended to the
    collation buffer in input order, not completion order.

`execute_calls()` is the thin coordinator used by `loop_body.run_turn`
when the model issues multiple parallelizable tool calls in one
provider response. It accepts an arbitrary `execute_one` callable so
it can be tested without needing a full Session.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple


logger = logging.getLogger("mucli")


@dataclass
class ToolCall:
    tool_name: str
    tool_args: Dict[str, Any]
    tool_call_id: Optional[str] = None
    thought_signature: Optional[str] = None
    # Caller-attached metadata (e.g. the approval plan for this call).
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    tool_name: str
    tool_args: Dict[str, Any]
    result: Any
    tool_call_id: Optional[str] = None
    thought_signature: Optional[str] = None
    error: Optional[BaseException] = None
    elapsed_ms: int = 0


async def _run_one(
    call: ToolCall,
    execute_one: Callable[[ToolCall], Any],
    semaphore: asyncio.Semaphore,
) -> ToolResult:
    """Wrap a single execution with the semaphore and a try/except.

    `execute_one` is allowed to be either synchronous or async. We detect
    a coroutine return and `await` it; otherwise we just use the value.
    Synchronous handlers run in a thread pool so they don't block other
    parallel calls.
    """

    import time

    async with semaphore:
        start = time.monotonic()
        try:
            if asyncio.iscoroutinefunction(execute_one):
                outcome = await execute_one(call)
            else:
                # Sync handler — run on a worker thread so the event loop
                # stays free for other parallel calls. Crucial: schedule
                # the call itself, not its result.
                outcome = await asyncio.get_running_loop().run_in_executor(
                    None, execute_one, call
                )
                if asyncio.iscoroutine(outcome):
                    # Defensive: handler returned a coroutine despite being
                    # a sync function. Await it.
                    outcome = await outcome
            elapsed = int((time.monotonic() - start) * 1000)
            return ToolResult(
                tool_name=call.tool_name,
                tool_args=call.tool_args,
                result=outcome,
                tool_call_id=call.tool_call_id,
                thought_signature=call.thought_signature,
                elapsed_ms=elapsed,
            )
        except BaseException as exc:  # noqa: BLE001
            elapsed = int((time.monotonic() - start) * 1000)
            logger.warning(
                "Parallel tool call %s raised %s", call.tool_name, exc
            )
            return ToolResult(
                tool_name=call.tool_name,
                tool_args=call.tool_args,
                result=None,
                tool_call_id=call.tool_call_id,
                thought_signature=call.thought_signature,
                error=exc,
                elapsed_ms=elapsed,
            )


async def execute_calls_async(
    calls: List[ToolCall],
    execute_one: Callable[[ToolCall], Any],
    *,
    max_concurrency: int = 4,
) -> List[ToolResult]:
    """Async coordinator: schedule every call, gather, return in input order.

    `execute_one(call)` can return either a value or a coroutine. The
    returned list is in the *same order* as `calls`.
    """
    if not calls:
        return []
    sem = asyncio.Semaphore(max(1, int(max_concurrency)))
    tasks = [
        asyncio.create_task(_run_one(c, execute_one, sem)) for c in calls
    ]
    return await asyncio.gather(*tasks)


def execute_calls(
    calls: List[ToolCall],
    execute_one: Callable[[ToolCall], Any],
    *,
    max_concurrency: int = 4,
) -> List[ToolResult]:
    """Sync wrapper around `execute_calls_async`.

    Starts (or reuses) an event loop. Suitable to call from synchronous
    code paths (the agent turn body is synchronous). If we are already
    inside a running loop, fall back to running on a fresh loop in a
    worker thread.
    """
    if not calls:
        return []

    try:
        asyncio.get_running_loop()
        # We're inside a running loop; punt to a worker thread.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                lambda: asyncio.run(
                    execute_calls_async(
                        calls, execute_one, max_concurrency=max_concurrency
                    )
                )
            ).result()
    except RuntimeError:
        # No running loop — use asyncio.run directly.
        return asyncio.run(
            execute_calls_async(
                calls, execute_one, max_concurrency=max_concurrency
            )
        )


# --------------------------------------------------------------- safety set

# Tools that are safe to dispatch concurrently. The defining property: each
# invocation's effect is independent of the others within a single turn.
#
# Inclusions:
#   * Read-only tools (workspace + research) — pure reads, no shared mutation.
#   * `spawn_agent` — every child gets its own SessionManager / scratchpad /
#     memory store, so concurrent children don't race on harness state. They
#     can still race on workspace writes if the model spawns conflicting
#     children, but that is a model-level decision, not a harness invariant.
#
# Exclusions (NOT in this set; run serially):
#   * `bash` — a shell command can perform arbitrary workspace/process
#     mutations; a subprocess with its own cwd does NOT isolate filesystem
#     effects, so two concurrent bash calls can clobber the same file or
#     race on repo state.
#   * Filesystem writes (`write_file`, `apply_diff`, `search_and_replace_file`)
#     — two concurrent edits of the same file would silently clobber.
#   * Memory / scratchpad mutators — `BaseNoteStore` increments a shared
#     `_next_id` counter without a lock.
#   * Feature-mode mutators — shared feature plan state machine.
#   * `flush` — must be a barrier so it sees all preceding collation writes.
#   * `raise_blocker` — control-flow signal; serial keeps the order obvious.
PARALLEL_SAFE_TOOLS = frozenset(
    {
        # Workspace reads
        "read_file",
        "list_dir",
        "get_chunk",
        "get_workspace_details",
        "search_for_string",
        "search_references",
        "retrieve_relevant_context",
        # Research / network reads
        "web_search",
        "arxiv_search",
        "doi_resolve",
        "reddit_search",
        "stackoverflow_search",
        "hackernews_search",
        "url_grounding",
        "read_document",
        # Sub-agents — each has its own SessionManager
        "spawn_agent",
        # Memory reads
        "search_memory",
        "list_memory",
        "search_scratchpad",
        "list_scratchpad",
    }
)


def is_parallel_safe(tool_name: str) -> bool:
    return bool(tool_name) and tool_name in PARALLEL_SAFE_TOOLS


# Read-only shell commands that may run concurrently with other reads (harness latency
# plan, Phase 4). Anything else — writes, redirects, pipes into other programs, command
# chaining, subshells — stays serial.
READ_ONLY_COMMANDS = frozenset({"ls", "cat", "head", "tail", "wc", "rg", "grep", "egrep", "fgrep", "find", "stat",
                                "file", "tree", "du", "pwd", "which", "realpath", "basename", "dirname", "sha256sum",
                                "md5sum", "diff", "jq"})
READ_ONLY_GIT = frozenset({"status", "diff", "log", "show", "blame", "ls-files", "rev-parse", "branch", "grep"})
_SHELL_META = ("&&", "||", ";", "`", "$(", ">", "<", "\n", "&")
_FIND_WRITES = ("-delete", "-exec", "-execdir", "-ok", "-fprint", "-fprintf", "-fls")


def is_read_only_command(command: str) -> bool:
    """True for a single allow-listed read-only command, optionally piped into other
    read-only commands (``rg foo | head``)."""
    import shlex
    text = str(command or "").strip()
    if not text or any(meta in text for meta in _SHELL_META):
        return False
    for segment in text.split("|"):
        try:
            argv = shlex.split(segment)
        except ValueError:
            return False
        if not argv:
            return False
        name = argv[0].rsplit("/", 1)[-1]
        if name == "git":
            sub = next((a for a in argv[1:] if not a.startswith("-")), "")
            if sub not in READ_ONLY_GIT or any(a in ("--output", "-o") or a.startswith("--output=") for a in argv):
                return False
            if sub == "branch" and any(a.startswith("-") and a not in ("-a", "--all", "-r", "-v", "-vv", "--list") for a in argv[2:]):
                return False
        elif name not in READ_ONLY_COMMANDS:
            return False
        if name == "find" and any(a in _FIND_WRITES for a in argv):
            return False
        if name == "tree" and any(a == "-o" or a.startswith("-o") and len(a) > 2 for a in argv[1:]):
            return False
        if name == "rg" and any(a == "--pre" or a.startswith("--pre=") for a in argv):
            return False
    return True


def is_parallel_safe_call(tool_name: str, tool_args: Any = None, *, approvals_needed: bool = True) -> bool:
    """Per-call parallel safety: the static set, plus read-only ``bash`` when no
    approval prompt would be needed (approval dialogs must stay on the agent thread)."""
    if is_parallel_safe(tool_name):
        return True
    if tool_name == "bash" and not approvals_needed:
        command = (tool_args or {}).get("command") if isinstance(tool_args, dict) else ""
        return is_read_only_command(str(command or ""))
    return False


def execution_segments(calls: Sequence[Tuple[str, Any]], safe) -> List[Tuple[List[int], List[int]]]:
    """Order-preserving plan: consecutive parallel-safe calls form one concurrent group
    (≥2 calls), everything else runs serially in place. Returns ``[(parallel, serial)]``
    segments, each with exactly one non-empty side."""
    segments: List[Tuple[List[int], List[int]]] = []
    run: List[int] = []

    def close_run() -> None:
        if len(run) >= 2:
            segments.append((list(run), []))
        elif run:
            segments.append(([], list(run)))
        run.clear()
    for index, (name, args) in calls:
        if name != "flush" and safe(name, args):
            run.append(index)
            continue
        close_run()
        if segments and segments[-1][1] and not segments[-1][0]:
            segments[-1][1].append(index)
        else:
            segments.append(([], [index]))
    close_run()
    return segments


__all__ = [
    "PARALLEL_SAFE_TOOLS",
    "ToolCall",
    "ToolResult",
    "execute_calls",
    "execute_calls_async",
    "is_parallel_safe",
]
