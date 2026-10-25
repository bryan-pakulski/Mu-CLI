"""Outputs come from the ledger: report skeleton, bibliography, exports."""

import os

import pytest

from mu.research import ResearchState
from mu.research.report import report_skeleton
from mu.tools.research.ledger_tools import research_export
from mu.workspace.folder_context import FolderContext

from research_helpers import research_session, tool_context

PAD = "Unrelated context sentence for padding the page body. " * 6


def _read(state, url, sentence, credibility):
    s = state.mark_read(url=url, title=f"Title of {url.rsplit('/', 1)[-1]}", text=PAD + sentence + PAD)
    state.assess_source(s.citation_id, credibility)
    return s.citation_id


def _ev(cid, excerpt, stance="supports"):
    return {"citation_id": cid, "stance": stance, "excerpt": excerpt}


@pytest.fixture
def seeded():
    state = ResearchState()
    state.add_question("How fast is the new chip?")           # q1
    state.add_question("What does it cost?")                  # q2
    state.add_question("Who makes it?")                       # q3 — left without claims
    state.update_question("q1", select=True)
    state.add_candidate(url="https://rumours.example/leak", title="Leak thread")
    state.update_question("q2", select=True)
    price_a = _read(state, "https://shop.example/listing", "The chip retails for 499 dollars.", 0.7)
    price_b = _read(state, "https://review.example/price", "Launch price was 450 dollars.", 0.8)
    state.update_question("q1", select=True)
    speed_a = _read(state, "https://lab.example/bench", "Benchmarks show a 30 percent speedup.", 0.9)
    speed_b = _read(state, "https://press.example/launch", "A 30 percent speedup over last year.", 0.8)
    unused = _read(state, "https://wiki.example/chip", "The chip has eight cores.", 0.5)
    state.update_question("q3", select=True)
    state.add_candidate(url="https://maker.example/about", title="About the maker")

    state.record_claim("q2", "It costs 499 dollars", evidence=[
        _ev(price_a, "The chip retails for 499 dollars."),
        _ev(price_b, "Launch price was 450 dollars.", "contradicts")])
    state.record_claim("q1", "30 percent faster", evidence=[
        _ev(speed_a, "Benchmarks show a 30 percent speedup."),
        _ev(speed_b, "A 30 percent speedup over last year.")])
    return state, {"price_a": price_a, "price_b": price_b, "speed_a": speed_a,
                   "speed_b": speed_b, "unused": unused}


def test_report_skeleton_is_built_from_recorded_claims(seeded):
    state, ids = seeded
    report = report_skeleton(state)

    sections = [line for line in report.splitlines() if line.startswith("## ")]
    assert sections == ["## Answer", "## Findings", "## Contested", "## Gaps", "## Method", "## Sources"]
    findings = report.split("## Findings", 1)[1].split("## Contested", 1)[0]
    # Question order, with computed confidence and status.
    assert findings.index("How fast is the new chip?") < findings.index("What does it cost?")
    assert "(confidence: high · answered)" in findings
    assert f"30 percent faster [^{ids['speed_a']}][^{ids['speed_b']}] — high" in findings
    assert f'contradicts: "Launch price was 450 dollars." — [^{ids["price_b"]}]' in findings

    contested = report.split("## Contested", 1)[1].split("## Gaps", 1)[0]
    assert "It costs 499 dollars" in contested and "review.example" in contested
    gaps = report.split("## Gaps", 1)[1].split("## Method", 1)[0]
    assert "Who makes it? (q3)" in gaps and "About the maker" in gaps
    assert "- 7 sources found · 5 read · 4 cited" in report


def test_bibliography_keeps_citation_numbers_and_never_lists_candidates(seeded):
    state, ids = seeded
    bib = state.compile_bibliography()
    entries = [line.split(":", 1)[0] for line in bib.splitlines() if line.startswith("[^")]
    # Cited sources grouped by question, then the read-but-uncited one under Consulted.
    assert entries == [f"[^{ids['price_a']}]", f"[^{ids['price_b']}]", f"[^{ids['speed_a']}]",
                       f"[^{ids['speed_b']}]", f"[^{ids['unused']}]"]
    assert bib.index("### What does it cost?") < bib.index("### Consulted")
    assert "Leak thread" not in bib and "About the maker" not in bib

    cited_only = state.compile_bibliography(include="cited")
    assert "Consulted" not in cited_only and f"[^{ids['unused']}]" not in cited_only


def test_export_writes_the_three_files_inside_the_workspace(seeded, tmp_path):
    state, _ = seeded
    root = tmp_path / "ws"
    root.mkdir()
    fc = FolderContext()
    fc.add_folder(str(root))
    session = research_session(folder_context=fc)
    session.session_manager.research = state

    out = research_export(str(root / "supporting_data"), tool_context(session))
    assert out.startswith("Exported")
    written = sorted(os.listdir(root / "supporting_data"))
    assert written == ["evidence_log.md", "questions.md", "source_register.md"]
    log = (root / "supporting_data" / "evidence_log.md").read_text()
    assert "Benchmarks show a 30 percent speedup." in log and "contradicts" in log
    questions = (root / "supporting_data" / "questions.md").read_text()
    assert "**q2** What does it cost? — contested" in questions

    outside = tmp_path / "elsewhere"
    refused = research_export(str(outside), tool_context(session))
    assert refused.startswith("Error")
    assert not outside.exists()
