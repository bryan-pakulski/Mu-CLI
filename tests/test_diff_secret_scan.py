from mu.security.diff_scan import scan_patch, summarize


PATCH = """diff --git a/app/config.py b/app/config.py
--- a/app/config.py
+++ b/app/config.py
@@ -1,2 +1,4 @@
 import os
-OLD = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
+TOKEN = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
+KEY = os.environ["X"]
+leak = "literal-credential-value-123"
"""


def test_added_lines_are_scanned_with_paths_and_line_numbers():
    findings = scan_patch(PATCH)
    assert [(f.path, f.line, f.label) for f in findings] == [("app/config.py", 2, "GitHub PAT")]


def test_removed_secrets_are_not_reported():
    patch = PATCH.replace('+TOKEN = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"\n', "")
    assert scan_patch(patch) == []


def test_injected_literals_match_without_being_echoed():
    findings = scan_patch(PATCH, literals=["literal-credential-value-123", "short"])
    labels = [(f.line, f.label) for f in findings]
    assert (4, "injected credential") in labels
    assert "literal-credential-value-123" not in summarize(findings)


def test_pem_header_on_added_line_is_reported():
    patch = "+++ b/k.pem\n@@ -0,0 +1,2 @@\n+-----BEGIN RSA PRIVATE KEY-----\n+MIIB\n"
    assert [f.label for f in scan_patch(patch)] == ["PEM private key"]


def test_empty_patch():
    assert scan_patch("") == []
