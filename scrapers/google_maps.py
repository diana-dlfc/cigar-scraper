# scrapers/google_maps.py
"""
Google Maps scraper via Playwright — fuente alternativa cuando Yelp alcanza su límite.

Interfaz idéntica a scrapers/yelp.py:
  search_city(city, state) → list[dict]  (compatible con db.upsert_lounge)

No modifica ningún otro archivo del proyecto.
"""

import asyncio
import hashlib
import re
from urllib.parse import quote
from loguru import logger

from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth

from utils.helpers import make_slug, normalize_phone, normalize_url, now_utc, safe_float, safe_int
from utils.validators import is_cigar_venue, sanitize_lounge_data

MAPS_SEARCH  = "https://www.google.com/maps/search/{query}"
MAX_SCROLLS  = 6
SCROLL_PAUSE = 1200   # ms entre scrolls
MAX_VISITS   = 25     # máximo de lugares a visitar por ciudad
CONCURRENCY  = 4      # páginas en paralelo (workers)

# Palabras que indican un lugar relacionado con cigarros/tabaco
_CIGAR_HINTS = {
    "cigar", "tobacco", "smoke", "smoker", "pipe",
    "humidor", "tobacconist", "stogie", "lounge",
}

# Categorías que descartan el resultado sin visitar la página
_EXCLUDE_CATS = {
    "casino", "bar", "restaurant", "hotel", "brewery",
    "liquor store", "nightclub", "sports bar", "karaoke",
    "grocery", "supermarket", "pharmacy", "gas station",
}

# Estados de EE.UU. para validar direcciones
US_STATE_CODES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN",
    "IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV",
    "NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN",
    "TX","UT","VT","VA","WA","WV","WI","WY","DC"
}


def _is_worth_visiting(card_name: str, card_category: str) -> bool:
    """
    Pre-filtro sobre los datos visibles en la lista (sin abrir la página).
    Devuelve True si vale la pena visitar el lugar; False lo descarta.
    """
    name_low = card_name.lower()
    cat_low  = card_category.lower()
    combined = f"{name_low} {cat_low}"

    for bad in _EXCLUDE_CATS:
        if bad in cat_low:
            if not any(h in combined for h in _CIGAR_HINTS):
                return False

    if not is_cigar_venue(card_name, description=card_category):
        if not any(h in combined for h in _CIGAR_HINTS):
            return False

    return True


def _is_us_address(address: str, expected_state: str) -> bool:
    """
    Verifica que la dirección pertenezca realmente al estado buscado.
    Evita resultados como Nicaragua, Canadá, etc.
    """
    if not address:
        return False

    addr = address.upper()

    # Debe contener el estado esperado
    if f", {expected_state}" not in addr and f" {expected_state} " not in addr:
        return False

    # Debe contener algún indicador de Estados Unidos
    if not (
        "UNITED STATES" in addr or
        "ESTADOS UNIDOS" in addr or
        " USA" in addr
    ):
        return False

    return True


# ── Scroll del feed ──────────────────────────────────────────────────────────

async def _scroll_feed(page) -> None:
    prev = 0
    for _ in range(MAX_SCROLLS):
        count = await page.locator('div[role="feed"] > div').count()
        if count == prev:
            break
        prev = count
        await page.evaluate(
            "document.querySelector('div[role=\"feed\"]')?.scrollBy(0, 2000)"
        )
        await page.wait_for_timeout(SCROLL_PAUSE)


# ── Leer tarjetas del feed sin abrir cada lugar ──────────────────────────────

async def _read_feed_cards(page) -> list[tuple[str, str, str]]:
    """
    Lee nombre, categoría y href de cada tarjeta del feed.
    Devuelve lista de (name, category, href) — sin navegar a ninguna página.
    """
    cards: list[tuple[str, str, str]] = []
    seen:  set[str] = set()

    items = await page.locator('div[role="feed"] > div').all()
    for item in items:
        try:
            link = item.locator('a[href*="/maps/place/"]').first
            href = await link.get_attribute("href", timeout=1000) or ""
            if not href:
                continue
            if href.startswith("/"):
                href = "https://www.google.com" + href
            if href in seen:
                continue
            seen.add(href)

            name = ""
            for name_sel in [
                '[class*="fontHeadlineSmall"]',
                '[class*="qBF1Pd"]',
                'div[class*="NrDZNb"] span',
            ]:
                try:
                    name = (await item.locator(name_sel).first.inner_text(timeout=800)).strip()
                    if name:
                        break
                except Exception:
                    pass
            if not name:
                name = (await link.get_attribute("aria-label") or "").strip()

            category = ""
            for cat_sel in [
                '[class*="W4Efsd"] > span:first-child',
                '[class*="W4Efsd"] span[class*="Io6YTe"]',
                'div[class*="W4Efsd"]:first-of-type span',
            ]:
                try:
                    category = (await item.locator(cat_sel).first.inner_text(timeout=800)).strip()
                    if category:
                        break
                except Exception:
                    pass

            cards.append((name, category, href))

        except Exception:
            continue

    return cards


# ── Extracción de datos de un lugar ─────────────────────────────────────────

async def _extract_place(page, city: str, state: str) -> dict | None:
    """Extrae los datos del lugar actualmente abierto en la página de Google Maps."""

    name = ""
    for sel in ["h1.DUwDvf", "h1[class*='fontHeadlineLarge']", "h1"]:
        try:
            name = (await page.locator(sel).first.inner_text(timeout=4000)).strip()
            if name:
                break
        except Exception:
            continue

    if not name:
        return None

    if not is_cigar_venue(name, description="cigar lounge tobacco smoke"):
        return None

    address = ""
    for sel in [
        'button[data-item-id="address"]',
        '[data-tooltip="Copy address"]',
        'button[aria-label*="ddress"]',
    ]:
        
        
        try:
            address = (await page.locator(sel).first.inner_text(timeout=2000)).strip()
            if address:
                break
        except Exception:
            continue

    # -----------------------------
    # Validar país y estado
    # -----------------------------
    if not _is_us_address(address, state):
        logger.debug(f"Descartado por dirección: {name} -> {address}")
        return None

    phone = ""
    for sel in [
        'button[data-item-id^="phone"]',
        'button[aria-label*="hone"]',
    ]:
        try:
            phone = (await page.locator(sel).first.inner_text(timeout=2000)).strip()
            if phone:
                break
        except Exception:
            continue

    website = None
    for sel in [
        'a[data-item-id="authority"]',
        'a[aria-label*="website"]',
        'a[aria-label*="sitio"]',
    ]:
        try:
            website = await page.locator(sel).first.get_attribute("href", timeout=2000)
            if website:
                break
        except Exception:
            continue

    rating = None
    for sel in ['div.F7nice span[aria-hidden="true"]', 'span.ceNzKf[aria-hidden="true"]']:
        try:
            txt = (await page.locator(sel).first.inner_text(timeout=1500)).strip()
            rating = safe_float(txt.replace(",", "."))
            if rating:
                break
        except Exception:
            continue

    review_count = None
    for sel in [
        'span[aria-label*="review"]', 'button[aria-label*="review"]',
        'span[aria-label*="reseña"]', 'button[aria-label*="reseña"]',
    ]:
        try:
            label = await page.locator(sel).first.get_attribute("aria-label", timeout=1500)
            if label:
                nums = re.findall(r"[\d,]+", label)
                if nums:
                    review_count = safe_int(nums[0].replace(",", ""))
                    break
        except Exception:
            continue

    slug = make_slug(name, city, state)

    return sanitize_lounge_data({
        "name":            name,
        "slug":            slug,
        "description":     None,
        "website":         normalize_url(website) if website else None,
        "phone":           normalize_phone(phone),
        "address":         address,
        "city":            city,
        "state":           state,
        "country":         "US",
        "latitude":        None,
        "longitude":       None,
        "rating":          rating,
        "review_count":    review_count,
        "price_level":     None,
        "google_maps_url": None,
        "source_url":      None,
        "last_scraped_at": now_utc(),
        "enriched":        False,
    })


# ── Worker: visita un lugar en su propia página ──────────────────────────────

async def _visit_place(
    context,
    href: str,
    city: str,
    state: str,
    semaphore: asyncio.Semaphore,
) -> dict | None:
    """Abre una página nueva del contexto existente, extrae datos y la cierra.

    No hace deduplicación interna: con varios workers corriendo en paralelo,
    dos tareas pueden leer seen_ids antes de que cualquiera escriba en él.
    La deduplicación se hace después de asyncio.gather() por _source_id.
    """
    async with semaphore:
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)
        try:
            await page.goto(href, timeout=20000, wait_until="domcontentloaded")
            await page.wait_for_selector("h1", timeout=6000)

            maps_url = page.url
            data = await _extract_place(page, city, state)
            if data:
                data["google_maps_url"] = maps_url
                data["_source_id"] = hashlib.md5(
                    f"{data.get('name','')}{data.get('address','')}".encode()
                ).hexdigest()
                data["_source"] = "google_maps"
                logger.debug(f"  ✓ {data.get('name')}")
                return data

        except Exception as e:
            logger.debug(f"  Error en lugar ({href[:60]}): {e}")
        finally:
            await page.close()

    return None


# ── Scraper principal (async) ────────────────────────────────────────────────

async def _scrape_city_async(city: str, state: str) -> list[dict]:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )

        # Página de búsqueda (se mantiene durante todo el proceso)
        search_page = await context.new_page()
        await Stealth().apply_stealth_async(search_page)

        query      = f"cigar lounge {city} {state}"
        search_url = MAPS_SEARCH.format(query=quote(query))
        logger.info(f"Google Maps → {query}")

        try:
            await search_page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
        except Exception as e:
            logger.warning(f"Navigation error {city}, {state}: {e}")
            await browser.close()
            return []

        # Consentimiento de cookies
        for sel in [
            'button[aria-label*="Accept"]',
            'button[aria-label*="Aceptar"]',
            'form button:last-child',
        ]:
            try:
                await search_page.click(sel, timeout=2000)
                break
            except Exception:
                pass

        # Esperar feed o manejar página directa de negocio
        try:
            await search_page.wait_for_selector('div[role="feed"]', timeout=20000)
        except PWTimeout:
            logger.warning(f"No feed for {city}, {state} — trying single-place fallback")
            results = []
            try:
                await search_page.wait_for_selector("h1", timeout=5000)
                data = await _extract_place(search_page, city, state)
                if data:
                    data["google_maps_url"] = search_page.url
                    data["_source_id"] = hashlib.md5(
                        f"{data.get('name','')}{data.get('address','')}".encode()
                    ).hexdigest()
                    data["_source"] = "google_maps"
                    results.append(data)
                    logger.info(f"  Fallback: 1 lugar extraído en {city}, {state}")
            except Exception as fe:
                logger.debug(f"  Fallback failed: {fe}")
            await browser.close()
            return results

        # Scroll para cargar todos los resultados
        await _scroll_feed(search_page)

        # Pre-filtro desde la lista (sin abrir páginas)
        cards = await _read_feed_cards(search_page)
        logger.info(f"  {len(cards)} tarjetas en feed para {city}, {state}")

        to_visit: list[str] = []
        skipped = 0
        for card_name, card_cat, href in cards:
            if _is_worth_visiting(card_name, card_cat):
                to_visit.append(href)
            else:
                skipped += 1
                logger.debug(f"  ✗ Saltando '{card_name}' [{card_cat}]")

        to_visit = to_visit[:MAX_VISITS]   # límite por ciudad

        if skipped:
            logger.info(
                f"  Pre-filtro: {skipped} descartados, {len(to_visit)} a visitar "
                f"(límite={MAX_VISITS})"
            )

        # Visitar en paralelo con CONCURRENCY workers, reutilizando el mismo browser
        semaphore = asyncio.Semaphore(CONCURRENCY)

        tasks = [
            _visit_place(context, href, city, state, semaphore)
            for href in to_visit
        ]
        place_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Deduplicar por _source_id (hash nombre+dirección) después del gather.
        # Esto cubre el caso donde dos URLs distintas resuelven al mismo negocio.
        seen_source_ids: set[str] = set()
        results: list[dict] = []
        for r in place_results:
            if not isinstance(r, dict):
                continue
            sid = r.get("_source_id", "")
            if sid in seen_source_ids:
                logger.debug(f"  Duplicado post-gather descartado: {r.get('name')}")
                continue
            seen_source_ids.add(sid)
            results.append(r)

        await browser.close()

    logger.info(f"Google Maps: {len(results)} venues en {city}, {state}")
    return results


# ── Interfaz pública (síncrona) ──────────────────────────────────────────────

def search_city(city: str, state: str) -> list[dict]:
    """
    Busca cigar lounges en Google Maps para una ciudad.
    Misma interfaz que scrapers/yelp.search_city() — compatible con db.upsert_lounge().
    """
    return asyncio.run(_scrape_city_async(city, state))