import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class ToolDefinition:
    name: str
    description: str
    risk_level: str
    requires_approval: bool
    executor: dict | None = None


_DEFAULT_TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        "read_file",
        "Read file contents from the workspace",
        "low",
        False,
        {"kind": "builtin", "name": "read_file"},
    ),
    ToolDefinition(
        "write_file",
        "Write or overwrite a file in the workspace",
        "medium",
        True,
        {"kind": "builtin", "name": "write_file"},
    ),
    ToolDefinition(
        "apply_patch",
        "Apply a structured patch to workspace files",
        "medium",
        True,
        {"kind": "builtin", "name": "apply_patch"},
    ),
    ToolDefinition(
        "git",
        "Run non-modifying git commands (status, log, diff, show)",
        "low",
        False,
        {"kind": "builtin", "name": "git"},
    ),
    ToolDefinition(
        "fetch_url_context",
        "Fetch page content and extract textual context from a URL",
        "low",
        False,
        {"kind": "builtin", "name": "fetch_url_context"},
    ),
    ToolDefinition(
        "fetch_pdf_context",
        "Fetch and extract text context from PDF resources",
        "low",
        False,
        {"kind": "builtin", "name": "fetch_pdf_context"},
    ),
    ToolDefinition(
        "extract_links_context",
        "Extract and summarize links from a web page",
        "low",
        False,
        {"kind": "builtin", "name": "extract_links_context"},
    ),
    ToolDefinition(
        "search_web_context",
        "Search the web and return contextual snippets",
        "low",
        False,
        {"kind": "builtin", "name": "search_web_context"},
    ),
    ToolDefinition(
        "search_arxiv_papers",
        "Search arXiv papers and return relevant metadata/context",
        "low",
        False,
        {"kind": "builtin", "name": "search_arxiv_papers"},
    ),
    ToolDefinition(
        "score_sources",
        "Score and rank gathered context sources",
        "low",
        False,
        {"kind": "builtin", "name": "score_sources"},
    ),
    ToolDefinition(
        "list_workspace_files",
        "List files in the configured workspace",
        "low",
        False,
        {"kind": "builtin", "name": "list_workspace_files"},
    ),
    ToolDefinition(
        "get_workspace_file_context",
        "Get contextual snippet(s) for a workspace file",
        "low",
        False,
        {"kind": "builtin", "name": "get_workspace_file_context"},
    ),
    ToolDefinition(
        "execute_command",
        "Run a command in the workspace and capture stdout/stderr",
        "high",
        True,
        {"kind": "builtin", "name": "execute_command"},
    ),
    ToolDefinition(
        "run_make_agent_job",
        "Queue a nested make-agent job",
        "high",
        True,
        {"kind": "builtin", "name": "run_make_agent_job"},
    ),
    ToolDefinition(
        "list_uploaded_context_files",
        "List uploaded context files available to the session",
        "low",
        False,
        {"kind": "builtin", "name": "list_uploaded_context_files"},
    ),
    ToolDefinition(
        "get_uploaded_context_file",
        "Read a specific uploaded context file",
        "low",
        False,
        {"kind": "builtin", "name": "get_uploaded_context_file"},
    ),
    ToolDefinition(
        "clear_uploaded_context_store",
        "Clear uploaded context file storage",
        "medium",
        True,
        {"kind": "builtin", "name": "clear_uploaded_context_store"},
    ),
    ToolDefinition(
        "retrieve_conversation_summary",
        "Retrieve condensed summary for prior conversation state",
        "low",
        False,
        {"kind": "builtin", "name": "retrieve_conversation_summary"},
    ),
]


class ToolRegistry:
    def __init__(self) -> None:
        self._store_root = Path(__file__).resolve().parents[2] / "store" / "tools"
        self._store_file = self._store_root / "tools.json"
        self._tools: dict[str, ToolDefinition] = {}
        self._ensure_store()
        self._load_tools()

    def _ensure_store(self) -> None:
        self._store_root.mkdir(parents=True, exist_ok=True)
        if self._store_file.exists():
            return
        self._store_file.write_text(
            json.dumps([asdict(tool) for tool in _DEFAULT_TOOLS], indent=2) + "\n",
            encoding="utf-8",
        )

    def _load_tools(self) -> None:
        try:
            payload = json.loads(self._store_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = [asdict(tool) for tool in _DEFAULT_TOOLS]

        parsed: dict[str, ToolDefinition] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            parsed[name] = ToolDefinition(
                name=name,
                description=str(item.get("description", "")),
                risk_level=str(item.get("risk_level", "low")),
                requires_approval=bool(item.get("requires_approval", False)),
                executor=item.get("executor") if isinstance(item.get("executor"), dict) else None,
            )

        if not parsed:
            parsed = {tool.name: tool for tool in _DEFAULT_TOOLS}
        self._tools = parsed

    def list_tools(self) -> list[ToolDefinition]:
        self._load_tools()
        return list(self._tools.values())

    def get(self, name: str) -> ToolDefinition | None:
        self._load_tools()
        return self._tools.get(name)


tool_registry = ToolRegistry()
