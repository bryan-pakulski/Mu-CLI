# Plan: Research mode v2 — read, record, structure, present

Status: implemented 2026-10-23 (all six phases; see §8) · Builds on: `documentation/research_mode.md`, `mu/skills/deep-research/SKILL.md` ·
Reference: Wash design guide, `documentation/visualizations.md`

## 1. Problem

Research mode has a good method but weak machinery. Most of the method lives in prompt text, not
in data:

- **Every search hit becomes a citation.** `web_search`, `arxiv_search`, `reddit_search`,
  `stackoverflow_search` and `hackernews_search` call `register_source` on each result row
  (`mu/tools/research/handlers.py` ~334, 413, 428, 505, 692, 1044, 1208, 1372). The bibliography
  fills with unread snippets that have credibility 0.0.
- **Page fetching is crude.** `url_grounding` returns `soup.get_text()` of the whole page
  (navigation, footers, cookie banners), has no length cap, and registers the source with
  `title=url`.
- **Exact-string dedup.** `CitationManager.add_source` keys on the raw URL; `utm_*`, a trailing
  `/`, `http`/`https`, `www.` or a `#fragment` produce duplicate sources.
- **No claim model.** "Cross-reference across ≥2 sources" is an instruction only. Nothing records
  which claim rests on which source, which sources disagree, or whether two sources are
  independent.
- **Two disconnected systems.** The `deep-research` skill keeps a markdown source register and
  evidence log that never touch `CitationManager`.
- **Flat categorisation.** One `current_topic` string; no question tree, no link to the todo plan.
- **Process-global registry.** `get_citation_manager()` is a module singleton; `SessionManager`
  hydrates/snapshots it (`mu/session/manager.py` ~1181–1194). Two concurrent GUI sessions share
  one live registry between those calls.
- **Plain output.** Footnotes and a bibliography; no per-finding confidence, no contested or gaps
  section, no visual.

### Goals

- **Read before cite.** Search returns *candidates*; a source enters the bibliography only when
  it is read or cited.
- **Clean, bounded reading.** Main-content extraction with metadata, full text in the result
  store, query-relevant passages in context.
- **One evidence ledger.** Questions → claims → evidence → sources, with stance and independence,
  owned by the session and persisted with it.
- **Generated outputs.** Report skeleton, bibliography (cited only), deep-research
  `supporting_data/*.md` files and GUI panel all derive from the ledger.
- **Session isolation.** No research state leaks between concurrent sessions.

### Non-goals

- New search engines or paid APIs.
- Automatic claim extraction by a second model. The agent records claims through tools; the
  harness stores and checks them.
- Mobile editing of research state (Android stays read-only, may show the summary later).
- Migration of old `research_sources` snapshots beyond a straight load (AGENTS.md: no
  migrations/backfills; unknown fields are ignored, missing ones default).

## 2. What exists (reuse)

| Need | Existing piece |
|---|---|
| Source model, credibility caps, AI assessment | `utils/citation_manager.py` `Source`, `SourceType`, `source_type_cap`, `CitationManager.assess_source` |
| Topics | `CitationManager.set_topic`, `_topic_order`, `compile_bibliography(topic)` |
| Persistence | `CitationManager.to_dict` / `load_dict`; `SessionManager.research_sources` |
| Tools | `mu/tools/research/handlers.py` (8 tools + `assess_source`); registry entries in `mu/tools/capabilities.py`, `descriptors.py`, `_dispatcher.py` |
| HTML parsing | `beautifulsoup4` (already in `requirements.txt`) |
| Large-result handling | durable result store + `recall` / `result_*` ops (context packaging v2) |
| Commands | `mu/commands/research.py` (`/research status|sources|show|bibliography|topics`) |
| GUI | `mu/gui/routers/research.py` `GET /api/research/state`; `mu/gui/mode_workspace.py` `research_workspace`; Alpine store `research` in `static/js/app.js` |
| Report method | `mu/skills/deep-research/SKILL.md` (REPORT.md + `supporting_data/`) |
| Visuals | `publish_visualization` + visualization-design skill |
| Tests | `tests/modes/test_citation_manager.py`, `tests/modes/test_mode_workspace.py` |

## 3. Design

### 3.1 Data model (`utils/citation_manager.py` → package `mu/research/`)

Move the engine to `mu/research/` (`sources.py`, `ledger.py`, `urls.py`, `extract.py`,
`report.py`). `utils/citation_manager.py` stays as a thin re-export so existing imports keep
working until phase 6 removes it.

```python
@dataclass
class Source:                     # existing fields kept
    citation_id: int
    url: str                      # as first seen
    canonical_url: str            # new: normalised key (§3.3)
    title: str
    source_type: SourceType
    authors: list[str]; published: str | None; site: str | None   # from extraction
    content_hash: str | None      # sha256 of extracted main text
    origin: str                   # new: independence group, default = registrable domain
    state: Literal["candidate", "read", "cited"]                   # new
    credibility: float; importance: float; rationale: str          # existing assessment
    question_ids: list[str]       # replaces single topic (topic kept as alias, §3.4)
    result_ref: str | None        # durable result-store key of the full text

@dataclass
class Question:
    id: str                       # "q1", "q1.2"
    text: str
    parent_id: str | None
    status: Literal["open", "answered", "contested", "dropped"]
    todo_id: int | None           # optional link to the session todo item

@dataclass
class Claim:
    id: str                       # "c7"
    question_id: str
    text: str
    kind: Literal["observed", "derived", "inferred"]
    evidence: list[Evidence]
    confidence: Literal["high", "medium", "low"] | None   # computed (§3.5), agent may lower, never raise

@dataclass
class Evidence:
    citation_id: int
    stance: Literal["supports", "contradicts", "qualifies"]
    excerpt: str                  # ≤ 500 chars, must occur in the source's extracted text
    locator: str | None           # section / page / anchor
```

`ResearchState` owns sources, questions and claims, and serialises to one dict
`{"version": 2, "sources": [...], "questions": [...], "claims": [...]}`. `load_dict` also accepts
the current list-of-sources shape (treated as `state="cited"`, `question_ids=[topic]`), which is a
read of the existing format, not a migration.

### 3.2 Session ownership

- `SessionManager` holds `self.research = ResearchState()` and persists it where
  `research_sources` is persisted today.
- Tool handlers resolve state through the tool context (`context.session.research`), not
  `get_citation_manager()`.
- `get_citation_manager()` remains for code paths with no session (tests, CLI one-offs): it reads
  a `ContextVar` set by the agent loop per turn, falling back to a private default instance.
  Concurrent sessions therefore never share live state.
- Remove the hydrate/snapshot dance at `manager.py` ~1181–1194 once all callers use the session.

### 3.3 URL normalisation (`mu/research/urls.py`)

`canonical_url(url, html_canonical=None)`:

1. Prefer `<link rel="canonical">` when same registrable domain.
2. Lowercase scheme/host, drop `www.`, force `https` when the original was `http` and the host
   matches, remove default ports, fragment, trailing `/` (except root).
3. Strip tracking params (`utm_*`, `gclid`, `fbclid`, `mc_*`, `ref`, `ref_src`, `igshid`, `si`);
   sort the rest.
4. Known rewrites: `arxiv.org/pdf/X(.pdf)` and `/abs/Xv2` → `arxiv.org/abs/X`;
   `doi.org`/`dx.doi.org` → `doi:<lowercased DOI>`; `old.reddit.com`/`m.` → `reddit.com`;
   `mobile.twitter.com`/`x.com` → `twitter.com`.

Dedup key is `canonical_url`; a second check on `content_hash` merges mirrors (keeps the earlier
ID, records the alias URL). `origin` = registrable domain via a small public-suffix list shipped
in-tree (no network).

### 3.4 Retrieval tools

**Search tools (`web_search`, `arxiv_search`, `reddit_search`, `stackoverflow_search`,
`hackernews_search`, `doi_resolve`):** register results as `state="candidate"` and return a
short `candidate_id` (`s12`) per row instead of a citation number. Candidates are deduped, carry
the snippet and metadata, and are excluded from the bibliography and footnote numbering.
`doi_resolve` and `arxiv_search` metadata (authors, date) is kept so a later read inherits it.

**`url_grounding(url, query=None, max_chars=6000)`** rewritten on `mu/research/extract.py`:

1. Fetch (existing timeout, size cap 5 MB, content-type check; PDFs route to the
   `read_document` path).
2. Extract main content: `trafilatura.extract(..., output_format="markdown", with_metadata=True)`
   when installed; fallback is a bs4 heuristic (drop `nav/header/footer/aside/script/style/form`,
   pick the densest `article|main|div` by text/link ratio). Title/author/date from
   `og:*`, `<meta name="citation_*">`, JSON-LD, `<title>`.
3. Chunk the markdown by headings into ~800-char passages.
4. Store the full markdown in the durable result store; `Source.result_ref` points to it.
5. Return: header (title, site, date, citation ID, `result_ref`), then the top passages ranked by
   BM25 against `query` (or the session's current open question text when `query` is omitted),
   capped at `max_chars`, plus an outline of remaining sections. The agent pulls more with
   `result_search`/`result_range`.
6. Promote the source to `state="read"`.

**`read_document`:** same promotion and metadata path; PDF text chunked per page, locator =
page number.

**New `research_fetch(urls: list[str], query=None)`:** reads up to 6 URLs concurrently (thread
pool, per-URL timeout), one approval prompt, returns the same per-URL block truncated to
`max_chars / n`. Read-only; same egress and secret-scrub checks as `url_grounding`.

**Dependency decision:** add `trafilatura` to `requirements.txt` (pure Python, maintained). The
fallback keeps tests and minimal installs working; a test covers both paths.

### 3.5 Evidence ledger tools

All are read/write on session state only (no filesystem or network), so they run without an
approval prompt, like `assess_source` today.

| Tool | Purpose |
|---|---|
| `research_question(action=add|update|drop, text, parent_id?, todo_id?)` | Build the question tree. `set_topic` becomes an alias that adds/selects a root question. |
| `record_claim(question_id, text, kind, evidence=[{citation_id, stance, excerpt, locator?}])` | Add a claim. Validates: question exists; each source is `read`; excerpt occurs in the source text (whitespace/case-normalised fuzzy match ≥ 0.9) — else the tool returns an error naming the mismatch. Citing promotes the source to `cited`. |
| `add_evidence(claim_id, citation_id, stance, excerpt)` | Attach more evidence or a contradiction later. |
| `research_status(question_id?)` | Coverage view: per question, claims, distinct origins, contradictions, confidence, unread high-value candidates, and open questions with no claims. |

**Confidence (computed, deterministic):**

- `high`: ≥ 2 supporting origins, at least one with credibility ≥ 0.7, no contradicting evidence.
- `medium`: ≥ 2 supporting origins, or 1 origin with credibility ≥ 0.8; contradictions present
  but outnumbered by origins.
- `low`: otherwise. `kind="inferred"` caps at `medium`.
- A question is `contested` when any of its claims has supporting and contradicting evidence
  from different origins; `answered` when every claim is ≥ `medium`.

Origins, not URLs, are counted, so three pages on one site count once. The agent may override an
`origin` via `assess_source(citation_id, origin=...)` (e.g. syndicated wire copy).

### 3.6 Outputs

**Bibliography:** `compile_bibliography()` lists only `cited` sources, grouped by root question,
numbered in first-citation order. `include="read"` shows read-but-uncited sources as a
"Consulted" section.

**`research_report(question_id?, format="markdown")`** returns a skeleton built from the ledger
for the agent to write into:

```
## Answer                       ← placeholder, agent writes
## Findings
### <question text>   (confidence: high)
- <claim> [1][3]
  - evidence: "<excerpt>" — [1] §2
## Contested
- <claim>: supports [2] vs contradicts [5]
## Gaps
- <open question with no claims> · <unread candidates worth reading>
## Method                       ← tools used, counts of searched / read / cited
## Sources                      ← cited bibliography
```

**deep-research skill:** `research_export(dir)` writes `supporting_data/source_register.md`,
`evidence_log.md` and `questions.md` from the ledger (approval-gated write, workspace path rules).
Update `SKILL.md` to record through the ledger tools and export, not hand-maintain those files.

**Visual:** `research_status(visual=true)` returns data suitable for `publish_visualization`; the
mode prompt suggests a claims × sources map for reports with ≥ 5 claims.

### 3.7 Commands and GUI

- `/research` gains `questions`, `claims [qid]`, `gaps`, `export <dir>`; `sources` gets
  `--state candidate|read|cited`.
- `GET /api/research/state` returns `{questions, claims, sources, coverage}` from the session's
  state (not the global manager). Findings stop reading `task_memory` entries generically.
- Research panel (Wash, rows not cards): question tree on the left as indented rows with status
  and confidence; selecting a question shows its claims; a claim opens a `WashSheet` with its
  evidence excerpts and sources; a "Sources" tab with state filter and credibility; a "Matrix"
  disclosure (claims × origins, supports/contradicts/qualifies glyphs). Colours and status words
  from `mu/design/tokens.json` / `vocabulary.json` (add `contested`, `answered` if missing, then
  `python3 scripts/design/build_tokens.py`).

### 3.8 Prompt and docs

- `mu/prompts` research block: replace the prose cross-referencing rules with the loop
  *plan questions → search (candidates) → read → record claims → check status → report*.
  Keep it short; the tools enforce the rules.
- Rewrite `documentation/research_mode.md` sections "Core behavior", "Citation requirements",
  "Output format".

## 4. Phases

Each phase lands green on the full suite (`python3 -m pytest tests/ -q`).

### Phase 1 — Session isolation and package move (S)
1. Create `mu/research/` with `sources.py` (moved `Source`, `SourceType`, `CitationManager`),
   re-export from `utils/citation_manager.py`.
2. `SessionManager.research`; tool context exposes it; ContextVar fallback for
   `get_citation_manager()`.
3. Handlers, `/research` command and GUI router read session state.

Tests (`tests/modes/test_research_state.py`): two sessions registering sources concurrently
(threads) never see each other's; save/load round-trip; loading the current list-shaped
snapshot works.

### Phase 2 — Candidates and URL normalisation (S)
1. `mu/research/urls.py` + in-tree suffix list.
2. `Source.state`, `canonical_url`, `origin`; search tools register candidates.
3. Bibliography excludes candidates.

Tests (`tests/tools/test_research_urls.py`, `test_research_candidates.py`): URL equivalence
table (tracking params, scheme, www, fragment, arXiv pdf/abs/version, DOI forms); a stubbed
`web_search` returns 10 rows → 0 bibliography entries; reading one → 1 entry with stable ID.

### Phase 3 — Extraction and bounded reading (M)
1. `mu/research/extract.py` (trafilatura + bs4 fallback, metadata, chunking, BM25 ranking — small
   in-tree BM25, no new dep).
2. Rewrite `url_grounding`; full text to result store; `read_document` promotion.
3. `research_fetch` tool + registry entries (`capabilities.py`, `descriptors.py`,
   `_dispatcher.py`).
4. `trafilatura` in `requirements.txt`.

Tests (`tests/tools/test_research_extract.py`): local HTML fixtures served by a stdlib
`http.server` on loopback (no internet): boilerplate removed, metadata parsed, output ≤
`max_chars`, query-relevant passage ranked first, `result_ref` recall returns full text, same
results with trafilatura monkeypatched away; `research_fetch` reads 3 URLs with one approval and
reports one failing URL without failing the rest; content-hash mirror merges.

### Phase 4 — Evidence ledger (M)
1. `mu/research/ledger.py`: `Question`, `Claim`, `Evidence`, confidence and status rules.
2. Tools `research_question`, `record_claim`, `add_evidence`, `research_status`; `set_topic`
   alias; `assess_source(origin=)`.
3. Persist with the session.

Tests (`tests/modes/test_research_ledger.py`): excerpt not in source → error; candidate source
cannot be cited; confidence table (same-origin pair = 1 origin → not high; contradiction →
contested; inferred capped); coverage lists unanswered questions; round-trip persistence.

### Phase 5 — Outputs (M)
1. Cited-only bibliography with "Consulted" option.
2. `research_report` skeleton; `research_export` (approval-gated).
3. Update `deep-research/SKILL.md`, research prompt block, `documentation/research_mode.md`.

Tests (`tests/modes/test_research_report.py`): a seeded ledger produces Findings ordered by
question, Contested and Gaps entries, numbering in first-citation order; export writes the three
files inside the workspace and refuses paths outside it.

### Phase 6 — Commands, GUI, cleanup (M)
1. `/research questions|claims|gaps|export`, `sources --state`.
2. `/api/research/state` new shape; research panel (tree, claims sheet, sources tab, matrix);
   tokens if needed.
3. Remove `utils/citation_manager.py` shim and the manager hydrate/snapshot code; delete tests
   whose behaviour is no longer specified.

Tests: `tests/gui/test_research_state_api.py` (shape, session isolation through two clients);
`tests/gui/frontend` node test for the panel store (tree build, matrix from claims);
`tests/terminal` for the new `/research` subcommands' output.

## 5. Security and invariants

- Fetching keeps existing egress rules, timeouts and secret scrubbing; `research_fetch` caps URL
  count and total bytes, and goes through the normal approval gate (one prompt per batch).
- Ledger tools mutate only session state; `research_export` writes through the normal
  approval-gated file path with workspace bounds.
- Extracted text is untrusted: stored and returned as data, never interpolated into system
  prompt layers; excerpts are length-capped.
- Context budgets: `url_grounding` output is bounded by `max_chars`; full text lives in the
  durable result store (L4 recall), not L5.
- Plan mode: search/read/status tools allowed; `research_export` blocked like any write.

## 6. Risks

| Risk | Mitigation |
|---|---|
| Agents skip `record_claim` and write prose anyway | Prompt loop + `research_report` only renders recorded claims; `research_status` gaps surface missing work |
| Excerpt check too strict on reformatted text | Normalised fuzzy match ≥ 0.9; error message shows nearest passage |
| trafilatura misses content on JS-heavy pages | bs4 fallback; empty result reported explicitly with a hint to try another source |
| Origin = domain is wrong for platforms (medium.com, github.io) | Suffix list includes common hosting platforms as private suffixes; `assess_source(origin=)` override |
| Tool count growth | Ledger tools only registered in research mode (`capabilities.py`) |

## 7. Open decisions

1. Add `trafilatura` as a hard dependency (proposed) or optional extra?
2. Keep `set_topic` as an alias permanently, or remove after phase 6?
3. Should non-research modes get `research_fetch` (useful in debug/default), or keep it
   research-only?
4. Android: show the question tree read-only in a later mobile parity pass?

## 8. Status

Implemented 2026-10-23, phases 1–6. `utils/citation_manager.py` is gone; the engine is
`mu/research/` (`sources.py` source model and `ResearchState`, `ledger.py`, `urls.py`,
`extract.py`, `report.py`, `texts.py` — the content-addressed store of extracted text under
`~/.mucli/research/texts/` that excerpt checks read). Ledger tools are in
`mu/tools/research/ledger_tools.py`; the panel model in `static/js/research_ledger.js`.

Decisions taken (§7):

1. `trafilatura` is a hard dependency in `requirements.txt`; the bs4 fallback stays and both
   paths are tested.
2. There was no `set_topic` tool; `ResearchState.set_topic` (used by `/research topic`)
   selects or creates a root question by name and stays.
3. `research_fetch` is research-only (`phase="research"`), like the other research tools.
4. Android reads the claims through the existing `findings` shape (read-only); a question
   tree on mobile is left for a later parity pass.

Where the build differs from the text above:

- **Citation numbers are assigned when a source is read**, not renumbered in first-citation
  order: the agent writes `[^n]` from the number it saw when reading, so renumbering would
  break prose written before the report. The bibliography lists cited sources grouped by
  root question, then read-but-uncited ones under "Consulted" (the default; `include="cited"`
  drops it).
- **`research_fetch` has no approval prompt**, the same as `url_grounding`: it is a read-only
  fetch with the same caps (6 URLs, 5 MB each, per-URL timeout).
- **A parent question is judged on its subtree** (its own claims plus those of live
  sub-questions), so a parent answered through its sub-questions is not reported as a gap.
- A page with under 80 characters of main content after a Playwright retry is reported as
  unreadable rather than recorded.
- Vocabulary gained `research_question`, `research_confidence`, `research_stance` and
  `research_source_state`.
