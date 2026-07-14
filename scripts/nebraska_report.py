"""
nebraska_report.py — Reporte de cobertura de Nebraska.
Ejecutar desde la raíz del proyecto:
    venv\Scripts\python scripts\nebraska_report.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()
from supabase import create_client

db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

# ── Carga completa de Nebraska ────────────────────────────────────────────────
rows = []
offset = 0
while True:
    batch = (
        db.table("cigar_lounges")
        .select("id,name,enriched,last_enriched_at,email,facebook_url,instagram_url,tiktok_url,website,google_maps_url")
        .eq("state", "Nebraska")
        .range(offset, offset + 999)
        .execute()
        .data or []
    )
    rows.extend(batch)
    if len(batch) < 1000:
        break
    offset += 1000

n = len(rows)
if n == 0:
    print("No se encontraron negocios en Nebraska.")
    sys.exit(0)

# ── Métricas básicas ──────────────────────────────────────────────────────────
enriched       = sum(1 for r in rows if r.get("enriched"))
with_date      = sum(1 for r in rows if r.get("last_enriched_at"))

has_fb         = sum(1 for r in rows if r.get("facebook_url"))
has_ig         = sum(1 for r in rows if r.get("instagram_url"))
has_tt         = sum(1 for r in rows if r.get("tiktok_url"))
has_em         = sum(1 for r in rows if r.get("email"))
has_web        = sum(1 for r in rows if r.get("website"))
has_maps       = sum(1 for r in rows if r.get("google_maps_url"))

# Negocios donde facebook_url sigue vacío (= se intentó buscar pero no se encontró,
# o ni siquiera había website/maps para intentarlo)
missing_fb     = [r for r in rows if not r.get("facebook_url")]
missing_all    = [r for r in rows if not r.get("facebook_url") and not r.get("instagram_url") and not r.get("tiktok_url") and not r.get("email")]
all_social     = sum(1 for r in rows if r.get("facebook_url") and r.get("instagram_url") and r.get("tiktok_url") and r.get("email"))

# Negocios sin ninguna fuente de datos para enriquecer
no_source      = sum(1 for r in rows if not r.get("website") and not r.get("google_maps_url"))
only_web       = sum(1 for r in rows if r.get("website") and not r.get("google_maps_url"))
only_maps      = sum(1 for r in rows if not r.get("website") and r.get("google_maps_url"))
both_sources   = sum(1 for r in rows if r.get("website") and r.get("google_maps_url"))

# ── Reporte ───────────────────────────────────────────────────────────────────
SEP = "=" * 58
print(SEP)
print("  REPORTE NEBRASKA")
print(SEP)
print(f"  Total negocios en DB           : {n}")
print(f"  enriched=true                  : {enriched}  ({100*enriched//n}%)")
print(f"  Con last_enriched_at           : {with_date}  ({100*with_date//n}%)")
print()
print("  COBERTURA DE CAMPOS")
print(f"  {'facebook_url':<22}: {has_fb:>4}/{n}  ({100*has_fb//n}%)")
print(f"  {'instagram_url':<22}: {has_ig:>4}/{n}  ({100*has_ig//n}%)")
print(f"  {'tiktok_url':<22}: {has_tt:>4}/{n}  ({100*has_tt//n}%)")
print(f"  {'email':<22}: {has_em:>4}/{n}  ({100*has_em//n}%)")
print(f"  {'website':<22}: {has_web:>4}/{n}  ({100*has_web//n}%)")
print(f"  {'google_maps_url':<22}: {has_maps:>4}/{n}  ({100*has_maps//n}%)")
print()
print("  DIAGNÓSTICO DE COBERTURA BAJA")
print(f"  Sin ningún campo social        : {len(missing_all)}")
print(f"  Sin facebook específicamente   : {len(missing_fb)}")
print(f"  Con TODOS los campos sociales  : {all_social}")
print()
print("  FUENTES DISPONIBLES POR NEGOCIO")
print(f"  Sin website NI google_maps_url : {no_source}  (sin datos para enriquecer)")
print(f"  Solo website                   : {only_web}")
print(f"  Solo google_maps_url           : {only_maps}")
print(f"  Ambas fuentes                  : {both_sources}")
print()
print("  NOTA: Playwright exceptions, timeouts y caché")
print("  no se almacenan en DB — solo aparecen en logs de Railway.")
print(SEP)

# ── Muestra de negocios sin Facebook con ambas fuentes disponibles ────────────
sample = [
    r for r in rows
    if not r.get("facebook_url")
    and r.get("website")
    and r.get("google_maps_url")
][:10]

if sample:
    print(f"\n  Muestra (hasta 10): sin facebook_url PERO con website+maps")
    print(f"  {'enriched':<8} {'name':<35} {'website'}")
    print(f"  {'-'*7} {'-'*34} {'-'*30}")
    for r in sample:
        enr = "YES" if r.get("enriched") else "NO "
        print(f"  {enr:<8} {(r.get('name') or '?')[:34]:<35} {(r.get('website') or '')[:40]}")
print()
