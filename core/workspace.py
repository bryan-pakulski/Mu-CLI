# FolderContext (agentic map/tools)

import os
import mimetypes
import difflib
import fnmatch


class FolderContext:
    def __init__(self):
        self.folders = []
        self.initial_snapshots = {}  # path -> content at start
        self.gitignore_patterns = {}  # folder_path -> list of patterns
        self.ignore_patterns = {
            ".git",
            "__pycache__",
            ".DS_Store",
            "venv",
            "node_modules",
            ".idea",
            ".vscode",
            ".env",
            ".venv",
            ".key",
        }

    def add_folder(self, folder_path):
        folder_path = os.path.abspath(os.path.expanduser(folder_path))
        if not os.path.exists(folder_path):
            return False

        if folder_path not in self.folders:
            self.folders.append(folder_path)
            self.gitignore_patterns[folder_path] = self._load_gitignore(folder_path)
            self._scan_and_snapshot(folder_path)
        return True

    def _load_gitignore(self, folder_path):
        patterns = []
        gitignore_path = os.path.join(folder_path, ".gitignore")
        if os.path.exists(gitignore_path):
            try:
                with open(gitignore_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            patterns.append(line)
            except Exception:
                pass
        return patterns

    def is_ignored(self, full_path):
        """Returns True if the path should be ignored based on global and gitignore patterns."""
        full_path = os.path.abspath(full_path)

        # 1. Check hardcoded ignore patterns in any part of the path
        parts = full_path.split(os.sep)
        for part in parts:
            if part in self.ignore_patterns:
                return True

        # 2. Check gitignore patterns for the relevant folder
        for folder in self.folders:
            if full_path.startswith(folder):
                rel_path = os.path.relpath(full_path, folder)
                if rel_path == ".":
                    continue

                patterns = self.gitignore_patterns.get(folder, [])
                for pattern in patterns:
                    # Basic matching: relative path match or filename match
                    if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(
                        os.path.basename(full_path), pattern
                    ):
                        return True
                    # Directory match (e.g., "dist/" should match "dist" directory)
                    if pattern.endswith("/") and (
                        fnmatch.fnmatch(rel_path + "/", pattern)
                        or fnmatch.fnmatch(rel_path, pattern.rstrip("/"))
                    ):
                        return True
                break  # Found the containing folder
        return False

    def remove_folder(self, folder_path):
        folder_path = os.path.abspath(os.path.expanduser(folder_path))
        if folder_path in self.folders:
            self.folders.remove(folder_path)
            if folder_path in self.gitignore_patterns:
                del self.gitignore_patterns[folder_path]
            # Remove snapshots associated with this folder
            keys_to_remove = [
                k for k in self.initial_snapshots if k.startswith(folder_path)
            ]
            for k in keys_to_remove:
                del self.initial_snapshots[k]
            return True
        return False

    def _is_text_file(self, filepath):
        """Simple heuristic to check if file is text."""
        mime, _ = mimetypes.guess_type(filepath)
        if (
            mime
            and not mime.startswith("text/")
            and mime != "application/json"
            and mime != "application/javascript"
        ):
            # Fallback: try reading first chunk
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    f.read(1024)
                return True
            except (UnicodeDecodeError, IOError):
                return False
        return True

    def _scan_and_snapshot(self, folder_path):
        for root, dirs, files in os.walk(folder_path):
            # Prune directories based on ignore patterns
            dirs[:] = [d for d in dirs if not self.is_ignored(os.path.join(root, d))]

            for file in files:
                full_path = os.path.join(root, file)
                if self.is_ignored(full_path):
                    continue

                # Only snapshot if not already tracked
                if full_path not in self.initial_snapshots and self._is_text_file(
                    full_path
                ):
                    try:
                        with open(
                            full_path, "r", encoding="utf-8", errors="ignore"
                        ) as f:
                            self.initial_snapshots[full_path] = f.read()
                    except Exception:
                        pass

    def get_tree_map(self) -> str:
        """Returns a fast, lightweight string representation of the file tree."""
        if not self.folders:
            return "No workspace attached."

        tree = []
        for folder in self.folders:
            tree.append(f"📁 {os.path.basename(folder)}/ ({folder})")
            for root, dirs, files in os.walk(folder):
                # Prune directories
                dirs[:] = [
                    d for d in dirs if not self.is_ignored(os.path.join(root, d))
                ]

                # Calculate relative path to determine indentation level
                rel_root = os.path.relpath(root, folder)
                if rel_root == ".":
                    level = 1
                else:
                    level = rel_root.count(os.sep) + 2

                indent = "  " * level

                for f in files:
                    full_path = os.path.join(root, f)
                    if not self.is_ignored(full_path):
                        tree.append(f"{indent}📄 {f}")

        return "\n".join(tree)

    def get_initial_context_xml(self):
        """Returns the cached content of files at the time they were added."""
        if not self.initial_snapshots:
            return ""

        blocks = []
        for path, content in self.initial_snapshots.items():
            blocks.append(f"<file path='{path}'>\n{content}\n</file>")

        return (
            f"<initial_folder_context>\nThe following files are tracked in the workspace (cached state):\n"
            + "\n".join(blocks)
            + "\n</initial_folder_context>"
        )

    def get_context_diff_xml(self):
        """Refreshes and returns the context string with diffs."""
        if not self.folders:
            return ""

        updates = []
        current_files = set()

        # 1. Scan current state
        for folder in self.folders:
            for root, dirs, files in os.walk(folder):
                dirs[:] = [
                    d for d in dirs if not self.is_ignored(os.path.join(root, d))
                ]
                for file in files:
                    full_path = os.path.join(root, file)
                    if self.is_ignored(full_path):
                        continue

                    current_files.add(full_path)

                    if not self._is_text_file(full_path):
                        continue

                    try:
                        with open(
                            full_path, "r", encoding="utf-8", errors="ignore"
                        ) as f:
                            current_content = f.read()
                    except Exception:
                        continue

                    # Compare with snapshot
                    original = self.initial_snapshots.get(full_path, "")
                    if original != current_content:
                        diff = difflib.unified_diff(
                            original.splitlines(),
                            current_content.splitlines(),
                            fromfile=f"original/{os.path.basename(full_path)}",
                            tofile=f"current/{os.path.basename(full_path)}",
                            lineterm="",
                        )
                        diff_text = "\n".join(diff)
                        if diff_text:
                            updates.append(
                                f"""
### FILE CHANGE: {full_path}
 ┌── diff ─────────────────────────────────
\n{diff_text}\n
 └────────────────────────────────────────────
"""
                            )
                        else:
                            # Content changed but diff is empty (whitespace?), show full
                            updates.append(
                                f"""                                                                                               

### FILE CONTENT: {full_path}
 ┌── text ─────────────────────────────────
\n{current_content}\n
 └────────────────────────────────────────────
"""
                            )
                    elif full_path not in self.initial_snapshots:
                        # New file found after initial snapshot
                        updates.append(
                            f"""
<new_file path='{full_path}'>\n{current_content}\n</new_file>
"""
                        )
                    else:  # No change
                        pass
        # 2. Detect deletions
        for path in list(self.initial_snapshots.keys()):
            # Only check if the path belongs to currently tracked folders
            in_scope = any(path.startswith(f) for f in self.folders)
            if in_scope and path not in current_files:
                updates.append(f"<file_deleted path='{path}' />")

        if not updates:
            return ""

        return (
            f"<folder_context_diffs>\nThe following changes have been detected in the workspace relative to the initial context:\n"
            + "\n".join(updates)
            + "\n</folder_context_diffs>"
        )

    def get_file_list(self):
        return list(self.initial_snapshots.keys())

    def to_dict(self):
        return {
            "folders": self.folders,
            "initial_snapshots": self.initial_snapshots,
            "gitignore_patterns": self.gitignore_patterns,
        }

    def from_dict(self, data):
        self.folders = data.get("folders", [])
        self.initial_snapshots = data.get("initial_snapshots", {})
        self.gitignore_patterns = data.get("gitignore_patterns", {})
