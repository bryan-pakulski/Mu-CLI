# FolderContext (agentic map/tools)

import os
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

    # Class-level registry of all live instances (for test cleanup)
    _instances: list = []

    def __init__(self):
        self.folders = []
        self.initial_snapshots = {}  # path -> content at start (lazy)
        FolderContext._instances.append(self)
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

    @classmethod
    def reset_all(cls):
        """Clear all live FolderContext instances (for test isolation)."""
        for instance in cls._instances:
            instance.folders.clear()
            instance.initial_snapshots.clear()
            instance.gitignore_patterns.clear()
        cls._instances.clear()

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
                    spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
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
                        spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
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
        """Heuristic to check if file is text using null-byte detection.

        Checks the first 8KB for null bytes (\\x00). Binary files almost
        always contain null bytes early. Avoids opening files in text mode
        as a fallback, which caused massive I/O and memory leaks on large
        workspaces with many binary files.
        """
        try:
            with open(filepath, "rb") as f:
                chunk = f.read(8192)
            if b"\x00" in chunk:
                return False
        except (OSError, IOError):
            return False
        return True

    def _get_file_size(self, filepath):
        """Get file size safely."""
        try:
            return os.path.getsize(filepath)
        except OSError:
            return 0

    def _scan_and_snapshot(self, folder_path):
        """Scan and snapshot file CONTENT at add time.

        Each tracked file's initial content is materialized here so that
        ``get_context_diff_xml`` can compare the true initial state against
        the current (possibly mutated) state. The previous lazy
        placeholder (``None``) design broke diff detection once
        ``get_initial_context_xml`` switched to tree-only: nothing
        materialized the snapshots, so the diff loaded the *current*
        post-mutation content as the "original" and never detected
        changes.

        ``None`` now means "tracked but content unavailable" (file too
        large, or a new file added later via ``track_file``) — the diff
        treats such entries as new files. Respects ``max_files_to_load``
        and ``max_file_size_bytes`` to bound memory.
        """
        files_tracked = len(self.initial_snapshots)
        for root, dirs, files in os.walk(folder_path):
            # Prune directories based on ignore patterns
            dirs[:] = [
                d for d in dirs if not self._is_ignored_path(os.path.join(root, d))]

            for file in files:
                # Cap: stop scanning once we've tracked enough files
                if files_tracked >= self.max_files_to_load:
                    return

                full_path = os.path.join(root, file)
                if self.is_ignored(full_path):
                    continue

                # Only track if not already tracked
                if full_path not in self.initial_snapshots and self._is_text_file(full_path):
                    # Materialize the initial content now so diffs can
                    # compare against it later. None means "unavailable"
                    # (too large); the diff skips those by size anyway.
                    self.initial_snapshots[full_path] = self._load_file_content(full_path)
                    files_tracked += 1

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

    def get_initial_context_xml(self, *, tree_only: bool = True):
        """Returns the initial workspace context as XML.

        When ``tree_only=True`` (default, fix for L0 system-prompt bloat),
        emits only the file tree — paths under the tracked folders, no
        file contents. The agent has ``read_file``/``get_chunk`` for
        content on demand; dumping up to 50 full file bodies into the
        system prompt every iteration was the root cause of the 787k
        L0 bloat in long-horizon runs.

        When ``tree_only=False``, preserves the legacy behavior of
        embedding cached file contents inside ``<file path=...>`` tags.
        Kept for callers/tests that explicitly want contents.
        """
        if not self.initial_snapshots and tree_only:
            # Tree-only view still needs the folder list; fall through
            # to get_tree_map when there are folders but no snapshots.
            if self.folders:
                tree = self.get_tree_map()
                return (
                    "<initial_folder_context>\n"
                    "Tracked files in the workspace (tree only — use "
                    "read_file/get_chunk to inspect contents):\n"
                    + tree
                    + "\n</initial_folder_context>"
                ) if tree else ""
            return ""

        if tree_only:
            tree = self.get_tree_map()
            if not tree:
                return ""
            return (
                "<initial_folder_context>\n"
                "Tracked files in the workspace (tree only — use "
                "read_file/get_chunk to inspect contents):\n"
                + tree
                + "\n</initial_folder_context>"
            )

        # Legacy path: embed full file contents (kept for opt-in callers).
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

    def get_context_diff_xml(self, *, max_chars: int | None = None):
        """Refreshes and returns the context string with per-file diffs.

        NOTE: this is a standalone utility — it is **no longer injected
        into the system prompt**. Per-file diffs were the original source
        of unbounded L0 system-prompt growth (~787k tokens in long-horizon
        runs), and budgeting them with drop-oldest eviction risked silently
        discarding relevant changes. L1C (`_build_folder_context_block`) is
        now tree-only; the model reads file contents on demand. This method
        is kept for ad-hoc/debug use (e.g. a future `/diff` command) and is
        exercised by its own unit tests.

        ``max_chars`` caps the total diff XML size (default 8192). When
        the cumulative diff exceeds the budget, the oldest entries are
        dropped first (drop-oldest eviction) so the most recent changes
        stay visible.
        """
        if not self.folders:
            return ""

        # Default budget; can be overridden by caller or config.
        if max_chars is None:
            max_chars = 8192

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

                    # Compare with the snapshotted initial content. A None
                    # snapshot means the file was tracked without its
                    # content being captured — either a new file added
                    # later via track_file/sync_with_filesystem, or a file
                    # too large to snapshot. Treat both as a new file
                    # (show current content) rather than loading the
                    # current content as the "original" (which would hide
                    # all changes — the bug that left L1C diffs empty).
                    original = self.initial_snapshots.get(full_path)
                    if full_path not in self.initial_snapshots or original is None:
                        # New / unsnapshotted file → emit current content.
                        updates.append(
                            f"""
<new_file path='{full_path}'>\n{current_content}\n</new_file>
"""
                        )
                        continue

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

        header = (
            "<folder_context_diffs>\nThe following changes have been "
            "detected in the workspace relative to the initial context:\n"
        )
        footer = "\n</folder_context_diffs>"

        # Enforce the char budget with drop-oldest eviction: the diff is
        # rebuilt every turn/iteration from the full workspace walk, and in
        # long-horizon runs where the agent writes many files the cumulative
        # diff grew unbounded (the root cause of the ~787k L0 system-prompt
        # bloat). Drop entries from the FRONT of `updates` (oldest in
        # path-walk order) until the remainder fits, so the most recent
        # changes stay visible.
        budget_for_body = max(0, max_chars - len(header) - len(footer))
        body = "\n".join(updates)
        dropped = 0
        if len(body) > budget_for_body and len(updates) > 1:
            while updates and len("\n".join(updates)) > budget_for_body:
                updates.pop(0)
                dropped += 1
            body = "\n".join(updates)
        if dropped:
            marker = (
                f"...[diffs truncated: {dropped} older change(s) dropped "
                f"to fit {max_chars}-char budget]\n"
            )
            body = marker + body

        return header + body + footer

    def get_file_list(self):
        return list(self.initial_snapshots.keys())

    def track_file(self, filepath):
        """Add a file to initial_snapshots if not already tracked.
        Called by file-modifying tools (write_file, search_and_replace_file, apply_diff)
        to ensure new/modified files are visible to search_for_string.
        """
        if filepath not in self.initial_snapshots and self._is_text_file(filepath):
            self.initial_snapshots[filepath] = None  # lazy loaded on demand

    def sync_with_filesystem(self):
        """Re-scan tracked folders to pick up externally added/removed files.
        Called before search_for_string to ensure developer file changes
        outside mucli are visible.
        """
        files_tracked = len(self.initial_snapshots)
        for folder in self.folders:
            for root, dirs, files in os.walk(folder):
                dirs[:] = [d for d in dirs if not self._is_ignored_path(os.path.join(root, d))]
                for file in files:
                    if files_tracked >= self.max_files_to_load:
                        break
                    full_path = os.path.join(root, file)
                    if not self.is_ignored(full_path) and full_path not in self.initial_snapshots:
                        if self._is_text_file(full_path):
                            self.initial_snapshots[full_path] = None
                            files_tracked += 1

        # Remove files that no longer exist on disk
        for path in list(self.initial_snapshots.keys()):
            if not os.path.exists(path):
                del self.initial_snapshots[path]

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
