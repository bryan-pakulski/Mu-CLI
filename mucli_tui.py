#!/usr/bin/env python3
"""Dedicated TUI client entrypoint for MuCLI."""

from __future__ import annotations

import sys

import mucli


if __name__ == "__main__":
    # TUI client mode should never force --server.
    sys.argv = [arg for arg in sys.argv if arg != "--server"]
    mucli.main()
