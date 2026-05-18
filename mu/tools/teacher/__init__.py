"""Teacher-mode `@tool` registrations.

Importing this module triggers the `@tool` decorators in `handlers.py`,
which mirror the descriptors and handlers into `mu.tools.descriptors` /
`mu.tools._dispatcher` so the agent loop can dispatch them.
"""

from mu.tools.teacher import handlers as _handlers  # noqa: F401

__all__: list[str] = []
