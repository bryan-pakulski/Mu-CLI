"""Generate the job-settings reference table in documentation/configuration.md.

The table between ``<!-- job-settings:begin ... -->`` and ``<!-- job-settings:end -->``
is generated from :data:`mu.jobs.job_settings.RULES_SCHEMA` so it never falls behind
the settings the GUI, Android app and ``/job settings`` actually offer.

    python3 -m mu.jobs.settings_doc            # print the table
    python3 -m mu.jobs.settings_doc --write    # rewrite documentation/configuration.md
    python3 -m mu.jobs.settings_doc --check    # exit 1 when the document is stale

``tests/test_job_settings_doc.py`` asserts the committed table matches.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

BEGIN = "<!-- job-settings:begin (generated from mu/jobs/job_settings.py RULES_SCHEMA) -->"
END = "<!-- job-settings:end -->"
DOC_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        "documentation", "configuration.md")


def _default_text(field: Dict[str, Any]) -> str:
    from .job_settings import _group

    value = field.get("default")
    if value is None or value == "" or value == []:
        return "—"
    kind = field["kind"]
    if kind == "bool":
        return "on" if value else "off"
    if kind in ("int", "float"):
        return _group(value) + (f" {field['unit']}" if field.get("unit") else "")
    if isinstance(value, (list, tuple)):
        return ", ".join(f"`{v}`" for v in value)
    return f"`{value}`"


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def markdown_table(schema: Optional[List[Dict[str, Any]]] = None) -> str:
    """Markdown for every RULES_SCHEMA section (without the begin/end markers)."""
    from .job_settings import RULES_SCHEMA

    blocks = []
    for section in schema if schema is not None else RULES_SCHEMA:
        rows = [f"**{section['title']}**", "", "| Key | Default | Description |", "| --- | --- | --- |"]
        for field in section["fields"]:
            hint = str(field.get("hint") or "").strip()
            description = f"{field['label']}. {hint}" if hint else f"{field['label']}."
            rows.append(f"| `{field['path']}` | {_cell(_default_text(field))} | {_cell(description)} |")
        blocks.append("\n".join(rows))
    return "\n\n".join(blocks)


def render_document(text: str, table: Optional[str] = None) -> str:
    """``text`` with the generated block replaced (raises ``ValueError`` without markers)."""
    start = text.find(BEGIN)
    end = text.find(END)
    if start < 0 or end < start:
        raise ValueError("job-settings markers not found in configuration.md")
    body = table if table is not None else markdown_table()
    return text[:start] + BEGIN + "\n" + body + "\n" + text[end:]


def current_block(text: str) -> str:
    start = text.find(BEGIN)
    end = text.find(END)
    if start < 0 or end < start:
        raise ValueError("job-settings markers not found in configuration.md")
    return text[start + len(BEGIN):end].strip("\n")


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--write" in args or "--check" in args:
        with open(DOC_PATH, "r", encoding="utf-8") as handle:
            text = handle.read()
        updated = render_document(text)
        if "--check" in args:
            if updated != text:
                print(f"{DOC_PATH}: job-settings table is stale; run python3 -m mu.jobs.settings_doc --write")
                return 1
            return 0
        if updated != text:
            with open(DOC_PATH, "w", encoding="utf-8") as handle:
                handle.write(updated)
        print(f"wrote {DOC_PATH}")
        return 0
    print(markdown_table())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
