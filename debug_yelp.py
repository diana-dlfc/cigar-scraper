# debug_yelp.py — Diagnóstico detallado
# Run: venv\Scripts\python debug_yelp.py

import requests
from dotenv import load_dotenv
load_dotenv()

from config.settings import YELP_API_KEY
from utils.validators import is_cigar_venue

YELP_SEARCH_URL = "https://api.yelp.com/v3/businesses/search"
HEADERS = {"Authorization": f"Bearer {YELP_API_KEY}"}

TEST_CITIES = [
    ("Las Vegas", "NV"),
    ("Seattle", "WA"),
    ("Portland", "OR"),
]

for city, state in TEST_CITIES:
    print(f"\n{'─'*50}")
    print(f"{city}, {state}")

    params = {
        "location": f"{city}, {state}",
        "categories": "cigarlounge,cigarbar,tobaccoshops",
        "limit": 10,
    }
    try:
        resp = requests.get(YELP_SEARCH_URL, headers=HEADERS, params=params, timeout=15)
        data = resp.json()
        error = data.get("error", {})

        if error:
            print(f"  ERROR: {error.get('code')} — {error.get('description','')[:80]}")
            continue

        businesses = data.get("businesses", [])
        total = data.get("total", 0)
        print(f"  Total Yelp: {total} | Devueltos: {len(businesses)}")

        for biz in businesses[:8]:
            name = biz.get("name", "?")
            cats = [c.get("alias") for c in biz.get("categories", [])]
            cat_titles = " ".join(c.get("title","") for c in biz.get("categories", []))
            passes = is_cigar_venue(name, cats, description=cat_titles)
            state_loc = biz.get("location", {}).get("state", "?")
            print(f"  {'✓' if passes else '✗'} [{state_loc}] {name} | {cats}")

    except Exception as e:
        print(f"  EXCEPCIÓN: {e}")
