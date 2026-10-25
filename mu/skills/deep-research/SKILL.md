---
name: deep-research
description: Conduct a validated, evidence-led deep dive with durable research artifacts and a final report.
trigger: \b(deep[ -]?(research|dive)|comprehensive research|research report|thorough investigation|investigate in depth)\b
---

# Deep Research Protocol

Use this protocol only for a genuine deep dive: a question with material
uncertainty, competing explanations, consequential recommendations, or a need
to compare sources. Do not use it for ordinary lookup, a narrow code search,
or a quick answer.

## Deliverables — mandatory

At the working-directory root, create:

```text
REPORT.md
supporting_data/
  research_plan.md
  questions.md         ← generated from the evidence ledger
  source_register.md   ← generated from the evidence ledger
  evidence_log.md      ← generated from the evidence ledger
  hypotheses.md
  validation_log.md
  gap_analysis.md
```

In research mode, sources, claims and evidence live in the session's evidence
ledger: record them with the ledger tools as you go and run
`research_export("supporting_data")` to write `questions.md`,
`source_register.md` and `evidence_log.md` — re-run it after recording more,
and never hand-edit those three. Outside research mode (no ledger tools),
maintain them by hand with the same columns. Write the other files with
`write_file`, `apply_diff`, or `bash`, as evidence is obtained; do not defer
all documentation until the end. Keep raw downloads, command output, datasets, extracts, reproductions,
and derived tables under `supporting_data/` with descriptive names. Never put
large raw blobs in `REPORT.md`; link them by relative path.

## Method

1. **Frame.** Restate the original ask as answerable questions, decision
   criteria, scope, assumptions, exclusions, and success conditions. Record
   the questions with `research_question` (sub-questions via `parent_id`) and
   write `supporting_data/research_plan.md` before broad searching.
2. **Map hypotheses.** List the leading explanation or answer, credible
   alternatives, falsifiers, and evidence needed to distinguish them in
   `supporting_data/hypotheses.md`. Do not let the first plausible source set
   the conclusion.
3. **Collect independently.** Search in parallel (`web_search`,
   `arxiv_search`, `doi_resolve`, `stackoverflow_search`, `hackernews_search`,
   repository search tools) — results are unread candidates — then read the
   promising ones with `url_grounding`, `research_fetch` or `read_document`,
   and use `bash` for local reproduction or data analysis. Prefer primary
   sources: official documentation, original papers, standards, source code,
   direct measurements, and reproducible commands. Use secondary sources to
   discover leads, not as sole support for consequential claims.
4. **Grade every source you rely on.** `assess_source` with a rationale;
   set `origin` when two sources share an independence group (syndicated
   copy, the same lab's papers) so corroboration counts them once.
5. **Log evidence, not impressions.** `record_claim` for each claim with
   short quoted excerpts and exact locators, `kind` observed / derived /
   inferred, and `add_evidence` for counterevidence (`stance:
   "contradicts"`). The ledger computes confidence from independent origins
   and checks every excerpt against the source text.
6. **Test hypotheses.** Actively seek disconfirming evidence. Reproduce
   technical claims with `bash` where feasible; record commands, environment,
   inputs, output, and result in `supporting_data/validation_log.md`. Compare
   independent sources and resolve contradictions explicitly; do not average
   them away.
7. **Review.** Perform a separate review pass after drafting findings:
   check source independence, recency/version, primary-source coverage,
   citation-to-claim alignment, arithmetic/units, and whether alternatives
   were actually tested. Use a subagent for an independent adversarial review
   when the question is broad enough to justify it.
8. **Gap analysis.** Start from `research_status` (open questions, claims
   resting on one origin, contested claims, unread leads) and compare evidence
   and conclusions against every item in the original ask. Write missing evidence, untested hypotheses, unresolved
   conflicts, limitations, and the next highest-value validation step to
   `supporting_data/gap_analysis.md`.

## Final report

Write `REPORT.md` only after the validation and gap-analysis passes. Start
from `research_report`, which lays out findings with confidence and excerpts,
contested claims, gaps and the bibliography from the ledger. Use this
structure:

1. **Executive answer** — direct answer and confidence.
2. **Scope and method** — questions, criteria, and validation approach.
3. **Findings** — claim-by-claim, with source links/paths and confidence.
4. **Hypotheses and tests** — what survived, failed, or remains unresolved.
5. **Source comparison** — agreement, disagreement, independence, and why
   preferred evidence won.
6. **Limitations and gaps** — no hidden uncertainty.
7. **Artifact index** — links to every `supporting_data/` file and raw asset.
8. **Recommended next actions** — only if justified by the evidence.

Do not claim completeness merely because many sources were found. The work is
complete only when the original ask has been checked against the gap analysis,
every material claim has traceable evidence, and unresolved uncertainty is
stated plainly.
