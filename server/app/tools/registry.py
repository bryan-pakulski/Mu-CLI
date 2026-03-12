import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class ToolDefinition:
    name: str
    description: str
    risk_level: str
    requires_approval: bool


_DEFAULT_TOOLS: list[ToolDefinition] = [
    ToolDefinition("read_file", "Read file contents from the workspace", "low", False),
    ToolDefinition("write_file", "Write or overwrite a file in the workspace", "medium", True),
    ToolDefinition("apply_patch", "Apply a structured patch to workspace files", "medium", True),
    ToolDefinition("git", "Run non-modifying git commands (status, log, diff, show)", "low", False),
    ToolDefinition("fetch_url_context", "Fetch page content and extract textual context from a URL", "low", False),
    ToolDefinition("fetch_pdf_context", "Fetch and extract text context from PDF resources", "low", False),
    ToolDefinition("extract_links_context", "Extract and summarize links from a web page", "low", False),
    ToolDefinition("search_web_context", "Search the web and return contextual snippets", "low", False),
    ToolDefinition("search_arxiv_papers", "Search arXiv papers and return relevant metadata/context", "low", False),
    ToolDefinition("score_sources", "Score and rank gathered context sources", "low", False),
    ToolDefinition("list_workspace_files", "List files in the configured workspace", "low", False),
    ToolDefinition("get_workspace_file_context", "Get contextual snippet(s) for a workspace file", "low", False),
    ToolDefinition("run_make_agent_job", "Queue a nested make-agent job", "high", True),
    ToolDefinition("list_uploaded_context_files", "List uploaded context files available to the session", "low", False),
    ToolDefinition("get_uploaded_context_file", "Read a specific uploaded context file", "low", False),
    ToolDefinition("clear_uploaded_context_store", "Clear uploaded context file storage", "medium", True),
    ToolDefinition("retrieve_conversation_summary", "Retrieve condensed summary for prior conversation state", "low", False),
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
