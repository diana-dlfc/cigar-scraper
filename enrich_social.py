# enrich_social.py
# Enriquecimiento de redes sociales: email, Facebook, Instagram, TikTok.
# Por defecto procesa Florida (FL). Acepta otros estados como argumentos.
#
# Run:
#   venv\Scripts\python enrich_social.py              # Florida
#   venv\Scripts\python enrich_social.py TX            # Texas
#   venv\Scripts\python enrich_social.py FL TX CA      # múltiples estados
#   venv\Scripts\python enrich_social.py FL 50         # primeros 50 de Florida (prueba)

import sys
import asyncio
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

from database.supabase_client import SupabaseClient
from enrichment.social_enricher import enrich_batch_async

# ── Argumentos ───────────────────────────────────────────────────────────────

args   = sys.argv[1:]
states = []
limit  = None
debug  = False

for arg in args:
    if arg == "--debug":
        debug = True
    elif arg.isdigit():
        limit = int(arg)
    else:
        states.append(arg.upper())

if not states:
    states = ["FL"]

if debug:
    import enrichment.social_enricher as _se
    import enrichment.browser_enricher as _be
    _se.DEBUG = True
    _be.DEBUG = True

# ── Cargar lounges con campos faltantes ──────────────────────────────────────

PAGE_SIZE = 1000
db = SupabaseClient()

print(f"\n{'='*55}")
print(f"  Enriquecimiento social — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print(f"  Estados: {', '.join(states)}")
print(f"{'='*55}\n")

print(f"Cargando lounges con campos vacíos...", end="", flush=True)

lounges = []
offset = 0
while True:
    query = (
        db.client.table("cigar_lounges")
        .select("id,name,city,state,address,website,email,facebook_url,instagram_url,tiktok_url,google_maps_url")
        .in_("state", states)
        .or_("email.is.null,facebook_url.is.null,instagram_url.is.null,tiktok_url.is.null")
        .range(offset, offset + PAGE_SIZE - 1)
    )
    res   = query.execute()
    batch = res.data or []
    lounges.extend(batch)
    if len(batch) < PAGE_SIZE:
        break
    offset += PAGE_SIZE

if limit:
    lounges = lounges[:limit]

print(f" {len(lounges)} lounges a procesar.\n")

if not lounges:
    print("Nada que enriquecer.")
    exit(0)

asyncio.run(enrich_batch_async(lounges, db))
