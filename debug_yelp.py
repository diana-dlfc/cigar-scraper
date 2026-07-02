# debug_yelp.py — Diagnóstico: qué devuelve Yelp para los estados faltantes
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
    ("Charlotte", "NC"),
    ("Salt Lake City", "UT"),
]

for city, state in TEST_CITIES:
    print(f"\n{'─'*50}")
    print(f"{city}, {state}")

    # Búsqueda por categoría
    params = {
        "location": f"{city}, {state}",
        "categories": "cigarlounge,cigarbar,tobaccoshops",
        "limit": 10,
    }
    try:
        resp = requests.get(YELP_SEARCH_URL, headers=HEADERS, params=params, timeout=15)
        data = resp.json()
        businesses = data.get("businesses", [])
        total = data.get("total", 0)
        error = data.get("error", {})

        if error:
            print(f"  ERROR Yelp: {error}")
            continue

        print(f"  Total en Yelp: {total} | Devolvió: {len(businesses)}")

        for biz in businesses[:5]:
            name = biz.get("name", "?")
            cats = [c.get("alias") for c in biz.get("categories", [])]
            cat_titles = " ".join(c.get("title") for c in biz.get("categories", []))
            passes = is_cigar_venue(name, cats, description=cat_titles)
            print(f"  {'✓' if passes else '✗'} {name} | cats={cats}")

    except Exception as e:
        print(f"  EXCEPCIÓN: {e}")
