"""Tests for scan mode prompts and metadata."""

from utils.config import AGENTIC_MODES, AGENTIC_MODE_SYSTEM_PROMPTS, AGENT_MODE_METADATA, SECURITY_FINDING_SCHEMA


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


def test_scan_finding_schema_contract_exists():
    required = {
        "id", "title", "severity", "confidence", "cwe", "cvss",
        "affected_files", "affected_functions", "preconditions", "exploit_steps",
        "evidence", "fix_recommendation", "verification_steps", "status",
    }
    assert required.issubset(set(SECURITY_FINDING_SCHEMA.keys()))
    assert SECURITY_FINDING_SCHEMA["status"] == "confirmed|unconfirmed|false_positive"


def test_scan_mode_prompt_mentions_structured_fields_and_status():
    prompt = AGENTIC_MODES["scan"].lower()
    assert "machine-parseable schema" in prompt
    assert "false_positive" in prompt
