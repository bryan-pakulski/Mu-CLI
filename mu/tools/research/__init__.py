"""Research / web-data tools.

Eight `@tool` handlers for fetching web pages, academic papers, forum
posts, and structured Q&A. Implementations stay in `core/tools.py`;
this module is the registration surface.
"""

from . import handlers  # noqa: F401 — registers 8 research tools at import time

__all__: list = []
