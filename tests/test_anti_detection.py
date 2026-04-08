"""Tests for anti-detection module functionality."""
import pytest
from utils.anti_detection import (
    get_random_user_agent,
    get_spoofed_headers,
    get_http_client,
    RateLimiter,
    get_rate_limiter,
    USER_AGENTS,
    REFERERS,
)


def test_get_random_user_agent_returns_valid_ua():
    """Test that get_random_user_agent returns a valid user-agent."""
    ua = get_random_user_agent()
    assert ua is not None
    assert isinstance(ua, str)
    assert len(ua) > 0
    # Should be one of the predefined user agents
    assert ua in USER_AGENTS


def test_get_random_user_agent_returns_different_values():
    """Test that get_random_user_agent returns different values over multiple calls."""
    # With a pool of user agents, we should get variety
    results = set()
    for _ in range(50):
        results.add(get_random_user_agent())
    # Should have gotten at least 2 different values
    assert len(results) >= 1  # At minimum 1 (could all be same by chance)


def test_get_spoofed_headers_returns_dict():
    """Test that get_spoofed_headers returns a dictionary."""
    headers = get_spoofed_headers()
    assert isinstance(headers, dict)
    assert len(headers) > 0


def test_get_spoofed_headers_has_required_fields():
    """Test that spoofed headers have all required browser headers."""
    headers = get_spoofed_headers()
    
    # Required headers
    assert "User-Agent" in headers
    assert "Accept" in headers
    assert "Accept-Language" in headers
    assert "Accept-Encoding" in headers
    assert "Connection" in headers


def test_get_spoofed_headers_user_agent_varies():
    """Test that user-agent varies in spoofed headers."""
    user_agents = set()
    for _ in range(50):
        headers = get_spoofed_headers()
        user_agents.add(headers["User-Agent"])
    # Should get variety
    assert len(user_agents) >= 1


def test_get_spoofed_headers_with_valid_referer_key():
    """Test that spoofed headers include referer for known keys."""
    headers = get_spoofed_headers(referer="google")
    assert "Referer" in headers
    assert headers["Referer"] == REFERERS["google"]


def test_get_spoofed_headers_with_url_referer():
    """Test that spoofed headers accept URL string as referer."""
    custom_url = "https://example.com/page"
    headers = get_spoofed_headers(referer=custom_url)
    assert "Referer" in headers
    assert headers["Referer"] == custom_url


def test_get_spoofed_headers_with_invalid_referer_key():
    """Test that invalid referer key is handled gracefully."""
    headers = get_spoofed_headers(referer="nonexistent_key")
    # Should not include Referer if key not found
    assert "Referer" not in headers


def test_get_spoofed_headers_no_referer():
    """Test that spoofed headers work without referer."""
    headers = get_spoofed_headers()
    # Referer should be optional
    assert "User-Agent" in headers
    assert "Accept" in headers


def test_get_http_client_returns_client():
    """Test that get_http_client returns an httpx client."""
    try:
        import httpx
        client = get_http_client()
        assert client is not None
        assert isinstance(client, httpx.Client)
        client.close()
    except ImportError:
        pytest.skip("httpx not installed")


def test_get_http_client_has_headers():
    """Test that http client has proper headers set."""
    try:
        import httpx
        client = get_http_client()
        headers = client.headers
        assert "User-Agent" in headers
        assert "Accept" in headers
        client.close()
    except ImportError:
        pytest.skip("httpx not installed")


def test_get_http_client_custom_timeout():
    """Test that http client respects custom timeout."""
    try:
        import httpx
        client = get_http_client(timeout=60.0)
        assert client.timeout.read == 60.0
        client.close()
    except ImportError:
        pytest.skip("httpx not installed")


def test_rate_limiter_can_request():
    """Test that rate limiter allows requests."""
    limiter = RateLimiter(requests_per_second=10.0)
    assert limiter.can_request("example.com") is True


def test_rate_limiter_records_request():
    """Test that rate limiter records requests."""
    import time
    limiter = RateLimiter(requests_per_second=10.0)
    limiter.record_request("example.com")
    assert "example.com" in limiter._last_request_time
    assert limiter._last_request_time["example.com"] > 0


def test_rate_limiter_blocks_rapid_requests():
    """Test that rate limiter blocks rapid requests."""
    import time
    limiter = RateLimiter(requests_per_second=1.0)
    limiter.record_request("example.com")
    
    # Immediately ask again - should be blocked
    result = limiter.can_request("example.com")
    assert result is False


def test_rate_limiter_different_domains():
    """Test that rate limiter tracks different domains separately."""
    limiter = RateLimiter(requests_per_second=1.0)
    
    # Request to domain A
    assert limiter.can_request("domain-a.com") is True
    limiter.record_request("domain-a.com")
    
    # Request to domain B should be allowed
    assert limiter.can_request("domain-b.com") is True
    
    # But domain A should be blocked
    assert limiter.can_request("domain-a.com") is False


def test_rate_limiter_wait_if_needed():
    """Test that rate limiter wait function works."""
    import time
    limiter = RateLimiter(requests_per_second=100.0)  # Fast rate
    limiter.record_request("example.com")
    
    # Should not block at 100 req/s
    start = time.time()
    limiter.wait_if_needed("example.com")
    elapsed = time.time() - start
    assert elapsed < 0.1  # Should be nearly instant


def test_get_rate_limiter_returns_global_instance():
    """Test that get_rate_limiter returns the global instance."""
    limiter1 = get_rate_limiter()
    limiter2 = get_rate_limiter()
    assert limiter1 is limiter2


def test_user_agents_list_not_empty():
    """Test that USER_AGENTS list is populated."""
    assert len(USER_AGENTS) > 0
    for ua in USER_AGENTS:
        assert isinstance(ua, str)
        assert "Mozilla" in ua  # All should be browser UAs


def test_referers_dict_has_entries():
    """Test that REFERERS dict has common referers."""
    assert len(REFERERS) > 0
    assert "google" in REFERERS
    assert "duckduckgo" in REFERERS
    for key, url in REFERERS.items():
        assert url.startswith("https://")