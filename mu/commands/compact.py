"""Manual compaction: `/compact [focus]`.

Summarizes older conversation history into the L2 conversation summary to
free context, preserving recent tool results and `[cache:KEY]` recall tags.
Optional `focus` text steers what the summary keeps front-of-mind (Claude
Code's `/compact <focus>` style). Backed by `mu.agent.compactor.manual_compact`.
"""

from typing import Any

from . import CommandResult, command


@command(
    "/compact",
    help="Summarize older history to free context. Optional focus steers what to preserve.",
)
def compact_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    from mu.agent.compactor import manual_compact

    focus = (args or "").strip()
    result = manual_compact(session, focus=focus)

    if not result.get("ok"):
        msg = f"Compaction failed: {result.get('error', 'unknown error')}"
        if allow_prompt:
            _print(session, msg)
        return CommandResult(ok=False, message=msg, data=result)

    if not result.get("compacted"):
        msg = "Nothing to compact — history is already caught up to the keep-recent boundary."
    else:
        b, a = result["before"], result["after"]
        msg = (
            f"Compacted L5 history: {b['est_tokens']} -> {a['est_tokens']} est tokens "
            f"(messages {b['history_len']} -> {a['history_len']}, "
            f"summary anchor {b['summary_anchor']} -> {a['summary_anchor']})."
        )
        if result.get("focus"):
            msg += f" Focus: {result['focus']}"

    if allow_prompt:
        _print(session, msg)
    return CommandResult(ok=True, message=msg, data=result)


def _print(session: Any, msg: str) -> None:
    ui = getattr(session, "ui", None)
    console = getattr(ui, "console", None) if ui is not None else None
    if console is not None:
        try:
            console.print(msg)
        except Exception:  # noqa: BLE001 — never let rendering block the command
            pass