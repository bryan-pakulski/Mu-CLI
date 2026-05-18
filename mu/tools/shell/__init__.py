"""Shell tools (bash + background-task family).

Six `@tool`-registered handlers:

  * `bash`              — synchronous one-shot shell command
  * `bash_background`   — fire-and-poll long-running task
  * `bash_status`       — poll a bg task's state + tail
  * `bash_logs`         — read a bg task's stdout/stderr buffer
  * `bash_kill`         — terminate a bg task
  * `bash_list`         — list every bg task in the session

`bash` and the bg mutators (`bash_background`, `bash_kill`) carry
`requires_approval=True`. The background-task registry is resolved
from `context.session.background_tasks`; a process-global fallback
keeps the handlers usable from session-less unit tests.
"""

from . import handlers  # noqa: F401 — registers the 6 tools at import time

__all__: list = []
