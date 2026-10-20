"""The built-in starter pack (plan §7): tools that make a fresh master useful.

Read-only reports (disk, memory, services, pending updates, …) plus the node maintenance
tools behind the node page's Maintenance panel (``os.reboot``, ``os.security-updates``,
``os.full-upgrade``, ``os.service``: mutating, check-mode capable, run through the access
policy and the node's own policy like any tool). Each directory under ``tools/`` holds a
``tool.yaml`` manifest plus its files; they are installed (signed and published) by
``installStarterPack`` and double as house-style examples for the AI.
"""

from __future__ import annotations

import os
from typing import Dict, Iterator, Tuple

import yaml

TOOLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")


def load_starter_tools() -> Iterator[Tuple[dict, Dict[str, bytes]]]:
    for name in sorted(os.listdir(TOOLS_DIR)):
        folder = os.path.join(TOOLS_DIR, name)
        manifest_path = os.path.join(folder, "tool.yaml")
        if not os.path.isfile(manifest_path):
            continue
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = yaml.safe_load(fh)
        files = {}
        for fname in sorted(os.listdir(folder)):
            if fname != "tool.yaml" and os.path.isfile(os.path.join(folder, fname)):
                with open(os.path.join(folder, fname), "rb") as fh:
                    files[fname] = fh.read()
        yield manifest, files
