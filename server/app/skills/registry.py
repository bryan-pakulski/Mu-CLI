from dataclasses import dataclass
from pathlib import Path

from server.app.workspace.discovery import WorkspaceStore

@dataclass
class SkillDefinition:
    name: str
    description: str
    file_path: str


class SkillRegistry:
    def __init__(self) -> None:
        self._store_root = Path(__file__).resolve().parents[2] / "store" / "skills"
        self._ensure_store()

    def _ensure_store(self) -> None:
        self._store_root.mkdir(parents=True, exist_ok=True)
        has_skill = any(self._store_root.glob("*/SKILL.md"))
        if has_skill:
            return
        default_skill_dir = self._store_root / "default"
        default_skill_dir.mkdir(parents=True, exist_ok=True)
        (default_skill_dir / "SKILL.md").write_text(
            "# default\n\nDefault local skill stored in server/store/skills.\n",
            encoding="utf-8",
        )

    @property
    def root(self) -> Path:
        self._ensure_store()
        return self._store_root

    def discover(self, workspace: WorkspaceStore | None = None) -> list[SkillDefinition]:
        self._ensure_store()
        skills: list[SkillDefinition] = []
        for skill_file in sorted(self._store_root.glob("*/SKILL.md")):
            name = skill_file.parent.name
            skills.append(
                SkillDefinition(
                    name=name,
                    description="Local server skill",
                    file_path=str(skill_file.relative_to(self._store_root)),
                )
            )
        return skills

    def get_skill_file(self, skill_name: str) -> Path | None:
        safe_name = skill_name.strip().replace("..", "")
        if not safe_name:
            return None
        skill_file = self._store_root / safe_name / "SKILL.md"
        if not skill_file.exists():
            return None
        return skill_file


skill_registry = SkillRegistry()
