"""Unit tests for the secret-output scrubber.

Each well-known pattern in `_SECRET_PATTERNS` should redact a realistic
example, leave ordinary text untouched, and report an accurate replacement
count. Over-matching is the failure mode we care most about.
"""

import pytest

from core.secret_paths import redact_secrets


def _redacted(text):
    out, n = redact_secrets(text)
    return out, n


# ----------------------------------------------------- pattern coverage


@pytest.mark.parametrize(
    "secret, label",
    [
        ("AKIAIOSFODNN7EXAMPLE", "AWS access key"),
        ("ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789", "GitHub PAT"),
        ("gho_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789", "GitHub OAuth token"),
        ("ghs_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789", "GitHub server token"),
        ("glpat-aBcDeFgHiJkLmNoPqRsTuV", "GitLab PAT"),
        ("xoxb-123456789012-1234567890123-ABCDEFabcdefABCDEFabcdef", "Slack token"),
        (
            "sk-aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789AAAA",
            "OpenAI/sk-style key",
        ),
        # Real Google API key format: AIza + exactly 35 chars (total 39).
        ("AIzaabcdefghijklmnopqrstuvwxyz012345678", "Google API key"),
    ],
)
def test_redacts_known_token_patterns(secret, label):
    text = f"the token is {secret} please rotate"
    out, n = _redacted(text)
    assert n >= 1
    assert secret not in out
    assert f"[REDACTED:{label}]" in out


def test_redacts_anthropic_key_specifically():
    key = "sk-ant-api03-" + "A" * 95
    out, n = _redacted(f"key: {key}")
    assert n == 1
    assert "[REDACTED:Anthropic API key]" in out
    assert key not in out


def test_redacts_pem_block_multiline():
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n"
        "yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy=\n"
        "-----END RSA PRIVATE KEY-----"
    )
    out, n = _redacted(f"here is a key:\n{pem}\nafter")
    assert n == 1
    assert "[REDACTED:PEM private key]" in out
    assert "MIIEowIBAA" not in out


def test_redacts_jwt():
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4iLCJpYXQiOjE1MTYyMzkwMjJ9"
        ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    out, n = _redacted(f"Authorization: Bearer {jwt}")
    assert n == 1
    assert "[REDACTED:JWT]" in out
    assert "eyJhbGc" not in out


def test_redacts_aws_secret_assignment_line():
    line = 'aws_secret_access_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"'
    out, n = _redacted(line)
    assert n == 1
    assert "wJalrXUtn" not in out


# ----------------------------------------------------- multiple / non-secret


def test_redacts_multiple_in_one_string():
    text = (
        "AWS=AKIAIOSFODNN7EXAMPLE and GH=ghp_"
        + "X" * 36
        + " ok"
    )
    _, n = _redacted(text)
    assert n == 2


@pytest.mark.parametrize(
    "text",
    [
        "this is just regular text",
        "the function name is encode_jwt(...)",
        "akia not a key",
        "ghp without underscore",
        "sk= (short, ambiguous)",
        "-----BEGIN PUBLIC KEY-----\nfoo\n-----END PUBLIC KEY-----",
    ],
)
def test_leaves_non_secret_text_unchanged(text):
    out, n = _redacted(text)
    assert out == text, f"expected no change, got {out!r}"
    assert n == 0


def test_empty_and_non_str_inputs_are_safe():
    assert redact_secrets("") == ("", 0)
    assert redact_secrets(None) == (None, 0)
