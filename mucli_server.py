#!/usr/bin/env python3
"""Dedicated server entrypoint for MuCLI."""

from __future__ import annotations

import sys

import mucli


if __name__ == "__main__":
    if "--server" not in sys.argv:
        sys.argv.append("--server")
    mucli.main()
