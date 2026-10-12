"""Build the shared runtime from a clean, explicit source allowlist."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

SOURCE_DIRS = ("mu", "providers", "utils", "prompts", "documentation")


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
    parser.add_argument("--tag", default="mucli-job-runtime:latest")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="mucli-job-build-") as tmp:
        prepare_context(Path(__file__).resolve().parents[3], Path(tmp))
        subprocess.run(["docker", "build", "-t", args.tag, tmp], check=True)


if __name__ == "__main__":
    main()
