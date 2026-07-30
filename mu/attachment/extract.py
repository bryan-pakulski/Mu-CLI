
"""Bounded text extraction and search for registered attachments."""
from __future__ import annotations

import html
import json
import os
import re
import zipfile
from typing import Any
from xml.etree import ElementTree

_MAX_EXTRACT_CHARS = int(os.getenv("MUCLI_ATTACHMENT_EXTRACT_MAX_CHARS", 2_000_000))
_TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".json",
    ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".xml", ".html",
    ".htm", ".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".scss", ".sql",
    ".sh", ".bash", ".zsh", ".fish", ".java", ".kt", ".kts", ".go", ".rs",
    ".c", ".h", ".cpp", ".hpp", ".cs", ".php", ".rb", ".swift", ".dart",
}


def _bounded(value: str) -> str:
    if len(value) <= _MAX_EXTRACT_CHARS:
        return value
    return value[:_MAX_EXTRACT_CHARS] + "\n...[extraction capped]"


def _decode_text(path: str) -> str:
    with open(path, "rb") as handle:
        raw = handle.read(_MAX_EXTRACT_CHARS + 1)
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _pdf(path: str) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("pypdf is required to read PDF attachments") from exc
    reader = PdfReader(path)
    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception as exc:
            raise ValueError("PDF is encrypted") from exc
    chunks: list[str] = []
    used = 0
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if not text.strip():
            continue
        block = f"\n\n--- Page {page_number} ---\n{text.strip()}"
        chunks.append(block)
        used += len(block)
        if used >= _MAX_EXTRACT_CHARS:
            break
    return _bounded("".join(chunks).strip())


def _docx(path: str) -> str:
    with zipfile.ZipFile(path) as archive:
        raw = archive.read("word/document.xml")
    root = ElementTree.fromstring(raw)
    paragraphs: list[str] = []
    for paragraph in root.iter():
        if paragraph.tag.endswith("}p"):
            text = "".join(
                node.text or "" for node in paragraph.iter() if node.tag.endswith("}t")
            ).strip()
            if text:
                paragraphs.append(text)
    return _bounded("\n\n".join(paragraphs))


def _html(path: str) -> str:
    source = _decode_text(path)
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(source, "html.parser")
        for node in soup(["script", "style", "noscript"]):
            node.decompose()
        return _bounded(soup.get_text("\n"))
    except ImportError:
        text = re.sub(r"<[^>]+>", " ", source)
        return _bounded(html.unescape(text))


def extract_text(path: str, mime_type: str = "") -> tuple[str, str]:
    ext = os.path.splitext(path)[1].lower()
    mime = str(mime_type or "").split(";", 1)[0].lower()
    if ext == ".pdf" or mime == "application/pdf":
        return _pdf(path), "pdf"
    if ext == ".docx" or mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return _docx(path), "docx"
    if ext in {".html", ".htm"} or mime in {"text/html", "application/xhtml+xml"}:
        return _html(path), "html"
    if mime.startswith("text/") or ext in _TEXT_EXTENSIONS:
        text = _decode_text(path)
        if ext == ".json":
            try:
                text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
            except ValueError:
                pass
        return _bounded(text), "text"
    raise ValueError(
        f"No text extractor for {mime or 'application/octet-stream'} ({ext or 'no extension'}). "
        "The file remains available for download."
    )


def read_chunk(path: str, mime_type: str, *, offset: int = 0, max_chars: int = 12_000) -> dict[str, Any]:
    text, format_name = extract_text(path, mime_type)
    start = max(0, int(offset or 0))
    limit = max(500, min(50_000, int(max_chars or 12_000)))
    chunk = text[start:start + limit]
    next_offset = start + len(chunk)
    return {
        "text": chunk,
        "format": format_name,
        "offset": start,
        "next_offset": next_offset if next_offset < len(text) else None,
        "total_chars": len(text),
        "truncated": next_offset < len(text),
    }


def search_text(text: str, query: str, *, max_results: int = 12) -> list[dict[str, Any]]:
    needle = str(query or "").strip()
    if not needle:
        return []
    pattern = re.compile(re.escape(needle), re.IGNORECASE)
    results: list[dict[str, Any]] = []
    for match in pattern.finditer(text):
        start = max(0, match.start() - 220)
        end = min(len(text), match.end() + 380)
        results.append({
            "offset": match.start(),
            "snippet": text[start:end].strip(),
        })
        if len(results) >= max(1, min(50, int(max_results or 12))):
            break
    return results
