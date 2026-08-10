"""Visibility rules for user-facing session discovery.

Durable engineering jobs deliberately reuse MuCLI Session persistence so they
can resume the normal agent runtime and retain full harness traces.  Those
execution sessions are implementation detail, however, and should not pollute
lists of conversations created by the user.

Visibility is therefore orthogonal to persistence: exact session lookup/load is
unchanged; only discovery surfaces call these predicates.
"""

from __future__ import annotations

import re
from typing import Any, Mapping


# Historical durable-job sessions used the deterministic logical name
# ``job-<first 20 chars of job id>`` before an explicit metadata marker was
# guaranteed on every saved session.  Keep the fallback deliberately narrow so
# a normal user session called e.g. ``job-notes`` is never hidden.
_LEGACY_DURABLE_JOB_NAME = re.compile(r"^job-[0-9a-f]{20}$", re.IGNORECASE)


def is_durable_job_session(name: str, data: Mapping[str, Any] | None = None) -> bool:
    """Return True when a persisted session belongs to a durable job runtime."""

    session_name = str(name or "").strip()
    variables: Mapping[str, Any] = {}
    if isinstance(data, Mapping):
        raw_variables = data.get("variables")
        if isinstance(raw_variables, Mapping):
            variables = raw_variables

    if str(variables.get("internal_session_kind") or "").strip().lower() == "durable_job":
        return True
    if str(variables.get("durable_job_id") or "").strip():
        return True
    return bool(_LEGACY_DURABLE_JOB_NAME.fullmatch(session_name))


def is_user_visible_session(name: str, data: Mapping[str, Any] | None = None) -> bool:
    """Return whether a session belongs in ordinary user conversation lists."""

    return not is_durable_job_session(name, data)


__all__ = ["is_durable_job_session", "is_user_visible_session"]
