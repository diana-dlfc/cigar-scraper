# database/supabase_client.py
from supabase import create_client, Client
from config.settings import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY


class SupabaseClient:
    def __init__(self):
        self.client: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

    # --------------------------------------------------
    # CIGAR LOUNGES
    # --------------------------------------------------

    def upsert_lounge(self, data: dict) -> dict:
        """Inserta o actualiza por google_maps_url (evita duplicados)."""
        res = (
            self.client.table("cigar_lounges")
            .upsert(data, on_conflict="slug")
            .execute()
        )
        return res.data[0] if res.data else {}

    def get_lounge_by_slug(self, slug: str) -> dict | None:
        res = (
            self.client.table("cigar_lounges")
            .select("*")
            .eq("slug", slug)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None

    def get_lounges(self, city: str = None, state: str = None, limit: int = 100) -> list:
        query = self.client.table("cigar_lounges").select("*")
        if city:
            query = query.eq("city", city)
        if state:
            query = query.eq("state", state)
        return query.limit(limit).execute().data or []

    # --------------------------------------------------
    # CIGAR LOUNGES SOURCES
    # --------------------------------------------------

    def insert_source(self, data: dict) -> dict:
        """
        data esperado:
        {
            "lounge_id": "uuid",
            "source": "google_places" | "yelp" | "manual",
            "source_id": "...",
            "source_url": "...",
            "raw_data": {...}   # jsonb
        }
        """
        res = self.client.table("cigar_lounges_sources").insert(data).execute()
        return res.data[0] if res.data else {}

    def source_exists(self, source: str, source_id: str) -> bool:
        res = (
            self.client.table("cigar_lounges_sources")
            .select("id")
            .eq("source", source)
            .eq("source_id", source_id)
            .limit(1)
            .execute()
        )
        return bool(res.data)

    # --------------------------------------------------
    # JOBS
    # --------------------------------------------------

    def create_job(self, job_type: str, state: str = None) -> dict:
        res = (
            self.client.table("jobs")
            .insert({"type": job_type, "state": state, "status": "pending"})
            .execute()
        )
        return res.data[0] if res.data else {}

    def update_job(self, job_id: str, data: dict) -> dict:
        """
        Campos actualizables: status, records_found, records_saved,
        error_message, started_at, completed_at
        """
        res = (
            self.client.table("jobs")
            .update(data)
            .eq("id", job_id)
            .execute()
        )
        return res.data[0] if res.data else {}

    def get_job(self, job_id: str) -> dict | None:
        res = (
            self.client.table("jobs")
            .select("*")
            .eq("id", job_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None