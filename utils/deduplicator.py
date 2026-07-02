# utils/deduplicator.py
import math
from utils.helpers import make_slug


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance in km between two lat/lon points."""
    R = 6371  # Earth radius in km
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def are_duplicates(a: dict, b: dict, distance_threshold_km: float = 0.1) -> bool:
    """
    Returns True if two lounge dicts appear to be the same place.
    Checks by slug first, then by geographic proximity + name similarity.
    """
    # Same slug = same place
    if a.get("slug") and a["slug"] == b.get("slug"):
        return True

    # Same Google place_id (stored in source data)
    if a.get("source_id") and a["source_id"] == b.get("source_id"):
        return True

    # Geographic + name check
    a_lat, a_lon = a.get("latitude"), a.get("longitude")
    b_lat, b_lon = b.get("latitude"), b.get("longitude")

    if all(v is not None for v in [a_lat, a_lon, b_lat, b_lon]):
        dist = haversine_distance_km(a_lat, a_lon, b_lat, b_lon)
        if dist < distance_threshold_km:
            # Very close — check name similarity
            a_name = (a.get("name") or "").lower().strip()
            b_name = (b.get("name") or "").lower().strip()
            if a_name == b_name:
                return True
            # Check if one name contains the other (covers abbreviations)
            if a_name in b_name or b_name in a_name:
                return True

    return False


def deduplicate_batch(lounges: list[dict], distance_threshold_km: float = 0.1) -> list[dict]:
    """
    Remove duplicates from a list of lounge dicts.
    Keeps the first occurrence of each unique place.
    """
    unique: list[dict] = []
    for candidate in lounges:
        is_dup = False
        for existing in unique:
            if are_duplicates(candidate, existing, distance_threshold_km):
                is_dup = True
                break
        if not is_dup:
            unique.append(candidate)
    return unique


class DatabaseDeduplicator:
    """
    Checks against already-saved slugs and source IDs to avoid re-inserting
    records that exist in Supabase.
    """

    def __init__(self, db_client):
        self.db = db_client
        self._slug_cache: set[str] = set()
        self._source_cache: dict[str, set[str]] = {}  # source → set of source_ids

    def load_existing_slugs(self, state: str = None):
        """Pre-load slugs for a state to minimize per-record DB lookups."""
        lounges = self.db.get_lounges(state=state, limit=10000)
        self._slug_cache = {l["slug"] for l in lounges if l.get("slug")}

    def slug_exists(self, slug: str) -> bool:
        if slug in self._slug_cache:
            return True
        result = self.db.get_lounge_by_slug(slug)
        if result:
            self._slug_cache.add(slug)
            return True
        return False

    def source_exists(self, source: str, source_id: str) -> bool:
        if source not in self._source_cache:
            self._source_cache[source] = set()
        cache = self._source_cache[source]
        if source_id in cache:
            return True
        exists = self.db.source_exists(source, source_id)
        if exists:
            cache.add(source_id)
        return exists

    def register(self, slug: str, source: str = None, source_id: str = None):
        """Register a newly saved record to keep cache in sync."""
        if slug:
            self._slug_cache.add(slug)
        if source and source_id:
            self._source_cache.setdefault(source, set()).add(source_id)
