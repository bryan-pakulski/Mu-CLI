"""Versioned plan document (guide §5, decision D2).

``plan.md`` = YAML-ish front-matter + fixed markdown sections. ``PlanDoc``
parses/renders it losslessly for the structured fields; every save writes
``plans/plan.v<N>.md`` and copies the newest to ``plan.md``.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

SECTIONS = (
    ("summary", "Summary"),
    ("approach", "Approach"),
    ("acceptance", "Acceptance criteria"),
    ("checks", "Checks"),
    ("files", "Files likely touched"),
    ("risks", "Risks / out of scope"),
)
_TITLE_TO_KEY = {title.lower(): key for key, title in SECTIONS}
_AC_RE = re.compile(r"^\s*[-*]\s*\[(?P<id>[A-Za-z0-9_.-]+)\]\s*(?P<text>.*)$")
_CHECK_RE = re.compile(r"^\s*[-*]\s*suite:\s*(?P<suite>\S+)\s+cmd:\s*(?P<cmd>.+?)\s*$")
_NUM_RE = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+(?P<text>.*)$")


class PlanValidationError(ValueError):
    def __init__(self, errors: List[str]):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


@dataclass
class Criterion:
    id: str
    text: str


@dataclass
class CheckSpec:
    suite: str
    cmd: str


@dataclass
class PlanDoc:
    summary: str = ""
    approach: List[str] = field(default_factory=list)
    acceptance: List[Criterion] = field(default_factory=list)
    checks: List[CheckSpec] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    version: int = 1
    author: str = "planner"
    based_on: Optional[int] = None

    # ------------------------------------------------------------ convert
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanDoc":
        data = dict(data or {})
        acceptance = []
        for i, item in enumerate(data.get("acceptance") or [], 1):
            if isinstance(item, dict):
                acceptance.append(Criterion(str(item.get("id") or f"AC-{i}").strip(),
                                            str(item.get("text") or "").strip()))
            else:
                acceptance.append(Criterion(f"AC-{i}", str(item).strip()))
        checks = []
        for i, item in enumerate(data.get("checks") or [], 1):
            if isinstance(item, dict):
                checks.append(CheckSpec(str(item.get("suite") or f"check{i}").strip(),
                                        str(item.get("cmd") or "").strip()))
            else:
                checks.append(CheckSpec(f"check{i}", str(item).strip()))
        based_on = data.get("based_on")
        return cls(
            summary=str(data.get("summary") or "").strip(),
            approach=_clean(data.get("approach")),
            acceptance=acceptance,
            checks=checks,
            files=_clean(data.get("files")),
            risks=_clean(data.get("risks")),
            version=max(1, int(data.get("version") or 1)),
            author=str(data.get("author") or "planner"),
            based_on=int(based_on) if based_on not in (None, "", "null") else None,
        )

    @classmethod
    def from_legacy(cls, plan: Dict[str, Any], job: Any = None) -> "PlanDoc":
        """Build from the legacy ``metadata.plan`` dict (stages.parse_plan shape)."""
        plan = dict(plan or {})
        acs = list(plan.get("acceptance_criteria") or [])
        cmds = list(plan.get("validation_commands") or [])
        if job is not None:
            acs = acs or list(getattr(job, "acceptance_criteria", None) or [])
            cmds = cmds or list(getattr(job, "validation_commands", None) or [])
        return cls(
            summary=str(plan.get("summary") or "").strip(),
            approach=_clean(plan.get("steps")),
            acceptance=[Criterion(f"AC-{i}", str(t).strip()) for i, t in enumerate(acs, 1) if str(t).strip()],
            checks=[CheckSpec(f"check{i}", str(c).strip()) for i, c in enumerate(cmds, 1) if str(c).strip()],
            files=_clean(plan.get("files")),
            risks=_clean(plan.get("risks")),
            version=max(1, int(plan.get("version") or 1)),
            author="planner",
        )

    # ------------------------------------------------------------ render
    def render(self) -> str:
        based = "null" if self.based_on is None else str(self.based_on)
        out = ["---", f"version: {self.version}", f"author: {self.author}", f"based_on: {based}", "---"]
        out += ["## Summary", self.summary.strip(), ""]
        out += ["## Approach"] + [f"{i}. {s}" for i, s in enumerate(self.approach, 1)] + [""]
        out += ["## Acceptance criteria"] + [f"- [{c.id}] {c.text}" for c in self.acceptance] + [""]
        out += ["## Checks"] + [f"- suite: {c.suite}   cmd: {c.cmd}" for c in self.checks] + [""]
        out += ["## Files likely touched"] + [f"- {f}" for f in self.files] + [""]
        out += ["## Risks / out of scope"] + [f"- {r}" for r in self.risks]
        return "\n".join(out).rstrip() + "\n"

    @classmethod
    def parse(cls, text: str) -> "PlanDoc":
        front, body = _split_front_matter(str(text or ""))
        sections: Dict[str, List[str]] = {key: [] for key, _ in SECTIONS}
        current: Optional[str] = None
        for line in body.splitlines():
            m = re.match(r"^\s*##\s+(.*?)\s*$", line)
            if m:
                current = _TITLE_TO_KEY.get(m.group(1).strip().lower())
                continue
            if current is not None:
                sections[current].append(line)
        acceptance, checks = [], []
        for line in sections["acceptance"]:
            m = _AC_RE.match(line)
            if m:
                acceptance.append(Criterion(m.group("id"), m.group("text").strip()))
            elif line.strip():
                item = _list_item(line)
                if item:
                    acceptance.append(Criterion(f"AC-{len(acceptance) + 1}", item))
        for line in sections["checks"]:
            m = _CHECK_RE.match(line)
            if m:
                checks.append(CheckSpec(m.group("suite"), m.group("cmd")))
            elif line.strip():
                item = _list_item(line)
                if item:
                    checks.append(CheckSpec(f"check{len(checks) + 1}", item))
        based = front.get("based_on")
        return cls(
            summary="\n".join(sections["summary"]).strip(),
            approach=[i for i in (_list_item(l) for l in sections["approach"]) if i],
            acceptance=acceptance,
            checks=checks,
            files=[i for i in (_list_item(l) for l in sections["files"]) if i],
            risks=[i for i in (_list_item(l) for l in sections["risks"]) if i],
            version=max(1, int(front.get("version") or 1)),
            author=str(front.get("author") or "planner"),
            based_on=int(based) if based not in (None, "", "null") else None,
        )

    # ------------------------------------------------------------ validate
    def validate(self, allowed: Optional[frozenset] = None) -> List[str]:
        """Return validation errors (empty = valid). Guide §5.1."""
        from ..dynamic_checks import DEFAULT_ALLOWED_EXECUTABLES, check_command

        allowed = allowed if allowed is not None else DEFAULT_ALLOWED_EXECUTABLES
        errors: List[str] = []
        ids = [c.id for c in self.acceptance]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            errors.append(f"duplicate acceptance criterion ids: {', '.join(dupes)}")
        if any(not c.id.strip() for c in self.acceptance):
            errors.append("acceptance criterion id is empty")
        if any(not c.text.strip() for c in self.acceptance):
            errors.append("acceptance criterion text is empty")
        suites = [c.suite for c in self.checks]
        if len(set(suites)) != len(suites):
            errors.append("duplicate check suite names")
        for c in self.checks:
            ok, why = check_command(c.cmd, allowed)
            if not ok:
                errors.append(f"check {c.suite!r}: {why}")
        if not self.acceptance and not self.checks:
            errors.append("plan needs at least one acceptance criterion or check")
        return errors

    def ensure_valid(self, allowed: Optional[frozenset] = None) -> "PlanDoc":
        errors = self.validate(allowed)
        if errors:
            raise PlanValidationError(errors)
        return self


# ------------------------------------------------------------------ helpers
def _clean(values: Any) -> List[str]:
    if isinstance(values, str):
        values = [values]
    return [str(v).strip() for v in (values or []) if str(v).strip()]


def _list_item(line: str) -> str:
    m = _NUM_RE.match(line)
    return (m.group("text") if m else line).strip()


def _split_front_matter(text: str) -> Tuple[Dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    front: Dict[str, str] = {}
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return front, "\n".join(lines[idx + 1:])
        key, _, value = lines[idx].partition(":")
        if key.strip():
            front[key.strip()] = value.strip()
    return {}, text  # unterminated front-matter: treat whole thing as body


# ------------------------------------------------------------------ store
class PlanStore:
    """Versioned plan persistence on top of ``JobArtifacts`` (guide §5.1)."""

    def __init__(self, artifacts: Any):
        self.art = artifacts

    def versions(self) -> List[int]:
        import os

        out = []
        try:
            names = os.listdir(self.art.plans_dir)
        except FileNotFoundError:
            return []
        for name in names:
            m = re.match(r"^plan\.v(\d+)\.md$", name)
            if m:
                out.append(int(m.group(1)))
        return sorted(out)

    def latest_version(self) -> int:
        v = self.versions()
        return v[-1] if v else 0

    def load(self, version: Optional[int] = None) -> Optional[PlanDoc]:
        if version is None:
            text = self.art.read_text("plan.md")
        else:
            text = self.art.read_text(f"plans/plan.v{int(version)}.md")
        return PlanDoc.parse(text) if text is not None else None

    def load_text(self, version: Optional[int] = None) -> Optional[str]:
        if version is None:
            return self.art.read_text("plan.md")
        return self.art.read_text(f"plans/plan.v{int(version)}.md")

    def save(self, doc: PlanDoc, *, author: str, allowed: Optional[frozenset] = None) -> PlanDoc:
        """Validate and write a NEW version; ``plan.md`` mirrors the newest."""
        latest = self.latest_version()
        new = PlanDoc.from_dict(doc.to_dict())
        new.based_on = latest or None
        new.version = latest + 1
        new.author = author
        new.ensure_valid(allowed)
        text = new.render()
        self.art.ensure()
        self.art.write_text(f"plans/plan.v{new.version}.md", text)
        self.art.write_text("plan.md", text)
        self.clear_draft()
        return new

    def save_draft(self, text: str) -> None:
        self.art.ensure()
        self.art.write_text("plans/draft.md", str(text or ""))

    def load_draft(self) -> Optional[str]:
        return self.art.read_text("plans/draft.md")

    def clear_draft(self) -> None:
        import os

        try:
            os.remove(self.art.draft)
        except FileNotFoundError:
            pass


def diff_versions(old_text: str, new_text: str) -> str:
    import difflib

    return "".join(difflib.unified_diff(
        (old_text or "").splitlines(keepends=True), (new_text or "").splitlines(keepends=True),
        fromfile="previous", tofile="current",
    ))
