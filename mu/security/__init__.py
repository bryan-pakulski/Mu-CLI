"""Security audit engine + always-on path/secret protection.

Modules:
  * secret_paths — `is_denied_path` denylist + `redact_secrets` scrubber
  * engine       — `SecurityReport` audit workflow (proof + remediation gates)
"""
