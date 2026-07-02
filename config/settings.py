import os
from dotenv import load_dotenv

load_dotenv()

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

# Google Places API
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")

# Yelp Fusion API
YELP_API_KEY = os.getenv("YELP_API_KEY")

# Google Sheets
GOOGLE_SHEETS_CREDENTIALS_FILE = os.getenv("GOOGLE_SHEETS_CREDENTIALS_FILE", "credentials.json")
GOOGLE_SHEETS_SPREADSHEET_ID = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")

# Scraper settings
DEFAULT_SEARCH_RADIUS_METERS = int(os.getenv("SEARCH_RADIUS_METERS", 50000))  # 50 km
REQUEST_DELAY_SECONDS = float(os.getenv("REQUEST_DELAY_SECONDS", 1.0))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))