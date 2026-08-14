"""MuCLI visualization skill, theme bridge, and visual integrity contract."""
from __future__ import annotations

import asyncio
from pathlib import Path

import mu.gui.routers.artifacts as artifact_router
from mu.artifact import ArtifactRegistry
from mu.skills import clear_skill_cache, discover_skills, match_trigger


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_visualization_design_is_a_triggered_builtin_skill():
    clear_skill_cache()
    skill = next(item for item in discover_skills([]) if item.name == "visualization-design")

    assert match_trigger(skill, "Plot a heatmap of context over time")
    assert "Start bar axes at zero" in skill.body
    assert "Preserve missing values as missing" in skill.body
    assert "uncertainty" in skill.body
    assert "one quiet glass surface" in skill.body
    assert "prefers-reduced-motion" in skill.body
    assert "320px" in skill.body
    assert "mucli-theme-change" in skill.body


def test_visualization_template_has_both_themes_and_accessibility_hooks():
    template = read("mu/skills/visualization-design/assets/template.html")

    assert ':root[data-theme="light"]' in template
    assert ':root[data-theme="dark"]' in template
    assert "--mu-accent" in template
    assert "prefers-reduced-motion" in template
    assert 'role="img"' in template
    assert "mucli-theme-change" in template


def test_artifact_view_injects_the_theme_contract(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions" / "demo"
    registry = ArtifactRegistry(str(session_dir))
    artifact = registry.add(
        name="themed.html",
        content="<!doctype html><html><head></head><body>chart</body></html>",
        mime_type="text/html",
        kind="visualization",
    )
    monkeypatch.setattr(artifact_router, "HISTORY_DIR", str(tmp_path))

    response = asyncio.run(artifact_router.view_artifact("demo", artifact["artifact_id"]))
    body = bytes(response.body).decode("utf-8")

    assert 'id="mucli-visualization-theme"' in body
    assert "new URLSearchParams(location.search).get('mucli_theme')" in body
    assert "document.documentElement.dataset.theme = theme" in body
    assert "mucli-theme-change" in body


def test_web_and_mobile_forward_the_actual_mucli_theme():
    web = read("mu/gui/static/js/app.js")
    chat = read("mu/gui/templates/fragments/chat.html")
    mobile = read("mobile/android/src/components/VisualizationCard.tsx")
    tool = read("mu/tools/artifact/handlers.py")

    assert "visualizationThemeUrl(t.artifact.view_url, $store.theme.current)" in chat
    assert 'frame.contentWindow?.postMessage({ type: "mucli-theme", theme }, "*")' in web
    assert 'url.searchParams.set("mucli_theme"' in web
    assert "const { colors, isDark } = useTheme()" in mobile
    assert "?mucli_theme=${isDark ? 'dark' : 'light'}" in mobile
    assert "visualization-design skill" in tool
