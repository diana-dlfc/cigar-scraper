# utils/deduplicator.py


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
