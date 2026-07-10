# enrich_all.py
# Enrichment: Google Places API → website + maps | Playwright → email + social
#
# Run:
#   venv\Scripts\python enrich_all.py        # todos los lounges incompletos
#   venv\Scripts\python enrich_all.py 20     # primeros 20 (para probar)

import sys
import asyncio
import time
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

from loguru import logger
from database.supabase_client import SupabaseClient
from enrichment.google_places_finder import enrich_with_google_places
from enrichment.browser_enricher import scrape_website

LIMIT       = int(sys.argv[1]) if len(sys.argv) > 1 else 99999
CONCURRENCY = 5

db = SupabaseClient()


# ── Paso 1: Google Places (síncrono — la API no tiene async SDK) ─────────────

def fill_from_google_places(lounges: list[dict]) -> int:
    """Para cada lounge sin website, busca en Google Places."""
    updated = 0
    total   = len(lounges)

    for i, lounge in enumerate(lounges):
        if lounge.get("website") and lounge.get("google_maps_url"):
            continue  # ya tiene todo, saltar

        name = lounge.get("name", "?")
        data = enrich_with_google_places(lounge)

        if data:
            try:
                db.client.table("cigar_lounges").update(data).eq("id", lounge["id"]).execute()
                lounge.update(data)
                updated += 1
            except Exception as e:
                logger.warning(f"DB update failed for {name}: {e}")

        if (i + 1) % 50 == 0:
            print(f"  Google Places: {i+1}/{total} procesados ({updated} con datos)")

        time.sleep(0.1)  # ~10 req/seg para no saturar la API

    print(f"  Google Places completo: {updated}/{total} con website o maps")
    return updated


# ── Paso 2: Playwright scraping (async) ──────────────────────────────────────

async def scrape_all_websites(lounges: list[dict]) -> dict:
    """Visita el website de cada lounge y extrae email + redes sociales."""
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    lounges_with_web = [l for l in lounges if l.get("website")]
    total = len(lounges_with_web)

    if not total:
        print("  Ningún lounge con website — omitiendo scraping.")
        return {"with_email": 0, "with_social": 0}

    print(f"  Scraping {total} websites con Playwright...")

    with_email  = 0
    with_social = 0
    done        = 0
    semaphore   = asyncio.Semaphore(CONCURRENCY)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )

        async def process(lounge):
            nonlocal done, with_email, with_social
            async with semaphore:
                page = await browser.new_page()
                await Stealth().apply_stealth_async(page)
                try:
                    scraped = await scrape_website(lounge["website"], page)
                except Exception as e:
                    logger.debug(f"Scrape error {lounge.get('name')}: {e}")
                    scraped = {}
                finally:
                    await page.close()

                update = {
                    k: v for k, v in {
                        "email":         scraped.get("email"),
                        "instagram_url": scraped.get("instagram_url"),
                        "facebook_url":  scraped.get("facebook_url"),
                        "tiktok_url":    scraped.get("tiktok_url"),
                    }.items()
                    if v and not lounge.get(k)
                }
                update["enriched"] = True

                if len(update) > 1:
                    try:
                        db.client.table("cigar_lounges").update(update).eq("id", lounge["id"]).execute()
                    except Exception as e:
                        logger.warning(f"DB update failed: {e}")

                found = [k for k, v in update.items() if v and k != "enriched"]
                done += 1
                if "email" in found: with_email += 1
                if any(s in found for s in ("instagram_url", "facebook_url", "tiktok_url")):
                    with_social += 1

                if done % 10 == 0 or done == total:
                    print(f"  [{done}/{total}] email:{with_email} | social:{with_social}")

        tasks = [process(l) for l in lounges_with_web]
        await asyncio.gather(*tasks, return_exceptions=True)
        await browser.close()

    return {"with_email": with_email, "with_social": with_social}


# ── Main ─────────────────────────────────────────────────────────────────────

async def main():
    print(f"\n{'='*60}")
    print(f"  Enrichment — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    resp = db.client.table("cigar_lounges") \
        .select("id,name,city,state,address,website,email,instagram_url,facebook_url,google_maps_url,enriched") \
        .or_("website.is.null,email.is.null,instagram_url.is.null,google_maps_url.is.null") \
        .limit(LIMIT) \
        .execute()

    lounges = resp.data or []
    print(f"Lounges a procesar: {len(lounges)}\n")

    if not lounges:
        print("Nada que enriquecer.")
        return

    print("Paso 1: Google Places API → website + Google Maps URL")
    fill_from_google_places(lounges)

    print("\nPaso 2: Playwright → email + redes sociales")
    stats = await scrape_all_websites(lounges)

    ids = [l["id"] for l in lounges]
    for i in range(0, len(ids), 500):
        db.client.table("cigar_lounges") \
            .update({"enriched": True}) \
            .in_("id", ids[i:i+500]) \
            .execute()

    print(f"\n{'='*60}")
    print(f"  COMPLETADO: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Con email  : {stats['with_email']}")
    print(f"  Con social : {stats['with_social']}")
    print(f"{'='*60}")
    print("\nPróximo paso: venv\\Scripts\\python test_sheets.py")


if __name__ == "__main__":
    asyncio.run(main())
