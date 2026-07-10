# audit_invalid_states.py
# Auditoría de registros con state inválido (fuera de los 50 estados + DC).
# NO elimina ningún registro.
#
# Run: venv\Scripts\python scripts\audit\audit_invalid_states.py

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from collections import defaultdict
from dotenv import load_dotenv
load_dotenv()

from database.supabase_client import SupabaseClient
from config.states import US_STATES

VALID_STATES = set(US_STATES.keys())   # 50 estados + DC

db = SupabaseClient()

# ── Cargar registros ──────────────────────────────────────────────────────────

print("Cargando registros de Supabase...")
all_records = []
PAGE_SIZE = 1000
offset = 0
while True:
    res = db.client.table("cigar_lounges") \
        .select("id,name,address,city,state") \
        .range(offset, offset + PAGE_SIZE - 1) \
        .execute()
    batch = res.data or []
    all_records.extend(batch)
    if len(batch) < PAGE_SIZE:
        break
    offset += PAGE_SIZE

print(f"Total registros cargados: {len(all_records)}\n")

# ── Auditoría ─────────────────────────────────────────────────────────────────

# { state_code: [ {name, city, state, address}, ... ] }
invalid: dict[str, list[dict]] = defaultdict(list)

for record in all_records:
    state = (record.get("state") or "").strip().upper()

    if state not in VALID_STATES:
        invalid[state or "(vacío)"].append({
            "id":      record["id"],
            "name":    (record.get("name")    or "").strip(),
            "city":    (record.get("city")    or "").strip(),
            "state":   (record.get("state")   or "").strip(),
            "address": (record.get("address") or "").strip(),
        })

# ── Reporte ───────────────────────────────────────────────────────────────────

total_invalid = sum(len(v) for v in invalid.values())

print("=" * 60)
print(f"  AUDITORÍA — Registros con state inválido: {total_invalid}")
print("=" * 60)

if not invalid:
    print("\n  ✓ Todos los registros tienen un state válido de EE.UU.")
else:
    # Resumen por código
    print("\nRESUMEN POR CÓDIGO DE STATE:")
    print(f"  {'State':<12} {'Registros':>10}")
    print(f"  {'─'*12} {'─'*10}")
    for state_code in sorted(invalid, key=lambda s: -len(invalid[s])):
        print(f"  {state_code:<12} {len(invalid[state_code]):>10}")

    # Detalle por código
    for state_code in sorted(invalid, key=lambda s: -len(invalid[s])):
        records = invalid[state_code]
        print(f"\n{'─'*60}")
        print(f"  STATE: '{state_code}'  ({len(records)} registros)")
        print(f"{'─'*60}")
        for r in records:
            print(f"  Nombre:    {r['name']}")
            print(f"  Ciudad:    {r['city']}")
            print(f"  Dirección: {r['address']}")
            print()

print("=" * 60)
print(f"  Total inválidos : {total_invalid}")
print(f"  Total válidos   : {len(all_records) - total_invalid}")
print(f"  Total en tabla  : {len(all_records)}")
print("=" * 60)
print("\nEste reporte es solo informativo. No se eliminó ningún registro.")
