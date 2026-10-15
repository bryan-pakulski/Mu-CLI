"""GUI work surface exposes per-stage model selection (execution.stages)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "mu/gui/templates/work.html").read_text(encoding="utf-8")
JS = (ROOT / "mu/gui/static/js/work.js").read_text(encoding="utf-8")
CSS = (ROOT / "mu/gui/static/css/wash/patterns/work.css").read_text(encoding="utf-8")  # Wash W4: /work page bundle

STAGES = ("plan", "implement", "review")


AGENTS = (ROOT / "mu/gui/static/js/work/agents.js").read_text(encoding="utf-8")


def test_create_form_has_one_per_stage_picker_under_advanced():
    # One agent+model override picker per stage, inside the sheet's Advanced section.
    assert 'id="work-advanced-details"' in HTML and "<summary>Advanced</summary>" in HTML
    assert HTML.index('id="job-agents"') > HTML.index('id="work-advanced-details"')
    assert 'id="work-stage-details"' not in HTML and 'id="work-execution-details"' not in HTML


def test_every_stage_has_labelled_provider_and_model_inputs():
    for field in ("provider", "model"):
        assert f'aria-label="${{STAGE_LABEL[stage]}} stage {field}"' in AGENTS
    assert 'id="job-stage-${stage}-provider"' in AGENTS and 'data-agent-model="${stage}"' in AGENTS


def test_submit_sends_only_filled_stages():
    assert "function collectModels()" in AGENTS
    assert "if (m) stages[stage] = { ...(p ? { provider: p } : {}), model: m };" in AGENTS
    assert "const stages = agentPicker?.collectModels() || {};" in JS
    assert "if (Object.keys(stages).length) execution.stages = stages;" in JS


def test_job_detail_renders_stage_rows():
    view = (ROOT / "mu/gui/static/js/work/view.js").read_text()
    assert "job.stage_models" in view
    assert "muOpenStageModels" in view


def test_stage_inputs_have_focus_ring_and_narrow_layout():
    # Wash W4: inputs and selects in the stage grid keep the accent focus ring;
    # the grid is two columns and collapses to one below the md breakpoint.
    assert ".work-stage-grid :is(input, select):focus-visible" in CSS
    grid = CSS.split(".work-stage-grid {", 1)[1].split("}", 1)[0]
    assert "grid-template-columns: 1fr 1fr;" in grid
    assert ".work-stage-grid, .work-stage-model-row { grid-template-columns: 1fr; }" in CSS
