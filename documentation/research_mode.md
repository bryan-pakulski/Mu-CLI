# Research Mode

For "help me *understand* X" — explanations, write-ups, surveys.
Switch in via `/mode research`.

The output is a synthesized analysis with citations, not a code change.
Source credibility is weighted, claims are cross-referenced, and every
external fact gets a footnote.

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

3. **Cast a wide net in parallel**
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

4. **Lead with semantic retrieval for in-repo questions**
   - `retrieve_relevant_context` surfaces the right files faster than
     blind `read_file`.
   - Follow with `read_file` on the top hits, in parallel.

5. **Delegate multi-angle deep dives**
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

6. **Read primary sources**
   - `url_grounding` for landing pages.
   - `read_document` for PDFs.
   - `read_file` for in-repo files.
   - Don't synthesize from snippets when full text is available.

7. **Persist findings as you go**
   - `save_memory` with discovered invariants, gotchas, key numbers.
     Multi-turn research compounds — tag aggressively with the topic.

8. **Synthesize, cite, deliver**
   - Cross-reference, weight by credibility, write the answer.

## Citation requirements

- ALL sources must be registered with the CitationManager before being
  cited.
- Every claim from external sources gets a footnote ref `[^n]`.
- End with a bibliography via `compile_bibliography()`.

## Source credibility (for weighting conflicting claims)

| Tier | Sources |
| --- | --- |
| ★ 0.8 | Academic (arXiv, DOI, peer-reviewed) |
| ★ 0.7 | Official documentation / vendor sources |
| ★ 0.6 | Reputable news / industry analysis |
| ★ 0.5 | Web search hits (varies — inspect the host) |
| ★ 0.4 | Forums (Reddit, HN — useful for "is this really what people hit?", not facts) |
| ★ 0.3 | Social media |

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
