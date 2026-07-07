# enrichment/browser_enricher.py
#
# Crawler inteligente del website oficial.
# Visita hasta MAX_PAGES páginas internas (contact, about, etc.)
# extrayendo email, Facebook, Instagram y TikTok desde:
#   - enlaces <a href> y mailto:
#   - texto visible y scripts
#   - JSON-LD (application/ld+json)
#   - meta tags
# Se detiene en cuanto encuentra todos los campos. Nunca sobresescribe.

import re
import json
import asyncio
from urllib.parse import urlparse, urljoin
from loguru import logger
from bs4 import BeautifulSoup

CONCURRENCY = 5
MAX_PAGES   = 5    # máximo de páginas internas a visitar por sitio

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
}

GMAPS_RE = re.compile(r"(?:maps\.google\.com|goo\.gl/maps|google\.com/maps)[^\s\"'<>]+", re.I)

SKIP_HANDLES = {
    "sharer", "share", "intent", "dialog", "login", "signup", "home",
    "pages", "groups", "events", "p", "reel", "stories", "reels",
    "hashtag", "explore", "about", "watch", "shorts", "create",
}

JUNK_EMAIL_DOMAINS = {
    "example.com", "sentry.io", "wixpress.com", "squarespace.com",
    "wordpress.com", "godaddy.com", "cloudflare.com", "w3.org", "schema.org",
    "google.com", "gmail.com",
}

# Palabras clave para priorizar páginas internas
PRIORITY_KEYWORDS = [
    "contact", "contacto", "about", "acerca", "connect",
    "reach", "privacy", "info", "social", "footer",
]

# Activar desde social_enricher.py o cualquier runner con --debug
DEBUG = False


def _log_debug(msg: str):
    if DEBUG:
        print(f"  [crawler] {msg}")


# ── Extracción completa de una página ────────────────────────────────────────

def _build_social_url(field: str, handle: str) -> str | None:
    if "instagram" in field:
        return f"https://instagram.com/{handle}"
    if "facebook" in field:
        return f"https://facebook.com/{handle}"
    if "tiktok" in field:
        return f"https://tiktok.com/@{handle}"
    return None


def _extract_from_html(html: str, base_url: str = "") -> dict:
    """
    Extracción completa desde una sola página.
    Fuentes: hrefs, mailto, texto, scripts, JSON-LD, meta tags.
    """
    soup = BeautifulSoup(html, "lxml")
    result = {
        "email":           None,
        "instagram_url":   None,
        "facebook_url":    None,
        "tiktok_url":      None,
        "google_maps_url": None,
    }

    all_hrefs = [a.get("href", "") for a in soup.find_all("a", href=True)]

    # ── 1. JSON-LD ────────────────────────────────────────────────────────────
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            raw = script.string or ""
            data = json.loads(raw)
            items = data if isinstance(data, list) else [data]
            for item in items:
                # email
                if not result["email"]:
                    email_raw = item.get("email", "")
                    if email_raw and "@" in email_raw:
                        domain = email_raw.split("@")[-1].lower()
                        if domain not in JUNK_EMAIL_DOMAINS:
                            result["email"] = email_raw.lower().strip()

                # sameAs → redes sociales
                for same_as in item.get("sameAs", []):
                    for field, pattern in SOCIAL_RE.items():
                        if result[field]:
                            continue
                        match = pattern.search(same_as)
                        if match:
                            handle = match.group(1).strip("/")
                            if handle.lower() not in SKIP_HANDLES and len(handle) > 1:
                                result[field] = _build_social_url(field, handle)

                # url del negocio (no social) → ignorar aquí, ya tenemos website
        except Exception:
            pass

    # ── 2. Meta tags ─────────────────────────────────────────────────────────
    for meta in soup.find_all("meta"):
        prop    = (meta.get("property") or meta.get("name") or "").lower()
        content = (meta.get("content") or "").strip()
        if not content:
            continue

        # Email en meta
        if not result["email"] and "email" in prop and "@" in content:
            domain = content.split("@")[-1].lower()
            if domain not in JUNK_EMAIL_DOMAINS:
                result["email"] = content.lower()

        # Redes sociales en meta (og:see_also, profile:username, etc.)
        for field, pattern in SOCIAL_RE.items():
            if result[field]:
                continue
            match = pattern.search(content)
            if match:
                handle = match.group(1).strip("/")
                if handle.lower() not in SKIP_HANDLES and len(handle) > 1:
                    result[field] = _build_social_url(field, handle)

    # ── 3. mailto: en hrefs ───────────────────────────────────────────────────
    if not result["email"]:
        for href in all_hrefs:
            if href.startswith("mailto:"):
                email = href.replace("mailto:", "").split("?")[0].strip().lower()
                domain = email.split("@")[-1] if "@" in email else ""
                if domain and domain not in JUNK_EMAIL_DOMAINS:
                    result["email"] = email
                    break

    # ── 4. Redes sociales en hrefs ────────────────────────────────────────────
    for href in all_hrefs:
        for field, pattern in SOCIAL_RE.items():
            if result[field]:
                continue
            match = pattern.search(href)
            if match:
                handle = match.group(1).strip("/")
                if handle.lower() not in SKIP_HANDLES and len(handle) > 1:
                    result[field] = _build_social_url(field, handle)

    # ── 5. Google Maps en hrefs ───────────────────────────────────────────────
    if not result["google_maps_url"]:
        for href in all_hrefs:
            if GMAPS_RE.search(href):
                result["google_maps_url"] = href
                break

    # ── 6. Regex en texto completo (scripts incluidos) ────────────────────────
    full_text = html

    if not result["email"]:
        for e in EMAIL_RE.findall(full_text):
            domain = e.split("@")[-1].lower()
            if domain not in JUNK_EMAIL_DOMAINS and "." in domain:
                result["email"] = e.lower()
                break

    for field, pattern in SOCIAL_RE.items():
        if result[field]:
            continue
        match = pattern.search(full_text)
        if match:
            handle = match.group(1).strip("/")
            if handle.lower() not in SKIP_HANDLES and len(handle) > 1:
                result[field] = _build_social_url(field, handle)

    if not result["google_maps_url"]:
        match = GMAPS_RE.search(full_text)
        if match:
            result["google_maps_url"] = match.group(0)

    return result


# ── Descubrimiento de páginas internas ───────────────────────────────────────

def _find_priority_links(html: str, base_url: str) -> list[str]:
    """
    Encuentra enlaces internos relevantes (contact, about, etc.).
    Devuelve URLs absolutas del mismo dominio, priorizadas por keyword.
    """
    soup        = BeautifulSoup(html, "lxml")
    base_domain = urlparse(base_url).netloc.lower()
    base_clean  = base_url.rstrip("/")
    seen        = {base_clean}
    priority    = []
    others      = []

    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        text = a.get_text(strip=True).lower()

        try:
            abs_url = urljoin(base_url, href)
            parsed  = urlparse(abs_url)
        except Exception:
            continue

        # Solo mismo dominio, http/https
        if parsed.netloc.lower() != base_domain:
            continue
        if parsed.scheme not in ("http", "https"):
            continue

        # Quitar fragmento, normalizar
        clean = abs_url.split("#")[0].rstrip("/")
        if clean in seen:
            continue
        seen.add(clean)

        path_lower = parsed.path.lower()
        is_priority = any(
            kw in path_lower or kw in text
            for kw in PRIORITY_KEYWORDS
        )

        if is_priority:
            priority.append(clean)
        else:
            others.append(clean)

    # Prioritarios primero, luego el resto
    return (priority + others)[:MAX_PAGES]


# ── Cookie banners y Age Gates ───────────────────────────────────────────────

# Textos de botones de cookies (case-insensitive, coincidencia parcial)
_COOKIE_TEXTS = [
    "accept all cookies", "accept all", "accept cookies",
    "allow all cookies", "allow all", "allow cookies",
    "i agree", "agree to all", "agree",
    "got it", "okay", "ok",
    "consent", "accept",
]

# Textos de botones de age gate (más específico — se evalúa después)
_AGE_GATE_TEXTS = [
    "i'm 21 or older", "i am 21 or older",
    "i'm 21+", "i am 21+",
    "i am of legal smoking age", "i am of legal age",
    "i am old enough",
    "yes, i am", "yes i am",
    "yes, enter", "enter site",
    "verify age",
    "i am an adult",
    "enter",
    "yes",
]


async def _try_click_text(page, texts: list[str]) -> str | None:
    """
    Intenta hacer click en el primer elemento visible que contenga
    alguno de los textos dados. Devuelve el texto clickeado o None.
    """
    selector = (
        "button, a[href], [role='button'], "
        "input[type='submit'], input[type='button'], label"
    )
    for text in texts:
        try:
            loc = page.locator(selector).filter(
                has_text=re.compile(rf"\b{re.escape(text)}\b", re.I)
            )
            if await loc.count() > 0:
                el = loc.first
                # Verificar que esté visible
                if await el.is_visible():
                    await el.click(timeout=3000)
                    return text
        except Exception:
            pass
    return None


async def _try_dob_form(page) -> bool:
    """
    Detecta y completa formularios de fecha de nacimiento (age gate).
    Soporta: input[type=date], tres <select> month/day/year,
    y tres <input type=text> para mes/día/año.
    Devuelve True si encontró y completó el formulario.
    """
    try:
        # Caso 1: un solo input[type="date"]
        date_inp = page.locator("input[type='date']")
        if await date_inp.count() > 0 and await date_inp.first.is_visible():
            await date_inp.first.fill("1980-01-01")
            _log_debug("DOB input[type=date] completado con 01/01/1980")
            submit = page.locator(
                "button[type='submit'], input[type='submit'], "
                "button:has-text('Enter'), button:has-text('Continue'), "
                "button:has-text('Submit'), button:has-text('Verify')"
            )
            if await submit.count() > 0:
                await submit.first.click(timeout=3000)
                return True

        # Caso 2: tres <select> month/day/year
        month_sel = page.locator(
            "select[name*='month' i], select[id*='month' i], "
            "select[name*='Month'], select[class*='month' i]"
        )
        year_sel  = page.locator(
            "select[name*='year' i], select[id*='year' i], "
            "select[name*='Year'], select[class*='year' i]"
        )
        day_sel   = page.locator(
            "select[name*='day' i], select[id*='day' i], "
            "select[name*='Day'], select[class*='day' i]"
        )

        if await month_sel.count() > 0 and await year_sel.count() > 0:
            await month_sel.first.select_option(value="1")
            if await day_sel.count() > 0:
                await day_sel.first.select_option(value="1")
            # Intentar valor "1980" o el más cercano disponible
            for yr in ("1980", "1981", "1979", "1975"):
                try:
                    await year_sel.first.select_option(value=yr)
                    break
                except Exception:
                    pass
            _log_debug("Formulario DOB (selects) completado con 01/01/1980")
            submit = page.locator(
                "button[type='submit'], input[type='submit'], "
                "button:has-text('Enter'), button:has-text('Continue'), "
                "button:has-text('Submit'), button:has-text('Verify')"
            )
            if await submit.count() > 0:
                await submit.first.click(timeout=3000)
                return True

        # Caso 3: inputs de texto para mes/día/año (formato MM/DD/YYYY)
        text_inputs = page.locator(
            "input[placeholder*='MM'], input[placeholder*='month' i], "
            "input[name*='month' i], input[id*='month' i]"
        )
        if await text_inputs.count() > 0 and await text_inputs.first.is_visible():
            await text_inputs.first.fill("01")
            day_inp = page.locator(
                "input[placeholder*='DD'], input[name*='day' i], input[id*='day' i]"
            )
            yr_inp  = page.locator(
                "input[placeholder*='YYYY'], input[placeholder*='year' i], "
                "input[name*='year' i], input[id*='year' i]"
            )
            if await day_inp.count() > 0:
                await day_inp.first.fill("01")
            if await yr_inp.count() > 0:
                await yr_inp.first.fill("1980")
            _log_debug("Formulario DOB (text inputs) completado con 01/01/1980")
            submit = page.locator(
                "button[type='submit'], input[type='submit'], "
                "button:has-text('Enter'), button:has-text('Continue')"
            )
            if await submit.count() > 0:
                await submit.first.click(timeout=3000)
                return True

    except Exception as e:
        logger.debug(f"DOB form error: {e}")

    return False


async def _bypass_overlays(page) -> None:
    """
    Detecta y supera automáticamente:
    - Banners de cookies (Accept, I Agree, etc.)
    - Age Gates (Yes, Enter, I'm 21+, formulario DOB)
    Debe llamarse justo después de cargar la página.
    """
    # Dar tiempo a que aparezcan los overlays
    await asyncio.sleep(0.8)

    # ── 1. Cookie banner ──────────────────────────────────────────────────────
    clicked = await _try_click_text(page, _COOKIE_TEXTS)
    if clicked:
        _log_debug(f"Cookies aceptadas ('{clicked}')")
        await asyncio.sleep(0.4)

    # ── 2. Age Gate — botones ─────────────────────────────────────────────────
    age_clicked = await _try_click_text(page, _AGE_GATE_TEXTS)
    if age_clicked:
        _log_debug(f"Age Gate superado (botón '{age_clicked}')")
        await asyncio.sleep(0.4)
        return

    # ── 3. Age Gate — formulario DOB ─────────────────────────────────────────
    dob_done = await _try_dob_form(page)
    if dob_done:
        _log_debug("Age Gate superado (formulario DOB)")
        await asyncio.sleep(0.4)


# ── Crawler principal ────────────────────────────────────────────────────────

async def scrape_website(url: str, page, missing: set[str] | None = None) -> dict:
    """
    Crawler multi-página del website oficial.

    Visita la página principal y hasta MAX_PAGES páginas internas
    (priorizando contact, about, etc.). Se detiene en cuanto encuentra
    todos los campos requeridos.

    Args:
        url:     URL del website.
        page:    Playwright page ya abierta.
        missing: Campos que todavía hacen falta. Si es None busca todos.

    Returns:
        dict con los campos encontrados (solo los que tienen valor).
    """
    from playwright_stealth import Stealth

    ALL_FIELDS = {"email", "facebook_url", "instagram_url", "tiktok_url", "google_maps_url"}
    if missing is None:
        missing = ALL_FIELDS

    await Stealth().apply_stealth_async(page)

    result:  dict      = {}
    visited: set[str]  = set()

    async def _visit(visit_url: str, is_homepage: bool = False) -> str | None:
        """Visita una URL, extrae datos y devuelve el HTML."""
        if visit_url in visited:
            return None
        visited.add(visit_url)

        remaining = missing - {k for k, v in result.items() if v}
        if not remaining:
            return None  # ya tenemos todo, no seguir

        try:
            await page.goto(visit_url, timeout=20000, wait_until="domcontentloaded")
            # En la homepage superar overlays antes de extraer
            if is_homepage:
                _log_debug(f"Comenzando crawler en: {visit_url}")
                await _bypass_overlays(page)
                # Recargar el HTML tras superar overlays (pueden haber cambiado el DOM)
                await asyncio.sleep(0.3)
            else:
                await asyncio.sleep(0.5)
            html = await page.content()
        except Exception as e:
            logger.debug(f"Crawler: falló {visit_url}: {e}")
            return None

        found = _extract_from_html(html, base_url=visit_url)
        for field, value in found.items():
            if field in remaining and value and not result.get(field):
                result[field] = value
                logger.debug(f"Crawler: [{field}] encontrado en {visit_url}")

        return html

    # ── Página principal ──────────────────────────────────────────────────────
    homepage_html = await _visit(url, is_homepage=True)

    # ── Páginas internas prioritarias ────────────────────────────────────────
    if homepage_html:
        still_missing = missing - {k for k, v in result.items() if v}
        if still_missing:
            priority_links = _find_priority_links(homepage_html, url)
            logger.debug(f"Crawler: páginas internas encontradas: {priority_links}")

            for link in priority_links:
                still_missing = missing - {k for k, v in result.items() if v}
                if not still_missing:
                    break
                await _visit(link)

    pages_visited = len(visited)
    logger.debug(f"Crawler: {pages_visited} página(s) visitadas en {url} → {[k for k,v in result.items() if v]}")

    return {k: v for k, v in result.items() if v}
