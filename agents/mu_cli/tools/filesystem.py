from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
import subprocess
import textwrap
import xml.etree.ElementTree as ET
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from mu_cli.tools.base import ToolResult
from mu_cli.workspace import WorkspaceStore


class ReadFileTool:
    name = "read_file"
    description = "Read a UTF-8 text file from disk."
    mutating = False
    schema = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Path to file"}},
        "required": ["path"],
    }

    def __init__(self, workspace_root_getter: Callable[[], Path | None] | None = None) -> None:
        self.workspace_root_getter = workspace_root_getter

    def _resolve(self, path_value: str) -> tuple[Path | None, str | None]:
        root = self.workspace_root_getter() if self.workspace_root_getter else None
        raw = Path(path_value).expanduser()
        target = (root / raw).resolve() if root is not None and not raw.is_absolute() else raw.resolve()
        if root is not None:
            root_resolved = root.resolve()
            if target != root_resolved and root_resolved not in target.parents:
                return None, f"Path is outside attached workspace: {target}"
        return target, None

    def run(self, args: dict[str, str]) -> ToolResult:
        path, err = self._resolve(args["path"])
        if err:
            return ToolResult(ok=False, output=err)
        assert path is not None
        if not path.exists():
            return ToolResult(ok=False, output=f"Path not found: {path}")
        if path.is_dir():
            return ToolResult(ok=False, output=f"Path is a directory: {path}")
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(ok=False, output=f"File is not UTF-8 text: {path}")
        return ToolResult(ok=True, output=content)


class WriteFileTool:
    name = "write_file"
    description = "Write UTF-8 content to a file path (creates parent dirs)."
    mutating = True
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to write"},
            "content": {"type": "string", "description": "UTF-8 text content"},
        },
        "required": ["path", "content"],
    }

    def __init__(self, workspace_root_getter: Callable[[], Path | None] | None = None) -> None:
        self.workspace_root_getter = workspace_root_getter

    def _resolve(self, path_value: str) -> tuple[Path | None, str | None]:
        root = self.workspace_root_getter() if self.workspace_root_getter else None
        raw = Path(path_value).expanduser()
        target = (root / raw).resolve() if root is not None and not raw.is_absolute() else raw.resolve()
        if root is not None:
            root_resolved = root.resolve()
            if target != root_resolved and root_resolved not in target.parents:
                return None, f"Path is outside attached workspace: {target}"
        return target, None

    def run(self, args: dict[str, str]) -> ToolResult:
        path, err = self._resolve(args["path"])
        if err:
            return ToolResult(ok=False, output=err)
        assert path is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args["content"], encoding="utf-8")
        return ToolResult(ok=True, output=f"Wrote file: {path}")


def _normalize_patch_text(raw_patch: str) -> str:
    patch = textwrap.dedent(str(raw_patch or ""))

    fenced = re.match(r"^```(?:diff|patch)?\s*\n([\s\S]*?)\n```\s*$", patch.strip())
    if fenced:
        patch = fenced.group(1)

    lines = patch.splitlines()
    if lines and lines[0].strip().lower() in {"diff", "patch"}:
        patch = "\n".join(lines[1:])

    expanded: list[str] = []
    for line in patch.splitlines():
        if line and line[0] in {"+", "-", " "} and "\\n" in line:
            marker = line[0]
            parts = line[1:].split("\\n")
            expanded.extend(f"{marker}{part}" for part in parts)
            continue
        expanded.append(line)
    patch = "\n".join(expanded)

    if "\\n" in patch and patch.count("\\n") > patch.count("\n"):
        patch = patch.replace("\\n", "\n")
    if "\\t" in patch and patch.count("\\t") > patch.count("\t"):
        patch = patch.replace("\\t", "\t")

    if patch and not patch.endswith("\n"):
        patch += "\n"
    return patch


class ApplyPatchTool:
    name = "apply_patch"
    description = "Apply a unified diff patch using git apply."
    mutating = True
    schema = {
        "type": "object",
        "properties": {"patch": {"type": "string", "description": "Unified diff patch content"}},
        "required": ["patch"],
    }

    def __init__(self, workspace_root_getter: Callable[[], Path | None] | None = None) -> None:
        self.workspace_root_getter = workspace_root_getter

    def run(self, args: dict[str, str]) -> ToolResult:
        patch = _normalize_patch_text(args["patch"])
        root = self.workspace_root_getter() if self.workspace_root_getter else None
        proc = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            input=patch,
            text=True,
            capture_output=True,
            cwd=str(root) if root is not None else None,
        )
        if proc.returncode != 0:
            return ToolResult(ok=False, output=proc.stderr.strip() or "git apply failed")
        return ToolResult(ok=True, output="Patch applied successfully")


class GitTool:
    name = "git"
    description = "Run a constrained git operation (status, diff, log, add, commit)."
    mutating = True
    schema = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["status", "diff", "log", "add", "commit"],
                "description": "Git operation",
            },
            "args": {"type": "array", "items": {"type": "string"}, "description": "Extra args"},
        },
        "required": ["operation"],
    }

    SAFE_OPS = {
        "status": ["git", "status", "--short"],
        "diff": ["git", "diff"],
        "log": ["git", "log", "--oneline", "-n", "20"],
        "add": ["git", "add"],
        "commit": ["git", "commit"],
    }

    def __init__(self, workspace_root_getter: Callable[[], Path | None] | None = None) -> None:
        self.workspace_root_getter = workspace_root_getter

    def run(self, args: dict) -> ToolResult:
        op = str(args["operation"])
        extra = [str(item) for item in args.get("args", [])]
        if op not in self.SAFE_OPS:
            return ToolResult(ok=False, output=f"Unsupported operation: {op}")
        command = self.SAFE_OPS[op] + extra
        root = self.workspace_root_getter() if self.workspace_root_getter else None
        proc = subprocess.run(command, text=True, capture_output=True, cwd=str(root) if root is not None else None)
        output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        if proc.returncode != 0:
            return ToolResult(ok=False, output=output.strip() or "git command failed")
        return ToolResult(ok=True, output=output.strip() or "ok")


class ListWorkspaceFilesTool:
    name = "list_workspace_files"
    description = "List indexed workspace files, optionally filtered by a query."
    mutating = False
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Optional search substring"},
            "limit": {"type": "integer", "description": "Maximum files to return", "default": 25},
        },
    }

    def __init__(self, store: WorkspaceStore) -> None:
        self.store = store

    def run(self, args: dict[str, str | int]) -> ToolResult:
        query = args.get("query")
        limit = int(args.get("limit", 25))
        items = self.store.list_files(query=str(query) if query else None, limit=limit)
        if not items:
            return ToolResult(ok=True, output="No indexed files matched.")
        lines = [f"- {item.path} ({item.size_bytes} bytes)" for item in items]
        return ToolResult(ok=True, output="\n".join(lines))


class GetWorkspaceFileContextTool:
    name = "get_workspace_file_context"
    description = "Fetch context for an indexed workspace file by relative path."
    mutating = False
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Indexed relative file path"},
            "max_chars": {"type": "integer", "description": "Maximum characters", "default": 4000},
        },
        "required": ["path"],
    }

    def __init__(self, store: WorkspaceStore) -> None:
        self.store = store

    def run(self, args: dict[str, str | int]) -> ToolResult:
        path = str(args["path"])
        max_chars = int(args.get("max_chars", 4000))
        text = self.store.get_file_context(path=path, max_chars=max_chars)
        ok = not text.startswith("Path not indexed") and not text.startswith("Unable to read")
        return ToolResult(ok=ok, output=text)


class ListUploadedContextFilesTool:
    name = "list_uploaded_context_files"
    description = "List files in uploaded context store for the active session."
    mutating = False
    schema = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Maximum files to return", "default": 50},
        },
    }

    def __init__(self, root_dir: Path, session_name_getter: Callable[[], str]) -> None:
        self.root_dir = root_dir
        self.session_name_getter = session_name_getter

    def run(self, args: dict) -> ToolResult:
        limit = int(args.get("limit", 50))
        session_dir = self.root_dir / self.session_name_getter()
        if not session_dir.exists():
            return ToolResult(ok=True, output="No uploaded context files.")
        files = [item for item in sorted(session_dir.iterdir(), key=lambda x: x.name.lower()) if item.is_file()]
        if not files:
            return ToolResult(ok=True, output="No uploaded context files.")
        lines = [f"- {file.name} ({file.stat().st_size} bytes)" for file in files[:limit]]
        return ToolResult(ok=True, output="\n".join(lines))


class GetUploadedContextFileTool:
    name = "get_uploaded_context_file"
    description = "Read an uploaded UTF-8 context file by filename from the active session store."
    mutating = False
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Filename in uploaded context store"},
            "max_chars": {"type": "integer", "description": "Maximum characters", "default": 8000},
        },
        "required": ["name"],
    }

    def __init__(self, root_dir: Path, session_name_getter: Callable[[], str]) -> None:
        self.root_dir = root_dir
        self.session_name_getter = session_name_getter

    def run(self, args: dict) -> ToolResult:
        name = Path(str(args["name"])).name
        max_chars = int(args.get("max_chars", 8000))
        session_dir = (self.root_dir / self.session_name_getter()).resolve()
        target = (session_dir / name).resolve()
        if session_dir not in target.parents and target != session_dir:
            return ToolResult(ok=False, output="Invalid uploaded file path")
        if not target.exists() or not target.is_file():
            return ToolResult(ok=False, output=f"Uploaded file not found: {name}")
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(ok=False, output=f"Uploaded file is not UTF-8 text: {name}")
        return ToolResult(ok=True, output=text[:max_chars])


class ClearUploadedContextStoreTool:
    name = "clear_uploaded_context_store"
    description = "Clear all uploaded context files for the active session store."
    mutating = True
    schema = {"type": "object", "properties": {}}

    def __init__(self, root_dir: Path, session_name_getter: Callable[[], str]) -> None:
        self.root_dir = root_dir
        self.session_name_getter = session_name_getter

    def run(self, args: dict) -> ToolResult:
        _ = args
        session_dir = self.root_dir / self.session_name_getter()
        if not session_dir.exists():
            return ToolResult(ok=True, output="Uploaded context store already empty.")
        removed = 0
        for item in session_dir.iterdir():
            if item.is_file():
                item.unlink()
                removed += 1
        return ToolResult(ok=True, output=f"Removed {removed} uploaded file(s).")


class FetchUrlContextTool:
    name = "fetch_url_context"
    description = "Fetch a URL and return a clean text excerpt for grounding context."
    mutating = False
    schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute URL to fetch"},
            "max_chars": {"type": "integer", "description": "Maximum characters", "default": 6000},
        },
        "required": ["url"],
    }

    @staticmethod
    def _html_to_text(html: str) -> str:
        text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
        text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def run(self, args: dict) -> ToolResult:
        url = str(args.get("url", "")).strip()
        max_chars = int(args.get("max_chars", 6000))
        if not url.startswith("http://") and not url.startswith("https://"):
            return ToolResult(ok=False, output="url must start with http:// or https://")

        request = urllib.request.Request(
            url,
            headers={"User-Agent": "mu_cli/1.0 (+grounding-tool)"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as resp:
                raw = resp.read()
                content_type = str(resp.headers.get("Content-Type", "")).lower()
        except urllib.error.URLError as exc:
            return ToolResult(ok=False, output=f"URL fetch failed: {exc}")

        try:
            body = raw.decode("utf-8", errors="replace")
        except Exception:
            body = raw.decode(errors="replace")

        text = self._html_to_text(body) if "html" in content_type or "<html" in body.lower() else body
        return ToolResult(ok=True, output=text[:max_chars])


class SearchWebContextTool:
    name = "search_web_context"
    description = "Search the web for supporting sources (DuckDuckGo or Google CSE grounding)."
    mutating = False
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer", "description": "Maximum results", "default": 5},
            "provider": {
                "type": "string",
                "enum": ["auto", "duckduckgo", "google"],
                "default": "auto",
                "description": "Search provider (auto prefers Google when configured).",
            },
        },
        "required": ["query"],
    }

    @staticmethod
    def _request_json(url: str) -> tuple[dict | None, str | None]:
        req = urllib.request.Request(url, headers={"User-Agent": "mu_cli/1.0 (+grounding-tool)"}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                payload = resp.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as exc:
            return None, str(exc)
        try:
            return json.loads(payload), None
        except json.JSONDecodeError as exc:
            return None, f"invalid JSON response: {exc}"

    def _search_google(self, query: str, max_results: int) -> ToolResult:
        api_key = os.environ.get("GOOGLE_CSE_API_KEY", "").strip()
        cse_id = os.environ.get("GOOGLE_CSE_ID", "").strip()
        if not api_key or not cse_id:
            return ToolResult(ok=False, output="Google grounding unavailable: set GOOGLE_CSE_API_KEY and GOOGLE_CSE_ID")

        q = urllib.parse.quote(query)
        url = (
            "https://www.googleapis.com/customsearch/v1"
            f"?key={urllib.parse.quote(api_key)}&cx={urllib.parse.quote(cse_id)}&q={q}&num={max(1, min(max_results, 10))}"
        )
        data, err = self._request_json(url)
        if err or data is None:
            return ToolResult(ok=False, output=f"Google search failed: {err}")
        items = data.get("items", []) if isinstance(data, dict) else []
        if not items:
            return ToolResult(ok=True, output="No Google results.")
        lines = []
        for item in items[:max_results]:
            title = str(item.get("title", "(untitled)"))
            link = str(item.get("link", ""))
            snippet = str(item.get("snippet", ""))
            lines.append(f"- {title}\n  URL: {link}\n  Snippet: {snippet}")
        return ToolResult(ok=True, output="\n".join(lines))

    def _search_duckduckgo(self, query: str, max_results: int) -> ToolResult:
        q = urllib.parse.quote(query)
        url = f"https://api.duckduckgo.com/?q={q}&format=json&no_html=1&skip_disambig=1"
        data, err = self._request_json(url)
        if err or data is None:
            return ToolResult(ok=False, output=f"DuckDuckGo search failed: {err}")

        rows: list[tuple[str, str, str]] = []
        if isinstance(data, dict):
            abstract = str(data.get("AbstractText", "")).strip()
            abstract_url = str(data.get("AbstractURL", "")).strip()
            heading = str(data.get("Heading", "")).strip() or "DuckDuckGo instant answer"
            if abstract or abstract_url:
                rows.append((heading, abstract_url, abstract))

            topics = data.get("RelatedTopics", [])
            if isinstance(topics, list):
                for topic in topics:
                    if isinstance(topic, dict) and "Topics" in topic and isinstance(topic["Topics"], list):
                        for nested in topic["Topics"]:
                            if isinstance(nested, dict):
                                rows.append((str(nested.get("Text", ""))[:80], str(nested.get("FirstURL", "")), str(nested.get("Text", ""))))
                    elif isinstance(topic, dict):
                        rows.append((str(topic.get("Text", ""))[:80], str(topic.get("FirstURL", "")), str(topic.get("Text", ""))))

        rows = [row for row in rows if row[1] or row[2]]
        if not rows:
            return ToolResult(ok=True, output="No DuckDuckGo results.")

        lines = []
        for title, link, snippet in rows[:max_results]:
            lines.append(f"- {title or '(result)'}\n  URL: {link}\n  Snippet: {snippet}")
        return ToolResult(ok=True, output="\n".join(lines))

    def run(self, args: dict) -> ToolResult:
        query = str(args.get("query", "")).strip()
        if not query:
            return ToolResult(ok=False, output="query is required")
        max_results = int(args.get("max_results", 5))
        provider = str(args.get("provider", "auto")).strip().lower() or "auto"

        if provider == "google":
            return self._search_google(query, max_results)
        if provider == "duckduckgo":
            return self._search_duckduckgo(query, max_results)

        google = self._search_google(query, max_results)
        if google.ok:
            return google
        duck = self._search_duckduckgo(query, max_results)
        if duck.ok:
            return duck
        return ToolResult(ok=False, output=f"auto search failed; google={google.output}; duckduckgo={duck.output}")


class ExtractLinksContextTool:
    name = "extract_links_context"
    description = "Fetch a web page and return discovered links for research follow-up."
    mutating = False
    schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute URL to inspect"},
            "max_results": {"type": "integer", "description": "Maximum links to return", "default": 25},
        },
        "required": ["url"],
    }

    def run(self, args: dict) -> ToolResult:
        url = str(args.get("url", "")).strip()
        max_results = int(args.get("max_results", 25))
        if not url.startswith("http://") and not url.startswith("https://"):
            return ToolResult(ok=False, output="url must start with http:// or https://")

        req = urllib.request.Request(url, headers={"User-Agent": "mu_cli/1.0 (+grounding-tool)"}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as exc:
            return ToolResult(ok=False, output=f"URL fetch failed: {exc}")

        hrefs = re.findall(r'href=["\']([^"\']+)["\']', body, flags=re.IGNORECASE)
        links: list[str] = []
        for href in hrefs:
            absolute = urllib.parse.urljoin(url, href.strip())
            if absolute.startswith("http://") or absolute.startswith("https://"):
                links.append(absolute)

        unique: list[str] = []
        seen = set()
        for link in links:
            if link in seen:
                continue
            seen.add(link)
            unique.append(link)
        if not unique:
            return ToolResult(ok=True, output="No links found.")
        return ToolResult(ok=True, output="\n".join(f"- {item}" for item in unique[:max_results]))


class SearchArxivPapersTool:
    name = "search_arxiv_papers"
    description = "Search arXiv papers and return titles, links, and summaries."
    mutating = False
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "arXiv query"},
            "max_results": {"type": "integer", "description": "Maximum papers", "default": 5},
        },
        "required": ["query"],
    }

    def run(self, args: dict) -> ToolResult:
        query = str(args.get("query", "")).strip()
        max_results = int(args.get("max_results", 5))
        if not query:
            return ToolResult(ok=False, output="query is required")
        limit = max(1, min(max_results, 20))
        url = (
            "http://export.arxiv.org/api/query?"
            f"search_query=all:{urllib.parse.quote(query)}&start=0&max_results={limit}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "mu_cli/1.0 (+research-tool)"}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                xml_text = resp.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as exc:
            return ToolResult(ok=False, output=f"arXiv search failed: {exc}")

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            return ToolResult(ok=False, output=f"arXiv XML parse failed: {exc}")

        ns = {"atom": "http://www.w3.org/2005/Atom"}
        rows: list[str] = []
        for entry in root.findall("atom:entry", ns)[:limit]:
            title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip().replace("\n", " ")
            summary = (entry.findtext("atom:summary", default="", namespaces=ns) or "").strip().replace("\n", " ")
            link = ""
            pdf = ""
            for link_node in entry.findall("atom:link", ns):
                href = str(link_node.attrib.get("href", ""))
                rel = str(link_node.attrib.get("rel", ""))
                typ = str(link_node.attrib.get("type", ""))
                if not link and rel == "alternate":
                    link = href
                if not pdf and ("pdf" in typ or link_node.attrib.get("title") == "pdf"):
                    pdf = href
            rows.append(f"- {title}\n  URL: {link}\n  PDF: {pdf}\n  Summary: {summary}")

        if not rows:
            return ToolResult(ok=True, output="No arXiv papers found.")
        return ToolResult(ok=True, output="\n".join(rows))


class FetchPdfContextTool:
    name = "fetch_pdf_context"
    description = "Fetch a PDF URL and extract text for research grounding."
    mutating = False
    schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute PDF URL"},
            "max_chars": {"type": "integer", "description": "Maximum text length", "default": 8000},
        },
        "required": ["url"],
    }

    def run(self, args: dict) -> ToolResult:
        url = str(args.get("url", "")).strip()
        max_chars = int(args.get("max_chars", 8000))
        if not url.startswith("http://") and not url.startswith("https://"):
            return ToolResult(ok=False, output="url must start with http:// or https://")

        req = urllib.request.Request(url, headers={"User-Agent": "mu_cli/1.0 (+grounding-tool)"}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
        except urllib.error.URLError as exc:
            return ToolResult(ok=False, output=f"PDF fetch failed: {exc}")

        module_name = "pypdf" if importlib.util.find_spec("pypdf") else "PyPDF2" if importlib.util.find_spec("PyPDF2") else ""
        if not module_name:
            return ToolResult(ok=False, output="PDF parsing unavailable: install pypdf or PyPDF2")
        pdf_module = importlib.import_module(module_name)
        PdfReader = getattr(pdf_module, "PdfReader", None)
        if PdfReader is None:
            return ToolResult(ok=False, output=f"PDF parsing unavailable: {module_name}.PdfReader not found")

        import io

        try:
            reader = PdfReader(io.BytesIO(raw))
            pages = [page.extract_text() or "" for page in reader.pages]
        except Exception as exc:
            return ToolResult(ok=False, output=f"PDF parse failed: {exc}")

        text = "\n".join(pages).strip()
        if not text:
            return ToolResult(ok=False, output="PDF contained no extractable text")
        return ToolResult(ok=True, output=text[:max_chars])


class CustomCommandTool:
    """User-defined shell command tool with a fixed command template."""

    mutating = True

    def __init__(
        self,
        *,
        name: str,
        description: str,
        command: list[str],
        mutating: bool = True,
        workspace_root_getter: Callable[[], Path | None] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.command = command
        self.mutating = mutating
        self.workspace_root_getter = workspace_root_getter
        self.schema = {
            "type": "object",
            "properties": {
                "args": {
                    "type": "object",
                    "description": "Optional key/value variables used in command placeholders like {path}",
                }
            },
        }

    def run(self, args: dict) -> ToolResult:
        values = args.get("args", {})
        if values is None:
            values = {}
        if not isinstance(values, dict):
            return ToolResult(ok=False, output="args must be an object")

        expanded: list[str] = []
        for token in self.command:
            try:
                expanded.append(token.format_map({k: str(v) for k, v in values.items()}))
            except KeyError as exc:
                return ToolResult(ok=False, output=f"Missing placeholder value: {exc}")

        root = self.workspace_root_getter() if self.workspace_root_getter else None
        proc = subprocess.run(
            expanded,
            text=True,
            capture_output=True,
            cwd=str(root) if root is not None else None,
            timeout=30,
        )
        output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        if proc.returncode != 0:
            return ToolResult(ok=False, output=output.strip() or "command failed")
        return ToolResult(ok=True, output=output.strip() or "ok")
