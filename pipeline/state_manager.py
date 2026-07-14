# pipeline/state_manager.py
#
# StateManager: acceso a Supabase para el pipeline.
# Solo carga y cuenta lounges — sin lógica de enriquecimiento ni scraping.

from database.supabase_client import SupabaseClient
from config.states import US_STATES

# Campos que el pipeline de enriquecimiento social intenta completar.
# Debe mantenerse en sintonía con enrichment/social_enricher.py (_missing_fields).
_SOCIAL_COLS = (
    "id,name,city,state,address,website,google_maps_url,"
    "email,facebook_url,instagram_url,tiktok_url,youtube_url"
)
_SOCIAL_FILTER = "email.is.null,facebook_url.is.null,instagram_url.is.null,tiktok_url.is.null,youtube_url.is.null"

_PAGE_SIZE = 1000


class StateManager:
    """
    Centraliza el acceso a Supabase para un estado dado.

    No duplica lógica de enriquecimiento. Solo sabe:
      - si el estado es válido
      - cuántos lounges hay en la DB para ese estado
      - cuáles de esos lounges aún necesitan enriquecimiento social
    """

    def __init__(self, db: SupabaseClient, state: str):
        self.db    = db
        self.state = state.upper()

    # ── Validación ────────────────────────────────────────────────────────────

    def is_valid_state(self) -> bool:
        return self.state in US_STATES

    def state_name(self) -> str:
        return US_STATES.get(self.state, {}).get("name", self.state)

    # ── Conteos ───────────────────────────────────────────────────────────────

    def count_total(self) -> int:
        """Total de lounges en Supabase para este estado."""
        res = (
            self.db.client.table("cigar_lounges")
            .select("id", count="exact")
            .eq("state", self.state)
            .execute()
        )
        return res.count or 0

    def count_needing_enrichment(self) -> int:
        """Lounges con al menos un campo social vacío."""
        res = (
            self.db.client.table("cigar_lounges")
            .select("id", count="exact")
            .eq("state", self.state)
            .or_(_SOCIAL_FILTER)
            .execute()
        )
        return res.count or 0

    # ── Carga de datos ────────────────────────────────────────────────────────

    def load_for_enrichment(self, limit: int | None = None) -> list[dict]:
        """
        Devuelve los lounges de este estado con al menos un campo social vacío.
        Usa paginación para no superar los límites de Supabase.
        """
        lounges: list[dict] = []
        offset = 0

        while True:
            query = (
                self.db.client.table("cigar_lounges")
                .select(_SOCIAL_COLS)
                .eq("state", self.state)
                .or_(_SOCIAL_FILTER)
                .range(offset, offset + _PAGE_SIZE - 1)
            )
            batch = query.execute().data or []
            lounges.extend(batch)

            if len(batch) < _PAGE_SIZE:
                break
            if limit and len(lounges) >= limit:
                break

            offset += _PAGE_SIZE

        return lounges[:limit] if limit else lounges
