# clean_database.py
# Limpieza conservadora de la tabla cigar_lounges en Supabase.
#
# Run: venv\Scripts\python scripts\maintenance\clean_database.py

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()

from database.supabase_client import SupabaseClient

db = SupabaseClient()

# ── Configuración ─────────────────────────────────────────────────────────────

# Nombres exactos a eliminar (case-insensitive, sin espacios extra)
JUNK_NAMES = {"bcn", "11-11"}

# Palabras en dirección que indican registro fuera de EE.UU.
FOREIGN_ADDRESS_KEYWORDS = [
    "nicaragua", "managua", "costa rica", "canada", "canadá",
    "mexico", "méxico", "barcelona", "bcn", "españa", "spain",
    "reino unido", "united kingdom", "honduras", "guatemala",
    "el salvador", "panama", "panamá",
]

# Abreviaturas válidas: 50 estados + DC
VALID_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","DC","FL","GA","HI","ID","IL",
    "IN","IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE",
    "NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD",
    "TN","TX","UT","VT","VA","WA","WV","WI","WY",
}

# ── Cargar todos los registros ────────────────────────────────────────────────

print("Cargando registros de Supabase...")
all_records = []
PAGE_SIZE = 1000
offset = 0
while True:
    res = db.client.table("cigar_lounges") \
        .select("id,name,address,city,state,country") \
        .range(offset, offset + PAGE_SIZE - 1) \
        .execute()
    batch = res.data or []
    all_records.extend(batch)
    if len(batch) < PAGE_SIZE:
        break
    offset += PAGE_SIZE

print(f"Total registros cargados: {len(all_records)}\n")

# ── Contadores ────────────────────────────────────────────────────────────────

deleted_by_name    = 0
deleted_by_country = 0
deleted_empty_name = 0
deleted_city       = 0
deleted_address    = 0
deleted_country_f3 = 0
deleted_state      = 0

already_deleted: set = set()   # evita doble-borrado si un registro califica en varias fases


def _delete(rid, name, address, motivo: str) -> None:
    print("Eliminando:")
    print(f"  Nombre:    {name!r}")
    print(f"  Dirección: {address!r}")
    print(f"  Motivo:    {motivo}\n")
    db.client.table("cigar_lounges").delete().eq("id", rid).execute()
    already_deleted.add(rid)


# ── Fase 1: Nombre basura ─────────────────────────────────────────────────────

for record in all_records:
    rid     = record["id"]
    name    = (record.get("name") or "").strip()
    address = (record.get("address") or "").strip()

    if name.lower() in JUNK_NAMES:
        _delete(rid, name, address, f"Nombre basura exacto ({name!r})")
        deleted_by_name += 1

# ── Fase 2: Dirección / country extranjero ────────────────────────────────────

for record in all_records:
    rid     = record["id"]
    if rid in already_deleted:
        continue

    name    = (record.get("name") or "").strip()
    address = (record.get("address") or "").strip()
    country = (record.get("country") or "").strip()

    address_low = address.lower()
    hit = next((kw for kw in FOREIGN_ADDRESS_KEYWORDS if kw in address_low), None)

    if hit:
        _delete(rid, name, address, f"Dirección extranjera (contiene '{hit}')")
        deleted_by_country += 1
        continue

    if country and country.upper() != "US":
        _delete(rid, name, address, f"Campo country = '{country}' (distinto de US)")
        deleted_by_country += 1

# ── Fase 3: Registros claramente corruptos ────────────────────────────────────

for record in all_records:
    rid     = record["id"]
    if rid in already_deleted:
        continue

    name    = (record.get("name") or "").strip()
    address = (record.get("address") or "").strip()
    city    = (record.get("city") or "").strip()
    state   = (record.get("state") or "").strip()
    country = (record.get("country") or "").strip()

    # name vacío
    if not name:
        _delete(rid, name, address, "Nombre vacío")
        deleted_empty_name += 1
        continue

    # city = "Lisboa"
    if city.lower() == "lisboa":
        _delete(rid, name, address, f"Ciudad inválida: '{city}'")
        deleted_city += 1
        continue

    # address contiene "Portugal"
    if "portugal" in address.lower():
        _delete(rid, name, address, "Dirección contiene 'Portugal'")
        deleted_address += 1
        continue

    # country distinto de US (captura los que no cayeron en fase 2 por tener country vacío antes)
    if country and country.upper() != "US":
        _delete(rid, name, address, f"Campo country = '{country}' (distinto de US)")
        deleted_country_f3 += 1
        continue

    # state no es abreviatura válida de EE.UU.
    if state and state.upper() not in VALID_STATES:
        _delete(rid, name, address, f"State inválido: '{state}'")
        deleted_state += 1
        continue

# ── Reporte final ─────────────────────────────────────────────────────────────

total_deleted  = (deleted_by_name + deleted_by_country +
                  deleted_empty_name + deleted_city +
                  deleted_address + deleted_country_f3 + deleted_state)
total_restante = db.client.table("cigar_lounges").select("id", count="exact").execute().count

print("=" * 55)
print(f"  [Fase 1] Nombre basura              : {deleted_by_name}")
print(f"  [Fase 2] Dirección/country extranjero: {deleted_by_country}")
print(f"  [Fase 3] Nombre vacío               : {deleted_empty_name}")
print(f"  [Fase 3] Ciudad inválida (Lisboa)   : {deleted_city}")
print(f"  [Fase 3] Dirección con Portugal     : {deleted_address}")
print(f"  [Fase 3] Country distinto de US     : {deleted_country_f3}")
print(f"  [Fase 3] State inválido             : {deleted_state}")
print(f"  {'─'*45}")
print(f"  Total eliminado                     : {total_deleted}")
print(f"  Total restante en Supabase          : {total_restante}")
print("=" * 55)
