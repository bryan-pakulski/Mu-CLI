# Research Mode

For "help me *understand* X" — explanations, write-ups, surveys.
Switch in via `/mode research`.

The output is a synthesized analysis with citations, not a code change.
Source credibility is AI-assessed per source, claims are cross-referenced,
and every external fact gets a footnote. Sources are grouped by research
topic so the bibliography stays organized by ask.

For "fix this bug" use [debug](debug_mode.md). For "make this change"
use [default](default_mode.md). For multi-hour deep dives use
[loop](loop_mode.md) on top of research mode.

## Core behavior

1. **Recall first**
   - `search_memory` with the topic. Prior research turns may have
     saved key findings — start from those instead of re-fetching.

2. **Plan the investigation**
   - Publish a `todo_write` of open questions so the user can see the
     angles being pursued.
   - Mark one `in_progress`; promote / defer as evidence comes in.

3. **Set the research topic**
   - Call `set_research_topic("<short ask>")` before firing searches for
     a new rabbit hole. Sources registered afterwards inherit that topic,
     so the bibliography stays grouped by ask.
   - Call it again whenever pivoting to a new sub-question.

4. **Cast a wide net in parallel**
   For a single question, fire multiple search tools in one turn —
   they execute concurrently:
   - `web_search` + `stackoverflow_search` — "how does X work" /
     library questions
   - `arxiv_search` + `doi_resolve` — academic / technical-paper
     questions
   - `reddit_search` + `hackernews_search` — community perspectives /
     war stories
   - `retrieve_relevant_context` + `search_references` — codebase
     research

5. **Lead with semantic retrieval for in-repo questions**
   - `retrieve_relevant_context` surfaces the right files faster than
     blind `read_file`.
   - Follow with `read_file` on the top hits, in parallel.

6. **Delegate multi-angle deep dives**
   - When a sub-question would consume significant context (read 30+
     docs, follow 50+ refs), fire `spawn_agent` with a research-tool
     whitelist:
     ```
     tools=["web_search","arxiv_search","doi_resolve",
            "stackoverflow_search","url_grounding","read_document",
            "retrieve_relevant_context","search_for_string","read_file"]
     ```
   - The child returns a focused summary; the parent stays free to
     synthesize.

7. **Read primary sources**
   - `url_grounding` for landing pages.
   - `read_document` for PDFs — accepts either a local path or an
     `http(s)://` URL. Passing a URL fetches the PDF directly (no
     curl/download step) and auto-registers the source in the citation
     engine (arxiv → academic, otherwise documentation).
   - `read_file` for in-repo files.
   - Don't synthesize from snippets when full text is available.

8. **Persist findings as you go**
   - `save_memory` with discovered invariants, gotchas, key numbers.
     Multi-turn research compounds — tag aggressively with the topic.

9. **Synthesize, cite, deliver**
   - Cross-reference, weight by credibility, write the answer.

## Citation requirements

- ALL sources must be registered with the CitationManager before being
  cited.
- Set the research topic with `set_research_topic("<ask>")` before
  registering sources for a new rabbit hole — the bibliography is
  grouped by topic.
- Every claim from external sources gets a footnote ref `[^n]`.
- End with a bibliography via `compile_bibliography()` — it emits
  sources grouped by topic under `### <topic>` headings. Pass an
  optional topic argument to compile just one bucket.

## Source credibility (AI-assessed, not hardcoded)

Sources default to **0.0 (unassessed)** until the AI explicitly grades
them. The AI owns the weighting decision.

After reading a source you intend to cite, call
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

- Cross-reference important claims across at least two sources.
- Prefer recent sources for fast-moving topics.
- Note conflicts of interest in the write-up (e.g. a vendor blog about
  a vendor's own product).

## Output format

A self-contained write-up: lead with the synthesized answer, follow
with the reasoning / evidence, end with the bibliography. No tool-call
narration; the user wants the finding, not the search history.

## When to leave research mode

- Findings point at a concrete change → `/mode default` to implement.
- Findings point at a specific failure to reproduce → `/mode debug`.
- The write-up itself is long-horizon (multi-day survey) → `/mode loop`.
