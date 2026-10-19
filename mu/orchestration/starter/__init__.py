"""The built-in starter pack (plan §7): read-only tools that make a fresh master useful.

Each directory under ``tools/`` holds a ``tool.yaml`` manifest plus its files. They are
installed (signed and published) on first use and double as house-style examples for the AI.
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
