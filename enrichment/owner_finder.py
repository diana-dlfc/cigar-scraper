# enrichment/owner_finder.py
import re
import time
import requests
from loguru import logger
from urllib.parse import quote_plus

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
    "Accept-Language": "en-US,en;q=0.9",
}

# Ownership keywords — includes verb forms like "founded", "opened"
OWNERSHIP_KEYWORDS = re.compile(
    r"owner|found(?:er|ers|ed)|co-founder|proprietor|manager|operator"
    r"|president|ceo|principal|opened\s+by|started\s+by|established\s+by"
    r"|run\s+by|operated\s+by",
    re.IGNORECASE,
)

# Proper name: 2–4 title-case words (no IGNORECASE so [A-Z] = uppercase only)
PROPER_NAME_RE = re.compile(r"\b([A-Z][a-z]{1,20}(?:\s[A-Z][a-z]{1,20}){1,3})\b")

# Words that look like names but are not people
SKIP_WORDS = {
    "owner", "founder", "founded", "manager", "operator", "president",
    "ceo", "principal", "united", "states", "new", "los", "las", "san",
    "about", "contact", "read", "more", "view", "all", "learn", "sign",
    "log", "click", "privacy", "rights", "reserved", "terms",
    # Business type words that are not person names
    "cigar", "cigars", "lounge", "bar", "club", "shop", "store",
    "tobacco", "smoke", "premium", "elite", "luxury", "fine", "best",
    "house", "room", "place", "center", "group", "company", "inc",
}

SKIP_NAMES = {
    "United States", "New York", "Los Angeles", "Las Vegas",
    "About Us", "Contact Us", "Our Team", "Read More",
    "All Rights", "Rights Reserved", "Privacy Policy",
}


def _clean_name(name: str) -> str | None:
    if not name:
        return None
    name = name.strip()
    words = name.split()
    if len(words) < 2 or len(words) > 4:
        return None
    if name in SKIP_NAMES:
        return None
    if any(w.lower() in SKIP_WORDS for w in words):
        return None
    if any(char.isdigit() for char in name):
        return None
    return name


def _extract_owner_from_text(text: str) -> str | None:
    """
    Search for a proper name near an ownership keyword.
    Looks BEFORE the keyword first (John Smith, owner)
    then AFTER it (Owner John Smith / Founded by Maria Lopez).
    """
    for kw_match in OWNERSHIP_KEYWORDS.finditer(text):
        kw_start = kw_match.start()
        kw_end   = kw_match.end()

        # 1. Look BEFORE keyword: "John Smith, owner of..."
        before = text[max(0, kw_start - 80) : kw_start]
        for m in PROPER_NAME_RE.finditer(before):
            cleaned = _clean_name(m.group(1))
            if cleaned:
                return cleaned

        # 2. Look AFTER keyword: "owner John Smith" / "founded by Maria Lopez"
        after = text[kw_end : min(len(text), kw_end + 80)]
        for m in PROPER_NAME_RE.finditer(after):
            cleaned = _clean_name(m.group(1))
            if cleaned:
                return cleaned

    return None


def _fetch(url: str, timeout: int = 12) -> str | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        logger.debug(f"Fetch failed {url}: {e}")
    return None


def scrape_website_owner(website: str) -> tuple[str | None, str]:
    if not website:
        return None, ""

    pages = [
        website,
        website.rstrip("/") + "/about",
        website.rstrip("/") + "/about-us",
        website.rstrip("/") + "/our-story",
        website.rstrip("/") + "/team",
    ]

    for url in pages:
        html = _fetch(url)
        if not html:
            continue

        text = html
        if BS4_AVAILABLE:
            try:
                soup = BeautifulSoup(html, "html.parser")
                for tag in soup(["script", "style", "nav", "header"]):
                    tag.decompose()
                text = soup.get_text(separator=" ")
            except Exception:
                pass

        owner = _extract_owner_from_text(text)
        if owner:
            return owner, url

        time.sleep(0.3)

    return None, ""


def google_search_owner(name: str, city: str, state: str) -> str | None:
    query = f'"{name}" owner OR founder "{city}" {state} cigar'
    url = f"https://www.google.com/search?q={quote_plus(query)}&num=5&hl=en"
    html = _fetch(url)
    if not html:
        return None
    if BS4_AVAILABLE:
        try:
            soup = BeautifulSoup(html, "html.parser")
            for div in soup.find_all("div", class_=re.compile(r"BNeawe|VwiC3b|s3v9rd")):
                owner = _extract_owner_from_text(div.get_text())
                if owner:
                    return owner
        except Exception:
            pass
    return _extract_owner_from_text(html)


def find_owner(lounge: dict) -> dict:
    name    = lounge.get("name", "")
    website = lounge.get("website")
    city    = lounge.get("city", "")
    state   = lounge.get("state", "")

    if website:
        logger.info(f"[owner] Scanning website for '{name}'")
        owner, source_url = scrape_website_owner(website)
        if owner:
            logger.info(f"[owner] Found '{owner}' on {source_url}")
            return {"owner_name": owner, "owner_source": "website"}

    if name and city:
        logger.info(f"[owner] Trying Google search for '{name}' in {city}, {state}")
        time.sleep(1.5)
        owner = google_search_owner(name, city, state)
        if owner:
            logger.info(f"[owner] Found '{owner}' via Google")
            return {"owner_name": owner, "owner_source": "google"}

    logger.debug(f"[owner] No owner found for '{name}'")
    return {"owner_name": None, "owner_source": None}
