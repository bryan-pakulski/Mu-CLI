"""GUI work surface exposes per-stage model selection (execution.stages)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "mu/gui/templates/work.html").read_text(encoding="utf-8")
JS = (ROOT / "mu/gui/static/js/work.js").read_text(encoding="utf-8")
CSS = (ROOT / "mu/gui/static/css/work.css").read_text(encoding="utf-8")

STAGES = ("plan", "implement", "review")


def test_create_form_has_collapsible_stage_section():
    assert 'id="work-stage-details"' in HTML
    assert "<summary>Stage models</summary>" in HTML


def test_every_stage_has_labelled_provider_and_model_inputs():
    for stage in STAGES:
        for field in ("provider", "model"):
            element_id = f'id="job-stage-{stage}-{field}"'
            assert element_id in HTML, element_id
        assert f'aria-label="{stage.capitalize()} stage provider"' in HTML
        assert f'aria-label="{stage.capitalize()} stage model"' in HTML


def test_submit_sends_only_filled_stages():
    assert "function collectStageModels()" in JS
    assert "if (Object.keys(entry).length) stages[stage] = entry;" in JS
    assert "if (Object.keys(stages).length) execution.stages = stages;" in JS


def test_job_detail_renders_stage_rows():
    view = (ROOT / "mu/gui/static/js/work/view.js").read_text()
    assert "job.stage_models" in view
    assert "muOpenStageModels" in view


def test_stage_inputs_have_focus_ring_and_narrow_layout():
    assert ".work-stage-grid input:focus-visible" in CSS
    assert ".work-stage-grid { grid-template-columns: 1fr 1fr; }" in CSS
