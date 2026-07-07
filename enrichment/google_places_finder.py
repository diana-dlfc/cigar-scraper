# enrichment/google_places_finder.py
"""
Usa Google Places API (New - v1) para encontrar website y Google Maps URL.

Flujo:
  1. Text Search  → place_id  (nombre + ciudad + estado)
  2. Place Details → website + googleMapsUri
"""

import os
import requests
from loguru import logger

PLACES_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()

# New Places API (v1) endpoints
SEARCH_URL  = "https://places.googleapis.com/v1/places:searchText"
DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"


def find_place_id(name: str, city: str, state: str) -> str | None:
    logger.debug(f"Places: buscando {name} | {city} | {state}")
    if not PLACES_KEY:
        logger.warning("GOOGLE_PLACES_API_KEY no configurada")
        return None

    query = f"{name}, {city}, {state}"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": PLACES_KEY,
        "X-Goog-FieldMask": "places.id",
    }
    body = {
        "textQuery": query,
        "maxResultCount": 1,
    }
    try:
        resp = requests.post(SEARCH_URL, json=body, headers=headers, timeout=10)

        logger.debug(f"Places: status {resp.status_code} | {resp.text[:200]}")
        if resp.status_code == 200:
            places = resp.json().get("places", [])
            if places:
                return places[0].get("id")
        else:
            logger.warning(f"Text Search {resp.status_code}: {resp.text[:200]} for: {query}")
    except Exception as e:
        logger.debug(f"Text Search error for {name}: {e}")
    return None


def get_place_details(place_id: str) -> dict:
    """Obtiene website y Google Maps URL del Place ID (nueva API)."""
    if not PLACES_KEY or not place_id:
        return {}

    headers = {
        "X-Goog-Api-Key": PLACES_KEY,
        "X-Goog-FieldMask": "websiteUri,googleMapsUri,displayName",
    }
    try:
        url = DETAILS_URL.format(place_id=place_id)
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            logger.debug(f"Places: details {data}")
            return {
                "website":         data.get("websiteUri"),
                "google_maps_url": data.get("googleMapsUri"),
            }
        else:
            logger.debug(f"Place Details {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.debug(f"Place Details error for {place_id}: {e}")
    return {}


def enrich_with_google_places(lounge: dict) -> dict:
    name  = lounge.get("name", "")
    city  = lounge.get("city", "")
    state = lounge.get("state", "")

    place_id = find_place_id(name, city, state)
    if not place_id:
        return {}

    details = get_place_details(place_id)
    found = {k: v for k, v in details.items() if v}

    if found:
        logger.info(f"[Places] '{name}' → {list(found.keys())}")

    return found
