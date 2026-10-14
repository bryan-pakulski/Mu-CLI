"""W10 web: live diff, environment approval banner, budget header, git URL in the New job sheet."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "mu" / "gui" / "static"
HTML = (ROOT / "mu" / "gui" / "templates" / "work.html").read_text(encoding="utf-8")
VIEW = (STATIC / "js" / "work" / "view.js").read_text(encoding="utf-8")
SHELL = (STATIC / "js" / "work.js").read_text(encoding="utf-8")


def test_diff_tab_prefers_live_diff_and_polls_only_the_diff_part():
    assert "j?.diff?.live ? j.diff.live.patch : j?.diff?.patch" in VIEW
    assert "parsePatch(diffPatch(job))" in VIEW
    assert "LIVE_DIFF_MS = 5000" in VIEW and "setInterval(() => { void refreshLiveDiff(); }, LIVE_DIFF_MS)" in VIEW
    assert "?parts=diff`" in VIEW and "tab !== 'diff' || !job?.diff?.live" in VIEW
    # Viewed marks belong to the checkpointed diff; the live view hides them.
    assert "const seen = live ? new Set() : viewed();" in VIEW
    assert "data-live-diff" in VIEW


def test_environment_approval_banner_uses_shared_actions():
    assert "job.environment_approval" in VIEW
    assert "BANNER_ACTIONS = new Set(['approveTool', 'denyTool', 'approveEnvironment', 'rejectEnvironment'])" in VIEW
    assert "rejectEnvironment:'reason'" in VIEW
    for field in ("ea.kind", "ea.ref", "ea.fingerprint", "ea.label"):
        assert field in VIEW


def test_header_shows_cost_vs_cap_active_time_and_pr_state():
    assert "data-job-budget" in VIEW and "'no cap'" in VIEW and "b.active_seconds" in VIEW
    assert "job.pr.state" in VIEW and "/^https?:\\/\\//.test(job.pr?.url" in VIEW


def test_new_job_sheet_accepts_git_url_as_repository():
    assert "isRepoUrl" in SHELL and "repository: workspace" in SHELL
    assert "remote ? { repository: workspace } : { workspace_root: workspace }" in SHELL
    assert "https://github.com/org/repo.git" in HTML


def test_new_job_sheet_offers_repository_environment_approval():
    assert 'id="job-environments-approval"' in HTML
    assert "'/api/jobs/environments/approve'" in SHELL and "envPreview?.suggestion?.approval" in SHELL
