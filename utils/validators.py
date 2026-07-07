# utils/validators.py
import unicodedata
from config.search_config import CIGAR_KEYWORDS, EXCLUDE_KEYWORDS


def _normalize(text):
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()


def is_cigar_venue(name, types=None, description=""):
    text = _normalize(f"{name} {description}")
    for kw in EXCLUDE_KEYWORDS:
        if kw in text:
            return False
    for kw in CIGAR_KEYWORDS:
        if kw in text:
            return True
    return False


def sanitize_lounge_data(data):
    cleaned = {}
    str_fields = ["name", "slug", "description", "website", "phone",
                  "address", "city", "state", "country", "google_maps_url", "source_url"]
    for field in str_fields:
        val = data.get(field)
        cleaned[field] = val.strip() if isinstance(val, str) else val
    for field in ["latitude", "longitude", "rating"]:
        val = data.get(field)
        try:
            cleaned[field] = float(val) if val is not None else None
        except (TypeError, ValueError):
            cleaned[field] = None
    for field in ["review_count", "price_level"]:
        val = data.get(field)
        try:
            cleaned[field] = int(val) if val is not None else None
        except (TypeError, ValueError):
            cleaned[field] = None
    cleaned["enriched"] = bool(data.get("enriched", False))
    for ts in ["last_scraped_at", "created_at", "updated_at"]:
        if ts in data:
            cleaned[ts] = data[ts]
    if not cleaned.get("country"):
        cleaned["country"] = "US"
    return {k: v for k, v in cleaned.items() if v is not None or k == "enriched"}
