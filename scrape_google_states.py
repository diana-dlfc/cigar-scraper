# scrape_google_states.py
# Scrape cigar lounges por estado via Google Maps → Supabase
# Úsalo cuando Yelp alcance su límite diario.
#
# Run:
#   venv\Scripts\python scrape_google_states.py                           # todos los estados
#   venv\Scripts\python scrape_google_states.py MT NV NM NC ND OR UT VT WA  # estados faltantes
#   venv\Scripts\python scrape_google_states.py TX                        # un estado

import sys
import time
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

from loguru import logger
from scrapers.google_maps import search_city
from database.supabase_client import SupabaseClient
from config.states import US_STATES
from config.cities.all_states import STATE_CITIES

ALL_STATES    = sorted(STATE_CITIES.keys(), key=lambda k: US_STATES.get(k, {}).get("name", k))
TARGET_STATES = [s.upper() for s in sys.argv[1:]] if len(sys.argv) > 1 else ALL_STATES

db = SupabaseClient()

grand_total_saved  = 0
grand_total_errors = 0
state_summary: dict = {}

print(f"\n{'='*60}")
print(f"  Google Maps Scraper — {len(TARGET_STATES)} estados")
print(f"  Inicio: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"{'='*60}\n")

for i, state_abbr in enumerate(TARGET_STATES, 1):
    state_name = US_STATES.get(state_abbr, {}).get("name", state_abbr)
    cities     = STATE_CITIES.get(state_abbr, [])
    print(f"\n[{i}/{len(TARGET_STATES)}] {state_name} ({state_abbr}) — {len(cities)} ciudades")

    state_saved   = 0
    state_errors  = 0
    seen_source_ids: set[str] = set()

    for city in cities:
        print(f"  {city}...", end=" ", flush=True)
        try:
            results = search_city(city, state_abbr)
            print(f"{len(results)} encontrados", end=" ", flush=True)
        except Exception as e:
            print(f"ERROR: {e}")
            logger.error(f"Failed to scrape {city}, {state_abbr}: {e}")
            state_errors += 1
            continue

        city_saved = 0
        for lounge in results:
            source_id = lounge.pop("_source_id", None)
            lounge.pop("_source", None)

            if source_id and source_id in seen_source_ids:
                continue
            if source_id:
                seen_source_ids.add(source_id)

            try:
                row = db.upsert_lounge(lounge)
                if row and source_id:
                    try:
                        db.insert_source({
                            "lounge_id":  row["id"],
                            "source":     "google_maps",
                            "source_id":  source_id,
                            "source_url": lounge.get("google_maps_url"),
                            "raw_data":   lounge,
                        })
                    except Exception:
                        pass
                city_saved     += 1
                state_saved    += 1
                grand_total_saved += 1
            except Exception as e:
                state_errors      += 1
                grand_total_errors += 1
                logger.warning(f"Upsert error ({city}, {state_abbr}): {e}")

        print(f"→ {city_saved} guardados")
        time.sleep(2)

    state_summary[state_abbr] = {"saved": state_saved, "errors": state_errors}
    print(f"  ✓ {state_name}: {state_saved} guardados, {state_errors} errores")
    time.sleep(3)

# ── Reporte final ──────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"  COMPLETADO: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"  Estados procesados: {len(TARGET_STATES)}")
print(f"  Total guardados:    {grand_total_saved}")
print(f"  Total errores:      {grand_total_errors}")
print(f"{'='*60}")

print("\nTop estados por lounges encontrados:")
for abbr, info in sorted(state_summary.items(), key=lambda x: -x[1]["saved"]):
    if info["saved"] > 0:
        name = US_STATES.get(abbr, {}).get("name", abbr)
        print(f"  {name} ({abbr}): {info['saved']}")

total_db = db.client.table("cigar_lounges").select("id", count="exact").execute().count
print(f"\nTotal en Supabase: {total_db}")
print("\nPróximo paso: venv\\Scripts\\python test_sheets.py")
