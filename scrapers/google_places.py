# scrapers/google_places.py
"""
Google Places scraper for cigar lounges.

Uses two endpoints:
  1. Text Search  — search by query + location string (e.g. "cigar lounge in Miami, FL")
  2. Place Details — enrich each result with phone, website, hours, etc.

Docs: https://developers.google.com/maps/documentation/places/web-service
"""

import time
import requests
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config.settings import GOOGLE_PLACES_API_KEY, REQUEST_DELAY_SECONDS
from config.search_config import SEARCH_QUERIES, TEXT_SEARCH_RADIUS_M, API_CALL_DELAY
from utils.helpers import make_slug, normalize_phone, normalize_url, now_utc, safe_float, safe_int
from utils.validators import is_cigar_venue, sanitize_lounge_data

BASE_URL = "https://maps.googleapis.com/maps/api/place"

# Fields to request in Place Details (controls billing tier)
DETAIL_FIELDS = [
    "place_id",
    "name",
    "formatted_address",
    "formatted_phone_number",
    "website",
    "rating",
    "user_ratings_total",
    "price_level",
    "geometry",
    "url",
    "editorial_summary",
    "address_components",
    "types",
]


# ---------------------------------------------------------------------------
# Low-level API calls (with retry)
# ---------------------------------------------------------------------------

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(requests.RequestException),
    reraise=True,
)
def _text_search(query: str, page_token: str = None) -> dict:
    params = {
        "query": query,
        "type": "establishment",
        "key": GOOGLE_PLACES_API_KEY,
    }
    if page_token:
        params = {"pagetoken": page_token, "key": GOOGLE_PLACES_API_KEY}
    resp = requests.get(f"{BASE_URL}/textsearch/json", params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(requests.RequestException),
    reraise=True,
)
def _place_details(place_id: str) -> dict:
    params = {
        "place_id": place_id,
        "fields": ",".join(DETAIL_FIELDS),
        "key": GOOGLE_PLACES_API_KEY,
    }
    resp = requests.get(f"{BASE_URL}/details/json", params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Data mapping
# ---------------------------------------------------------------------------

def _extract_address_component(components: list, component_type: str, prefer_short: bool = False) -> str | None:
    for comp in components:
        if component_type in comp.get("types", []):
            if prefer_short:
                return comp.get("short_name") or comp.get("long_name")
            return comp.get("long_name") or comp.get("short_name")
    return None


def _parse_place_result(result: dict, detail: dict = None) -> dict | None:
    """
    Map a Google Places result (+ optional detail) to our cigar_lounges schema.
    Returns None if the place fails cigar venue validation.
    """
    name = result.get("name", "")
    types = result.get("types", [])
    vicinity = result.get("vicinity", "")

    # Basic cigar venue check (name + types)
    if not is_cigar_venue(name, types):
        return None

    # Geometry
    geo = result.get("geometry", {}).get("location", {})
    lat = safe_float(geo.get("lat"))
    lon = safe_float(geo.get("lng"))

    # Address components (from details if available)
    city = state = address = None
    if detail:
        comps = detail.get("address_components", [])
        city = _extract_address_component(comps, "locality") or \
               _extract_address_component(comps, "sublocality")
        state = _extract_address_component(comps, "administrative_area_level_1", prefer_short=True)
        address = detail.get("formatted_address") or result.get("formatted_address") or vicinity
    else:
        address = result.get("formatted_address") or vicinity

    # Fallback: parse state from address string
    if state and len(state) > 2:
        # Google returns full state name in long_name; short_name has abbreviation
        # We already used long_name above — let's prefer short_name via re-query
        pass

    # Build slug
    slug = make_slug(name, city or "", state or "")

    # Detail-level fields
    phone = website = description = google_maps_url = None
    rating = review_count = price_level = None

    if detail:
        phone = normalize_phone(detail.get("formatted_phone_number", ""))
        website = normalize_url(detail.get("website", ""))
        description = (detail.get("editorial_summary") or {}).get("overview")
        google_maps_url = detail.get("url")
        rating = safe_float(detail.get("rating"))
        review_count = safe_int(detail.get("user_ratings_total"))
        price_level = safe_int(detail.get("price_level"))
    else:
        rating = safe_float(result.get("rating"))
        review_count = safe_int(result.get("user_ratings_total"))

    lounge = {
        "name": name,
        "slug": slug,
        "description": description,
        "website": website,
        "phone": phone,
        "address": address,
        "city": city,
        "state": state,
        "country": "US",
        "latitude": lat,
        "longitude": lon,
        "rating": rating,
        "review_count": review_count,
        "price_level": price_level,
        "google_maps_url": google_maps_url,
        "source_url": google_maps_url,
        "last_scraped_at": now_utc(),
        "enriched": False,
    }
    return sanitize_lounge_data(lounge)


# ---------------------------------------------------------------------------
# Public scraper functions
# ---------------------------------------------------------------------------

def search_city(city: str, state: str, fetch_details: bool = True) -> list[dict]:
    """
    Search for cigar lounges in a city using all SEARCH_QUERIES.
    Returns a list of validated, cleaned lounge dicts with source metadata.
    """
    if not GOOGLE_PLACES_API_KEY:
        raise ValueError("GOOGLE_PLACES_API_KEY is not set in environment variables")

    results: list[dict] = []
    seen_place_ids: set[str] = set()

    for query in SEARCH_QUERIES:
        full_query = f"{query} in {city}, {state}"
        logger.info(f"Google Places: searching '{full_query}'")

        page_token = None
        page = 0

        while True:
            page += 1
            try:
                if page_token:
                    # Google requires ~2s delay before using next_page_token
                    time.sleep(2)
                data = _text_search(full_query, page_token=page_token)
            except Exception as e:
                logger.error(f"Text search failed for '{full_query}' page {page}: {e}")
                break

            status = data.get("status")
            if status not in ("OK", "ZERO_RESULTS"):
                logger.warning(f"Google Places status: {status} for '{full_query}'")
                break

            places = data.get("results", [])
            logger.debug(f"  Page {page}: {len(places)} results")

            for place in places:
                place_id = place.get("place_id")
                if not place_id or place_id in seen_place_ids:
                    continue
                seen_place_ids.add(place_id)

                detail = None
                if fetch_details:
                    try:
                        detail_resp = _place_details(place_id)
                        detail = detail_resp.get("result", {})
                        time.sleep(API_CALL_DELAY)
                    except Exception as e:
                        logger.warning(f"  Place details failed for {place_id}: {e}")

                parsed = _parse_place_result(place, detail)
                if parsed:
                    parsed["_source_id"] = place_id  # carry for source table
                    results.append(parsed)

            page_token = data.get("next_page_token")
            if not page_token:
                break

            time.sleep(API_CALL_DELAY)

    logger.info(f"Google Places: found {len(results)} cigar venues in {city}, {state}")
    return results


def search_state(state: str, cities: list[str], fetch_details: bool = True) -> list[dict]:
    """
    Search across a list of cities in a state.
    Returns deduplicated results (by place_id).
    """
    all_results: list[dict] = []
    seen_place_ids: set[str] = set()

    for city in cities:
        city_results = search_city(city, state, fetch_details=fetch_details)
        for r in city_results:
            pid = r.pop("_source_id", None)
            if pid and pid not in seen_place_ids:
                seen_place_ids.add(pid)
                r["_source_id"] = pid
                all_results.append(r)
        time.sleep(REQUEST_DELAY_SECONDS)

    logger.info(f"Google Places: {len(all_results)} unique venues for state {state}")
    return all_results
