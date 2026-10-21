"""mucli-node: the standard-library-only node agent (plan §6.2, §9).

Everything in this package imports only the Python standard library and sibling modules, so it
ships as a single zipapp (``mucli-node.pyz``) to any Linux host with Python 3.10+. The master
imports the shared primitives (``canonical``, ``schema``, ``envelope``) from here so both sides
encode, hash and validate identically.
"""

AGENT_VERSION = "1.3.1"     # 1.3.0: utilisation samples (history charts); 1.3.1: a failed needs-restarting is not a reboot
# The first agent that can update itself (older ones are updated once by hand).
SELF_UPDATE_SINCE = "1.1.0"


def version_tuple(text: str) -> tuple:
    """``"1.10.2"`` → ``(1, 10, 2)``; anything unparseable sorts lowest."""
    try:
        return tuple(int(p) for p in str(text or "").strip().split("."))
    except ValueError:
        return (0,)
