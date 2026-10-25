"""URL normalisation and origin grouping for research sources.

``canonical_url`` produces the dedup key for a source; ``origin`` groups
sources that are not independent (same registrable domain or hosting
account). No network access: the public-suffix data is a small in-tree list.
"""

from __future__ import annotations

import hashlib
import re
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Multi-label public suffixes (ccTLD second levels) plus hosting platforms
# treated as private suffixes so ``alice.github.io`` and ``bob.github.io``
# are different origins.
_MULTI_LABEL_SUFFIXES = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "ltd.uk", "plc.uk", "me.uk", "net.uk",
    "com.au", "net.au", "org.au", "edu.au", "gov.au", "asn.au", "id.au",
    "co.nz", "org.nz", "govt.nz", "ac.nz", "net.nz",
    "co.jp", "ac.jp", "go.jp", "or.jp", "ne.jp",
    "co.kr", "or.kr", "ac.kr", "go.kr",
    "com.br", "org.br", "gov.br", "edu.br",
    "com.cn", "org.cn", "edu.cn", "gov.cn", "net.cn",
    "co.in", "org.in", "ac.in", "gov.in", "net.in",
    "co.za", "org.za", "ac.za", "gov.za",
    "com.mx", "org.mx", "gob.mx", "edu.mx",
    "com.sg", "edu.sg", "gov.sg", "org.sg",
    "com.hk", "edu.hk", "gov.hk", "org.hk",
    "com.tw", "edu.tw", "gov.tw", "org.tw",
    "com.ar", "com.tr", "com.my", "com.ph", "com.pk", "com.ng", "co.il", "ac.il",
    "eu.org",
    # Hosting platforms (private suffixes): each subdomain is its own origin.
    "github.io", "gitlab.io", "pages.dev", "netlify.app", "vercel.app",
    "herokuapp.com", "blogspot.com", "wordpress.com", "substack.com",
    "medium.com", "readthedocs.io", "web.app", "firebaseapp.com",
    "azurewebsites.net", "cloudfront.net", "s3.amazonaws.com", "tumblr.com",
    "notion.site", "hashnode.dev", "dev.to", "neocities.org", "glitch.me",
})

_TRACKING_PARAMS = frozenset({
    "gclid", "fbclid", "dclid", "msclkid", "yclid", "ref", "ref_src", "ref_url",
    "igshid", "si", "spm", "_hsenc", "_hsmi", "mkt_tok", "cmpid", "s_cid",
})
_TRACKING_PREFIXES = ("utm_", "mc_", "pk_", "hsa_")

_HOST_REWRITES = {
    "old.reddit.com": "reddit.com",
    "m.reddit.com": "reddit.com",
    "np.reddit.com": "reddit.com",
    "mobile.twitter.com": "twitter.com",
    "x.com": "twitter.com",
    "m.wikipedia.org": "wikipedia.org",
    "m.youtube.com": "youtube.com",
    "youtu.be": "youtube.com",
}

_ARXIV_ID = re.compile(r"^/(?:abs|pdf)/(?P<id>[a-z\-]+(?:\.[a-z]{2})?/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?/?$", re.I)
_DOI = re.compile(r"(10\.\d{4,9}/\S+)", re.I)


def _strip_www(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


def _norm_host(host: str) -> str:
    host = _strip_www((host or "").lower().rstrip("."))
    host = _HOST_REWRITES.get(host, host)
    if host.endswith(".m.wikipedia.org"):
        host = host.replace(".m.wikipedia.org", ".wikipedia.org")
    return host


def registrable_domain(host: str) -> str:
    """Return the registrable domain (eTLD+1) for ``host``."""
    host = _norm_host(host.split(":")[0])
    if not host or re.fullmatch(r"[\d.]+|\[.*\]", host):
        return host
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    for n in (3, 2):
        if len(labels) > n and ".".join(labels[-n:]) in _MULTI_LABEL_SUFFIXES:
            return ".".join(labels[-(n + 1):])
    if ".".join(labels[-2:]) in _MULTI_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _is_tracking(key: str) -> bool:
    k = key.lower()
    return k in _TRACKING_PARAMS or k.startswith(_TRACKING_PREFIXES)


def doi_from_url(url: str) -> Optional[str]:
    parts = urlsplit(url.strip())
    host = _norm_host(parts.netloc)
    if url.lower().startswith("doi:"):
        return url[4:].strip().lower() or None
    if host in ("doi.org", "dx.doi.org"):
        m = _DOI.search(parts.path.lstrip("/"))
        return m.group(1).lower() if m else None
    return None


def canonical_url(url: str, html_canonical: Optional[str] = None) -> str:
    """Normalise ``url`` into a stable dedup key.

    ``html_canonical`` (``<link rel="canonical">``) wins when it is on the
    same registrable domain as ``url``.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    if html_canonical:
        cand = html_canonical.strip()
        try:
            if cand and registrable_domain(urlsplit(cand).netloc) == registrable_domain(urlsplit(raw).netloc):
                raw = cand
        except ValueError:
            pass
    doi = doi_from_url(raw)
    if doi:
        return f"doi:{doi}"
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return raw
    host = _norm_host(parts.hostname or "")
    port = parts.port if parts.port not in (None, 80, 443) else None
    netloc = f"{host}:{port}" if port else host
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if host == "arxiv.org":
        m = _ARXIV_ID.match(path)
        if m:
            return f"https://arxiv.org/abs/{m.group('id').lower()}"
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k))
    return urlunsplit(("https", netloc, path, urlencode(query), ""))


def origin(url: str) -> str:
    """Independence group for a source URL (registrable domain)."""
    key = canonical_url(url)
    if key.startswith("doi:"):
        return "doi.org"
    try:
        return registrable_domain(urlsplit(key).netloc) or key
    except ValueError:
        return key


def content_hash(text: str) -> Optional[str]:
    """sha256 of whitespace/case-normalised text; None for near-empty text."""
    norm = " ".join((text or "").lower().split())
    if len(norm) < 200:
        return None
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def display_url(url: str) -> str:
    """``url`` without tracking parameters or fragment, scheme and host kept."""
    raw = (url or "").strip()
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    if parts.scheme.lower() not in ("http", "https"):
        return raw
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
