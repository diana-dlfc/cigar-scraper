# enrichment/duckduckgo_finder.py
"""
Busca websites y redes sociales de cigar lounges via DuckDuckGo HTML search.
No requiere API key.
"""

import re
import time
import random
import requests
from loguru import logger

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

DDG_URL = "https://html.duckduckgo.com/html/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Dominios a ignorar en resultados (no son el sitio del negocio)
SKIP_DOMAINS = {
    "yelp.com", "google.com", "facebook.com", "instagram.com",
    "tiktok.com", "twitter.com", "x.com", "youtube.com",
    "tripadvisor.com", "yellowpages.com", "mapquest.com",
    "foursquare.com", "bingmaps.com", "apple.com", "linkedin.com",
    "duckduckgo.com", "wikipedia.org", "bing.com",
}

SOCIAL_PATTERNS = {
    "instagram": re.compile(r"instagram\.com/([A-Za-z0-9_.]+)", re.I),
    "facebook":  re.compile(r"facebook\.com/([A-Za-z0-9_.@\-]+)", re.I),
    "tiktok":    re.compile(r"tiktok\.com/@([A-Za-z0-9_.]+)", re.I),
}

SKIP_HANDLES = {
    "sharer", "share", "intent", "dialog", "login", "signup",
    "home", "pages", "groups", "events", "watch", "shorts",
    "p", "reel", "stories", "reels",
}


def _ddg_search(query: str, retries: int = 2) -> list[str]:
    """Busca en DuckDuckGo y retorna lista de URLs de resultados."""
    for attempt in range(retries):
        try:
            resp = requests.post(
                DDG_URL,
                data={"q": query, "b": "", "kl": "us-en"},
                headers=HEADERS,
                timeout=15,
                allow_redirects=True,
            )
            if resp.status_code != 200:
                logger.debug(f"DDG returned {resp.status_code} for: {query}")
                time.sleep(2)
                continue

            if not BS4_AVAILABLE:
                # Fallback: regex sobre el HTML
                urls = re.findall(r'href="(https?://[^"]+)"', resp.text)
                return [u for u in urls if "duckduckgo" not in u]

            soup = BeautifulSoup(resp.text, "html.parser")
            urls = []
            for a in soup.select("a.result__a, a.result__url"):
                href = a.get("href", "")
                if href.startswith("http"):
                    urls.append(href)
            return urls

        except Exception as e:
            logger.debug(f"DDG search error (attempt {attempt+1}): {e}")
            time.sleep(3)

    return []


def _extract_social_from_urls(urls: list[str]) -> dict:
    """Extrae links de redes sociales de una lista de URLs."""
    found = {}
    for url in urls:
        for platform, pattern in SOCIAL_PATTERNS.items():
            if platform in found:
                continue
            match = pattern.search(url)
            if match:
                handle = match.group(1).strip("/")
                if handle.lower() not in SKIP_HANDLES and len(handle) > 1:
                    if platform == "tiktok":
                        found[platform] = f"https://tiktok.com/@{handle}"
                    elif platform == "instagram":
                        found[platform] = f"https://instagram.com/{handle}"
                    elif platform == "facebook":
                        found[platform] = f"https://facebook.com/{handle}"
    return found


def _extract_website_from_urls(urls: list[str]) -> str | None:
    """Retorna el primer URL que no sea un directorio/red social conocido."""
    for url in urls:
        domain = re.sub(r"https?://(www\.)?", "", url).split("/")[0].lower()
        if not any(skip in domain for skip in SKIP_DOMAINS):
            return url
    return None


def find_by_duckduckgo(lounge: dict) -> dict:
    """
    Busca website, Instagram, Facebook y TikTok para un lounge usando DDG.
    Retorna dict con los campos encontrados.
    """
    name  = lounge.get("name", "")
    city  = lounge.get("city", "")
    state = lounge.get("state", "")
    location = f"{city} {state}".strip()

    result = {
        "website":      None,
        "instagram_url": None,
        "facebook_url":  None,
        "tiktok_url":    None,
    }

    # ── Búsqueda 1: website oficial ─────────────────────────────────────────
    query_web = f'"{name}" cigar lounge {location}'
    urls_web  = _ddg_search(query_web)
    website   = _extract_website_from_urls(urls_web)
    if website:
        result["website"] = website

    # También buscar sociales en estos resultados
    socials = _extract_social_from_urls(urls_web)
    result.update({
        "instagram_url": socials.get("instagram"),
        "facebook_url":  socials.get("facebook"),
        "tiktok_url":    socials.get("tiktok"),
    })

    # ── Búsqueda 2: Instagram específico (si no se encontró) ─────────────────
    if not result["instagram_url"]:
        time.sleep(random.uniform(1.0, 2.0))
        query_ig = f'"{name}" {location} site:instagram.com'
        urls_ig  = _ddg_search(query_ig)
        socials2 = _extract_social_from_urls(urls_ig)
        if socials2.get("instagram"):
            result["instagram_url"] = socials2["instagram"]

    found = [k for k, v in result.items() if v]
    if found:
        logger.debug(f"[DDG] '{name}' → {found}")

    return result
