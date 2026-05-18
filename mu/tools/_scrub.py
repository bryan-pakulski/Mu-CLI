"""Output-scrubber wiring for tool results.

`scrub_and_annotate(text)` runs the secret-pattern redactor from
`core/secret_paths.py` over a tool's output and appends a one-line
notice when anything was redacted, so the model can see that the
result was sanitized.

Wire-in points (all in `core/tools.py`):
  * `read_file` body              — file contents
  * `bash_command` body           — combined stdout/stderr
  * `get_chunk` body              — partial-file reads
  * `search_for_string` body      — grep results
  * `search_references` body      — grep with context

The redaction itself lives in `core/secret_paths.py:redact_secrets`
(pattern coverage: AWS, GitHub, GitLab, Slack, Anthropic, OpenAI,
Google, JWT, PEM blocks). This module is the *wiring* — when and how
the redacted-count notice gets appended.

Tests: `tests/test_secret_scrubber.py`,
`tests/test_search_for_string_fix.py`.
"""

from __future__ import annotations

from typing import Any


def scrub_and_annotate(text: Any) -> Any:
    """Redact known secret patterns from `text` and, when anything was
    redacted, append a `[security: redacted N secret(s) from output]`
    trailer so the model knows the result was sanitized."""
    from core.secret_paths import redact_secrets

    if not isinstance(text, str) or not text:
        return text
    scrubbed, n = redact_secrets(text)
    if n > 0:
        scrubbed = (
            f"{scrubbed}\n\n"
            f"[security: redacted {n} secret(s) from output]"
        )
    return scrubbed


__all__ = ["scrub_and_annotate"]
