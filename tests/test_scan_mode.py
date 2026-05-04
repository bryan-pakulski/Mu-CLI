"""Tests for scan mode prompts and metadata."""

from utils.config import AGENTIC_MODES, AGENTIC_MODE_SYSTEM_PROMPTS, AGENT_MODE_METADATA


def test_scan_mode_exists_in_agentic_modes():
    assert "scan" in AGENTIC_MODES
    prompt = AGENTIC_MODES["scan"]
    assert "empirical evidence" in prompt.lower()
    assert "concrete validation loop" in prompt.lower()


def test_scan_mode_exists_in_system_prompts():
    assert "scan" in AGENTIC_MODE_SYSTEM_PROMPTS
    sys_prompt = AGENTIC_MODE_SYSTEM_PROMPTS["scan"].lower()
    assert "hypothesize" in sys_prompt
    assert "reproduce" in sys_prompt
    assert "confirmed" in sys_prompt


def test_scan_mode_metadata_exists():
    assert "scan" in AGENT_MODE_METADATA
    meta = AGENT_MODE_METADATA["scan"]
    assert meta.get("display_name") == "Scan Mode"



def test_scan_mode_prompt_mentions_structured_fields_and_status():
    prompt = AGENTIC_MODES["scan"].lower()
    assert "create_scan_finding" in prompt
    assert "attach_scan_artifact" in prompt
