"""Security audit tools.

Nine `@tool` handlers covering the full verified-PoC audit workflow:
create_security_report → add_security_finding → attach_security_proof
→ verify_security_proof → attach_remediation_patch → verify_remediation
→ approve_security_finding (or refute_security_finding). Bodies still
live in `core/tools.py` (they delegate to `core/security_mode.py`).
"""

from . import handlers  # noqa: F401 — registers 9 security tools at import time

__all__: list = []
