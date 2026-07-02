# enrichment/email_finder.py
"""
Email finder for cigar lounges.

Strategy (in order of priority):
  1. Scrape the lounge's own website for email addresses
  2. Hunter.io API (optional — requires HUNTER_API_KEY)
  3. Google search for "site:<domain> email" pattern

Returns a list of found emails ranked by confidence.
"""

import re
import time
import requests
from urllib.parse import urlparse, urljoin
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

import os

HUNTER_API_KEY = os.getenv("HUNTER_API_KEY")
HUNTER_URL = "https://api.hunter.io/v2/domain-search"

EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)

# Pages most likely to contain contact emails
CONTACT_PATHS = [
    "/contact",
    "/contact-us",
    "/contacto",
    "/about",
    "/about-us",
    "/info",
    "/reach-us",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

JUNK_DOMAINS = {
    "example.com", "sentry.io", "wixpress.com", "squarespace.com",
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "wordpress.com", "godaddy.com", "cloudflare.com",
    "w3.org", "schema.org",
}


def _extract_emails_from_text(text: str) -> list[str]:
    found = EMAIL_RE.findall(text)
    cleaned = []
    for e in found:
        e = e.lower().strip(".").strip(",")
        domain = e.split("@")[-1]
        if domain not in JUNK_DOMAINS and "." in domain:
            if e not in cleaned:
                cleaned.append(e)
    return cleaned


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(min=1, max=4),
    retry=retry_if_exception_type(requests.RequestException),
    reraise=False,
)
def _fetch(url: str, timeout: int = 10) -> str | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        logger.debug(f"Fetch failed {url}: {e}")
    return None


def scrape_website_emails(website: str) -> list[str]:
    if not website or not BS4_AVAILABLE:
        return []

    emails = []
    parsed = urlparse(website)
    base = f"{parsed.scheme}://{parsed.netloc}"

    # Fetch homepage first
    pages_to_check = [website] + [urljoin(base, p) for p in CONTACT_PATHS]

    visited = set()
    for url in pages_to_check:
        if url in visited:
            continue
        visited.add(url)

        html = _fetch(url)
        if not html:
            continue

        # Extract from raw HTML (catches mailto: obfuscation too)
        found = _extract_emails_from_text(html)
        for e in found:
            if e not in emails:
                emails.append(e)

        # Also check anchor href=mailto:
        try:
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.startswith("mailto:"):
                    email = href.replace("mailto:", "").split("?")[0].strip().lower()
                    if email and email not in emails:
                        domain = email.split("@")[-1] if "@" in email else ""
                        if domain not in JUNK_DOMAINS:
                            emails.append(email)
        except Exception:
            pass

        if emails:
            break
        time.sleep(0.5)

    return emails[:5]


def hunter_domain_search(domain: str) -> list[str]:
    if not HUNTER_API_KEY:
        return []
    try:
        resp = requests.get(
            HUNTER_URL,
            params={"domain": domain, "api_key": HUNTER_API_KEY, "limit": 5},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            return [e["value"] for e in data.get("emails", []) if e.get("value")]
    except Exception as e:
        logger.warning(f"Hunter.io search failed for {domain}: {e}")
    return []


def find_emails(lounge: dict) -> dict:
    """
    Main entry point. Accepts a lounge dict and returns enrichment data:
    {
        "email": "best@email.com",          # primary email
        "emails_all": ["a@x.com", ...],     # all found
        "email_source": "website" | "hunter"
    }
    """
    website = lounge.get("website")
    name = lounge.get("name", "")
    emails = []
    source = None

    # 1. Scrape website
    if website:
        logger.info(f"[email] Scraping website: {website}")
        website_emails = scrape_website_emails(website)
        if website_emails:
            emails.extend(website_emails)
            source = "website"

    # 2. Hunter.io fallback
    if not emails and website:
        domain = urlparse(website).netloc.replace("www.", "")
        if domain:
            logger.info(f"[email] Trying Hunter.io for domain: {domain}")
            hunter_emails = hunter_domain_search(domain)
            if hunter_emails:
                emails.extend(hunter_emails)
                source = "hunter"

    if emails:
        logger.info(f"[email] Found {len(emails)} email(s) for '{name}': {emails}")
    else:
        logger.debug(f"[email] No emails found for '{name}'")

    return {
        "email": emails[0] if emails else None,
        "emails_all": emails,
        "email_source": source,
    }
