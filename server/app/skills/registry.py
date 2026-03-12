from dataclasses import dataclass
from pathlib import Path


@dataclass
class SkillDefinition:
    name: str
    description: str
    file_path: str


class SkillRegistry:
    def discover(self, workspace_path: str) -> list[SkillDefinition]:
        root = Path(workspace_path)
        if not root.exists():
            return []

        skills: list[SkillDefinition] = []
        for skill_file in root.rglob("SKILL.md"):
            if any(
                part in {".git", ".venv", "node_modules", "__pycache__"}
                for part in skill_file.parts
            ):
                continue
            name = skill_file.parent.name
            description = "Workspace-discovered skill"
            skills.append(
                SkillDefinition(
                    name=name,
                    description=description,
                    file_path=str(skill_file.relative_to(root)),
                )
            )
        return skills


skill_registry = SkillRegistry()
