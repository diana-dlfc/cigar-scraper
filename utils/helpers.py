# utils/helpers.py
import re
import unicodedata
from datetime import datetime, timezone


def slugify(text: str) -> str:
    """Convert a string to a URL-safe slug. Example: 'Casa Fuente, Las Vegas' → 'casa-fuente-las-vegas'"""
    if not text:
        return ""
    text = text.lower().strip()
    # Normalize unicode (e.g. accents)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    # Replace non-alphanumeric characters with hyphens
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def make_slug(name: str, city: str, state: str) -> str:
    """Create a unique slug from name + city + state."""
    combined = f"{name} {city} {state}"
    return slugify(combined)


def normalize_phone(phone: str) -> str | None:
    """Strip all non-digit characters and return E.164-ish US number or None."""
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return phone  # Return as-is if format is unexpected


def normalize_url(url: str) -> str | None:
    """Ensure a URL has a scheme."""
    if not url:
        return None
    url = url.strip()
    if url and not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url or None


def now_utc() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def safe_float(value) -> float | None:
    """Convert a value to float, returning None on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value) -> int | None:
    """Convert a value to int, returning None on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
