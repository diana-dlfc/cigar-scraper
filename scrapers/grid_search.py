# scrapers/grid_search.py
"""
Geographic grid search for cigar lounges.

Divides a state's bounding box into a grid of cells and runs Google Places
Nearby Search at the center of each cell. This ensures no venue is missed
in areas not covered by city-name queries (rural zones, unincorporated areas).

Grid cell size is configurable — smaller = more API calls but higher recall.
"""

import math
import time
import requests
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config.settings import GOOGLE_PLACES_API_KEY, REQUEST_DELAY_SECONDS
from config.search_config import (
    SEARCH_QUERIES,
    GRID_CELL_SIZE_KM,
    NEARBY_SEARCH_RADIUS_M,
    API_CALL_DELAY,
)
from config.states import get_state_bbox
from utils.helpers import make_slug, normalize_phone, normalize_url, now_utc, safe_float, safe_int
from utils.validators import is_cigar_venue, sanitize_lounge_data

BASE_URL = "https://maps.googleapis.com/maps/api/place"

# Degrees of latitude per km (roughly constant)
LAT_DEG_PER_KM = 1 / 111.0

DETAIL_FIELDS = [
    "place_id", "name", "formatted_address", "formatted_phone_number",
    "website", "rating", "user_ratings_total", "price_level",
    "geometry", "url", "editorial_summary", "address_components", "types",
]


# ---------------------------------------------------------------------------
# Grid generation
# ---------------------------------------------------------------------------

def generate_grid(
    min_lat: float, min_lon: float,
    max_lat: float, max_lon: float,
    cell_size_km: float = GRID_CELL_SIZE_KM,
) -> list[tuple[float, float]]:
    """
    Generate lat/lon center points for a grid covering the bounding box.
    Returns a list of (lat, lon) tuples.
    """
    lat_step = cell_size_km * LAT_DEG_PER_KM
    points = []
    lat = min_lat + lat_step / 2
    while lat <= max_lat:
        # Longitude degrees per km varies with latitude
        lon_deg_per_km = 1 / (111.0 * math.cos(math.radians(lat)))
        lon_step = cell_size_km * lon_deg_per_km
        lon = min_lon + lon_step / 2
        while lon <= max_lon:
            points.append((round(lat, 6), round(lon, 6)))
            lon += lon_step
        lat += lat_step
    return points


# ---------------------------------------------------------------------------
# Low-level API
# ---------------------------------------------------------------------------

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(requests.RequestException),
    reraise=True,
)
def _nearby_search(lat: float, lon: float, query: str, page_token: str = None) -> dict:
    if page_token:
        params = {"pagetoken": page_token, "key": GOOGLE_PLACES_API_KEY}
    else:
        params = {
            "location": f"{lat},{lon}",
            "radius": NEARBY_SEARCH_RADIUS_M,
            "keyword": query,
            "key": GOOGLE_PLACES_API_KEY,
        }
    resp = requests.get(f"{BASE_URL}/nearbysearch/json", params=params, timeout=15)
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
# Data mapping (mirrors google_places.py logic)
# ---------------------------------------------------------------------------

def _extract_address_component(components: list, component_type: str, prefer_short: bool = False) -> str | None:
    for comp in components:
        if component_type in comp.get("types", []):
            if prefer_short:
                return comp.get("short_name") or comp.get("long_name")
            return comp.get("long_name") or comp.get("short_name")
    return None


def _parse_nearby_result(result: dict, detail: dict = None) -> dict | None:
    name = result.get("name", "")
    types = result.get("types", [])

    if not is_cigar_venue(name, types):
        return None

    geo = result.get("geometry", {}).get("location", {})
    lat = safe_float(geo.get("lat"))
    lon = safe_float(geo.get("lng"))

    city = state = address = None
    if detail:
        comps = detail.get("address_components", [])
        city = (_extract_address_component(comps, "locality") or
                _extract_address_component(comps, "sublocality"))
        state = _extract_address_component(comps, "administrative_area_level_1")
        address = detail.get("formatted_address") or result.get("vicinity")
    else:
        address = result.get("vicinity")

    slug = make_slug(name, city or "", state or "")

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

    return sanitize_lounge_data({
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
    })


# ---------------------------------------------------------------------------
# Public scraper functions
# ---------------------------------------------------------------------------

def search_grid_point(
    lat: float, lon: float, fetch_details: bool = True
) -> list[dict]:
    """
    Search for cigar lounges around a single lat/lon grid point.
    Returns a list of parsed lounge dicts with _source_id attached.
    """
    if not GOOGLE_PLACES_API_KEY:
        raise ValueError("GOOGLE_PLACES_API_KEY is not set")

    results: list[dict] = []
    seen_place_ids: set[str] = set()

    for query in SEARCH_QUERIES:
        page_token = None
        page = 0

        while True:
            page += 1
            try:
                if page_token:
                    time.sleep(2)
                data = _nearby_search(lat, lon, query, page_token=page_token)
            except Exception as e:
                logger.warning(f"  Nearby search failed at ({lat},{lon}) query='{query}': {e}")
                break

            status = data.get("status")
            if status not in ("OK", "ZERO_RESULTS"):
                logger.debug(f"  Status: {status} at ({lat},{lon})")
                break

            for place in data.get("results", []):
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
                        logger.warning(f"  Detail failed for {place_id}: {e}")

                parsed = _parse_nearby_result(place, detail)
                if parsed:
                    parsed["_source_id"] = place_id
                    results.append(parsed)

            page_token = data.get("next_page_token")
            if not page_token:
                break
            time.sleep(API_CALL_DELAY)

    return results


def search_state_grid(
    state: str,
    cell_size_km: float = GRID_CELL_SIZE_KM,
    fetch_details: bool = True,
    progress_callback=None,
) -> list[dict]:
    """
    Run a full grid search over a US state's bounding box.

    Args:
        state: 2-letter state abbreviation (e.g. "FL")
        cell_size_km: Size of each grid cell in km
        fetch_details: Whether to fetch Place Details for each result
        progress_callback: Optional callable(current, total, point) for progress tracking

    Returns:
        List of unique lounge dicts with _source_id attached
    """
    if not GOOGLE_PLACES_API_KEY:
        raise ValueError("GOOGLE_PLACES_API_KEY is not set")

    bbox = get_state_bbox(state)
    if not bbox:
        raise ValueError(f"No bounding box found for state: {state}")

    min_lat, min_lon, max_lat, max_lon = bbox
    grid_points = generate_grid(min_lat, min_lon, max_lat, max_lon, cell_size_km)

    logger.info(
        f"Grid search for {state}: {len(grid_points)} grid points "
        f"({cell_size_km}km cells, {NEARBY_SEARCH_RADIUS_M}m radius)"
    )

    all_results: list[dict] = []
    seen_place_ids: set[str] = set()

    for i, (lat, lon) in enumerate(grid_points):
        if progress_callback:
            progress_callback(i + 1, len(grid_points), (lat, lon))
        else:
            if (i + 1) % 10 == 0 or i == 0:
                logger.info(f"  Grid point {i+1}/{len(grid_points)} ({lat}, {lon})")

        point_results = search_grid_point(lat, lon, fetch_details=fetch_details)

        for r in point_results:
            pid = r.get("_source_id")
            if pid and pid not in seen_place_ids:
                seen_place_ids.add(pid)
                all_results.append(r)

        time.sleep(REQUEST_DELAY_SECONDS)

    logger.info(f"Grid search complete for {state}: {len(all_results)} unique venues found")
    return all_results
