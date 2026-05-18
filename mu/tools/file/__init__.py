"""File mutator tools (write_file, apply_diff, search_and_replace_file).

All three are write-side and `requires_approval=True`. They flow through
the same approval / diff-preview pipeline as before; only the
registration location moved.
"""

from . import handlers  # noqa: F401 — registers the 3 mutator tools at import time

__all__: list = []
