from pathlib import Path

from sqlalchemy import delete, func, select
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


def _priority(path: str, file_type: str, tags: list[str]) -> int:
    score = 0
    lower = path.lower()
    if "readme" in lower:
        score += 100
    if "main" in lower or "app" in lower:
        score += 60
    if file_type == "code":
        score += 40
    if file_type == "config":
        score += 25
    if "test" in lower:
        score -= 20
    if "generated" in tags:
        score -= 50
    return score


def _should_skip(path: Path) -> bool:
    return any(part in {".git", ".venv", "node_modules", "__pycache__"} for part in path.parts)


async def index_workspace(session_id: str, workspace_path: str, db: AsyncSession) -> int:
    root = Path(workspace_path)
    if not root.exists():
        return 0

    await db.execute(
        delete(WorkspaceFileIndexModel).where(WorkspaceFileIndexModel.session_id == session_id)
    )
    count = 0

    for path in root.rglob("*"):
        if not path.is_file() or _should_skip(path):
            continue

        file_type, language = _classify(path)
        try:
            stat = path.stat()
            content_hash = f"{int(stat.st_mtime_ns)}:{stat.st_size}"
        except OSError:
            continue

        rel_path = str(path.relative_to(root))
        tags = [file_type]
        rec = WorkspaceFileIndexModel(
            session_id=session_id,
            path=rel_path,
            file_type=file_type,
            language=language,
            last_modified=int(stat.st_mtime),
            content_hash=content_hash,
            description=_describe(path, file_type),
            key_symbols=[],
            tags=tags,
            priority_score=_priority(rel_path, file_type, tags),
        )
        db.add(rec)
        count += 1

    await db.commit()
    return count


async def refresh_workspace_index(
    session_id: str,
    workspace_path: str,
    db: AsyncSession,
) -> dict:
    root = Path(workspace_path)
    if not root.exists():
        return {"indexed_files": 0, "added": 0, "updated": 0, "removed": 0}

    existing_rows = (
        await db.scalars(
            select(WorkspaceFileIndexModel).where(
                WorkspaceFileIndexModel.session_id == session_id
            )
        )
    ).all()
    existing_by_path = {row.path: row for row in existing_rows}

    seen: set[str] = set()
    added = 0
    updated = 0

    for path in root.rglob("*"):
        if not path.is_file() or _should_skip(path):
            continue
        try:
            stat = path.stat()
            content_hash = f"{int(stat.st_mtime_ns)}:{stat.st_size}"
        except OSError:
            continue

        rel_path = str(path.relative_to(root))
        seen.add(rel_path)
        file_type, language = _classify(path)
        tags = [file_type]

        existing = existing_by_path.get(rel_path)
        if existing is None:
            db.add(
                WorkspaceFileIndexModel(
                    session_id=session_id,
                    path=rel_path,
                    file_type=file_type,
                    language=language,
                    last_modified=int(stat.st_mtime),
                    content_hash=content_hash,
                    description=_describe(path, file_type),
                    key_symbols=[],
                    tags=tags,
                    priority_score=_priority(rel_path, file_type, tags),
                )
            )
            added += 1
            continue

        if existing.content_hash != content_hash:
            existing.file_type = file_type
            existing.language = language
            existing.last_modified = int(stat.st_mtime)
            existing.content_hash = content_hash
            existing.description = _describe(path, file_type)
            existing.tags = tags
            existing.priority_score = _priority(rel_path, file_type, tags)
            updated += 1

    removed = 0
    for rel_path, existing in existing_by_path.items():
        if rel_path not in seen:
            await db.delete(existing)
            removed += 1

    await db.commit()

    total = await db.scalar(
        select(func.count())
        .select_from(WorkspaceFileIndexModel)
        .where(WorkspaceFileIndexModel.session_id == session_id)
    )
    return {
        "indexed_files": int(total or 0),
        "added": added,
        "updated": updated,
        "removed": removed,
    }


async def list_index(
    session_id: str,
    db: AsyncSession,
    file_type: str | None = None,
    tag: str | None = None,
    limit: int | None = None,
) -> list[WorkspaceFileIndexModel]:
    query = select(WorkspaceFileIndexModel).where(WorkspaceFileIndexModel.session_id == session_id)
    if file_type:
        query = query.where(WorkspaceFileIndexModel.file_type == file_type)
    if tag:
        query = query.where(WorkspaceFileIndexModel.tags.contains([tag]))

    query = query.order_by(
        WorkspaceFileIndexModel.priority_score.desc(),
        WorkspaceFileIndexModel.path,
    )
    if limit and limit > 0:
        query = query.limit(limit)

    return (await db.scalars(query)).all()
