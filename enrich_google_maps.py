# enrich_google_maps.py
# Enriquece registros EXISTENTES con datos de Google Maps.
# Nunca crea registros nuevos — solo actualiza campos vacíos.
#
# Run:
#   venv\Scripts\python enrich_google_maps.py TX
#   venv\Scripts\python enrich_google_maps.py TX FL NY

import sys
import asyncio
from urllib.parse import quote
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

from loguru import logger
from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth

from database.supabase_client import SupabaseClient
from config.states import US_STATES
from utils.helpers import normalize_phone, normalize_url

MAPS_SEARCH = "https://www.google.com/maps/search/{query}"
CONCURRENCY = 4
PAGE_SIZE   = 1000

db = SupabaseClient()

if sys.argv[1:]:
    TARGET_STATES = [s.upper() for s in sys.argv[1:]]
else:
    # Sin argumentos → todos los estados en orden alfabético por nombre
    TARGET_STATES = sorted(US_STATES.keys(), key=lambda k: US_STATES[k]["name"])


# ── Carga de registros ────────────────────────────────────────────────────────

def load_records(state: str) -> list[dict]:
    """Registros del estado que faltan google_maps_url o category."""
    records = []
    offset  = 0
    while True:
        res = (
            db.client.table("cigar_lounges")
            .select("id,name,city,state,website,phone,google_maps_url,category")
            .eq("state", state)
            .or_("google_maps_url.is.null,category.is.null")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        batch = res.data or []
        records.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return records


# ── Extracción desde la página de un lugar ───────────────────────────────────

async def _get_category(page) -> str | None:
    for sel in [
        "div.DkEaL",
        "button[jsaction*='category']",
        "div[jsaction*='category']",
        "span.YhemCb",
        "div[class*='skqShb']",
        "div[class*='fontBodyMedium'] > div:first-child",
    ]:
        try:
            text = (await page.locator(sel).first.inner_text(timeout=1500)).strip()
            if text and len(text) <= 60 and "\n" not in text:
                return text
        except Exception:
            continue
    return None


async def _get_website(page) -> str | None:
    for sel in [
        'a[data-item-id="authority"]',
        'a[aria-label*="website"]',
        'a[aria-label*="sitio"]',
    ]:
        try:
            href = await page.locator(sel).first.get_attribute("href", timeout=2000)
            if href:
                return normalize_url(href)
        except Exception:
            continue
    return None


async def _get_phone(page) -> str | None:
    for sel in [
        'button[data-item-id^="phone"]',
        'button[aria-label*="hone"]',
    ]:
        try:
            text = (await page.locator(sel).first.inner_text(timeout=2000)).strip()
            if text:
                return normalize_phone(text)
        except Exception:
            continue
    return None


# ── Navegación a la página del negocio ───────────────────────────────────────

async def _navigate_to_place(page, name: str, city: str, state: str) -> bool:
    query = f"{name} {city} {state}"
    url   = MAPS_SEARCH.format(query=quote(query))

    try:
        await page.goto(url, timeout=25000, wait_until="domcontentloaded")
    except Exception as e:
        logger.debug(f"Navigation error for {name}: {e}")
        return False

    for sel in ['button[aria-label*="Accept"]', 'button[aria-label*="Aceptar"]']:
        try:
            await page.click(sel, timeout=2000)
            break
        except Exception:
            pass

    # Caso 1: lista de resultados
    try:
        await page.wait_for_selector('div[role="feed"]', timeout=8000)
        first_link = page.locator('div[role="feed"] a[href*="/maps/place/"]').first
        href = await first_link.get_attribute("href", timeout=3000)
        if not href:
            return False
        if href.startswith("/"):
            href = "https://www.google.com" + href
        await page.goto(href, timeout=20000, wait_until="domcontentloaded")
        await page.wait_for_selector("h1", timeout=6000)
        return True
    except PWTimeout:
        pass

    # Caso 2: redirigió directo al lugar
    try:
        await page.wait_for_selector("h1", timeout=5000)
        return True
    except PWTimeout:
        return False


# ── Worker ────────────────────────────────────────────────────────────────────

async def _enrich_record(
    context,
    record:    dict,
    semaphore: asyncio.Semaphore,
    stats:     dict,
) -> None:
    async with semaphore:
        rid   = record["id"]
        name  = record.get("name",  "")
        city  = record.get("city",  "")
        state = record.get("state", "")

        page = await context.new_page()
        await Stealth().apply_stealth_async(page)

        try:
            found = await _navigate_to_place(page, name, city, state)

            if not found:
                stats["not_found"] += 1
            else:
                maps_url = page.url
                category = await _get_category(page)
                website  = await _get_website(page)
                phone    = await _get_phone(page)

                update: dict = {}

                if not record.get("google_maps_url"):
                    update["google_maps_url"] = maps_url
                    stats["added_url"] += 1

                if category and not record.get("category"):
                    update["category"] = category
                    stats["added_category"] += 1

                if website and not record.get("website"):
                    update["website"] = website
                    stats["added_website"] += 1

                if phone and not record.get("phone"):
                    update["phone"] = phone
                    stats["added_phone"] += 1

                if update:
                    db.client.table("cigar_lounges").update(update).eq("id", rid).execute()
                    stats["updated"] += 1
                    logger.debug(f"  ✓ {name} → {list(update.keys())}")
                else:
                    stats["no_change"] += 1

        except Exception as e:
            stats["errors"] += 1
            logger.warning(f"  ✗ {name} ({city}, {state}): {e}")
        finally:
            await page.close()

        stats["done"] += 1
        done  = stats["done"]
        total = stats["total"]
        pct   = done / total * 100
        print(
            f"  [{done}/{total}] {pct:.1f}%"
            f" | url:{stats['added_url']}"
            f" web:{stats['added_website']}"
            f" tel:{stats['added_phone']}"
            f" cat:{stats['added_category']}"
            f" err:{stats['errors']}",
            end="\r",
        )


# ── Main ─────────────────────────────────────────────────────────────────────

async def main():
    all_records: list[dict] = []

    for state in TARGET_STATES:
        if state not in US_STATES:
            print(f"⚠  Estado inválido ignorado: '{state}'")
            continue
        records = load_records(state)
        print(f"  {US_STATES[state]['name']} ({state}): {len(records)} registros")
        all_records.extend(records)

    if not all_records:
        print("Nada que enriquecer.")
        return

    total = len(all_records)
    print(f"\nTotal: {total} registros")
    print(f"Inicio: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    stats = {
        "total":          total,
        "done":           0,
        "updated":        0,
        "not_found":      0,
        "no_change":      0,
        "errors":         0,
        "added_url":      0,
        "added_website":  0,
        "added_phone":    0,
        "added_category": 0,
    }

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )

        semaphore = asyncio.Semaphore(CONCURRENCY)
        tasks     = [_enrich_record(context, r, semaphore, stats) for r in all_records]
        await asyncio.gather(*tasks, return_exceptions=True)

        await browser.close()

    print()
    print(f"\nFin: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)
    print(f"  Procesados                : {total}")
    print(f"  Registros actualizados    : {stats['updated']}")
    print(f"  No encontrados en Maps    : {stats['not_found']}")
    print(f"  Sin cambios (ya completos): {stats['no_change']}")
    print(f"  Errores                   : {stats['errors']}")
    print(f"  {'─'*43}")
    print(f"  google_maps_url agregados : {stats['added_url']}")
    print(f"  website agregados         : {stats['added_website']}")
    print(f"  teléfonos agregados       : {stats['added_phone']}")
    print(f"  categorías agregadas      : {stats['added_category']}")
    print("=" * 55)


if __name__ == "__main__":
    asyncio.run(main())
