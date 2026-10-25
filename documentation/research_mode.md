# Research Mode

For "help me *understand* X" — explanations, write-ups, surveys.
Switch in via `/mode research`.

The output is a synthesized analysis with citations, not a code change.
Research runs on a session-owned **evidence ledger**: questions → claims →
quoted evidence → sources the agent has read. Search results are only
candidates; a source is numbered and citable once it has been read, every
claim quotes the source text it rests on, and confidence is computed from how
many independent origins support it.

For "fix this bug" use [debug](debug_mode.md). For "make this change"
use [default](default_mode.md). For multi-hour deep dives use
[loop](loop_mode.md) on top of research mode.

## Core behavior

The loop is *plan questions → search (candidates) → read → record claims →
check status → report*.

1. **Recall first.** `search_memory` with the topic, and `research_status`
   for questions, claims and leads already recorded this session.

2. **Plan questions.** `research_question(action="add", text=…)` for the main
   question and each angle; `parent_id` makes a sub-question and `todo_id`
   links a todo item. Adding or `select`-ing a question files the sources read
   afterwards under it and ranks the passages reads return. (`/research topic
   <name>` selects or creates a root question by name.)

3. **Search in parallel.** Fire several search tools in one turn — they run
   concurrently:
   - `web_search` + `stackoverflow_search` — how-things-work / library questions
   - `arxiv_search` + `doi_resolve` — papers
   - `reddit_search` + `hackernews_search` — practitioner perspectives
   - `retrieve_relevant_context` + `search_references` — codebase research

   Every row is registered as a **candidate** (`s4`): deduplicated by
   canonical URL, carrying its snippet and metadata, but never numbered and
   never in the bibliography.

4. **Read before citing.**
   - `url_grounding(url or source_id, query, max_chars)` fetches a page
     (5 MB cap; PDFs go to the PDF path; a near-empty page gets one
     Playwright-rendered retry), extracts the main content (trafilatura, or a
     boilerplate-dropping bs4 fallback) with title, authors, date and site,
     and returns the passages most relevant to `query` — the current question
     when omitted — within `max_chars` (default 6000), plus an outline of the
     rest. The full text stays in the result store (`result_ref`; pull more
     with `result_search` / `result_range`).
   - `research_fetch([urls], query)` reads up to 6 pages concurrently; one
     failing URL is reported without failing the others.
   - `read_document` reads PDFs by path or URL the same way, one passage per
     page, so the page heading is the locator.
   - The source gets a stable citation number `[^n]` and is `read`. A second
     URL whose text matches an already-read source (a mirror) is merged into
     it.

5. **Record claims.** `record_claim(question_id, text, kind, evidence=[{citation_id,
   stance, excerpt, locator}])`. The tool refuses evidence from an unread
   candidate and any excerpt that does not occur in the source text
   (whitespace/case-normalised, fuzzy ≥ 0.9; the error shows the nearest
   passage). `kind` is `observed` (the source says it), `derived` (computed
   from sources) or `inferred` (judgement). Citing promotes a source to
   `cited`. `add_evidence` attaches later support, contradictions or
   qualifications.

6. **Check coverage.** `research_status` lists each question's status,
   claims, supporting origins, contradictions and unread candidates worth
   reading, and the questions with no claims yet. `visual=true` adds a
   claims × origins matrix for `publish_visualization`.

7. **Report.** `research_report` returns the skeleton below; the agent writes
   the answer and prose into it. `research_export(dir)` writes
   `questions.md`, `source_register.md` and `evidence_log.md` for the
   [deep-research](../mu/skills/deep-research/SKILL.md) deliverable
   (approval-gated, workspace bounds, blocked in plan mode).

Sub-agents (`spawn_agent`) keep their own ledger: read the key sources they
report before recording claims on them. Durable, non-obvious findings still
go to `save_memory` for future sessions.

### Confidence and status

Origins, not URLs, are counted — the registrable domain by default (hosting
platforms such as `github.io` or `substack.com` count per account), so three
pages on one site support a claim once. `assess_source(…, origin=…)`
overrides it, e.g. for syndicated wire copy.

| Claim confidence | Rule |
| --- | --- |
| high | ≥ 2 supporting origins, one with credibility ≥ 0.7, no contradicting evidence |
| medium | ≥ 2 supporting origins, or 1 origin with credibility ≥ 0.8; contradictions only while outnumbered |
| low | otherwise |

`kind="inferred"` caps at medium; the agent may lower a claim's confidence
but never raise it. A question is **contested** when a claim has supporting
and contradicting evidence from different origins, **answered** when every
claim is at least medium, otherwise **open** (or **dropped** by the agent).

## Citation requirements

- Cite only sources that were read; reference them with their `[^n]`
  number. Numbers are stable for the session, so prose written before the
  report still points at the right entry.
- Every claim from an external source is recorded with `record_claim` and a
  quoted excerpt; the report renders only recorded claims.
- The bibliography (`/research bibliography`, the report's Sources
  section) lists cited sources grouped by root question, then read but
  uncited sources under "Consulted". Candidates never appear.

## Source credibility (AI-assessed, not hardcoded)

A read source starts at a conservative prior (half its type's cap) until
the AI grades it; candidates score 0. The AI owns the weighting decision.

For each source you rely on, call
`assess_source(citation_id, importance, rationale)` with a 0–1
evidence-strength rating based on authority, methodology, relevance,
recency, and corroboration.

Source type supplies a **hard safety cap** — it bounds but never
flatly assigns the score:

| Source type | Cap |
| --- | --- |
| Academic (arXiv, DOI, peer-reviewed) | 1.0 |
| Official documentation / vendor | 0.95 |
| News / industry analysis | 0.85 |
| Web search hits | 0.80 |
| Forums (Reddit, HN, SO) | 0.65 |
| Social / other | 0.60 |

A weak academic paper can score 0.2; an excellent web source can be
strong but cannot exceed 0.80. If you add a source to the bibliography,
you are responsible for deciding how relevant and important it is.

- Corroborate important claims from independent origins — the ledger
  grades confidence on exactly that.
- Prefer recent sources for fast-moving topics.
- Note conflicts of interest in the write-up (e.g. a vendor blog about
  a vendor's own product).

## Output format

`research_report` lays out:

```
## Answer       ← the agent writes the direct answer
## Findings     ← per question: confidence, claims with [^n], quoted evidence
## Contested    ← claims with supporting vs contradicting origins
## Gaps         ← questions without claims, with unread candidates worth reading
## Method       ← sources found / read / cited, claim and question counts
## Sources      ← the bibliography
```

The final write-up leads with the answer, follows with the evidence, keeps
the Contested and Gaps sections honest, and ends with the bibliography. No
tool-call narration; the user wants the finding, not the search history.

## Commands and GUI

- `/research` — status; `questions`, `claims [qid]`, `gaps`,
  `sources [--state candidate|read|cited --type --min --query --topic]`,
  `show <n|sN>`, `bibliography [--cited] [qid]`, `stats`, `topic [name]`,
  `topics`, `export <dir>`, `clear`, `new <query>`.
- The Research panel shows the question tree with status and confidence,
  each question's claims (a claim opens its evidence and sources), a Sources
  tab with a state filter, and a claims × origins matrix.
  `GET /api/research/state` serves the same data for the requested session.

Research state is saved with the session (`research_sources` in
`session.json`); extracted source text is kept once per text under
`~/.mucli/research/texts/` for excerpt checks.

## When to leave research mode

- Findings point at a concrete change → `/mode default` to implement.
- Findings point at a specific failure to reproduce → `/mode debug`.
- The write-up itself is long-horizon (multi-day survey) → `/mode loop`.
