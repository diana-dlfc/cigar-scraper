print("1. Inicio")

from database.supabase_client import SupabaseClient

print("2. Importado")

db = SupabaseClient()

print("3. Cliente creado")

job = db.create_job("test", state="FL")
print("4. Job creado:", job)

updated = db.update_job(job["id"], {"status": "done", "records_found": 0})
print("5. Job actualizado:", updated)

lounges = db.get_lounges(state="FL")
print("6. Lounges:", lounges)

print("7. Fin")