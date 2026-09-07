#!/usr/bin/env python3
"""Write reproducibility metadata for an external CLI baseline run."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench.tb_support import file_sha256, normalize_ollama_cloud_model
from bench.write_tb_provenance import _prepared_images, _tb_version

_VERSIONS = {
    "opencode": "1.18.4",
    "claude-code": "2.1.211",
    "pi": "0.85.1",
}


def _ollama_version() -> str:
    try:
        result = subprocess.run(["ollama", "--version"], capture_output=True, text=True)
    except OSError:
        return "unavailable"
    return result.stdout.strip() or result.stderr.strip() or "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--prepared-manifest", type=Path, required=True)
    parser.add_argument("--harness", choices=sorted(_VERSIONS), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--attempts", type=int, required=True)
    parser.add_argument("--run-label", default="")
    parser.add_argument("--tb", type=Path, required=True)
    parser.add_argument("--setup-allowance-seconds", type=float, required=True)
    parser.add_argument("--outer-cleanup-margin-seconds", type=float, required=True)
    parser.add_argument("tasks", nargs="+")
    args = parser.parse_args()

    repo = args.repo.resolve()
    artifacts = repo / "bench" / "artifacts" / "agents"
    selected = {
        "opencode": [artifacts / "opencode" / "1.18.4" / "opencode"],
        "claude-code": [artifacts / "claude-code" / "2.1.211" / "claude"],
        "pi": [
            artifacts / "pi" / "pi-0.85.1.tar.gz",
            artifacts / "node" / "22.23.2" / "node-v22.23.2-linux-x64.tar.gz",
        ],
    }[args.harness]
    harness_files = [
        repo / "benchmark.sh",
        repo / "bench" / "run_cli_pack.sh",
        repo / "bench" / "tb_cli_agent.py",
        repo / "bench" / "tb_support.py",
        repo / "bench" / "summarize_tb.py",
        repo / "bench" / "write_cli_provenance.py",
    ]
    payload = {
        "schema": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_label": args.run_label,
        "harness": {
            "name": args.harness,
            "version": _VERSIONS[args.harness],
            "adapter_version": "controlled-cli-1",
            "model_requested": args.model,
            "model_native": normalize_ollama_cloud_model(args.model),
            "provider": "ollama-cloud",
            "endpoint": "https://ollama.com",
            "artifacts": [
                {"path": str(path.resolve()), "sha256": file_sha256(path)}
                for path in selected
            ],
            "files_sha256": {
                str(path.relative_to(repo)): file_sha256(path) for path in harness_files
            },
        },
        "terminal_bench": {
            "version": _tb_version(args.tb),
            "dataset": "terminal-bench-core==0.1.1",
            "dataset_path": str(args.dataset.resolve()),
            "tasks": args.tasks,
            "attempts_per_task": args.attempts,
            "n_concurrent": 1,
            "setup_allowance_seconds": args.setup_allowance_seconds,
            "outer_cleanup_margin_seconds": args.outer_cleanup_margin_seconds,
            "execution_time_excludes_agent_setup": True,
        },
        "task_images": _prepared_images(args.prepared_manifest, args.tasks),
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "ollama_cli": _ollama_version(),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
