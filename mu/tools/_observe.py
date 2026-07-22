"""Per-tool compact observers (spec #2, #3, #10).

When a raw tool result exceeds its inline token budget, the full raw is
stored externally (ResultStore) and a *compact structured observation* is
put in the model context instead. This module builds that observation per
tool kind:

  * status (ok/fail — already on the structured envelope)
  * material outcome (short summary)
  * important facts (counts, parsed structure)
  * unique errors and warnings (deduped diagnostics)
  * files / external state changed
  * small exact excerpts needed for evidence (head+tail within budget)
  * a reference to the full stored result (``stored_ref`` — added by caller)
  * an explicit note when content was omitted

Small results (at or below the inline budget) stay verbatim — no
observation transform is applied. Observers are best-effort and must never
raise into the loop.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

# Reuse the diagnostic/noise patterns from the result store so the in-context
# observation and the on-disk `diagnostics` op agree on what counts as a signal.
from mu.session.result_store import _DIAGNOSTIC_RE, _NOISE_RE


def resolve_inline_budget(tool_name: str, is_error: bool, variables: Any) -> int:
    """Per-tool inline token budget (spec #10). Failures get a larger budget."""
    try:
        budgets = variables.get("tool_inline_budgets", {}) or {}
        if tool_name in budgets:
            return max(32, int(budgets[tool_name]))
    except Exception:  # noqa: BLE001
        pass
    try:
        if is_error:
            return max(32, int(variables.get("tool_result_failure_budget", 1024) or 1024))
        return max(32, int(variables.get("tool_result_inline_budget", 256) or 256))
    except Exception:  # noqa: BLE001
        return 256


def _excerpt(raw_text: str, budget_tokens: int) -> str:
    """A head+tail excerpt sized to ~budget_tokens. Keeps the first and last
    lines so the model sees how the output started and ended, with a clear
    cut marker in the middle. Cheap char-based budgeting (~4 chars/token)."""
    if not raw_text:
        return ""
    char_budget = max(120, budget_tokens * 4)
    if len(raw_text) <= char_budget:
        return raw_text
    head = char_budget // 2
    tail = char_budget // 2
    head_txt = raw_text[:head]
    tail_txt = raw_text[-tail:]
    omitted = len(raw_text) - head - tail
    return f"{head_txt}\n…[omitted {omitted} chars; full result stored]…\n{tail_txt}"


def _unique_diagnostics(raw_text: str, max_lines: int = 12) -> list:
    """Unique diagnostic lines (errors/warnings), noise dropped, deduped."""
    seen: list = []
    for line in raw_text.splitlines():
        if _NOISE_RE.match(line):
            continue
        if not _DIAGNOSTIC_RE.search(line):
            continue
        if line in seen:
            continue
        seen.append(line)
        if len(seen) >= max_lines:
            break
    return seen


def _parse_bash_exit(raw_text: str) -> Optional[int]:
    """Best-effort exit code parse from bash output (exit=<n> / [exit N])."""
    m = re.search(r"exit[_= ]+(\d+)", raw_text, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def build_observation(
    tool_name: str,
    tool_args: Any,
    raw_text: str,
    data: Dict[str, Any],
    *,
    budget_tokens: int,
    is_error: bool,
) -> Tuple[Dict[str, Any], str]:
    """Augment the structured ``data`` with a compact observation and return
    ``(augmented_data, omitted_note)``. The caller sets ``raw=None`` and adds
    ``stored_ref``/``retrievable_via`` separately (it knows the cache key).

    The existing per-tool ``data`` (built by ``build_structured_tool_result``)
    already carries typed metadata — preview, counts, parsed matches, modified
    files. This adds the observation-only fields the spec calls out that aren't
    already present: diagnostics, an evidence excerpt, and the omission note.
    """
    obs: Dict[str, Any] = dict(data)
    omitted_bytes = len(raw_text)
    note = f"[content omitted: {omitted_bytes} bytes; full result stored — use recall(stored_ref) or result_* ops]"

    if tool_name == "bash":
        diags = _unique_diagnostics(raw_text)
        exit_code = _parse_bash_exit(raw_text)
        obs["diagnostics"] = diags
        if exit_code is not None:
            obs["exit_code"] = exit_code
        # Bash output is often long logs; the excerpt is the evidence.
        obs["excerpt"] = _excerpt(raw_text, budget_tokens)
    elif tool_name in {"read_file", "get_chunk", "read_document"}:
        # Keep the existing preview (requested/relevant range). Add a small
        # evidence excerpt only if there's room beyond the preview.
        obs["excerpt"] = _excerpt(raw_text, budget_tokens)
    elif tool_name in {"search_for_string", "search_references"}:
        # Match grouping already in data via _parse_search_results; the
        # excerpt gives line-level evidence without the full content.
        obs["excerpt"] = _excerpt(raw_text, budget_tokens)
    elif tool_name in {"write_file", "apply_diff", "search_and_replace_file"}:
        # modified_files already in data; excerpt the diff/write confirmation.
        obs["excerpt"] = _excerpt(raw_text, budget_tokens)
    else:
        # Default: an excerpt is the compact evidence.
        obs["excerpt"] = _excerpt(raw_text, budget_tokens)
        if is_error:
            obs["diagnostics"] = _unique_diagnostics(raw_text)

    obs["omitted"] = True
    obs["omitted_bytes"] = omitted_bytes
    return obs, note


RETRIEVABLE_VIA = (
    "recall(cache_key) | result_range | result_head | result_tail | "
    "result_search | result_diagnostics | result_json_path | compare_results"
)