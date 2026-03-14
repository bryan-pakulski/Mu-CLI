from server.app.tools.registry import (
    ApplyPatchTool,
    ClearUploadedContextStoreTool,
    GetUploadedContextFileTool,
    GetWorkspaceFileContextTool,
    GitTool,
    FetchUrlContextTool,
    FetchPdfContextTool,
    ExtractLinksContextTool,
    SearchWebContextTool,
    SearchArxivPapersTool,
    ScoreSourcesTool,
    CustomCommandTool,
    ListUploadedContextFilesTool,
    ListWorkspaceFilesTool,
    MakefileAgentTool,
    ReadFileTool,
    WriteFileTool,
)

# Custom command Tool is not included in __all__ because it has no name until we define it outselves
__all__ = [
    ApplyPatchTool,
    GitTool,
    FetchUrlContextTool,
    FetchPdfContextTool,
    ExtractLinksContextTool,
    SearchWebContextTool,
    SearchArxivPapersTool,
    ScoreSourcesTool,
    MakefileAgentTool,
    ListWorkspaceFilesTool,
    GetWorkspaceFileContextTool,
    ListUploadedContextFilesTool,
    GetUploadedContextFileTool,
    ClearUploadedContextStoreTool,
    ReadFileTool,
    WriteFileTool,
]

def get_all_tools() -> list:
    return __all__

def get_tool_by_name(name: str) -> object | None:
    if name in __all__:
        return globals()[name]
