# scrapers/yelp.py
"""
Yelp Fusion API scraper for cigar lounges.

Used as a secondary source to complement Google Places data.
Yelp returns up to 1,000 results per location (50 per page × 20 pages).

Docs: https://docs.developer.yelp.com/reference/v3_business_search
"""

import time
import requests
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config.settings import YELP_API_KEY, REQUEST_DELAY_SECONDS
from config.search_config import YELP_CATEGORIES, API_CALL_DELAY
from utils.helpers import make_slug, normalize_phone, normalize_url, now_utc, safe_float, safe_int
from utils.validators import is_cigar_venue, sanitize_lounge_data

YELP_SEARCH_URL = "https://api.yelp.com/v3/businesses/search"
YELP_DETAIL_URL = "https://api.yelp.com/v3/businesses/{id}"
MAX_OFFSET = 1000   # Yelp hard limit
PAGE_LIMIT = 50     # Max results per page


# ---------------------------------------------------------------------------
# Low-level API
# ---------------------------------------------------------------------------

def _yelp_headers() -> dict:
    if not YELP_API_KEY:
        raise ValueError("YELP_API_KEY is not set in environment variables")
    return {"Authorization": f"Bearer {YELP_API_KEY}"}


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(requests.RequestException),
    reraise=True,
)
def _search(params: dict) -> dict:
    resp = requests.get(
        YELP_SEARCH_URL,
        headers=_yelp_headers(),
        params=params,
        timeout=15,
    )

    if resp.status_code != 200:
        print("STATUS:", resp.status_code)
        print("HEADERS:", resp.headers)
        print("BODY:", resp.text)

    resp.raise_for_status()
    return resp.json()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(requests.RequestException),
    reraise=True,
)
def _business_detail(business_id: str) -> dict:
    resp = requests.get(
        YELP_DETAIL_URL.format(id=business_id),
        headers=_yelp_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Data mapping
# ---------------------------------------------------------------------------

def _parse_yelp_business(biz: dict) -> dict | None:
    """Map a Yelp business object to our cigar_lounges schema."""
    name = biz.get("name", "")
    categories = [c.get("alias", "") for c in biz.get("categories", [])]
    category_titles = " ".join(c.get("title", "") for c in biz.get("categories", []))

    if not is_cigar_venue(name, categories, description=category_titles):
        return None

    # Location
    loc = biz.get("location", {})
    address_parts = loc.get("display_address", [])
    address = ", ".join(address_parts) if address_parts else loc.get("address1", "")
    city = loc.get("city")
    state = loc.get("state")

    # Coordinates
    coords = biz.get("coordinates", {})
    lat = safe_float(coords.get("latitude"))
    lon = safe_float(coords.get("longitude"))

    # Build slug
    slug = make_slug(name, city or "", state or "")

    # Contact & meta
    phone = normalize_phone(biz.get("display_phone") or biz.get("phone", ""))
    website = normalize_url(biz.get("url"))  # Yelp page URL as fallback
    rating = safe_float(biz.get("rating"))
    review_count = safe_int(biz.get("review_count"))
    price = biz.get("price")  # "$", "$$", etc.
    price_level = len(price) if isinstance(price, str) else None

    yelp_url = biz.get("url")

    return sanitize_lounge_data({
        "name": name,
        "slug": slug,
        "description": None,  # Enriched later or via detail endpoint
        "website": None,       # Yelp business search doesn't return website; use detail
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
        "google_maps_url": None,
        "source_url": yelp_url,
        "last_scraped_at": now_utc(),
        "enriched": False,
    })


def _parse_yelp_detail(detail: dict, base: dict) -> dict:
    """
    Merge Yelp business detail (which includes website) into a base lounge dict.
    """
    website = normalize_url(detail.get("website"))
    description = None
    # Yelp doesn't return a description in the free tier, but keep for future use

    updated = {**base}
    if website:
        updated["website"] = website
    if description:
        updated["description"] = description
    return updated


# ---------------------------------------------------------------------------
# Public scraper functions
# ---------------------------------------------------------------------------

def search_city(
    city: str,
    state: str,
    fetch_details: bool = False,
) -> list[dict]:
    """
    Search Yelp for cigar lounges in a city.
    fetch_details=True will call the detail endpoint for each business (website, etc.)
    but costs more API calls and time.
    """
    results: list[dict] = []
    seen_ids: set[str] = set()

    for category in YELP_CATEGORIES:
        logger.info(f"Yelp: searching category='{category}' in {city}, {state}")
        offset = 0

        while offset < MAX_OFFSET:
            params = {
                "location": f"{city}, {state}",
                "categories": category,
                "limit": PAGE_LIMIT,
                "offset": offset,
            }
            try:
                data = _search(params)
            except Exception as e:
                logger.error(f"Yelp search failed for {city},{state} cat={category}: {e}")
                break

            businesses = data.get("businesses", [])
            total = data.get("total", 0)
            logger.debug(f"  offset={offset}, got {len(businesses)}/{total}")

            if not businesses:
                break

            for biz in businesses:
                biz_id = biz.get("id")
                if not biz_id or biz_id in seen_ids:
                    continue
                seen_ids.add(biz_id)

                parsed = _parse_yelp_business(biz)
                if not parsed:
                    continue

                if fetch_details:
                    try:
                        detail = _business_detail(biz_id)
                        parsed = _parse_yelp_detail(detail, parsed)
                        time.sleep(API_CALL_DELAY)
                    except Exception as e:
                        logger.warning(f"  Detail failed for Yelp {biz_id}: {e}")

                parsed["_source_id"] = biz_id
                parsed["_source"] = "yelp"
                results.append(parsed)

            offset += PAGE_LIMIT
            if offset >= total:
                break

            time.sleep(API_CALL_DELAY)

    # Also run a keyword search as backup (catches venues not in Yelp's category)
    logger.info(f"Yelp: keyword search for 'cigar lounge' in {city}, {state}")
    offset = 0
    while offset < MAX_OFFSET:
        params = {
            "location": f"{city}, {state}",
            "term": "cigar lounge",
            "limit": PAGE_LIMIT,
            "offset": offset,
        }
        try:
            data = _search(params)
        except Exception as e:
            logger.error(f"Yelp keyword search failed: {e}")
            break

        businesses = data.get("businesses", [])
        total = data.get("total", 0)
        if not businesses:
            break

        for biz in businesses:
            biz_id = biz.get("id")
            if not biz_id or biz_id in seen_ids:
                continue
            seen_ids.add(biz_id)

            parsed = _parse_yelp_business(biz)
            if not parsed:
                continue

            if fetch_details:
                try:
                    detail = _business_detail(biz_id)
                    parsed = _parse_yelp_detail(detail, parsed)
                    time.sleep(API_CALL_DELAY)
                except Exception as e:
                    logger.warning(f"  Detail failed for Yelp {biz_id}: {e}")

            parsed["_source_id"] = biz_id
            parsed["_source"] = "yelp"
            results.append(parsed)

        offset += PAGE_LIMIT
        if offset >= total:
            break
        time.sleep(API_CALL_DELAY)

    logger.info(f"Yelp: found {len(results)} cigar venues in {city}, {state}")
    return results


def search_state(state: str, cities: list[str], fetch_details: bool = False) -> list[dict]:
    """
    Search Yelp across a list of cities in a state.
    Returns deduplicated results by Yelp business ID.
    """
    all_results: list[dict] = []
    seen_ids: set[str] = set()

    for city in cities:
        city_results = search_city(city, state, fetch_details=fetch_details)
        for r in city_results:
            bid = r.get("_source_id")
            if bid and bid not in seen_ids:
                seen_ids.add(bid)
                all_results.append(r)
        time.sleep(REQUEST_DELAY_SECONDS)

    logger.info(f"Yelp: {len(all_results)} unique venues for state {state}")
    return all_results
