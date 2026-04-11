# FolderContext (agentic map/tools)

import os
import mimetypes
import pathspec
import difflib
import fnmatch

# Limits for lazy loading to prevent OOM on large workspaces
MAX_FILES_TO_LOAD = 50          # Max files to load content from in get_initial_context_xml
MAX_FILE_SIZE_BYTES = 128 * 1024  # 128KB per file


class FolderContext:
    # Instance-level limits (can be overridden)
    max_files_to_load = MAX_FILES_TO_LOAD
    max_file_size_bytes = MAX_FILE_SIZE_BYTES

    def __init__(self):
        self.folders = []
        self.initial_snapshots = {}  # path -> content at start (lazy)
        self.gitignore_patterns = {}  # folder_path -> list of patterns
        self.ignore_patterns = {
            ".git",
            "__pycache__",
            ".gitignore",
            ".DS_Store",
            "venv",
            "node_modules",
            ".idea",
            ".vscode",
            ".env",
            ".venv",
            ".key",
            ".ruff_cache",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            ".hypothesis",
            ".tox",
            ".coverage",
            "*.pyc",
            "*.pyo",
            "*.pyd",
            ".Python",
            "*.so",
            "*.egg",
            "*.egg-info",
            "dist",
            "build",
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

    def _walk_up_gitignores(self, folder_path):
        """Walk UP the directory tree collecting .gitignore patterns.
        Returns merged patterns from all .gitignore files found from root to folder.

        Gitignore semantics: patterns from parent directories apply to children,
        and child .gitignore patterns can negate parent patterns.
        """
        all_patterns = []
        current = os.path.abspath(folder_path)
        root = os.path.dirname(current)

        while True:
            gitignore_path = os.path.join(current, ".gitignore")
            if os.path.exists(gitignore_path):
                try:
                    with open(gitignore_path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.rstrip("\n\r")
                            # Skip comments and empty lines
                            if line and not line.startswith("#"):
                                all_patterns.append(line)
                except Exception:
                    pass

            parent = os.path.dirname(current)
            if parent == current:  # Reached filesystem root
                break
            current = parent

        return all_patterns

    def _load_gitignore(self, folder_path):
        """Load .gitignore patterns from folder and all parent directories.
        Uses pathspec for proper gitignore matching semantics.
        """
        return self._walk_up_gitignores(folder_path)

    def is_ignored(self, full_path):
        """Returns True if the path should be ignored based on gitignore patterns and hardcoded patterns."""
        full_path = os.path.abspath(full_path)
        basename = os.path.basename(full_path)
        is_dir = os.path.isdir(full_path)

        # First check hardcoded ignore patterns (these always apply)
        if basename in self.ignore_patterns:
            return True
        for pattern in self.ignore_patterns:
            # Pattern ends with / means it only matches directories
            if pattern.endswith("/"):
                if is_dir and fnmatch.fnmatch(basename, pattern.rstrip("/")):
                    return True
            elif fnmatch.fnmatch(basename, pattern):
                return True

        # Find the containing folder and check its gitignore
        for folder in self.folders:
            if full_path.startswith(folder):
                rel_path = os.path.relpath(full_path, folder)
                if rel_path == ".":
                    return False

                patterns = self.gitignore_patterns.get(folder, [])
                if patterns:
                    # Use pathspec for proper gitignore semantics
                    spec = pathspec.PathSpec.from_lines("gitignore", patterns)
                    # Use match_dir for directories, match_file for files
                    if is_dir:
                        return spec.match_file(rel_path + "/") or spec.match_file(rel_path)
                    return spec.match_file(rel_path)

        return False

    def _is_ignored_path(self, full_path):
        """Check if a path should be ignored for os.walk directory pruning."""
        basename = os.path.basename(full_path)
        is_dir = os.path.isdir(full_path)

        # Check hardcoded patterns
        if basename in self.ignore_patterns:
            return True
        for pattern in self.ignore_patterns:
            if pattern.endswith("/"):
                if is_dir and fnmatch.fnmatch(basename, pattern.rstrip("/")):
                    return True
            elif fnmatch.fnmatch(basename, pattern):
                return True

        # Check gitignore patterns for directories (to prune early)
        if is_dir:
            for folder in self.folders:
                if full_path.startswith(folder):
                    rel_path = os.path.relpath(full_path, folder)
                    patterns = self.gitignore_patterns.get(folder, [])
                    if patterns:
                        spec = pathspec.PathSpec.from_lines("gitignore", patterns)
                        # Check if the directory itself matches (dir patterns end with /)
                        if spec.match_file(rel_path + "/") or spec.match_file(rel_path):
                            return True
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

    def _get_file_size(self, filepath):
        """Get file size safely."""
        try:
            return os.path.getsize(filepath)
        except OSError:
            return 0

    def _scan_and_snapshot(self, folder_path):
        """Fast scan: only stores file paths, not content. Content is lazy loaded."""
        for root, dirs, files in os.walk(folder_path):
            # Prune directories based on ignore patterns
            dirs[:] = [
                d for d in dirs if not self._is_ignored_path(os.path.join(root, d))]

            for file in files:
                full_path = os.path.join(root, file)
                if self.is_ignored(full_path):
                    continue

                # Only track if not already tracked
                if full_path not in self.initial_snapshots and self._is_text_file(full_path):
                    # Store None as placeholder - content loaded lazily on demand
                    self.initial_snapshots[full_path] = None

    def _load_file_content(self, filepath):
        """Load file content with size limit. Returns None if too large or error."""
        if self._get_file_size(filepath) > self.max_file_size_bytes:
            return None
        
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            return None

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
        """Returns cached content of files at the time they were added.
        Uses lazy loading with size limits to prevent OOM on large workspaces.
        """
        if not self.initial_snapshots:
            return ""

        blocks = []
        files_loaded = 0
        
        for path, content in self.initial_snapshots.items():
            # Lazy load: only load content if not already cached
            if content is None and files_loaded < self.max_files_to_load:
                content = self._load_file_content(path)
                self.initial_snapshots[path] = content  # Cache for future use
                files_loaded += 1
            
            # Skip if content is None (too large or error)
            if content is None:
                continue
                
            blocks.append(f"<file path='{path}'>\n{content}\n</file>")

        if not blocks:
            return ""
            
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

                    # Skip large files for diff
                    if self._get_file_size(full_path) > self.max_file_size_bytes:
                        continue

                    try:
                        with open(
                            full_path, "r", encoding="utf-8", errors="ignore"
                        ) as f:
                            current_content = f.read()
                    except Exception:
                        continue

                    # Compare with snapshot (lazy load if needed)
                    original = self.initial_snapshots.get(full_path)
                    if original is None:
                        original = self._load_file_content(full_path)
                        if original is None:
                            continue  # Skip if can't load

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
 ├─────────────────────────── diff ─────────────────────────────────
\n{diff_text}\n
 └──────────────────────────────────────────────────────────────────
"""
                            )
                        else:
                            # Content changed but diff is empty (whitespace?), show full
                            updates.append(
                                f"""                                                                                              

### FILE CONTENT: {full_path}
 ├─────────────────────────── text ─────────────────────────────────
\n{current_content}\n
 └──────────────────────────────────────────────────────────────────
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
