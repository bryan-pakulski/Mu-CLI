"""
Anti-detection utilities for web scraping and API requests.

This module provides utilities for:
- Rotating user-agent strings
- Spoofed HTTP headers
- Referer header management
- HTTP client factory with anti-detection settings
- Rate limiting awareness
"""

import random
from typing import Optional

# Common browser user-agent strings for rotation
USER_AGENTS = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    # Chrome on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    # Firefox on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
    # Safari on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    # Chrome on Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    # Edge on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
]

# Common referer patterns for different sources
REFERERS = {
    "google": "https://www.google.com/",
    "bing": "https://www.bing.com/",
    "duckduckgo": "https://duckduckgo.com/",
    "reddit": "https://www.reddit.com/",
    "stackoverflow": "https://stackoverflow.com/",
    "hackernews": "https://news.ycombinator.com/",
    "arxiv": "https://arxiv.org/",
    "github": "https://github.com/",
    "twitter": "https://twitter.com/",
    "generic": "https://www.google.com/",
}


def get_random_user_agent() -> str:
    """
    Returns a random user-agent string from the pool of common browsers.
    
    Returns:
        A user-agent string that mimics a real browser.
    """
    return random.choice(USER_AGENTS)


def get_spoofed_headers(referer: Optional[str] = None) -> dict:
    """
    Generates HTTP headers that mimic a real browser request.
    
    Args:
        referer: Optional referer URL to include. Can be a URL string or
                 a key from REFERERS dict (e.g., 'google', 'duckduckgo').
    
    Returns:
        Dictionary of HTTP headers with spoofed values.
    """
    user_agent = get_random_user_agent()
    
    # Resolve referer
    referer_url = None
    if referer:
        if referer in REFERERS:
            referer_url = REFERERS[referer]
        elif referer.startswith("http"):
            referer_url = referer
    
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none" if not referer_url else "cross-site",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }
    
    if referer_url:
        headers["Referer"] = referer_url
    
    return headers


def get_http_client(timeout: float = 30.0, follow_redirects: bool = True):
    """
    Creates an httpx client with anti-detection settings.
    
    Args:
        timeout: Request timeout in seconds.
        follow_redirects: Whether to follow HTTP redirects.
    
    Returns:
        Configured httpx.Client instance.
    
    Note:
        This function requires httpx to be installed.
        The client uses rotating user-agents and proper headers.
    """
    try:
        import httpx
        
        headers = get_spoofed_headers()
        
        client = httpx.Client(
            headers=headers,
            timeout=timeout,
            follow_redirects=follow_redirects,
            # Simulate browser behavior
            verify=True,
            http2=False,  # HTTP/2 requires h2 package; keep False for compatibility
        )
        
        return client
    except ImportError:
        raise ImportError(
            "httpx is required for get_http_client(). "
            "Install it with: pip install httpx"
        )


class RateLimiter:
    """
    A simple rate limiter to track request frequencies and prevent
    overwhelming target servers.
    """
    
    def __init__(self, requests_per_second: float = 2.0):
        """
        Initialize rate limiter.
        
        Args:
            requests_per_second: Maximum requests per second allowed.
        """
        self.min_interval = 1.0 / requests_per_second
        self._last_request_time: dict[str, float] = {}
    
    def can_request(self, domain: str) -> bool:
        """
        Check if a request can be made to the given domain.
        
        Args:
            domain: The domain to check.
        
        Returns:
            True if the request can be made, False if rate limited.
        """
        import time
        
        now = time.time()
        last_time = self._last_request_time.get(domain, 0)
        
        if now - last_time >= self.min_interval:
            return True
        return False
    
    def record_request(self, domain: str) -> None:
        """
        Record that a request was made to the given domain.
        
        Args:
            domain: The domain that was requested.
        """
        import time
        self._last_request_time[domain] = time.time()
    
    def wait_if_needed(self, domain: str) -> None:
        """
        Wait if necessary before making a request to the domain.
        
        Args:
            domain: The domain to request.
        """
        import time
        
        now = time.time()
        last_time = self._last_request_time.get(domain, 0)
        elapsed = now - last_time
        
        if elapsed < self.min_interval:
            wait_time = self.min_interval - elapsed
            time.sleep(wait_time)


# Global rate limiter instance
_global_rate_limiter = RateLimiter(requests_per_second=2.0)


def get_rate_limiter() -> RateLimiter:
    """
    Get the global rate limiter instance.
    
    Returns:
        The global RateLimiter instance.
    """
    return _global_rate_limiter