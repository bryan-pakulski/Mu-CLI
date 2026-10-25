"""/research subcommands read the session's ledger and render it in the terminal."""

import io
from types import SimpleNamespace

import pytest
from rich.console import Console

from mu.commands.research import research_cmd
from mu.research import ResearchState
from mu.workspace.folder_context import FolderContext

PAD = "Context sentence that pads the extracted page text. " * 6


class _UI:
    def __init__(self):
        self.buffer = io.StringIO()
        self.console = Console(file=self.buffer, width=200, color_system=None)
        self.infos, self.errors = [], []

    def show_info(self, msg):
        self.infos.append(msg)

    def show_error(self, msg):
        self.errors.append(msg)


@pytest.fixture
def session(tmp_path):
    state = ResearchState()
    state.add_question("Is coffee healthy?")
    state.add_question("Effect on sleep?", parent_id="q1")
    state.add_question("Does decaf count?", select=False)
    a = state.mark_read(url="https://health.example/a", title="Study A",
                        text=PAD + "Moderate intake lowers mortality." + PAD)
    state.mark_read(url="https://blog.example/b", title="Blog B", text=PAD + "Coffee is fine." + PAD)
    state.add_candidate(url="https://forum.example/c", title="Forum C")
    state.record_claim("q1.1", "Lowers mortality", evidence=[
        {"citation_id": a.citation_id, "stance": "supports", "excerpt": "Moderate intake lowers mortality."}])
    fc = FolderContext()
    fc.add_folder(str(tmp_path))
    saves = []
    manager = SimpleNamespace(research=state, save_history=lambda fc=None: saves.append(1))
    return SimpleNamespace(session_manager=manager, ui=_UI(), folder_context=fc,
                           variables={"agent_mode": "research", "session_type": "workspace"},
                           saves=saves)


def test_questions_claims_and_gaps(session):
    questions = research_cmd(session, "questions")
    assert [(q["id"], q["status"]) for q in questions.data["questions"]] == [
        ("q1", "open"), ("q1.1", "open"), ("q2", "open")]
    rendered = session.ui.buffer.getvalue()
    assert "Is coffee healthy?" in rendered and "  Effect on sleep?" in rendered

    claims = research_cmd(session, "claims q1")
    assert [c["text"] for c in claims.data["claims"]] == ["Lowers mortality"]
    assert claims.data["claims"][0]["confidence"] == "low"
    assert research_cmd(session, "claims q9").ok is False

    gaps = research_cmd(session, "gaps")
    assert [q["id"] for q in gaps.data["questions_without_claims"]] == ["q2"]
    assert [c["id"] for c in gaps.data["low_confidence"]] == ["c1"]
    assert "no claims: q2 Does decaf count?" in session.ui.infos[-1]


def test_sources_filter_by_state(session):
    default = research_cmd(session, "sources")
    assert [(s["id"], s["state"]) for s in default.data["sources"]] == [(1, "cited"), (2, "read")]
    candidates = research_cmd(session, "sources --state candidate")
    assert [s["source_id"] for s in candidates.data["sources"]] == ["s3"]
    assert research_cmd(session, "sources --state nope").ok is False
    shown = research_cmd(session, "show s3")
    assert shown.data["title"] == "Forum C" and shown.data["id"] is None


def test_export_writes_files_and_refuses_chat_sessions(session, tmp_path):
    result = research_cmd(session, f"export {tmp_path / 'out'}")
    assert result.ok and sorted(result.data["written"]) == ["evidence_log.md", "questions.md",
                                                            "source_register.md"]
    assert (tmp_path / "out" / "source_register.md").read_text().count("| 1 | Study A") == 1

    session.variables["session_type"] = "chat"
    refused = research_cmd(session, f"export {tmp_path / 'chat'}")
    assert refused.ok is False and not (tmp_path / "chat").exists()


def test_topic_selects_a_root_question_and_saves(session):
    result = research_cmd(session, "topic Is coffee healthy?")
    assert result.data["question_id"] == "q1"
    assert session.saves
    fresh = research_cmd(session, "topic Caffeine and anxiety")
    assert fresh.data["question_id"] == "q3"
