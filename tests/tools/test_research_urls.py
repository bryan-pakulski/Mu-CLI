"""URL normalisation and origin grouping for research sources."""

import pytest

from mu.research.urls import canonical_url, content_hash, origin


@pytest.mark.parametrize(
    "variants",
    [
        [
            "https://example.com/article",
            "http://example.com/article",
            "https://www.example.com/article/",
            "https://EXAMPLE.com/article#section-2",
            "https://example.com/article?utm_source=x&utm_medium=y",
            "https://example.com:443/article?fbclid=abc&gclid=def",
        ],
        [
            "https://example.com/p?b=2&a=1",
            "https://example.com/p?a=1&b=2&ref=newsletter",
        ],
        [
            "https://arxiv.org/abs/2101.00001",
            "https://arxiv.org/abs/2101.00001v2",
            "https://arxiv.org/pdf/2101.00001",
            "https://arxiv.org/pdf/2101.00001v3.pdf",
        ],
        [
            "https://doi.org/10.1000/ABC.123",
            "https://dx.doi.org/10.1000/abc.123",
        ],
        [
            "https://old.reddit.com/r/python/comments/1",
            "https://www.reddit.com/r/python/comments/1/",
        ],
    ],
)
def test_equivalent_urls_share_one_canonical_form(variants):
    keys = {canonical_url(v) for v in variants}
    assert len(keys) == 1, keys


def test_distinct_pages_stay_distinct():
    assert canonical_url("https://example.com/a") != canonical_url("https://example.com/b")
    assert canonical_url("https://example.com/p?id=1") != canonical_url("https://example.com/p?id=2")


def test_html_canonical_used_only_on_same_domain():
    page = "https://example.com/story?id=9"
    assert canonical_url(page, "https://example.com/story/nine") == canonical_url(
        "https://example.com/story/nine"
    )
    # A canonical pointing at another site is ignored (could be hostile).
    assert canonical_url(page, "https://evil.test/x") == canonical_url(page)


def test_origin_groups_by_registrable_domain_and_hosting_platform():
    assert origin("https://docs.python.org/3/") == origin("https://www.python.org/about")
    assert origin("https://news.bbc.co.uk/a") == origin("https://www.bbc.co.uk/b")
    # Hosting platforms are private suffixes: different users are different origins.
    assert origin("https://alice.github.io/post") != origin("https://bob.github.io/post")


def test_content_hash_ignores_whitespace_and_case():
    assert content_hash("Hello   World\n") == content_hash("hello world")
    assert content_hash("") is None
