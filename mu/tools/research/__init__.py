"""Research / web-data tools.

``handlers`` holds search (candidates), reading (url_grounding,
research_fetch, read_document) and assess_source; ``ledger_tools`` the
evidence-ledger tools (questions, claims, evidence, status, report, export).
This module is the registration surface.
"""

from . import handlers  # noqa: F401 — registers search/read tools at import time
from . import ledger_tools  # noqa: F401 — registers the evidence-ledger tools

__all__: list = []
