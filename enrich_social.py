# enrich_social.py
# Enriquecimiento de redes sociales: email, Facebook, Instagram, TikTok.
# Por defecto procesa Florida (FL). Acepta otros estados como argumentos.
#
# Run:
#   venv\Scripts\python enrich_social.py                        # Florida
#   venv\Scripts\python enrich_social.py TX                     # Texas
#   venv\Scripts\python enrich_social.py FL TX CA               # multiples estados
#   venv\Scripts\python enrich_social.py FL 50                  # primeros 50 de FL (prueba)
#   venv\Scripts\python enrich_social.py --slug mi-lounge-slug  # un negocio por slug
#   venv\Scripts\python enrich_social.py --name "My Lounge"     # busqueda parcial por nombre
#   venv\Scripts\python enrich_social.py --slug mi-slug --debug # slug + debug

import csv
import os
import sys
import time
import asyncio
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

from database.supabase_client import SupabaseClient
from enrichment.social_enricher import enrich_batch_async

# == Argumentos ================================================================

args        = sys.argv[1:]
states      = []
limit       = None
debug       = False
filter_slug = None   # --slug <slug>
filter_name = None   # --name "<nombre>"

i = 0
while i < len(args):
    arg = args[i]
    if arg == "--debug":
        debug = True
    elif arg == "--slug" and i + 1 < len(args):
        filter_slug = args[i + 1]
        i += 1
    elif arg == "--name" and i + 1 < len(args):
        filter_name = args[i + 1]
        i += 1
    elif arg.isdigit():
        limit = int(arg)
    elif not arg.startswith("--"):
        states.append(arg.upper())
    i += 1

# Cuando se filtra por slug/name no se necesita estado por defecto
if not states and not filter_slug and not filter_name:
    states = ["FL"]

if debug:
    import enrichment.social_enricher as _se
    import enrichment.browser_enricher as _be
    _se.DEBUG = True
    _be.DEBUG = True

# == Cargar lounges ============================================================

PAGE_SIZE = 1000
db = SupabaseClient()

COLS = (
    "id,name,city,state,address,website,google_maps_url,"
    "email,facebook_url,instagram_url,tiktok_url"
)

print(f"\n{'='*55}")
print(f"  Enriquecimiento social - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
if filter_slug:
    print(f"  Filtro: slug = '{filter_slug}'")
elif filter_name:
    print(f"  Filtro: name ILIKE '%{filter_name}%'")
else:
    print(f"  Estados: {', '.join(states)}")
print(f"{'='*55}\n")

print("Cargando lounges...", end="", flush=True)

lounges = []

if filter_slug:
    # Un solo registro por slug exacto
    res = (
        db.client.table("cigar_lounges")
        .select(COLS)
        .eq("slug", filter_slug)
        .limit(1)
        .execute()
    )
    lounges = res.data or []

elif filter_name:
    # Busqueda parcial por nombre (case-insensitive), max 10 resultados
    res = (
        db.client.table("cigar_lounges")
        .select(COLS)
        .ilike("name", f"%{filter_name}%")
        .limit(10)
        .execute()
    )
    lounges = res.data or []

else:
    # Carga paginada por estado, solo registros con al menos un campo vacio
    offset = 0
    while True:
        query = (
            db.client.table("cigar_lounges")
            .select(COLS)
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

print(f" {len(lounges)} lounge(s) a procesar.\n")

if not lounges:
    print("Nada que enriquecer.")
    if filter_slug:
        print(f"  Hint: el slug '{filter_slug}' no existe en la base de datos.")
    elif filter_name:
        print(f"  Hint: ningun nombre coincide con '{filter_name}'.")
    sys.exit(0)

# == Diagnostico pre-proceso ==================================================
# Para cada lounge cargado, muestra si sera procesado o descartado y por que.
# Esto responde la pregunta: "por que Procesados: 0 aunque cargue N registros?"

from enrichment.social_enricher import _missing_fields as _calc_missing

SEP = "-" * 50
will_process  = []
will_skip     = []

for lnk in lounges:
    name    = lnk.get("name", "?")
    website = lnk.get("website") or None
    missing = _calc_missing(lnk)

    if not missing:
        reason = "already enriched (all tracked fields present)"
        will_skip.append((lnk, reason))
    elif not website:
        will_process.append((lnk, missing, "no website -- solo P2 (buscadores)"))
    else:
        will_process.append((lnk, missing, "OK"))

if will_skip:
    print("=" * 50)
    print(f"  SKIP ({len(will_skip)} registro(s) descartados antes de iniciar)")
    print("=" * 50)
    for lnk, reason in will_skip:
        print(SEP)
        print(f"  Name      : {lnk.get('name', '?')}")
        print(f"  Website   : {lnk.get('website') or '(ninguno)'}")
        print(f"  Email     : {lnk.get('email') or '(vacio)'}")
        print(f"  Facebook  : {lnk.get('facebook_url') or '(vacio)'}")
        print(f"  Instagram : {lnk.get('instagram_url') or '(vacio)'}")
        print(f"  TikTok    : {lnk.get('tiktok_url') or '(vacio)'}")
        print(f"  Reason    : {reason}")
    print(SEP)
    print()

print("=" * 50)
print(f"  QUEUE ({len(will_process)} registro(s) que entran al enriquecimiento)")
print("=" * 50)
for lnk, missing, note in will_process:
    print(f"  {lnk.get('name', '?')}")
    print(f"    Faltan : {sorted(missing)}")
    print(f"    Website: {lnk.get('website') or '(ninguno)'}")
    print(f"    Nota   : {note}")
print()

if not will_process:
    print("Ningun registro requiere enriquecimiento. Abortando.")
    sys.exit(0)

lounges = [lnk for lnk, _, _ in will_process]

# == Auditoria — captura estado ANTES y configura wrapper de tiempo ===========

TRACK_FIELDS = ["email", "facebook_url", "instagram_url", "tiktok_url"]

_before = {lnk["id"]: dict(lnk) for lnk in lounges}
_audit_times: dict = {}

import enrichment.social_enricher as _se

_original_enrich_one = _se.enrich_one


async def _timed_enrich_one(lounge, browser, db_conn, cache):
    t0 = time.time()
    result = await _original_enrich_one(lounge, browser, db_conn, cache)
    _audit_times[lounge["id"]] = time.time() - t0
    return result


_se.enrich_one = _timed_enrich_one

# == Ejecutar ==================================================================

try:
    asyncio.run(enrich_batch_async(lounges, db))
finally:
    _se.enrich_one = _original_enrich_one

# == Recuperar estado DESPUES del enriquecimiento =============================

_AUDIT_COLS = "id,name,state,website,email,facebook_url,instagram_url,tiktok_url"
all_ids   = list(_before.keys())
after_map = {}

_CHUNK = 50
for _i in range(0, len(all_ids), _CHUNK):
    chunk_ids = all_ids[_i : _i + _CHUNK]
    res = (
        db.client.table("cigar_lounges")
        .select(_AUDIT_COLS)
        .in_("id", chunk_ids)
        .execute()
    )
    for row in (res.data or []):
        after_map[row["id"]] = row

# == Construir filas de auditoria =============================================

audit_rows   = []
zero_results = 0

for lounge_id, before in _before.items():
    after   = after_map.get(lounge_id, before)
    name    = before.get("name", "?")
    state   = before.get("state", "?")
    website = after.get("website") or before.get("website") or ""
    t_sec   = _audit_times.get(lounge_id, 0.0)

    newly_found = {
        f: after.get(f)
        for f in TRACK_FIELDS
        if not before.get(f) and after.get(f)
    }

    missing_count = sum(1 for f in TRACK_FIELDS if not before.get(f))
    found_count   = len(newly_found)

    if found_count == 0:
        resultado = "sin datos"
        if website:
            zero_results += 1
    elif missing_count > 0 and found_count >= missing_count:
        resultado = "exito total"
    else:
        resultado = "exito parcial"

    audit_rows.append({
        "nombre":    name,
        "estado":    state,
        "website":   website,
        "email":     newly_found.get("email", ""),
        "facebook":  newly_found.get("facebook_url", ""),
        "instagram": newly_found.get("instagram_url", ""),
        "tiktok":    newly_found.get("tiktok_url", ""),
        "tiempo_s":  f"{t_sec:.1f}",
        "resultado": resultado,
    })

# == Escribir CSV ==============================================================

_csv_name = "enrichment_report_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv"
_csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), _csv_name)

with open(_csv_path, "w", newline="", encoding="utf-8") as _f:
    _writer = csv.DictWriter(_f, fieldnames=[
        "nombre", "estado", "website", "email", "facebook",
        "instagram", "tiktok", "tiempo_s", "resultado",
    ])
    _writer.writeheader()
    _writer.writerows(audit_rows)

# == Resumen extendido de auditoria ===========================================

_total   = len(lounges)
_with_ws = sum(1 for l in lounges if l.get("website"))
_no_ws   = _total - _with_ws

_email_found = sum(1 for r in audit_rows if r["email"])
_fb_found    = sum(1 for r in audit_rows if r["facebook"])
_ig_found    = sum(1 for r in audit_rows if r["instagram"])
_tt_found    = sum(1 for r in audit_rows if r["tiktok"])


def _pct(found, total):
    return f"{found / total * 100:.1f}%" if total else "0.0%"


_ws_times = [
    _audit_times[lid]
    for lid in _before
    if _before[lid].get("website") and lid in _audit_times
]
_avg_ws_time = sum(_ws_times) / len(_ws_times) if _ws_times else 0.0

print("\n" + "=" * 55)
print("  AUDITORIA DEL ENRIQUECIMIENTO")
print("=" * 55)
print(f"  Negocios procesados      : {_total}")
print(f"  Con website              : {_with_ws}")
print(f"  Sin website              : {_no_ws}")
print("=" * 55)
print("  Campo            Encontrados  Cobertura")
print("  " + "-" * 37)
print(f"  Email          : {_email_found:<12}  {_pct(_email_found, _total)}")
print(f"  Facebook       : {_fb_found:<12}  {_pct(_fb_found, _total)}")
print(f"  Instagram      : {_ig_found:<12}  {_pct(_ig_found, _total)}")
print(f"  TikTok         : {_tt_found:<12}  {_pct(_tt_found, _total)}")
print("=" * 55)
print(f"  Tiempo prom./website     : {_avg_ws_time:.1f}s")
print(f"  Websites sin resultados  : {zero_results}")
print("=" * 55)
print(f"  CSV: {_csv_path}")
print()
