"""Build the shared runtime from a clean, explicit source allowlist."""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

SOURCE_DIRS = ("mu", "providers", "utils", "prompts", "documentation")
# What the image runs (documentation is shipped but never executed): a change here means the
# image is stale and job stages would run old worker code.
CODE_DIRS = ("mu", "providers", "utils", "prompts")
CODE_FILES = ("mucli.py", "requirements.txt", "config/model_pricing.json", "mu/jobs/sandbox/Dockerfile")
SOURCE_LABEL = "io.mucli.source"
IMAGE = "mucli-job-runtime:latest"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def source_digest(source: Path | None = None) -> str:
    """Fingerprint of the code baked into the runtime image (paths + contents)."""
    source = source or repo_root()
    digest = hashlib.sha256()
    files = []
    for name in CODE_DIRS:
        # Python only (prompts: everything): web assets under mu/gui/static never run in a stage.
        for path in sorted((source / name).rglob("*" if name == "prompts" else "*.py")):
            if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts:
                files.append(path)
    files += [source / f for f in CODE_FILES if (source / f).is_file()]
    for path in files:
        digest.update(str(path.relative_to(source)).encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()[:16]


def image_digest(tag: str = IMAGE) -> str | None:
    """The source fingerprint label of a built image ("" = built before labels; None = missing)."""
    from .cli import container_cli
    result = subprocess.run([container_cli(), "image", "inspect", "--format", "{{index .Config.Labels \"%s\"}}" % SOURCE_LABEL, tag],
                            capture_output=True, text=True, timeout=20)
    if result.returncode:
        return None
    value = result.stdout.strip()
    return "" if value in ("", "<no value>") else value


def build_image(tag: str = IMAGE, log=None) -> None:
    """Build the runtime image, streaming docker's output lines to ``log``."""
    with tempfile.TemporaryDirectory(prefix="mucli-job-build-") as tmp:
        source = repo_root()
        prepare_context(source, Path(tmp))
        from .cli import container_cli
        command = [container_cli(), "build", "--label", f"{SOURCE_LABEL}={source_digest(source)}", "-t", tag, tmp]
        if log is None:
            subprocess.run(command, check=True)
            return
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in process.stdout:
            log(line.rstrip())
        if process.wait():
            raise RuntimeError(f"docker build failed (exit {process.returncode})")


def prepare_context(source: Path, target: Path):
    def ignore(directory, names):
        from mu.security.secret_paths import is_denied_path
        return [name for name in names if name in ("__pycache__", ".git", "node_modules")
                or Path(directory, name).is_symlink() or is_denied_path(str(Path(directory, name)))[0]]
    for name in SOURCE_DIRS:
        path = source / name
        if path.is_dir():
            shutil.copytree(path, target / name, ignore=ignore)
    for name in ("mucli.py", "requirements.txt"):
        shutil.copyfile(source / name, target / name)
    if (source / "config/model_pricing.json").is_file():
        (target / "config").mkdir()
        shutil.copyfile(source / "config/model_pricing.json", target / "config/model_pricing.json")
    # Voice/model downloads are optional desktop features, not job dependencies.
    requirements = (source / "requirements.txt").read_text().split("# For voice / audio")[0]
    (target / "requirements.txt").write_text(requirements)
    shutil.copyfile(Path(__file__).with_name("Dockerfile"), target / "Dockerfile")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default=IMAGE)
    args = parser.parse_args()
    build_image(args.tag)


if __name__ == "__main__":
    main()
