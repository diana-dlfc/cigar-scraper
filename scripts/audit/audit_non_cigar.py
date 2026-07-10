# audit_non_cigar.py
# Auditoría de registros sospechosos en cigar_lounges.
# NO elimina ningún registro — solo genera un reporte.
#
# Run: venv\Scripts\python scripts\audit\audit_non_cigar.py

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from collections import defaultdict
from dotenv import load_dotenv
load_dotenv()

from database.supabase_client import SupabaseClient

db = SupabaseClient()

# ── Configuración ─────────────────────────────────────────────────────────────

# Si el nombre contiene alguna de estas palabras → es cigar-related → ignorar
CIGAR_SAFE = {
    "cigar", "tobacco", "smoker", "humidor", "pipe", "stogie",
    "tobacconist", "smoke shop", "tabaco", "habano", "habanos",
}

# Categorías sospechosas y sus palabras clave (se buscan en el nombre)
SUSPICIOUS_CATEGORIES: dict[str, list[str]] = {
    "Restaurant / Food":   ["restaurant", "bistro", "diner", "grill", "kitchen",
                            "cafe", "cafeteria", "eatery", "pizza", "burger",
                            "taco", "sushi", "steakhouse", "bbq", "barbecue",
                            "seafood", "wings", "noodle", "ramen", "deli"],
    "Grocery / Market":    ["grocery", "supermarket", "supermercado", "market",
                            "mart", "food store", "bodega"],
    "Pharmacy":            ["pharmacy", "drugstore", "drug store", "cvs",
                            "walgreens", "rite aid", "boots"],
    "Gas Station":         ["gas station", "shell", "exxon", "mobil", "chevron",
                            "bp ", "sunoco", "citgo", "fuel"],
    "Hotel / Lodging":     ["hotel", "inn", "motel", "resort", "suites",
                            "lodge", "hostel", "airbnb"],
    "Brewery / Bar":       ["brewery", "brewing", "brewpub", "brew co",
                            "taproom", "tavern", "saloon", "pub "],
    "Nightclub":           ["nightclub", "night club", "gentlemen's club",
                            "strip club", "adult club"],
    "Liquor Store":        ["liquor store", "liquor mart", "bottle shop",
                            "wine shop", "wine & spirits", "spirits store"],
    "Karaoke":             ["karaoke"],
    "Casino":              ["casino"],
    "Spa / Salon":         ["spa ", " spa", "salon", "beauty", "nail ",
                            "barber shop", "barbershop", "hair "],
    "Hookah":              ["hookah", "shisha", "nargile", "narguile"],
    "Vape / E-cig":        ["vape", "vapor", "e-cig", "ecig", "vaping"],
    "Cannabis":            ["cannabis", "dispensary", "marijuana", "weed",
                            "cbd ", "hemp ", "thc"],
    "Sports / Gym":        ["gym", "fitness", "crossfit", "sports bar",
                            "billiards", "bowling"],
}

# ── Cargar registros ──────────────────────────────────────────────────────────

print("Cargando registros de Supabase...")
all_records = []
PAGE_SIZE = 1000
offset = 0
while True:
    res = db.client.table("cigar_lounges") \
        .select("id,name,description,address,city,state") \
        .range(offset, offset + PAGE_SIZE - 1) \
        .execute()
    batch = res.data or []
    all_records.extend(batch)
    if len(batch) < PAGE_SIZE:
        break
    offset += PAGE_SIZE

print(f"Total registros cargados: {len(all_records)}\n")

# ── Auditoría ─────────────────────────────────────────────────────────────────

# { categoria: [ {name, city, state, address}, ... ] }
flagged: dict[str, list[dict]] = defaultdict(list)

for record in all_records:
    name        = (record.get("name")        or "").strip()
    description = (record.get("description") or "").strip()
    city        = (record.get("city")        or "").strip()
    state       = (record.get("state")       or "").strip()
    address     = (record.get("address")     or "").strip()

    search_text = f"{name} {description}".lower()

    # Ignorar si claramente es un cigar venue
    if any(safe in search_text for safe in CIGAR_SAFE):
        continue

    # Buscar coincidencia con categorías sospechosas
    for category, keywords in SUSPICIOUS_CATEGORIES.items():
        if any(kw in search_text for kw in keywords):
            flagged[category].append({
                "name":    name,
                "city":    city,
                "state":   state,
                "address": address,
            })
            break   # una categoría por registro es suficiente

# ── Reporte ───────────────────────────────────────────────────────────────────

total_flagged = sum(len(v) for v in flagged.values())

print("=" * 60)
print(f"  AUDITORÍA — Registros sospechosos: {total_flagged} de {len(all_records)}")
print("=" * 60)

# Resumen por categoría
print("\nRESUMEN POR CATEGORÍA:")
print(f"  {'Categoría':<30} {'Registros':>10}")
print(f"  {'─'*30} {'─'*10}")
for cat in sorted(flagged, key=lambda c: -len(flagged[c])):
    print(f"  {cat:<30} {len(flagged[cat]):>10}")

# Detalle por categoría
for cat in sorted(flagged, key=lambda c: -len(flagged[c])):
    records = flagged[cat]
    print(f"\n{'─'*60}")
    print(f"  {cat.upper()} ({len(records)} registros)")
    print(f"{'─'*60}")
    for r in records:
        print(f"  Nombre:    {r['name']}")
        print(f"  Ciudad:    {r['city']}, {r['state']}")
        print(f"  Dirección: {r['address']}")
        print()

print("=" * 60)
print(f"  Total sospechosos: {total_flagged}")
print(f"  Sin marcar (probablemente válidos): {len(all_records) - total_flagged}")
print("=" * 60)
print("\nEste reporte es solo informativo. No se eliminó ningún registro.")
