"""GUI Engineering Work surfaces expose the single-step (cancel -> archive -> purge) delete."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_work_detail_panel_offers_force_delete_for_every_status():
    work = _read("mu/gui/static/js/work/view.js")
    assert "'delete'" in work
    assert "const action = job?.actions?.find" in work
    assert "api('/api/jobs/cmd'" in work
    assert "Git branch is preserved" in work
    assert "selectedId = ''; delete detailEl.dataset.jobId" in work


def test_work_management_delete_is_available_for_active_rows_and_bulk_forces():
    mgmt = _read("mu/gui/static/js/work_management.js")
    assert 'data-manage-action="delete"' in mgmt
    assert "${job.archived ? `<button" not in mgmt  # no longer gated on archived
    assert "?force=1`, { method: 'DELETE' }" in mgmt
    assert "force: action === 'delete'" in mgmt
    assert "Running jobs are cancelled first" in mgmt
