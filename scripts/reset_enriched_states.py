"""
reset_enriched_states.py — Resetea a 'pending' todos los estados con status='completed'
en pipeline_state para que el pipeline los vuelva a enriquecer.

Los datos ya enriquecidos (facebook, instagram, etc.) NO se borran.
Solo se re-procesan los negocios que aún tienen campos vacíos (email, youtube, etc.)

Ejecutar desde la raíz del proyecto:
    venv\Scripts\python scripts\reset_enriched_states.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()
from supabase import create_client

db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

# ── Ver estado actual ─────────────────────────────────────────────────────────
rows = db.table("pipeline_state").select("state,status,updated_at").execute().data or []

completed = [r for r in rows if r.get("status") == "completed"]
pending   = [r for r in rows if r.get("status") == "pending"]
running   = [r for r in rows if r.get("status") == "running"]

print(f"\nEstado actual de pipeline_state:")
print(f"  completed : {len(completed)}")
print(f"  pending   : {len(pending)}")
print(f"  running   : {len(running)}")

if not completed:
    print("\nNo hay estados en 'completed'. Nada que resetear.")
    sys.exit(0)

print(f"\nEstados a resetear ({len(completed)}):")
for r in sorted(completed, key=lambda x: x["state"]):
    print(f"  {r['state']:<30} → pending  (era completed desde {r.get('updated_at','?')[:10]})")

confirm = input("\n¿Confirmar reset a 'pending'? [s/N]: ").strip().lower()
if confirm != "s":
    print("Cancelado.")
    sys.exit(0)

# ── Resetear ──────────────────────────────────────────────────────────────────
states_to_reset = [r["state"] for r in completed]

resp = (
    db.table("pipeline_state")
    .update({"status": "pending", "locked_at": None, "error": None})
    .in_("state", states_to_reset)
    .execute()
)

print(f"\n✓ {len(resp.data or [])} estados reseteados a 'pending'.")
print("El pipeline los volverá a procesar en el próximo ciclo.")
print("Los datos existentes (facebook, instagram, etc.) NO fueron borrados.")
