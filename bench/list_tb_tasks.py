#!/usr/bin/env python3
"""Read task IDs from the pinned MuCLI Terminal-Bench suite."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


DEFAULT_SUITE = Path(__file__).with_name("tb_suite.yaml")
_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def load_task_ids(path: Path = DEFAULT_SUITE) -> list[str]:
    """Return the ``task_ids`` list without requiring a YAML dependency."""

    tasks: list[str] = []
    in_task_ids = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if raw_line == "task_ids:":
            in_task_ids = True
            continue
        if not in_task_ids:
            continue
        if raw_line and not raw_line[0].isspace() and not raw_line.startswith("#"):
            break
        match = re.match(r"^  - (\S+)\s*(?:#.*)?$", raw_line)
        if match:
            tasks.append(match.group(1))

    if not tasks:
        raise ValueError(f"no task_ids found in {path}")
    invalid = [task for task in tasks if not _TASK_ID.fullmatch(task)]
    if invalid:
        raise ValueError(f"invalid task IDs in {path}: {invalid}")
    if len(tasks) != len(set(tasks)):
        raise ValueError(f"duplicate task IDs in {path}")
    return tasks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    args = parser.parse_args()
    try:
        tasks = load_task_ids(args.suite)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print("\n".join(tasks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
