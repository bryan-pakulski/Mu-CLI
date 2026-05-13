---
name: code-review
description: Review a diff or file for correctness, security, and clarity issues.
trigger: \b(code\s+review|review\s+(this|the|my|a)?\s*(code|diff|change|pr|patch))\b
---

When reviewing code:

1. **Correctness** — does it do what the surrounding code claims it does? Run tests if any exist; if not, trace the call graph manually.
2. **Security** — flag command injection, SQL injection, XSS, path traversal, unsafe deserialization, missing authn/authz, secrets in source.
3. **Concurrency** — if the change touches shared state, check locking, race windows, atomicity of compound ops.
4. **Failure modes** — what happens on partial failure / retry / out-of-order delivery?
5. **API surface** — is the new signature obviously misuse-resistant? Required args ordered first, optional last, no boolean-trap params.
6. **Tests** — does the change have a regression test? If a bug fix, is there one that *would have caught* the bug?

Output format: a numbered list of findings, severity tag (blocker | warning | nit) prefixed. Always suggest a fix, not just a complaint. Skip the praise paragraph at the top — go straight to findings.
