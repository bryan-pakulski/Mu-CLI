---
name: debug-bisect
description: Locate the source of a regression by narrowing the search space.
trigger: when the user reports a recently-broken feature
---

When asked to debug something that "used to work":

1. **Establish ground truth** — what's the exact symptom? Reproduce it deterministically with the smallest test or shell command you can.
2. **Identify the last known good** — ask the user (or check git log) for the most recent commit / version where it worked.
3. **Narrow the blame range** — if many commits separate good and bad, use `git bisect run` with the repro command. For local changes, comment out half the new code.
4. **Find the offending change, not just the file** — once isolated to a commit, read its diff and explain *why* it breaks the feature.
5. **Propose the smallest fix that addresses the root cause** — not a workaround that papers over the symptom.

Never propose "add a try/except" as a fix unless the underlying state is genuinely unrecoverable — that hides bugs.
