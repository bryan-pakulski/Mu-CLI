"""Provider-call retry with exponential backoff.

`provider_generate_with_retry(session, ...)` wraps a provider.stream()
call with retry-on-transient-error semantics. Transient errors are
classified by message-string heuristics and known HTTP status codes;
the loop is bounded by a cumulative-wait budget (default 120s) plus a
hard max-attempts ceiling (default 30) as a safety belt.

The retry loop also drives the `pre_provider_call` / `post_provider_call`
hook points and the `_HookAbort` exception that lets a hook stop the
turn before the provider is contacted.

Backoff with the defaults (base=0.4, max=30, budget=120):
    attempt 1: ~0.4s   (total ~0.4s)
    attempt 2: ~0.8s   (total ~1.2s)
    attempt 3: ~1.6s   (total ~2.8s)
    attempt 4: ~3.2s   (total ~6.0s)
    attempt 5: ~6.4s   (total ~12.4s)
    attempt 6: ~12.8s  (total ~25.2s)
    attempt 7: ~25.6s  (total ~50.8s)
    attempt 8+: 30s capped
    stops once cumulative >= 120s.

Tests: `tests/test_provider_retry.py` (5 regression pins).
"""

from __future__ import annotations

import random
import re
import time
from typing import Any, Optional

from .hooks import HookContext, default_registry


# ---------------------------------------------------------------- error classification


_TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "temporarily unavailable",
    "temporary failure",
    "rate limit",
    "429",
    "502",
    "503",
    "504",
    "connection reset",
    "connection aborted",
    "network",
    "econnreset",
    "service unavailable",
    "try again",
    "overloaded",
    "capacity",
    "server error",
    "internal server error",
    "bad gateway",
    "gateway timeout",
    "server is",
)


_RETRYABLE_HTTP_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


def is_transient_provider_error(error: Exception) -> bool:
    """Classify whether `error` is worth retrying. String-match against
    known transient markers, then fall back to extracting an HTTP status
    code from the message."""
    message = str(error or "").lower()
    if any(marker in message for marker in _TRANSIENT_MARKERS):
        return True
    status = extract_http_status_code(message)
    if status is not None:
        return status in _RETRYABLE_HTTP_STATUS
    return False


def extract_http_status_code(message: str) -> Optional[int]:
    """Pull a 3-digit HTTP status code out of a provider error message.

    Patterns matched in order: `HTTP Error: 503`, `status_code=429`,
    bare `503`. Returns None if no plausible status is found."""
    patterns = (
        r"http error[: ]+(\d{3})",
        r"status_code[=: ]+(\d{3})",
        r"\b(?:http\s*)?(\d{3})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, message)
        if not match:
            continue
        try:
            code = int(match.group(1))
            if 100 <= code <= 599:
                return code
        except (TypeError, ValueError):
            continue
    return None


# ---------------------------------------------------------------- retry loop


def provider_generate_with_retry(
    session: Any,
    *,
    messages,
    system_prompt,
    thinking,
    tools,
):
    """Call `session.provider.stream(...)` with retry on transient
    errors. Fires `pre_provider_call` / `post_provider_call` hooks and
    honors `_HookAbort`.

    Returns the drained `ProviderResponse`. Re-raises any non-transient
    exception, and re-raises the last transient exception once the
    budget or attempt cap is exhausted.
    """
    # Lazy imports to keep this module cheap at import time.
    from mu.ui.stream import build_default_renderer
    import mu.agent.compactor  # noqa: F401 — registers auto-compaction hook
    import mu.agent.plan_mode  # noqa: F401 — registers plan-mode pre_tool hook
    import mu.agent.usage_tracker  # noqa: F401 — registers per-session usage hooks
    import mu.agent.secret_guard  # noqa: F401 — registers bash secret-guard hook
    from mu.session.session import _HookAbort

    base_delay = float(
        session.variables.get("provider_retry_base_delay", 0.4) or 0.4
    )
    max_delay = float(
        session.variables.get("provider_retry_max_delay", 30.0) or 30.0
    )
    total_budget_s = float(
        session.variables.get("provider_retry_max_total_wait_seconds", 120.0)
        or 120.0
    )
    max_attempts = max(
        1, int(session.variables.get("provider_max_retries", 30) or 30)
    )

    attempt = 0
    elapsed = 0.0

    while True:
        try:
            pre_ctx = HookContext(
                point="pre_provider_call",
                session=session,
                variables=session.variables,
                messages=messages,
                system_prompt=system_prompt,
                tools=tools,
            )
            _, _, abort = default_registry.fire_with_signals(
                "pre_provider_call", pre_ctx
            )
            if abort is not None:
                session._record_hook_abort("pre_provider_call", abort)
                raise _HookAbort(session._hook_abort_reason)

            renderer = build_default_renderer(session.ui)
            events = session.provider.stream(
                messages=messages,
                system_prompt=system_prompt,
                thinking=thinking,
                tools=tools,
            )
            response = renderer.consume(session.provider, events)
            post_ctx = HookContext(
                point="post_provider_call",
                session=session,
                variables=session.variables,
                messages=messages,
                system_prompt=system_prompt,
                response=response,
            )
            _, _, abort = default_registry.fire_with_signals(
                "post_provider_call", post_ctx
            )
            if abort is not None:
                session._record_hook_abort("post_provider_call", abort)
            return response
        except Exception as exc:
            # Delegate the transient-error classification to the session.
            # Tests monkeypatch `Session._is_transient_provider_error`
            # to inject test-specific transient/non-transient policies,
            # so we must consult it via the session rather than calling
            # the module-level helper directly.
            classify = getattr(
                session, "_is_transient_provider_error", None
            ) or is_transient_provider_error
            if not classify(exc):
                raise
            if elapsed >= total_budget_s or attempt >= max_attempts:
                # Budget exhausted — bubble up so the outer turn loop
                # can surface a clear failure instead of stalling forever.
                if session.ui:
                    session.ui.show_error(
                        f"Provider retry budget exhausted "
                        f"({attempt} attempts, {elapsed:.1f}s slept). Aborting."
                    )
                raise
            attempt += 1
            # Exponential backoff with jitter, capped at max_delay.
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += random.uniform(0, min(1.0, delay * 0.25))
            # Don't oversleep past the remaining budget.
            remaining = max(0.0, total_budget_s - elapsed)
            delay = max(0.05, min(delay, remaining))
            if session.ui:
                session.ui.show_info(
                    f"Transient provider error; retry {attempt} "
                    f"in {delay:.1f}s ({elapsed:.1f}s of "
                    f"{total_budget_s:.0f}s budget used)."
                )
            time.sleep(delay)
            elapsed += delay


__all__ = [
    "is_transient_provider_error",
    "extract_http_status_code",
    "provider_generate_with_retry",
]
