# clean_categories.py
# Recorre cada categoría una por una, muestra ejemplos y pregunta si eliminar.
#
# Run:
#   venv\Scripts\python clean_categories.py

from collections import defaultdict
from dotenv import load_dotenv
load_dotenv()

from database.supabase_client import SupabaseClient

PAGE_SIZE = 1000

db = SupabaseClient()

# ── Cargar todos los registros ───────────────────────────────────────────────

print("Cargando registros...", end="", flush=True)

all_lounges = []
offset = 0
while True:
    res = db.client.table("cigar_lounges") \
        .select("id,name,city,state,category") \
        .range(offset, offset + PAGE_SIZE - 1) \
        .execute()
    batch = res.data or []
    all_lounges.extend(batch)
    if len(batch) < PAGE_SIZE:
        break
    offset += PAGE_SIZE

print(f" {len(all_lounges)} registros totales.\n")

# ── Agrupar por categoría ────────────────────────────────────────────────────

null_count = 0
cat_records: dict[str, list] = defaultdict(list)

for r in all_lounges:
    cat = r.get("category")
    if not cat:
        null_count += 1
        continue
    cat_records[cat.strip()].append(r)

sorted_cats = sorted(cat_records.items(), key=lambda x: len(x[1]), reverse=True)

total_cats  = len(sorted_cats)
deleted_per_cat: dict[str, int] = {}
kept = 0

# ── Recorrer categoría por categoría ────────────────────────────────────────

print(f"{'='*65}")
print(f"  {total_cats} categorías encontradas. Escribe s=eliminar / n=conservar.")
print(f"{'='*65}\n")

for idx, (cat, records) in enumerate(sorted_cats, start=1):
    print(f"  [{idx}/{total_cats}]  {cat}  ({len(records)} registros)")
    for ex in records[:5]:
        print(f"    • {ex['name']} — {ex['city']}, {ex['state']}")
    if len(records) > 5:
        print(f"    ... y {len(records) - 5} más")

    while True:
        answer = input("  ¿Eliminar? (s/n): ").strip().lower()
        if answer in ("s", "n"):
            break
        print("  Escribe s o n.")

    if answer == "s":
        ids = [r["id"] for r in records]
        BATCH = 100
        for i in range(0, len(ids), BATCH):
            db.client.table("cigar_lounges").delete().in_("id", ids[i:i + BATCH]).execute()
        deleted_per_cat[cat] = len(ids)
        print(f"  ✓ Eliminados {len(ids)} registros.\n")
    else:
        kept += 1
        print(f"  — Conservada.\n")

# ── Resumen final ────────────────────────────────────────────────────────────

remaining_res = db.client.table("cigar_lounges").select("id", count="exact").execute()
remaining = remaining_res.count if remaining_res.count is not None else "?"

total_deleted = sum(deleted_per_cat.values())

print(f"\n{'='*65}")
print(f"  RESUMEN FINAL")
print(f"{'='*65}")
if deleted_per_cat:
    for cat in sorted(deleted_per_cat, key=lambda c: deleted_per_cat[c], reverse=True):
        print(f"  {deleted_per_cat[cat]:>5}  eliminados  →  {cat}")
    print(f"  ─────")
print(f"  {total_deleted:>5}  total eliminados")
print(f"  {kept:>5}  categorías conservadas")
print(f"  {remaining}  registros restantes en la base de datos")
print(f"{'='*65}\n")
