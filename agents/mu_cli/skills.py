from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    path: Path
    content: str


class SkillStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def list_skills(self) -> list[str]:
        return sorted(path.stem for path in self.root.glob("*.md") if path.is_file())

    def load_skill(self, name: str) -> Skill | None:
        if not name or any(char in name for char in ("/", "\\")):
            return None
        path = self.root / f"{name}.md"
        if not path.exists() or not path.is_file():
            return None
        return Skill(name=name, path=path, content=path.read_text(encoding="utf-8").strip())
