#!/usr/bin/env python3
"""Build immutable, reusable Terminal-Bench task images outside timed runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

if __package__:
    from bench.list_tb_tasks import load_task_ids
else:
    from list_tb_tasks import load_task_ids

_IMAGE_PLACEHOLDER = "${T_BENCH_TASK_DOCKER_CLIENT_IMAGE_NAME}"
_SAFE_TASK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_BUILD_ADJUSTMENTS = {
    # These tasks' base image lists matching Debian snapshots but leaves them
    # commented out. Use a fixed snapshot because the oldoldstable live mirror
    # can expire or rotate packages; signature and checksum checks remain on.
    "qemu-alpine-ssh": "apt-snapshot-https-20260824-v1",
    "qemu-startup": "apt-snapshot-https-20260824-v1",
}


def task_source_sha256(task_dir: Path) -> str:
    """Hash all task inputs, paths, modes, and symlink targets deterministically."""

    task_dir = Path(task_dir)
    if not task_dir.is_dir():
        raise ValueError(f"task directory does not exist: {task_dir}")
    digest = hashlib.sha256()
    for path in sorted(task_dir.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(task_dir).as_posix()
        digest.update(relative.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(f"{path.lstat().st_mode & 0o7777:o}".encode("ascii"))
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(b"symlink\0")
            digest.update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
        elif path.is_file():
            digest.update(b"file\0")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        else:
            digest.update(b"directory")
        digest.update(b"\0")
    return digest.hexdigest()


def cached_image_name(task: str, source_sha256: str) -> str:
    safe_task = re.sub(r"[^a-z0-9_.-]+", "-", task.lower())
    return (
        "mucli-tb-cache/terminal-bench-core-0.1.1/"
        f"{safe_task}:{source_sha256[:16]}"
    )


def stage_task(
    source_task: Path,
    target_task: Path,
    image_name: str,
    *,
    build_adjustment: str | None = None,
) -> None:
    """Copy one task and bind its compose file to a content-addressed image."""

    source_task = Path(source_task)
    target_task = Path(target_task)
    if not source_task.is_dir():
        raise ValueError(f"task directory does not exist: {source_task}")
    target_task.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{target_task.name}.staging-", dir=target_task.parent)
    )
    try:
        shutil.copytree(source_task, staging, symlinks=True, dirs_exist_ok=True)
        compose_path = staging / "docker-compose.yaml"
        try:
            compose = compose_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"cannot read task compose file: {compose_path}") from exc
        occurrences = compose.count(_IMAGE_PLACEHOLDER)
        if occurrences != 1:
            raise ValueError(
                f"expected one client image placeholder in {compose_path}; "
                f"found {occurrences}"
            )
        compose_path.write_text(
            compose.replace(_IMAGE_PLACEHOLDER, image_name), encoding="utf-8"
        )
        if build_adjustment == "apt-snapshot-https-20260824-v1":
            dockerfile_path = staging / "Dockerfile"
            try:
                dockerfile = dockerfile_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise ValueError(
                    f"cannot read task Dockerfile: {dockerfile_path}"
                ) from exc
            base_image = "FROM debian:bullseye-slim"
            occurrences = dockerfile.count(base_image)
            if occurrences != 1:
                raise ValueError(
                    f"expected one {base_image!r} line in {dockerfile_path}; "
                    f"found {occurrences}"
                )
            snapshot_bootstrap = (
                "RUN printf '%s\\n' \\\n"
                "    'deb [check-valid-until=no] "
                "http://snapshot.debian.org/archive/debian/20260824T000000Z "
                "bullseye main' \\\n"
                "    > /etc/apt/sources.list\n"
                "RUN apt-get -o Acquire::Retries=10 update \\\n"
                "    && apt-get -o Acquire::Retries=10 install -y ca-certificates \\\n"
                "    && rm -rf /var/lib/apt/lists/*"
            )
            snapshot_sources = (
                "RUN printf '%s\\n' \\\n"
                "    'deb [check-valid-until=no] "
                "https://snapshot.debian.org/archive/debian/20260824T000000Z "
                "bullseye main' \\\n"
                "    'deb [check-valid-until=no] "
                "https://snapshot.debian.org/archive/debian-security/"
                "20260824T000000Z bullseye-security main' \\\n"
                "    'deb [check-valid-until=no] "
                "https://snapshot.debian.org/archive/debian/20260824T000000Z "
                "bullseye-updates main' \\\n"
                "    > /etc/apt/sources.list"
            )
            dockerfile = dockerfile.replace(
                base_image,
                f"{base_image}\n\n{snapshot_bootstrap}\n{snapshot_sources}",
                1,
            )
            replacements = {
                "apt-get update": "apt-get -o Acquire::Retries=10 update",
                "apt-get install -y": "apt-get -o Acquire::Retries=10 install -y",
                "apt update -y": "apt -o Acquire::Retries=10 update -y",
                "apt install -y": "apt -o Acquire::Retries=10 install -y",
            }
            for original, adjusted in replacements.items():
                occurrences = dockerfile.count(original)
                if occurrences != 1:
                    raise ValueError(
                        f"expected one {original!r} command in {dockerfile_path}; "
                        f"found {occurrences}"
                    )
                dockerfile = dockerfile.replace(original, adjusted)
            dockerfile_path.write_text(dockerfile, encoding="utf-8")
        elif build_adjustment is not None:
            raise ValueError(f"unknown build adjustment: {build_adjustment}")
        if target_task.exists():
            shutil.rmtree(target_task)
        staging.replace(target_task)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _docker_env(image_name: str, task: str, scratch: Path) -> dict[str, str]:
    project = re.sub(r"[^a-z0-9_-]+", "-", f"mucli-tb-prepare-{task}".lower())
    project = f"{project[:48]}-{hashlib.sha256(task.encode()).hexdigest()[:8]}"
    logs = scratch / "logs"
    agent_logs = scratch / "agent-logs"
    logs.mkdir(parents=True, exist_ok=True)
    agent_logs.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "T_BENCH_TASK_DOCKER_CLIENT_IMAGE_NAME": image_name,
            "T_BENCH_TASK_DOCKER_CLIENT_CONTAINER_NAME": project,
            "T_BENCH_TASK_DOCKER_NAME_PREFIX": project,
            "T_BENCH_CONTAINER_LOGS_PATH": "/logs",
            "T_BENCH_CONTAINER_AGENT_LOGS_PATH": "/agent-logs",
            "T_BENCH_TEST_DIR": "/tests",
            "T_BENCH_TASK_LOGS_PATH": str(logs.resolve()),
            "T_BENCH_TASK_AGENT_LOGS_PATH": str(agent_logs.resolve()),
        }
    )
    return env


def _inspect_image(image_name: str) -> dict[str, Any]:
    result = subprocess.run(
        ["docker", "image", "inspect", image_name],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        raise ValueError(f"unexpected docker image inspect output for {image_name}")
    return payload[0]


def _load_manifest(output: Path) -> dict[str, Any]:
    path = output / "prepare-manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_manifest(output: Path, manifest: dict[str, Any]) -> None:
    path = output / "prepare-manifest.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def prepare_tasks(source: Path, output: Path, tasks: list[str]) -> dict[str, Any]:
    source = Path(source).resolve()
    output = Path(output).resolve()
    if source == output:
        raise ValueError("prepared dataset path must differ from the source dataset")
    output.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(output)
    if manifest.get("schema") != 1:
        manifest = {
            "schema": 1,
            "dataset": "terminal-bench-core==0.1.1",
            "source_dataset": str(source),
            "tasks": {},
        }
    manifest["source_dataset"] = str(source)
    entries = manifest.setdefault("tasks", {})

    for task in tasks:
        if not _SAFE_TASK.fullmatch(task):
            raise ValueError(f"invalid task id: {task!r}")
        source_task = source / task
        source_sha = task_source_sha256(source_task)
        build_adjustment = _BUILD_ADJUSTMENTS.get(task)
        image_input_sha = source_sha
        if build_adjustment is not None:
            image_input_sha = hashlib.sha256(
                f"{source_sha}\0{build_adjustment}".encode("utf-8")
            ).hexdigest()
        image_name = cached_image_name(task, image_input_sha)
        existing = entries.get(task)
        if (
            isinstance(existing, dict)
            and existing.get("source_sha256") == source_sha
            and existing.get("build_adjustment") == build_adjustment
        ):
            try:
                compose = (output / task / "docker-compose.yaml").read_text(
                    encoding="utf-8"
                )
                image = _inspect_image(image_name)
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
            else:
                if (
                    f"image: {image_name}" in compose
                    and image.get("Id") == existing.get("image_id")
                ):
                    print(f"-- reusing {task} -> {image_name}", flush=True)
                    continue
        print(f"-- preparing {task} -> {image_name}", flush=True)
        stage_task(
            source_task,
            output / task,
            image_name,
            build_adjustment=build_adjustment,
        )
        with tempfile.TemporaryDirectory(prefix=f"mucli-tb-prepare-{task}-") as raw:
            scratch = Path(raw)
            env = _docker_env(image_name, task, scratch)
            project = env["T_BENCH_TASK_DOCKER_CLIENT_CONTAINER_NAME"]
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "-p",
                    project,
                    "-f",
                    str((output / task / "docker-compose.yaml").resolve()),
                    "build",
                ],
                env=env,
                check=True,
            )
        image = _inspect_image(image_name)
        entries[task] = {
            "source_sha256": source_sha,
            "build_adjustment": build_adjustment,
            "image_name": image_name,
            "image_id": image.get("Id"),
            "repo_digests": sorted(image.get("RepoDigests") or []),
            "prepared_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_manifest(output, manifest)
    return manifest


def check_prepared(
    source: Path,
    output: Path,
    tasks: list[str],
    *,
    inspect_image: Callable[[str], dict[str, Any]] = _inspect_image,
) -> tuple[bool, list[str]]:
    """Validate copied task inputs and local Docker IDs against the manifest."""

    source = Path(source).resolve()
    output = Path(output).resolve()
    manifest = _load_manifest(output)
    errors: list[str] = []
    if manifest.get("schema") != 1:
        return False, [f"missing or invalid {output / 'prepare-manifest.json'}"]
    entries = manifest.get("tasks")
    if not isinstance(entries, dict):
        return False, ["prepared manifest has no task entries"]

    for task in tasks:
        entry = entries.get(task)
        if not isinstance(entry, dict):
            errors.append(f"{task}: not prepared")
            continue
        try:
            source_sha = task_source_sha256(source / task)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if entry.get("source_sha256") != source_sha:
            errors.append(f"{task}: source task changed since image preparation")
            continue
        if entry.get("build_adjustment") != _BUILD_ADJUSTMENTS.get(task):
            errors.append(f"{task}: build adjustment changed since image preparation")
            continue
        compose_path = output / task / "docker-compose.yaml"
        try:
            compose = compose_path.read_text(encoding="utf-8")
        except OSError:
            errors.append(f"{task}: prepared compose file missing")
            continue
        image_name = str(entry.get("image_name") or "")
        if not image_name or f"image: {image_name}" not in compose:
            errors.append(f"{task}: prepared compose image does not match manifest")
            continue
        try:
            image = inspect_image(image_name)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            errors.append(f"{task}: cached image unavailable ({exc})")
            continue
        if image.get("Id") != entry.get("image_id"):
            errors.append(f"{task}: cached image ID does not match manifest")
    return not errors, errors


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tasks", nargs="*", help="Task IDs (default: full MuCLI pack)")
    parser.add_argument("--smoke", action="store_true", help="Prepare hello-world only")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.smoke and args.tasks:
        _parser().error("--smoke cannot be combined with explicit task IDs")
    try:
        tasks = ["hello-world"] if args.smoke else list(args.tasks or load_task_ids())
        if args.check_only:
            healthy, errors = check_prepared(args.source, args.output, tasks)
            if not healthy:
                for error in errors:
                    print(error, file=sys.stderr)
                return 1
            print(args.output.resolve())
            return 0
        prepare_tasks(args.source, args.output, tasks)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"prepare failed: {exc}", file=sys.stderr)
        return 1
    print(f"prepared dataset: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
