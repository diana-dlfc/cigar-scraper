# test_sheets.py — exporta datos reales de Supabase a Google Sheets
from dotenv import load_dotenv
load_dotenv()

from sheets.sync import export_all_states_to_sheets
from config.settings import GOOGLE_SHEETS_SPREADSHEET_ID
from database.supabase_client import SupabaseClient

db = SupabaseClient()
count = db.client.table("cigar_lounges").select("id", count="exact").execute().count
print(f"Lounges en Supabase: {count}")

print("Exportando a Google Sheets...")
result = export_all_states_to_sheets(
    spreadsheet_id=GOOGLE_SHEETS_SPREADSHEET_ID,
    db=db,
)
print(f"Total exportados: {result['total_rows']} en {result['states_exported']} estados")
print(f"URL: {result['url']}")
