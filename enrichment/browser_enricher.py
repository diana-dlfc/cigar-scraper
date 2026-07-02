# enrichment/browser_enricher.py
"""
Enrichment asíncrono con Playwright + SearXNG/Brave.

Flujo por lounge:
  1. Buscar website en SearXNG → si falla, Brave Search
  2. Visitar website con Playwright
  3. Extraer: email, Instagram, Facebook, TikTok, YouTube, Google Maps
  4. Guardar en Supabase solo los campos que estén vacíos

Uso:
    import asyncio
    from enrichment.browser_enricher import enrich_batch_async
    asyncio.run(enrich_batch_async(lounges, db))
"""

import os
import re
import asyncio
import aiohttp
import aiohttp.helpers
from urllib.parse import quote as url_quote
from loguru import logger
from bs4 import BeautifulSoup
from urllib.parse import urlparse

# ── Configuración ────────────────────────────────────────────────────────────

BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")

SEARXNG_INSTANCES = [
    "https://searx.be",
    "https://search.sapti.me",
    "https://searxng.site",
    "https://searx.tiekoetter.com",
    "https://search.bus-hit.me",
]

CONCURRENCY = 5  # navegadores simultáneos

SKIP_DOMAINS = {
    "yelp.com", "google.com", "facebook.com", "instagram.com", "tiktok.com",
    "twitter.com", "x.com", "youtube.com", "tripadvisor.com", "yellowpages.com",
    "mapquest.com", "foursquare.com", "apple.com", "linkedin.com", "bing.com",
    "wikipedia.org", "duckduckgo.com", "bbb.org", "thumbtack.com", "angieslist.com",
    "nextdoor.com", "groupon.com", "opentable.com",
}

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.I)

SOCIAL_RE = {
    "instagram_url": re.compile(r"instagram\.com/([A-Za-z0-9_.]{2,30})(?:[/?]|$)", re.I),
    "facebook_url":  re.compile(r"facebook\.com/([A-Za-z0-9_.@\-]{2,60})(?:[/?]|$)", re.I),
    "tiktok_url":    re.compile(r"tiktok\.com/@([A-Za-z0-9_.]{2,30})(?:[/?]|$)", re.I),
    "youtube_url":   re.compile(r"youtube\.com/(?:@|channel/|user/)?([A-Za-z0-9_\-]{2,60})(?:[/?]|$)", re.I),
}

GMAPS_RE = re.compile(r"(?:maps\.google\.com|goo\.gl/maps|google\.com/maps)[^\s\"'<>]+", re.I)

SKIP_HANDLES = {
    "sharer", "share", "intent", "dialog", "login", "signup", "home",
    "pages", "groups", "events", "watch", "shorts", "p", "reel", "stories",
    "reels", "hashtag", "explore", "about",
}

JUNK_EMAIL_DOMAINS = {
    "example.com", "sentry.io", "wixpress.com", "squarespace.com",
    "wordpress.com", "godaddy.com", "cloudflare.com", "w3.org", "schema.org",
}


# ── Búsqueda de website ──────────────────────────────────────────────────────

async def _search_searxng(query: str, session: aiohttp.ClientSession) -> list[str]:
    """Busca en SearXNG y retorna lista de URLs. Prueba instancias en orden."""
    for instance in SEARXNG_INSTANCES:
        try:
            async with session.get(
                f"{instance}/search",
                params={"q": query, "format": "json", "categories": "general", "language": "en-US"},
                timeout=aiohttp.ClientTimeout(total=10),
                headers={"User-Agent": "Mozilla/5.0"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    urls = [r.get("url", "") for r in data.get("results", [])[:8]]
                    return [u for u in urls if u]
        except Exception as e:
            logger.debug(f"SearXNG {instance} failed: {e}")
            continue
    return []


async def _search_brave(query: str, session: aiohttp.ClientSession) -> list[str]:
    """Busca en Brave Search API (requiere BRAVE_API_KEY)."""
    if not BRAVE_API_KEY:
        return []
    try:
        async with session.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": 5},
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": BRAVE_API_KEY,
            },
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                return [r.get("url", "") for r in data.get("web", {}).get("results", [])]
    except Exception as e:
        logger.debug(f"Brave search failed: {e}")
    return []


def _pick_website(urls: list[str], name: str) -> str | None:
    """Elige el primer resultado que no sea un directorio conocido."""
    name_words = set(name.lower().split())
    for url in urls:
        try:
            domain = urlparse(url).netloc.lower().replace("www.", "")
        except Exception:
            continue
        if any(skip in domain for skip in SKIP_DOMAINS):
            continue
        return url
    return None


async def _search_bing_playwright(query: str, browser) -> list[str]:
    """Busca en Bing usando Playwright con stealth (evita detección de bot)."""
    from playwright_stealth import Stealth
    page = await browser.new_page()
    await Stealth().apply_stealth_async(page)
    try:
        url = f"https://www.bing.com/search?q={url_quote(query)}"
        await page.goto(url, timeout=20000, wait_until="domcontentloaded")
        await asyncio.sleep(2)

        # Extraer URLs de resultados de búsqueda
        links = await page.eval_on_selector_all(
            "li.b_algo h2 a, .b_algo a[href]",
            "els => els.map(e => e.href)"
        )
        return [l for l in links if l.startswith("http")]
    except Exception as e:
        logger.debug(f"Bing search failed: {e}")
        return []
    finally:
        await page.close()


async def find_website(lounge: dict, session: aiohttp.ClientSession, browser=None) -> str | None:
    """Busca el website oficial del lounge usando Bing (Playwright) → Brave API."""
    name  = lounge.get("name", "")
    city  = lounge.get("city", "")
    state = lounge.get("state", "")
    query = f'"{name}" cigar lounge {city} {state}'

    urls = []
    if browser:
        urls = await _search_bing_playwright(query, browser)
    if not urls:
        urls = await _search_brave(query, session)

    return _pick_website(urls, name)


# ── Scraping de website ──────────────────────────────────────────────────────

def _extract_from_html(html: str, base_url: str = "") -> dict:
    """Extrae email, redes sociales y Google Maps de HTML."""
    soup = BeautifulSoup(html, "lxml")
    result = {
        "email":         None,
        "instagram_url": None,
        "facebook_url":  None,
        "tiktok_url":    None,
        "youtube_url":   None,
        "google_maps_url": None,
    }

    # Recopilar todos los hrefs
    all_hrefs = [a.get("href", "") for a in soup.find_all("a", href=True)]
    full_text = html

    # ── Emails ──────────────────────────────────────────────────────────────
    # Priorizar mailto:
    for href in all_hrefs:
        if href.startswith("mailto:"):
            email = href.replace("mailto:", "").split("?")[0].strip().lower()
            domain = email.split("@")[-1] if "@" in email else ""
            if domain and domain not in JUNK_EMAIL_DOMAINS:
                result["email"] = email
                break

    # Fallback: regex en HTML
    if not result["email"]:
        emails = EMAIL_RE.findall(full_text)
        for e in emails:
            domain = e.split("@")[-1].lower()
            if domain not in JUNK_EMAIL_DOMAINS and "." in domain:
                result["email"] = e.lower()
                break

    # ── Redes sociales ───────────────────────────────────────────────────────
    for href in all_hrefs:
        for field, pattern in SOCIAL_RE.items():
            if result[field]:
                continue
            match = pattern.search(href)
            if match:
                handle = match.group(1).strip("/")
                if handle.lower() not in SKIP_HANDLES and len(handle) > 1:
                    if "instagram" in field:
                        result[field] = f"https://instagram.com/{handle}"
                    elif "facebook" in field:
                        result[field] = f"https://facebook.com/{handle}"
                    elif "tiktok" in field:
                        result[field] = f"https://tiktok.com/@{handle}"
                    elif "youtube" in field:
                        result[field] = f"https://youtube.com/@{handle}"

    # Fallback: regex en texto completo para sociales no encontrados en hrefs
    for field, pattern in SOCIAL_RE.items():
        if result[field]:
            continue
        match = pattern.search(full_text)
        if match:
            handle = match.group(1).strip("/")
            if handle.lower() not in SKIP_HANDLES and len(handle) > 1:
                if "instagram" in field:
                    result[field] = f"https://instagram.com/{handle}"
                elif "facebook" in field:
                    result[field] = f"https://facebook.com/{handle}"
                elif "tiktok" in field:
                    result[field] = f"https://tiktok.com/@{handle}"
                elif "youtube" in field:
                    result[field] = f"https://youtube.com/@{handle}"

    # ── Google Maps ──────────────────────────────────────────────────────────
    for href in all_hrefs:
        if GMAPS_RE.search(href):
            result["google_maps_url"] = href
            break
    if not result["google_maps_url"]:
        match = GMAPS_RE.search(full_text)
        if match:
            result["google_maps_url"] = match.group(0)

    return result


async def scrape_website(url: str, page) -> dict:
    """Visita el website con Playwright (stealth) y extrae datos."""
    from playwright_stealth import Stealth
    await Stealth().apply_stealth_async(page)
    try:
        await page.goto(url, timeout=20000, wait_until="domcontentloaded")
        await asyncio.sleep(1)  # esperar JS básico
        html = await page.content()
        return _extract_from_html(html, base_url=url)
    except Exception as e:
        logger.debug(f"Playwright scrape failed for {url}: {e}")
        return {}


# ── Pipeline principal ───────────────────────────────────────────────────────

async def enrich_one_async(lounge: dict, db, session: aiohttp.ClientSession, browser) -> dict:
    """Enriquece un solo lounge. Retorna dict con campos encontrados."""
    lounge_id = lounge["id"]
    name = lounge.get("name", "?")
    result = {"id": lounge_id, "found": []}

    update = {}

    # ── Paso 1: encontrar website ────────────────────────────────────────────
    website = lounge.get("website")
    if not website:
        website = await find_website(lounge, session, browser=browser)
        if website:
            update["website"] = website
            logger.debug(f"[{name}] website → {website}")

    # ── Paso 2: scraping del website ─────────────────────────────────────────
    if website:
        page = await browser.new_page()
        try:
            scraped = await scrape_website(website, page)
            for field, value in scraped.items():
                if value and not lounge.get(field):
                    update[field] = value
        finally:
            await page.close()

    # ── Paso 3: guardar en Supabase ──────────────────────────────────────────
    update["enriched"] = True
    try:
        db.client.table("cigar_lounges").update(update).eq("id", lounge_id).execute()
        result["found"] = [k for k, v in update.items() if v and k != "enriched"]
    except Exception as e:
        logger.warning(f"DB update failed for {name}: {e}")

    return result


async def enrich_batch_async(lounges: list[dict], db, concurrency: int = CONCURRENCY):
    """
    Enriquece una lista de lounges de forma asíncrona.
    Usa un pool de browsers Playwright y sesiones aiohttp compartidas.
    """
    from playwright.async_api import async_playwright

    total        = len(lounges)
    done         = 0
    with_website = 0
    with_social  = 0
    with_email   = 0

    semaphore = asyncio.Semaphore(concurrency)

    from playwright_stealth import Stealth

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )

        async with aiohttp.ClientSession() as session:

            async def process(lounge):
                nonlocal done, with_website, with_social, with_email
                async with semaphore:
                    result = await enrich_one_async(lounge, db, session, browser)
                    done += 1
                    found = result.get("found", [])
                    if "website"       in found: with_website += 1
                    if "email"         in found: with_email   += 1
                    if any(s in found for s in ("instagram_url", "facebook_url", "tiktok_url", "youtube_url")):
                        with_social += 1
                    if done % 10 == 0 or done == total:
                        print(f"  [{done}/{total}] web:{with_website} social:{with_social} email:{with_email}")
                    return result

            tasks = [process(l) for l in lounges]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        await browser.close()

    return {
        "total":        total,
        "with_website": with_website,
        "with_social":  with_social,
        "with_email":   with_email,
    }
