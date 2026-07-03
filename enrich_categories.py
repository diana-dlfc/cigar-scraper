# enrich_categories.py
# Extrae la categoría de Google Maps para cada lounge y la guarda en `category`.
#
# Run:
#   venv\Scripts\python enrich_categories.py        # todos los sin categoría
#   venv\Scripts\python enrich_categories.py 50     # primeros 50 (para probar)

import asyncio
import sys
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

from loguru import logger
from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth

from database.supabase_client import SupabaseClient

LIMIT       = int(sys.argv[1]) if len(sys.argv) > 1 else 999_999
CONCURRENCY = 5
PAGE_SIZE   = 1000

db = SupabaseClient()


# ── Paso 0: Verificar que la columna existe ───────────────────────────────────

def ensure_column():
    """Verifica que la columna 'category' existe en cigar_lounges."""
    try:
        db.client.table("cigar_lounges").select("category").limit(1).execute()
    except Exception as e:
        msg = str(e).lower()
        if "category" in msg or "column" in msg:
            print("\n⚠  La columna 'category' no existe todavía.")
            print("   Ejecuta este SQL en Supabase → SQL Editor y vuelve a correr el script:\n")
            print("   ALTER TABLE cigar_lounges ADD COLUMN category TEXT;\n")
            sys.exit(1)
        raise


# ── Paso 1: Cargar registros pendientes ──────────────────────────────────────

def load_pending() -> list[dict]:
    records = []
    offset  = 0
    while True:
        res = (
            db.client.table("cigar_lounges")
            .select("id,name,city,state,google_maps_url")
            .is_("category", "null")
            .not_.is_("google_maps_url", "null")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        batch = res.data or []
        records.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return records[:LIMIT]


# ── Paso 2: Extraer categoría de la página de Google Maps ────────────────────

async def extract_category(page, url: str) -> str | None:
    """
    Navega a la URL de Google Maps y extrae la categoría que aparece
    justo debajo del nombre del negocio.
    """
    try:
        await page.goto(url, timeout=20000, wait_until="domcontentloaded")
        await page.wait_for_selector("h1", timeout=8000)
    except Exception as e:
        logger.debug(f"Navigation error: {e}")
        return None

    # Selectores en orden de prioridad — Google Maps usa clases ofuscadas
    # que cambian, por eso se intenta con varios.
    selectors = [
        "div.DkEaL",                          # categoría como botón clicable
        "button[jsaction*='category']",
        "div[jsaction*='category']",
        "span.YhemCb",
        "div[class*='skqShb']",
        # Fallback genérico: primer elemento corto justo debajo del h1
        "div[class*='fontBodyMedium'] > div:first-child",
    ]

    for sel in selectors:
        try:
            el   = page.locator(sel).first
            text = (await el.inner_text(timeout=1500)).strip()
            # Validación: categoría válida es texto corto, sin saltos de línea
            if text and len(text) <= 60 and "\n" not in text:
                return text
        except Exception:
            continue

    return None


# ── Worker: procesa un lounge en su propia página ────────────────────────────

async def process_lounge(
    context,
    record: dict,
    semaphore: asyncio.Semaphore,
    stats: dict,
) -> None:
    async with semaphore:
        rid  = record["id"]
        name = record.get("name", "?")
        url  = record["google_maps_url"]

        page = await context.new_page()
        await Stealth().apply_stealth_async(page)

        try:
            category = await extract_category(page, url)

            if category:
                db.client.table("cigar_lounges") \
                    .update({"category": category}) \
                    .eq("id", rid) \
                    .execute()
                stats["ok"] += 1
                logger.debug(f"  ✓ {name} → {category}")
            else:
                stats["no_category"] += 1
                logger.debug(f"  ? {name} → sin categoría")

        except Exception as e:
            stats["errors"] += 1
            stats["failed_urls"].append(url)
            logger.warning(f"  ✗ {name}: {e}")

        finally:
            await page.close()

        # Progreso
        stats["done"] += 1
        done  = stats["done"]
        total = stats["total"]
        pct   = done / total * 100
        remaining = total - done
        print(
            f"  [{done}/{total}] {pct:.1f}% — faltan {remaining}"
            f"  | ok:{stats['ok']} sin_cat:{stats['no_category']} err:{stats['errors']}",
            end="\r",
        )


# ── Main ─────────────────────────────────────────────────────────────────────

async def main():
    ensure_column()

    print("Cargando registros sin categoría con google_maps_url...")
    records = load_pending()
    total   = len(records)

    if not total:
        print("No hay registros pendientes. Todos ya tienen categoría o no tienen google_maps_url.")
        return

    print(f"Registros a procesar: {total}\n")
    print(f"Inicio: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    stats = {
        "total":       total,
        "done":        0,
        "ok":          0,
        "no_category": 0,
        "errors":      0,
        "failed_urls": [],
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
        tasks = [
            process_lounge(context, record, semaphore, stats)
            for record in records
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        await browser.close()

    print()   # salto de línea tras el \r del progreso
    print(f"\nFin: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)
    print(f"  Total procesados     : {total}")
    print(f"  Con categoría        : {stats['ok']}")
    print(f"  Sin categoría (Maps) : {stats['no_category']}")
    print(f"  Errores              : {stats['errors']}")
    print("=" * 55)

    if stats["failed_urls"]:
        print(f"\nURLs con error ({len(stats['failed_urls'])}):")
        for u in stats["failed_urls"]:
            print(f"  {u}")

    print("\nPróximo paso: usar la columna 'category' para limpiar registros incorrectos.")


if __name__ == "__main__":
    asyncio.run(main())
