from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.app.persistence.models import WorkspaceFileIndexModel


def _classify(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix in {".py", ".ts", ".tsx", ".js", ".rs", ".go", ".java"}:
        return "code", suffix.lstrip(".")
    if suffix in {".md", ".txt", ".rst"}:
        return "docs", suffix.lstrip(".")
    if suffix in {".json", ".yaml", ".yml", ".toml", ".ini"}:
        return "config", suffix.lstrip(".")
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".svg"}:
        return "asset", suffix.lstrip(".")
    return "other", suffix.lstrip(".") if suffix else "unknown"


def _describe(path: Path, file_type: str) -> str:
    name = path.name.lower()
    if name in {"readme.md", "readme"}:
        return "Repository or module readme"
    if name.startswith("test_") or path.parent.name == "tests":
        return "Test file"
    if file_type == "config":
        return "Configuration file"
    if file_type == "docs":
        return "Documentation file"
    if file_type == "code":
        return "Source code file"
    return "Project file"


async def index_workspace(session_id: str, workspace_path: str, db: AsyncSession) -> int:
    root = Path(workspace_path)
    if not root.exists():
        return 0

    await db.execute(
        delete(WorkspaceFileIndexModel).where(WorkspaceFileIndexModel.session_id == session_id)
    )
    count = 0

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".git", ".venv", "node_modules", "__pycache__"} for part in path.parts):
            continue

        file_type, language = _classify(path)
        try:
            stat = path.stat()
            content_hash = f"{int(stat.st_mtime_ns)}:{stat.st_size}"
        except OSError:
            continue

        rel_path = str(path.relative_to(root))
        rec = WorkspaceFileIndexModel(
            session_id=session_id,
            path=rel_path,
            file_type=file_type,
            language=language,
            last_modified=int(stat.st_mtime),
            content_hash=content_hash,
            description=_describe(path, file_type),
            key_symbols=[],
            tags=[file_type],
        )
        db.add(rec)
        count += 1

    await db.commit()
    return count


async def list_index(session_id: str, db: AsyncSession) -> list[WorkspaceFileIndexModel]:
    return (
        await db.scalars(
            select(WorkspaceFileIndexModel)
            .where(WorkspaceFileIndexModel.session_id == session_id)
            .order_by(WorkspaceFileIndexModel.path)
        )
    ).all()
