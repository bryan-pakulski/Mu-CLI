"""Evidence ledger: claims must quote sources that were read; confidence counts origins."""

import pytest

from mu.research import ResearchError, ResearchState

FILLER = "Background material that sets the scene for the reader. " * 6


def _read(state, url, sentence, credibility=None, **kw):
    source = state.mark_read(url=url, title=url, text=FILLER + sentence + " " + FILLER, **kw)
    if credibility is not None:
        state.assess_source(source.citation_id, credibility)
    return source.citation_id


def _ev(cid, excerpt, stance="supports"):
    return {"citation_id": cid, "stance": stance, "excerpt": excerpt}


@pytest.fixture
def state():
    s = ResearchState()
    s.add_question("Does caffeine improve reaction time?")
    return s


def test_excerpt_must_occur_in_the_source_text(state):
    cid = _read(state, "https://a.org/study", "Caffeine cut reaction time by 12 percent.")
    with pytest.raises(ResearchError, match="Excerpt not found"):
        state.record_claim("q1", "Caffeine helps", evidence=[_ev(cid, "Caffeine doubled memory scores.")])
    assert state.claims() == []

    # Reformatted whitespace and case still match.
    claim = state.record_claim(
        "q1", "Caffeine helps", evidence=[_ev(cid, "caffeine   cut reaction\ntime by 12 percent")]
    )
    assert claim.evidence[0].citation_id == cid
    assert state.get_source(cid).state == "cited"


def test_unread_candidates_cannot_be_cited(state):
    candidate = state.add_candidate(url="https://a.org/abstract", title="Abstract", snippet="caffeine")
    with pytest.raises(ResearchError, match="unread search result"):
        state.record_claim("q1", "x", evidence=[_ev(candidate.candidate_id, "caffeine")])
    with pytest.raises(ResearchError, match="No question"):
        state.record_claim("q9", "x", evidence=[])


def test_one_bad_evidence_item_records_nothing(state):
    good = _read(state, "https://a.org/1", "Reaction time fell after 200 mg.")
    with pytest.raises(ResearchError):
        state.record_claim("q1", "x", evidence=[_ev(good, "Reaction time fell after 200 mg."),
                                                _ev(good, "This sentence is not in the source at all.")])
    assert state.claims() == []
    assert state.get_source(good).state == "read"


def test_same_origin_pages_count_once(state):
    a = _read(state, "https://news.example.com/a", "Trial shows faster reactions.", 0.8)
    b = _read(state, "https://www.example.com/b", "Faster reactions were measured.", 0.8)
    claim = state.record_claim("q1", "Faster", evidence=[_ev(a, "Trial shows faster reactions."),
                                                         _ev(b, "Faster reactions were measured.")])
    assert state.claim_origins(claim)["supports"] == ["example.com"]
    assert state.claim_confidence(claim) == "medium"  # one credible origin, not two


def test_confidence_rules(state):
    strong = _read(state, "https://journal.org/x", "Effect size was large.", 0.9)
    weak = _read(state, "https://blog.net/y", "The effect size was large indeed.", 0.3)
    other = _read(state, "https://forum.io/z", "We saw no effect at all.", 0.3)

    high = state.record_claim("q1", "Large effect", evidence=[
        _ev(strong, "Effect size was large."), _ev(weak, "The effect size was large indeed.")])
    assert state.claim_confidence(high) == "high"

    state.add_evidence(high.id, _ev(other, "We saw no effect at all.", "contradicts"))
    assert state.claim_confidence(high) == "medium"  # contradiction outnumbered by origins
    assert state.question_status(state.get_question("q1")) == "contested"

    lone_weak = state.record_claim("q1", "Weak", evidence=[_ev(weak, "The effect size was large indeed.")])
    assert state.claim_confidence(lone_weak) == "low"

    inferred = state.record_claim("q1", "Probably large", kind="inferred", evidence=[
        _ev(strong, "Effect size was large."), _ev(weak, "The effect size was large indeed.")])
    assert state.claim_confidence(inferred) == "medium"

    lowered = state.record_claim("q1", "Hedged", confidence="low", evidence=[
        _ev(strong, "Effect size was large."), _ev(weak, "The effect size was large indeed.")])
    assert state.claim_confidence(lowered) == "low"


def test_origin_override_merges_syndicated_copies(state):
    a = _read(state, "https://paper-one.com/s", "The minister resigned on Friday.", 0.8)
    b = _read(state, "https://paper-two.com/s", "The minister resigned on Friday afternoon.", 0.8)
    claim = state.record_claim("q1", "Resigned", evidence=[
        _ev(a, "The minister resigned on Friday."), _ev(b, "The minister resigned on Friday afternoon.")])
    assert state.claim_confidence(claim) == "high"
    state.assess_source(b, 0.8, origin="wire-agency")
    state.assess_source(a, 0.8, origin="wire-agency")
    assert state.claim_confidence(claim) == "medium"


def test_answered_needs_every_claim_at_medium(state):
    state.add_question("Side effects?", parent_id="q1")
    strong = _read(state, "https://journal.org/x", "Jitters were reported by 5 percent.", 0.9)
    claim = state.record_claim("q1.1", "Jitters", evidence=[_ev(strong, "Jitters were reported by 5 percent.")])
    assert state.claim_confidence(claim) == "medium"
    coverage = state.coverage()
    rows = {r["id"]: r for r in coverage["questions"]}
    assert rows["q1.1"]["status"] == "answered"
    # A parent is judged on its subtree: answered through q1.1, so not a gap.
    assert rows["q1"]["status"] == "answered" and rows["q1"]["claims"] == 1
    assert coverage["open_without_claims"] == []

    state.add_question("Long-term effects?", parent_id="q1")
    weak = _read(state, "https://blog.net/x", "Nobody knows the long-term effects.", 0.3)
    state.record_claim("q1.2", "Unknown", evidence=[_ev(weak, "Nobody knows the long-term effects.")])
    rows = {r["id"]: r for r in state.coverage()["questions"]}
    assert rows["q1"]["status"] == "open"  # one weak sub-answer keeps the parent open
    state.update_question("q1.2", dropped=True)
    rows = {r["id"]: r for r in state.coverage()["questions"]}
    assert rows["q1"]["status"] == "answered" and rows["q1.2"]["status"] == "dropped"


def test_ledger_round_trips_through_its_snapshot(state):
    cid = _read(state, "https://a.org/study", "Caffeine cut reaction time by 12 percent.", 0.85)
    state.add_candidate(url="https://b.org/later", title="Later")
    state.record_claim("q1", "Helps", evidence=[_ev(cid, "Caffeine cut reaction time by 12 percent.")])

    copy = ResearchState()
    copy.load_dict(state.to_dict())
    assert copy.to_dict() == state.to_dict()
    claim = copy.claims()[0]
    assert copy.claim_confidence(claim) == "medium"
    # Numbering continues after a reload instead of reusing IDs.
    nxt = copy.mark_read(url="https://c.org/new", title="New", text=FILLER)
    assert nxt.citation_id == cid + 1
    assert copy.add_question("Another?").id == "q2"
