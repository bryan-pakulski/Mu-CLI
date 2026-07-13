"""Research / web-data tools.

Eight `@tool` handlers for fetching web pages, academic papers, forum
posts, and structured Q&A. Implementations live in
`mu/tools/research/handlers.py`; this module is the registration surface.
"""

from . import handlers  # noqa: F401 — registers 8 research tools at import time

__all__: list = []
