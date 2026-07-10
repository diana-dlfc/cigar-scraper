# check_missing_states.py
# Muestra los estados que no tienen ningún lounge en la base de datos.
#
# Run: venv\Scripts\python scripts\audit\check_missing_states.py

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()

from database.supabase_client import SupabaseClient
from config.states import US_STATES
from config.cities.all_states import STATE_CITIES

db = SupabaseClient()

all_lounges = []
offset = 0
while True:
    res = db.client.table("cigar_lounges").select("state").range(offset, offset + 999).execute()
    batch = res.data or []
    all_lounges.extend(batch)
    if len(batch) < 1000:
        break
    offset += 1000

found = set(r["state"] for r in all_lounges if r.get("state"))
missing = [f"{k} - {US_STATES[k]['name']}" for k in STATE_CITIES if k not in found]
print(f"Estados sin lounges ({len(missing)}):")
for m in missing:
    print(f"  {m}")
