"""Main-content extraction, chunking and passage ranking for research reads.

``extract_html`` turns a fetched page into clean markdown-ish text plus
metadata. trafilatura is used when installed; otherwise a bs4 heuristic drops
boilerplate elements and keeps the densest content block. ``chunk_markdown``
splits by headings into ~800-char passages and ``rank_passages`` orders them
with a small in-tree BM25 against a query.

Extracted text is untrusted data: callers return it as tool output only.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

PASSAGE_CHARS = 800
_BOILERPLATE_TAGS = (
    "nav", "header", "footer", "aside", "script", "style", "form",
    "noscript", "iframe", "svg", "button", "template",
)
_BOILERPLATE_HINTS = re.compile(
    r"(cookie|consent|banner|newsletter|subscribe|sidebar|share|social|promo|advert|"
    r"related|comment|breadcrumb|menu|navbar|popup|modal)",
    re.I,
)
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an and are as at be but by for from has have in is it its of on or that the "
    "this to was were will with".split()
)


@dataclass
class Extracted:
    title: str = ""
    text: str = ""
    authors: List[str] = field(default_factory=list)
    published: Optional[str] = None
    site: Optional[str] = None
    canonical: Optional[str] = None
    method: str = "bs4"


@dataclass
class Passage:
    index: int
    heading: str
    text: str
    score: float = 0.0


# ------------------------------------------------------------------ metadata


def _meta(soup, *names: str) -> Optional[str]:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find(
            "meta", attrs={"name": name}
        )
        if tag and tag.get("content"):
            value = str(tag["content"]).strip()
            if value:
                return value
    return None


def _json_ld(soup) -> Dict[str, Any]:
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
        except (TypeError, ValueError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and "@graph" in item and isinstance(item["@graph"], list):
                items.extend(x for x in item["@graph"] if isinstance(x, dict))
        for item in items:
            if not isinstance(item, dict):
                continue
            kind = item.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if any(
                str(k).lower() in ("article", "newsarticle", "blogposting", "scholarlyarticle",
                                   "techarticle", "report", "webpage")
                for k in kinds
            ):
                return item
    return {}


def _ld_authors(value: Any) -> List[str]:
    out: List[str] = []
    items = value if isinstance(value, list) else [value]
    for item in items:
        if isinstance(item, dict) and item.get("name"):
            out.append(str(item["name"]).strip())
        elif isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


def _metadata(soup) -> Extracted:
    ld = _json_ld(soup)
    title = (
        _meta(soup, "citation_title", "og:title", "twitter:title")
        or (str(ld.get("headline")).strip() if ld.get("headline") else None)
        or (soup.title.get_text(strip=True) if soup.title else "")
    )
    authors = [
        str(t["content"]).strip()
        for t in soup.find_all("meta", attrs={"name": "citation_author"})
        if t.get("content")
    ]
    if not authors:
        authors = _ld_authors(ld.get("author"))
    if not authors:
        author = _meta(soup, "author", "article:author")
        if author and not author.startswith("http"):
            authors = [author]
    published = (
        _meta(soup, "citation_publication_date", "citation_date", "article:published_time",
              "date", "dc.date")
        or (str(ld.get("datePublished")) if ld.get("datePublished") else None)
    )
    site = _meta(soup, "og:site_name", "citation_journal_title")
    canonical = None
    link = soup.find("link", attrs={"rel": lambda v: v and "canonical" in (v if isinstance(v, list) else [v])})
    if link and link.get("href"):
        canonical = str(link["href"]).strip()
    return Extracted(
        title=title or "", authors=authors, published=published, site=site, canonical=canonical
    )


# ------------------------------------------------------------------ bs4 path


def _block_to_markdown(node) -> str:
    lines: List[str] = []
    for el in node.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "blockquote",
                             "td", "th", "dd", "dt"]):
        # Skip elements nested in another captured element (li > p etc.).
        if el.find_parent(["li", "pre", "blockquote", "td", "th"]) and el.name not in ("pre",):
            if el.name not in ("li",) or el.find_parent(["pre"]):
                continue
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        if el.name.startswith("h"):
            lines.append("#" * int(el.name[1]) + " " + text)
        elif el.name == "li":
            lines.append("- " + text)
        elif el.name == "pre":
            lines.append("```\n" + el.get_text() .strip("\n") + "\n```")
        elif el.name == "blockquote":
            lines.append("> " + text)
        else:
            lines.append(text)
    if not lines:
        text = node.get_text("\n", strip=True)
        lines = [ln for ln in text.splitlines() if ln.strip()]
    # Collapse duplicate consecutive lines.
    out: List[str] = []
    for ln in lines:
        if not out or out[-1] != ln:
            out.append(ln)
    return "\n\n".join(out)


def _density(node) -> float:
    text = node.get_text(" ", strip=True)
    if not text:
        return 0.0
    link_chars = sum(len(a.get_text(" ", strip=True)) for a in node.find_all("a"))
    paragraphs = len(node.find_all("p"))
    ratio = 1.0 - min(1.0, link_chars / max(1, len(text)))
    return len(text) * ratio * (1.0 + 0.1 * min(paragraphs, 20))


def _bs4_main(soup) -> str:
    for tag in soup.find_all(_BOILERPLATE_TAGS):
        tag.decompose()
    for tag in list(soup.find_all(True)):
        if tag.attrs is None:
            continue
        hint = " ".join(
            [str(tag.get("id") or "")] + [str(c) for c in (tag.get("class") or [])]
            + [str(tag.get("role") or "")]
        )
        if hint.strip() and _BOILERPLATE_HINTS.search(hint) and tag.name not in ("body", "html", "main", "article"):
            tag.decompose()
    candidates = soup.find_all(["article", "main"]) or []
    if not candidates:
        candidates = soup.find_all(["div", "section"])
    body = soup.body or soup
    best = max(candidates, key=_density, default=body)
    if _density(best) < 0.25 * _density(body):
        best = body
    return _block_to_markdown(best)


# ----------------------------------------------------------------- public API


def _trafilatura():
    try:
        import trafilatura  # type: ignore
    except Exception:  # pragma: no cover - optional
        return None
    return trafilatura


def extract_html(html: str, url: str = "") -> Extracted:
    """Extract main content and metadata from an HTML document."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    meta = _metadata(soup)
    text = ""
    traf = _trafilatura()
    if traf is not None:
        try:
            text = traf.extract(
                html,
                url=url or None,
                output_format="markdown",
                include_comments=False,
                include_tables=True,
                favor_precision=True,
            ) or ""
        except Exception:
            text = ""
        if text.strip():
            meta.method = "trafilatura"
    if not text.strip():
        text = _bs4_main(soup)
        meta.method = "bs4"
    meta.text = _clean(text)
    return meta


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_markdown(text: str, size: int = PASSAGE_CHARS) -> List[Passage]:
    """Split markdown by headings, then pack paragraphs into ~size passages."""
    passages: List[Passage] = []
    heading = ""
    buf: List[str] = []

    def flush():
        body = "\n\n".join(buf).strip()
        if body:
            passages.append(Passage(index=len(passages), heading=heading, text=body))
        buf.clear()

    for para in re.split(r"\n\s*\n", text or ""):
        para = para.strip()
        if not para:
            continue
        if re.match(r"^#{1,6}\s", para) and "\n" not in para:
            flush()
            heading = para.lstrip("#").strip()
            continue
        while len(para) > size * 1.5:
            cut = para.rfind(". ", 0, size)
            cut = cut + 1 if cut > size // 3 else size
            if buf and sum(len(b) for b in buf) + cut > size:
                flush()
            buf.append(para[:cut].strip())
            flush()
            para = para[cut:].strip()
        if buf and sum(len(b) + 2 for b in buf) + len(para) > size:
            flush()
        buf.append(para)
    flush()
    return passages


def _tokens(text: str) -> List[str]:
    return [t for t in _WORD.findall((text or "").lower()) if t not in _STOP]


def rank_passages(passages: List[Passage], query: Optional[str], k1: float = 1.5,
                  b: float = 0.75) -> List[Passage]:
    """Return passages ordered by BM25 score against ``query`` (stable on ties).

    With no usable query the document order is kept.
    """
    q = _tokens(query or "")
    if not q or not passages:
        return list(passages)
    docs = [_tokens(p.heading + " " + p.text) for p in passages]
    n = len(docs)
    avgdl = sum(len(d) for d in docs) / max(1, n)
    df: Dict[str, int] = {}
    for d in docs:
        for term in set(d):
            df[term] = df.get(term, 0) + 1
    for p, d in zip(passages, docs):
        tf: Dict[str, int] = {}
        for term in d:
            tf[term] = tf.get(term, 0) + 1
        score = 0.0
        for term in set(q):
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            f = tf[term]
            score += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * len(d) / max(1.0, avgdl)))
        p.score = round(score, 4)
    return sorted(passages, key=lambda p: (-p.score, p.index))


def select_passages(text: str, query: Optional[str], max_chars: int) -> Dict[str, Any]:
    """Pick the best passages within ``max_chars``; return them plus an outline."""
    passages = chunk_markdown(text)
    ranked = rank_passages(passages, query)
    chosen: List[Passage] = []
    used = 0
    for p in ranked:
        block = len(p.text) + len(p.heading) + 16
        if used + block > max_chars:
            if not chosen and max_chars > 0:
                p = Passage(index=p.index, heading=p.heading,
                            text=p.text[: max(0, max_chars - len(p.heading) - 16)], score=p.score)
                chosen.append(p)
            continue
        chosen.append(p)
        used += block
    if query and _tokens(query):
        # Relevant ones first (already ranked); keep that order.
        ordered = chosen
    else:
        ordered = sorted(chosen, key=lambda p: p.index)
    chosen_ids = {p.index for p in chosen}
    outline: List[str] = []
    for p in passages:
        if p.index not in chosen_ids and p.heading and p.heading not in outline:
            outline.append(p.heading)
    return {"passages": ordered, "outline": outline, "total_passages": len(passages)}


def normalise_for_match(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (text or "").lower())).strip()


def excerpt_match(excerpt: str, text: str, threshold: float = 0.9) -> Dict[str, Any]:
    """Check that ``excerpt`` occurs in ``text`` (normalised, fuzzy ≥ threshold).

    Returns ``{"ok": bool, "ratio": float, "nearest": str}``.
    """
    from difflib import SequenceMatcher

    ex = normalise_for_match(excerpt)
    body = normalise_for_match(text)
    if not ex:
        return {"ok": False, "ratio": 0.0, "nearest": ""}
    if ex in body:
        return {"ok": True, "ratio": 1.0, "nearest": excerpt}
    words = body.split()
    ex_words = ex.split()
    w = len(ex_words)
    if not words or w == 0:
        return {"ok": False, "ratio": 0.0, "nearest": ""}
    best_ratio, best = 0.0, ""
    best_overlap, nearest = 0, ""
    ex_set = set(ex_words)
    step = max(1, w // 4)
    for start in range(0, max(1, len(words) - w + 1), step):
        window = words[start:start + w]
        overlap = len(ex_set.intersection(window))
        if overlap > best_overlap:
            best_overlap, nearest = overlap, " ".join(window)
        if overlap < 0.5 * len(ex_set):
            continue
        cand = " ".join(window)
        ratio = SequenceMatcher(None, ex, cand, autojunk=False).ratio()
        if ratio > best_ratio:
            best_ratio, best = ratio, cand
    return {"ok": best_ratio >= threshold, "ratio": round(best_ratio, 3),
            "nearest": (best or nearest)[:300]}
