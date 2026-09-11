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
_ENVIRONMENT_REVISION = "prebuilt-grader-and-mucli-python-v3"
_MUCLI_PYTHON_IMAGE = "python:3.10.19-slim-bookworm"
_BUILD_ADJUSTMENTS = {
    # These tasks' base image lists matching Debian snapshots but leaves them
    # commented out. Use a fixed snapshot because the oldoldstable live mirror
    # can expire or rotate packages; signature and checksum checks remain on.
    "qemu-alpine-ssh": "apt-snapshot-https-20260824-v1",
    "qemu-startup": "apt-snapshot-https-20260824-v1",
}

_CUSTOM_GRADER_INSTALLS = {
    "csv-to-parquet": ["pandas", "pyarrow"],
    "reshard-c4-data": ["tqdm"],
    "simple-web-scraper": ["pandas"],
    "hf-model-inference": ["requests", "psutil"],
    "nginx-request-logging": ["requests"],
    "fibonacci-server": ["requests"],
    "grid-pattern-transform": ["numpy"],
    "path-tracing": ["numpy", "Pillow"],
    "sanitize-git-repo": ["GitPython"],
}
_CUSTOM_PYTEST_TASKS = {
    "simple-web-scraper": "-rA",
    "hf-model-inference": "-v -rA",
    "fix-pandas-version": "-rA",
    "incompatible-python-fasttext": "-rA",
    "nginx-request-logging": "-v -rA",
    "cartpole-rl-training": "-rA",
    "pytorch-model-cli": "-v -rA",
    "extract-safely": "-rA",
}
_SWE_TEST_TARGETS = {
    "swe-bench-astropy-1": "astropy/modeling/tests/test_separable.py",
    "swe-bench-astropy-2": "astropy/io/ascii/tests/test_qdp.py",
    "swe-bench-fsspec": "fsspec/implementations/tests/test_dirfs.py",
    "swe-bench-langcodes": "langcodes/tests/test_language.py",
}
_GRADER_APT_PACKAGES = {
    "configure-git-webserver": ["curl", "expect"],
    "git-multibranch": ["curl", "expect", "git", "openssh-client"],
    "qemu-alpine-ssh": ["sshpass"],
    "build-linux-kernel-qemu": [
        "build-essential",
        "libncurses-dev",
        "bison",
        "flex",
        "libssl-dev",
        "libelf-dev",
        "qemu-system",
        "bc",
        "cpio",
        "wget",
        "expect",
    ],
}


def _client_dockerfile(task_dir: Path) -> Path:
    """Resolve the client Dockerfile, including tasks with a nested context."""

    import yaml

    compose_path = task_dir / "docker-compose.yaml"
    try:
        compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        build = compose["services"]["client"]["build"]
    except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot resolve client build from {compose_path}") from exc
    if isinstance(build, str):
        context, dockerfile = build, "Dockerfile"
    elif isinstance(build, dict):
        context = str(build.get("context") or ".")
        dockerfile = str(build.get("dockerfile") or "Dockerfile")
    else:
        raise ValueError(f"invalid client build configuration in {compose_path}")
    path = task_dir / context / dockerfile
    if not path.is_file():
        raise ValueError(f"client Dockerfile does not exist: {path}")
    return path


def _write_common_grader_scripts(task_dir: Path, task: str) -> None:
    """Remove the per-trial online uv bootstrap from the common verifier."""

    tests_dir = task_dir / "tests"
    setup = tests_dir / "setup-uv-pytest.sh"
    runner = tests_dir / "run-uv-pytest.sh"
    if setup.is_file():
        setup.write_text(
            "#!/bin/bash\n"
            "set -euo pipefail\n"
            "test -x /opt/tb-grader/bin/python\n"
            "/opt/tb-grader/bin/python -c 'import pytest'\n",
            encoding="utf-8",
        )
        setup.chmod(0o755)
    if runner.is_file():
        runner.write_text(
            "#!/bin/bash\n"
            "set -euo pipefail\n"
            ': "${TEST_DIR:=/tests}"\n'
            "exec /opt/tb-grader/bin/python -m pytest "
            '"$TEST_DIR/test_outputs.py" -rA\n',
            encoding="utf-8",
        )
        runner.chmod(0o755)
    run_tests = task_dir / "run-tests.sh"
    if task not in _SWE_TEST_TARGETS and setup.is_file() and run_tests.is_file():
        run_tests.write_text(
            "#!/bin/bash\n"
            "set -euo pipefail\n"
            ': "${TEST_DIR:=/tests}"\n'
            "exec /opt/tb-grader/bin/python -m pytest "
            '"$TEST_DIR/test_outputs.py" -rA\n',
            encoding="utf-8",
        )
        run_tests.chmod(0o755)


def _write_custom_grader_runner(task_dir: Path, task: str) -> None:
    """Make custom graders consume only their image-baked environment."""

    run_tests = task_dir / "run-tests.sh"
    if task in _CUSTOM_PYTEST_TASKS:
        flags = _CUSTOM_PYTEST_TASKS[task]
        run_tests.write_text(
            "#!/bin/bash\n"
            "set -euo pipefail\n"
            ': "${TEST_DIR:=/tests}"\n'
            "exec /opt/tb-grader/bin/python -m pytest "
            f'"$TEST_DIR/test_outputs.py" {flags}\n',
            encoding="utf-8",
        )
        run_tests.chmod(0o755)
        return
    target = _SWE_TEST_TARGETS.get(task)
    if target is None:
        return
    original = run_tests.read_text(encoding="utf-8")
    marker = "apt-get update && apt-get install -y gcc"
    if original.count(marker) != 1:
        raise ValueError(f"cannot isolate grader setup for {task}")
    patch_prefix = original.split(marker, 1)[0].rstrip()
    run_tests.write_text(
        patch_prefix
        + "\n\n"
        + "patch --fuzz=5 -p1 -i /app/test_patch.diff\n"
        + f"exec /opt/tb-grader/bin/python -m pytest -rA -v {target}\n",
        encoding="utf-8",
    )
    run_tests.chmod(0o755)


def _repair_known_verifier_defects(task_dir: Path, task: str) -> None:
    """Repair pinned upstream verifiers that cannot produce a bounded verdict."""

    test_file = task_dir / "tests" / "test_outputs.py"
    if task == "tmux-advanced-workflow":
        run_tests = task_dir / "run-tests.sh"
        source = run_tests.read_text(encoding="utf-8")
        run_tests.write_text(
            "export PATH=/opt/mucli-task-bin:$PATH\n" + source,
            encoding="utf-8",
        )
        return
    if task == "cron-broken-network":
        test_file.write_text(
            "# Prepared benchmark verifier: stable behavioral curl check.\n"
            "import subprocess\n"
            "from pathlib import Path\n\n"
            "def _fetch():\n"
            "    result = subprocess.run(\n"
            "        ['/usr/bin/curl', '-fsSL', 'http://example.com'],\n"
            "        capture_output=True, text=True, timeout=30, check=False,\n"
            "    )\n"
            "    assert result.returncode == 0, result.stderr\n"
            "    Path('/app/example.html').write_text(result.stdout)\n"
            "    return result.stdout\n\n"
            "def test_curl_file_exists():\n"
            "    _fetch()\n"
            "    assert Path('/app/example.html').is_file()\n\n"
            "def test_curl_file_content():\n"
            "    text = _fetch().lower()\n"
            "    assert '<title>example domain</title>' in text\n",
            encoding="utf-8",
        )
        return
    if task == "pytorch-model-cli":
        source = test_file.read_text(encoding="utf-8")
        dataset_root = 'root="./data"'
        if source.count(dataset_root) != 1:
            raise ValueError("cannot isolate pytorch-model-cli test dataset")
        test_file.write_text(
            source.replace(
                dataset_root,
                'root="/opt/tb-grader-data"',
                1,
            ),
            encoding="utf-8",
        )
        return
    if task == "reshard-c4-data":
        source = test_file.read_text(encoding="utf-8")
        online_run = '["uv", "run", REVERT_SCRIPT]'
        if source.count(online_run) != 1:
            raise ValueError("cannot isolate reshard-c4-data verifier")
        test_file.write_text(
            source.replace(
                online_run,
                '["/opt/tb-grader/bin/python", REVERT_SCRIPT]',
                1,
            ),
            encoding="utf-8",
        )
        return
    if task in {"configure-git-webserver", "git-multibranch"}:
        source = test_file.read_text(encoding="utf-8")
        install_blocks = {
            "configure-git-webserver": (
                "# Install curl for testing\n"
                "apt-get update\n"
                "DEBIAN_FRONTEND=noninteractive apt-get install -y curl expect\n\n"
            ),
            "git-multibranch": (
                "apt-get update\n"
                "DEBIAN_FRONTEND=noninteractive apt-get install -y "
                "curl expect git openssh-client\n\n"
            ),
        }
        install_block = install_blocks[task]
        if source.count(install_block) != 1:
            raise ValueError(f"cannot isolate {task} verifier")
        test_file.write_text(
            source.replace(install_block, "", 1),
            encoding="utf-8",
        )
        return
    if task == "qemu-alpine-ssh":
        source = test_file.read_text(encoding="utf-8")
        online_install = '    os.popen("apt install -y sshpass").read()\n\n'
        if source.count(online_install) != 1:
            raise ValueError("cannot isolate qemu-alpine-ssh verifier")
        test_file.write_text(
            source.replace(online_install, "", 1),
            encoding="utf-8",
        )
        return
    if task not in {
        "build-initramfs-qemu",
        "build-linux-kernel-qemu",
        "build-tcc-qemu",
    }:
        return
    source = test_file.read_text(encoding="utf-8")
    if source.count("set timeout -1") != 1:
        raise ValueError(f"cannot bound expect verifier for {task}")
    source = source.replace("set timeout -1", "set timeout 120", 1)
    if source.rstrip().endswith("test_expect()"):
        source = source.rstrip()[: -len("test_expect()")].rstrip() + "\n"
    else:
        raise ValueError(f"cannot remove collection-time verifier call for {task}")
    if task == "build-linux-kernel-qemu":
        start = 'init = """\n'
        join = '\n+"""libelf-dev qemu-system bc cpio wget expect\n'
        if source.count(start) != 1 or source.count(join) != 1:
            raise ValueError("cannot repair build-linux-kernel-qemu verifier")
        source = source.replace(start, 'init = (\n    """\n', 1)
        source = source.replace(
            join, '\n    + """libelf-dev qemu-system bc cpio wget expect\n', 1
        )
        closing = '\n"""\n\n\ndef test_expect():'
        if source.count(closing) != 1:
            raise ValueError("cannot close repaired kernel init expression")
        source = source.replace(closing, '\n"""\n)\n\n\ndef test_expect():', 1)
        setup_commands = (
            "apt-get update -y\n"
            "apt-get install -y build-essential libncurses-dev bison flex "
            'libssl-dev """\n'
            '    + """libelf-dev qemu-system bc cpio wget expect\n\n'
        )
        if source.count(setup_commands) != 1:
            raise ValueError("cannot remove kernel verifier environment setup")
        source = source.replace(setup_commands, '"""\n    + """', 1)
        busybox_download = (
            "wget https://busybox.net/downloads/binaries/"
            "1.31.0-defconfig-multiarch-musl/busybox-x86_64"
        )
        if source.count(busybox_download) != 1:
            raise ValueError("cannot isolate kernel verifier busybox input")
        source = source.replace(
            busybox_download,
            "cp /opt/tb-grader-data/busybox-x86_64 ./busybox-x86_64",
            1,
        )
    if task == "build-tcc-qemu":
        assertion = 'assert "42" in actual_output.split("echo $?")[1], ('
        replacement = (
            'assert "echo $?" in actual_output and '
            '"42" in actual_output.split("echo $?", 1)[1], ('
        )
        if source.count(assertion) != 1:
            raise ValueError("cannot repair build-tcc-qemu verifier assertion")
        source = source.replace(assertion, replacement, 1)
    test_file.write_text(source, encoding="utf-8")


def _grader_install_commands(task: str) -> list[str]:
    commands = ["/opt/tb-grader/bin/python -m pip install --no-cache-dir 'pytest<9'"]
    packages = _CUSTOM_GRADER_INSTALLS.get(task)
    if packages:
        commands.append(
            "/opt/tb-grader/bin/python -m pip install --no-cache-dir "
            + " ".join(packages)
        )
    if task == "cartpole-rl-training":
        commands.extend(
            [
                "/opt/tb-grader/bin/python -m pip install --no-cache-dir "
                "torch==2.7.0 --index-url https://download.pytorch.org/whl/cpu",
                "/opt/tb-grader/bin/python -m pip install --no-cache-dir "
                "numpy gymnasium",
            ]
        )
    elif task == "pytorch-model-cli":
        commands.extend(
            [
                "/opt/tb-grader/bin/python -m pip install --no-cache-dir "
                "torch==2.7.0 torchvision==0.22.0 "
                "--index-url https://download.pytorch.org/whl/cpu",
                "/opt/tb-grader/bin/python -m pip install --no-cache-dir "
                "numpy opencv-python",
                "mkdir -p /opt/tb-grader-data && "
                '/opt/tb-grader/bin/python -c "from torchvision.datasets '
                "import MNIST; MNIST(root='/opt/tb-grader-data', train=False, "
                'download=True)"',
            ]
        )
    elif task == "train-fasttext":
        commands.append(
            "/opt/tb-grader/bin/python -m pip install --no-cache-dir "
            "numpy==1.24.0 scikit-learn fasttext-wheel"
        )
    elif task.startswith("swe-bench-astropy-"):
        commands.extend(
            [
                "/opt/tb-grader/bin/python -m pip install --no-cache-dir "
                "setuptools==68.0.0 numpy==1.23.4",
                "cd /app/astropy && cp pyproject.toml /tmp/tb-pyproject.toml && "
                'sed -i \'s/requires = ["setuptools",/'
                'requires = ["setuptools==68.0.0",/\' pyproject.toml && '
                "/opt/tb-grader/bin/python -m pip install --no-cache-dir -e '.[test]' && "
                "mv /tmp/tb-pyproject.toml pyproject.toml",
            ]
        )
    elif task == "swe-bench-fsspec":
        commands.append(
            "cd /app/fsspec && /opt/tb-grader/bin/python -m pip install "
            "--no-cache-dir -e '.[test]'"
        )
    elif task == "swe-bench-langcodes":
        commands.append(
            "cd /app/langcodes && /opt/tb-grader/bin/python -m pip install "
            "--no-cache-dir -e '.[test]'"
        )
    return commands


def _prepare_client_environment(task_dir: Path, task: str) -> None:
    """Bake MuCLI's compatible interpreter and grader deps into the task image."""

    dockerfile_path = _client_dockerfile(task_dir)
    dockerfile = dockerfile_path.read_text(encoding="utf-8")
    prefix = f"FROM {_MUCLI_PYTHON_IMAGE} AS mucli_benchmark_python\n\n"
    apt_packages = []
    if task == "pytorch-model-cli":
        apt_packages.extend(["ffmpeg", "libsm6", "libxext6"])
    if task in _SWE_TEST_TARGETS:
        apt_packages.append("gcc")
    apt_packages.extend(_GRADER_APT_PACKAGES.get(task, []))
    grader_python = (
        "/opt/mucli-python/bin/python3" if task == "train-fasttext" else "python3"
    )
    commands = [
        "set -eux",
        "if ! command -v python3 >/dev/null 2>&1; then "
        "apt-get update; apt-get install -y python3 python3-pip python3-venv; fi",
        "rm -rf /opt/tb-grader /tmp/tb-grader-probe",
        f"if ! {grader_python} -m venv --system-site-packages "
        "/tmp/tb-grader-probe; then "
        "apt-get update; apt-get install -y python3-venv; fi",
        "rm -rf /tmp/tb-grader-probe",
        f"{grader_python} -m venv --system-site-packages /opt/tb-grader",
        "/opt/tb-grader/bin/python -m pip install --no-cache-dir --upgrade pip",
    ]
    if apt_packages:
        commands.insert(
            2,
            "apt-get update; apt-get install -y " + " ".join(apt_packages),
        )
    commands.extend(_grader_install_commands(task))
    if task == "build-linux-kernel-qemu":
        commands.append(
            "mkdir -p /opt/tb-grader-data && "
            "wget -q https://busybox.net/downloads/binaries/"
            "1.31.0-defconfig-multiarch-musl/busybox-x86_64 "
            "-O /opt/tb-grader-data/busybox-x86_64 && "
            "chmod 0755 /opt/tb-grader-data/busybox-x86_64"
        )
    commands.extend(
        [
            "/opt/tb-grader/bin/python -c 'import pytest'",
            "rm -rf /var/lib/apt/lists/*",
        ]
    )
    continuation = " " + "\\" + "\n    && "
    environment = (
        "\n\nCOPY --from=mucli_benchmark_python /usr/local /opt/mucli-python\n"
        "RUN " + continuation.join(commands) + "\n"
    )
    dockerfile_path.write_text(
        prefix + dockerfile.rstrip() + environment,
        encoding="utf-8",
    )
    _write_common_grader_scripts(task_dir, task)
    _write_custom_grader_runner(task_dir, task)
    _repair_known_verifier_defects(task_dir, task)


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
        "mucli-tb-cache/terminal-bench-core-0.1.1/" f"{safe_task}:{source_sha256[:16]}"
    )


def stage_task(
    source_task: Path,
    target_task: Path,
    image_name: str,
    *,
    build_adjustment: str | None = None,
    prepare_environment: bool = False,
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
        if prepare_environment:
            _prepare_client_environment(staging, source_task.name)
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
        image_input_sha = hashlib.sha256(
            f"{source_sha}\0{build_adjustment or ''}\0{_ENVIRONMENT_REVISION}".encode(
                "utf-8"
            )
        ).hexdigest()
        image_name = cached_image_name(task, image_input_sha)
        existing = entries.get(task)
        if (
            isinstance(existing, dict)
            and existing.get("source_sha256") == source_sha
            and existing.get("build_adjustment") == build_adjustment
            and existing.get("environment_revision") == _ENVIRONMENT_REVISION
        ):
            try:
                compose = (output / task / "docker-compose.yaml").read_text(
                    encoding="utf-8"
                )
                image = _inspect_image(image_name)
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
            else:
                if f"image: {image_name}" in compose and image.get(
                    "Id"
                ) == existing.get("image_id"):
                    print(f"-- reusing {task} -> {image_name}", flush=True)
                    continue
        print(f"-- preparing {task} -> {image_name}", flush=True)
        stage_task(
            source_task,
            output / task,
            image_name,
            build_adjustment=build_adjustment,
            prepare_environment=True,
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
            "environment_revision": _ENVIRONMENT_REVISION,
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
        if entry.get("environment_revision") != _ENVIRONMENT_REVISION:
            errors.append(f"{task}: prepared environment revision is stale")
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
