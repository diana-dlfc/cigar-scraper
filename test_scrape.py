# test_scrape.py
from dotenv import load_dotenv
load_dotenv()

from scrapers.yelp import search_city
from database.supabase_client import SupabaseClient

db = SupabaseClient()

print("Scraping Miami, FL via Yelp...")
results = search_city("Miami", "FL")
print(f"Encontrados: {len(results)}")

saved = skipped = errors = 0
for lounge in results:
    source_id = lounge.pop("_source_id", None)
    lounge.pop("_source", None)
    try:
        row = db.upsert_lounge(lounge)
        if row and source_id:
            db.insert_source({
                "lounge_id": row["id"],
                "source": "yelp",
                "source_id": source_id,
                "source_url": lounge.get("source_url"),
                "raw_data": lounge,
            })
        saved += 1
    except Exception as e:
        errors += 1
        print(f"  ERROR: {e}")

print(f"\nResultado: {saved} guardados, {skipped} skipped, {errors} errores")
print("Verifica en: https://supabase.com/dashboard/project/fimxjqmpbickqbauwlaz/editor")
