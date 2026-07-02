# enrichment/social_finder.py
"""
Social media profile finder for cigar lounges.

Detects links to: Instagram, Facebook, Twitter/X, YouTube, TikTok, Yelp, LinkedIn.

Strategy:
  1. Scrape the lounge's website and extract social links from anchors
  2. Look for @handle patterns in page text
  3. Google search fallback (optional)
"""

import re
import time
import requests
from urllib.parse import urlparse
from loguru import logger

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

# Patterns to identify social platform from URL
SOCIAL_PATTERNS = {
    "instagram":  re.compile(r"instagram\.com/([A-Za-z0-9_.]+)", re.I),
    "facebook":   re.compile(r"facebook\.com/([A-Za-z0-9_.@\-]+)", re.I),
    "twitter":    re.compile(r"(?:twitter|x)\.com/([A-Za-z0-9_]+)", re.I),
    "youtube":    re.compile(r"youtube\.com/(?:@|channel/|user/)?([A-Za-z0-9_\-]+)", re.I),
    "tiktok":     re.compile(r"tiktok\.com/@([A-Za-z0-9_.]+)", re.I),
    "yelp":       re.compile(r"yelp\.com/biz/([A-Za-z0-9_\-]+)", re.I),
    "linkedin":   re.compile(r"linkedin\.com/(?:company|in)/([A-Za-z0-9_\-]+)", re.I),
}

# Skip these false-positive paths
SKIP_HANDLES = {
    "sharer", "share", "intent", "dialog", "login", "signup",
    "home", "pages", "groups", "events", "watch", "shorts",
    "p", "reel", "stories",
}


def _extract_socials_from_html(html: str) -> dict:
    socials = {}

    if BS4_AVAILABLE:
        soup = BeautifulSoup(html, "html.parser")
        # Check all <a href> links
        for a in soup.find_all("a", href=True):
            href = a["href"]
            for platform, pattern in SOCIAL_PATTERNS.items():
                if platform in socials:
                    continue
                match = pattern.search(href)
                if match:
                    handle = match.group(1).strip("/")
                    if handle.lower() not in SKIP_HANDLES and len(handle) > 1:
                        domain = _platform_domain(platform)
                        socials[platform] = f"https://{domain}/{handle}"

    # Also scan raw text for any missed links
    for platform, pattern in SOCIAL_PATTERNS.items():
        if platform in socials:
            continue
        match = pattern.search(html)
        if match:
            handle = match.group(1).strip("/")
            if handle.lower() not in SKIP_HANDLES and len(handle) > 1:
                domain = _platform_domain(platform)
                socials[platform] = f"https://{domain}/{handle}"

    return socials


def _platform_domain(platform: str) -> str:
    domains = {
        "instagram": "instagram.com",
        "facebook":  "facebook.com",
        "twitter":   "x.com",
        "youtube":   "youtube.com",
        "tiktok":    "tiktok.com/@",
        "yelp":      "yelp.com/biz",
        "linkedin":  "linkedin.com/company",
    }
    return domains.get(platform, f"{platform}.com")


def _fetch(url: str, timeout: int = 10) -> str | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        logger.debug(f"Fetch failed {url}: {e}")
    return None


def find_socials(lounge: dict) -> dict:
    """
    Main entry point. Returns a dict of found social profiles:
    {
        "instagram": "https://instagram.com/handle",
        "facebook":  "https://facebook.com/page",
        "twitter":   "https://x.com/handle",
        "youtube":   None,
        "tiktok":    None,
        "yelp":      "https://yelp.com/biz/...",
        "linkedin":  None,
    }
    """
    website = lounge.get("website")
    name = lounge.get("name", "")

    result = {p: None for p in SOCIAL_PATTERNS}

    if not website:
        return result

    logger.info(f"[social] Scanning website: {website}")
    html = _fetch(website)
    if html:
        found = _extract_socials_from_html(html)
        result.update(found)

    found_count = sum(1 for v in result.values() if v)
    if found_count:
        logger.info(f"[social] Found {found_count} social profile(s) for '{name}': "
                    f"{[k for k,v in result.items() if v]}")
    else:
        logger.debug(f"[social] No social profiles found for '{name}'")

    return result
