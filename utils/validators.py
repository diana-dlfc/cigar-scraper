# utils/validators.py
import re
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


def validate_lounge_data(data):
    errors = []
    if not data.get("name"):
        errors.append("Missing required field: name")
    if not data.get("slug"):
        errors.append("Missing required field: slug")
    lat = data.get("latitude")
    lon = data.get("longitude")
    if lat is not None and not (-90 <= lat <= 90):
        errors.append(f"Invalid latitude: {lat}")
    if lon is not None and not (-180 <= lon <= 180):
        errors.append(f"Invalid longitude: {lon}")
    rating = data.get("rating")
    if rating is not None and not (0 <= rating <= 5):
        errors.append(f"Invalid rating: {rating}")
    phone = data.get("phone")
    if phone and not re.match(r"^\+?[\d\s\-().]{7,20}$", phone):
        errors.append(f"Suspicious phone format: {phone}")
    website = data.get("website")
    if website and not website.startswith(("http://", "https://")):
        errors.append(f"Invalid website URL: {website}")
    return len(errors) == 0, errors


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
