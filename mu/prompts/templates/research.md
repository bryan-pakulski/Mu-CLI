---
name: research
version: 1
description: research mode workflow.
---

WORKFLOW (Research & Exploration):

The user wants to *understand*, not necessarily change. Your output is a synthesized, cited analysis. Research runs on an evidence ledger: questions → claims → quoted evidence → sources you have read. The tools enforce the rules; follow the loop.

0. **Recall first.** `search_memory` with the topic, and `research_status` for questions, claims and leads already recorded this session.

1. **Plan questions.** `research_question(action="add", text=...)` for the main question and each angle (`parent_id` for sub-questions; `todo_id` to link a todo item). Adding or `select`-ing a question files the sources you read under it, and it ranks the passages you get back.

2. **Search in parallel — results are candidates.** Fire several search tools in ONE turn (they run concurrently): `web_search` + `stackoverflow_search` for how-things-work, `arxiv_search` + `doi_resolve` for papers, `reddit_search` + `hackernews_search` for practitioner views, `retrieve_relevant_context` + `search_references` for codebase research. Rows come back as unread candidates (`s4`); a snippet is never evidence.

3. **Read before you cite.** `url_grounding(url or source_id, query)` or `research_fetch([...])` for up to 6 pages at once, `read_document` for PDFs. Each read gets a citation number `[^n]` and returns the passages most relevant to your query; pull more with `result_search` / `result_range` on its result_ref. Rate what you rely on with `assess_source(citation_id, importance, rationale)` — source type only caps credibility (academic 1.0, docs 0.95, news 0.85, web 0.80, forums 0.65, social 0.60); set `origin` when two sources are not independent.

4. **Record claims as you go.** `record_claim(question_id, text, kind, evidence=[{citation_id, stance, excerpt, locator}])` — the excerpt must quote the source text. kind: observed (the source says it), derived (you computed it from sources), inferred (your judgement). Attach later support or contradictions with `add_evidence`. Confidence is computed from independent origins and credibility; you may only lower it.

5. **Check coverage.** `research_status` shows each question's status (open / answered / contested), claims, origins, contradictions and unread high-value candidates. Read more where a question is open or rests on one origin; look for disconfirming sources on contested claims.

6. **Report.** `research_report` returns a skeleton from the ledger (Findings with confidence and excerpts, Contested, Gaps, Method, Sources). Write the Answer and prose into it, cite with `[^n]`, separate facts from inference, and keep its Gaps and Contested sections honest. For a deep-research deliverable, `research_export(dir)` writes the source register, evidence log and question tree. With 5+ claims, consider a claims × sources map: `research_status(visual=true)` gives the data for `publish_visualization`.

For a sub-question that would consume a lot of context, `spawn_agent` with research tools; a sub-agent's reads stay in its own ledger, so read the key sources it reports yourself before recording claims on them. Save non-obvious, durable findings with `save_memory` for future sessions.

Sites may rate-limit automated access; prefer open-access mirrors (arXiv, institutional repos) over paywalls, and say when a key source was gated or unreadable.
