# audit_categories.py
# Read-only. Muestra todas las categorías en cigar_lounges con estadísticas.
#
# Run:
#   venv\Scripts\python scripts\audit\audit_categories.py

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

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
        .select("name,city,state,category") \
        .range(offset, offset + PAGE_SIZE - 1) \
        .execute()
    batch = res.data or []
    all_lounges.extend(batch)
    if len(batch) < PAGE_SIZE:
        break
    offset += PAGE_SIZE

print(f" {len(all_lounges)} registros totales.")

# ── Agrupar por categoría ────────────────────────────────────────────────────

null_count = 0
cat_counts   = defaultdict(int)
cat_states   = defaultdict(set)
cat_examples = defaultdict(list)

for r in all_lounges:
    cat = r.get("category")
    if not cat:
        null_count += 1
        continue

    cat = cat.strip()
    cat_counts[cat] += 1
    cat_states[cat].add(r.get("state") or "?")
    if len(cat_examples[cat]) < 5:
        cat_examples[cat].append(r)

# ── Ordenar de mayor a menor ─────────────────────────────────────────────────

sorted_cats = sorted(cat_counts.items(), key=lambda x: x[1], reverse=True)

# ── Imprimir resultados ──────────────────────────────────────────────────────

print(f"\n{'='*65}")
print(f"  CATEGORÍAS EN cigar_lounges — {len(sorted_cats)} distintas")
print(f"{'='*65}\n")

for cat, count in sorted_cats:
    states_sorted = sorted(cat_states[cat])
    states_str    = ", ".join(states_sorted)
    print(f"  [{count:>5}]  {cat}")
    print(f"           Estados: {states_str}")
    for ex in cat_examples[cat]:
        print(f"           • {ex['name']} — {ex['city']}, {ex['state']}")
    print()

print(f"{'='*65}")
print(f"  Sin categoría (NULL): {null_count}")
print(f"  Con categoría       : {len(all_lounges) - null_count}")
print(f"  Total               : {len(all_lounges)}")
print(f"{'='*65}\n")
