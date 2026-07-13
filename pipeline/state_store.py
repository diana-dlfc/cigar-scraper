# pipeline/state_store.py
#
# StateStore: capa de persistencia del Pipeline Orchestrator.
# Gestiona las tablas pipeline_state y pipeline_config en Supabase.
#
# No contiene lógica de scraping, enriquecimiento ni exportación.
# Requiere que las tablas existan (ver pipeline/setup.sql).

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from loguru import logger

from database.supabase_client import SupabaseClient

# ── Constantes ────────────────────────────────────────────────────────────────

LOCK_TIMEOUT_MINUTES   = 30   # estados con locked_at más viejo que esto → reset
MAINTENANCE_INTERVAL_DAYS = 7
MAINTENANCE_RETRY_HOURS   = 1  # si el mantenimiento falla, reintentar en 1 hora

# Campos comparados antes/después del enriquecimiento para detectar cambios reales
CHANGE_DETECTION_FIELDS = [
    "email",
    "facebook_url",
    "instagram_url",
    "tiktok_url",
    "website",
    "phone",
    "google_maps_url",
]


# ── StateStore ────────────────────────────────────────────────────────────────

class StateStore:
    """
    CRUD sobre pipeline_state y pipeline_config.
    Todas las operaciones son síncronas (PostgREST via Supabase).
    """

    def __init__(self, db: SupabaseClient):
        self.db     = db
        self.client = db.client

    # ── Inicialización ────────────────────────────────────────────────────────

    def initialize(self, state_codes: list[str]) -> int:
        """
        Inserta los estados que aún no existen en pipeline_state.
        Seguro de llamar múltiples veces (idempotente).
        Devuelve el número de filas insertadas.
        """
        existing = {
            row["state_code"]
            for row in (
                self.client.table("pipeline_state")
                .select("state_code")
                .execute()
                .data or []
            )
        }
        to_insert = [
            {"state_code": s, "status": "pending"}
            for s in sorted(state_codes)
            if s not in existing
        ]
        if to_insert:
            self.client.table("pipeline_state").insert(to_insert).execute()
            logger.info(f"[StateStore] Insertados {len(to_insert)} estados nuevos.")
        return len(to_insert)

    # ── Modo global ───────────────────────────────────────────────────────────

    def get_mode(self) -> str:
        """Devuelve el modo actual del pipeline: 'enrichment' | 'maintenance'."""
        res = (
            self.client.table("pipeline_config")
            .select("mode")
            .eq("id", 1)
            .execute()
        )
        return (res.data or [{}])[0].get("mode", "enrichment")

    def set_mode(self, mode: str) -> None:
        """Cambia el modo global. La transición enrichment→maintenance es permanente."""
        self.client.table("pipeline_config").update(
            {"mode": mode, "updated_at": _now()}
        ).eq("id", 1).execute()
        logger.info(f"[StateStore] Modo cambiado a '{mode}'.")

    # ── Recuperación de bloqueos ──────────────────────────────────────────────

    def recover_stale_locks(self) -> int:
        """
        Restablece a 'pending' los estados bloqueados en 'running' por más de
        LOCK_TIMEOUT_MINUTES minutos. Protege contra reinicios de Railway.
        Devuelve el número de estados recuperados.
        """
        cutoff = _now_minus_minutes(LOCK_TIMEOUT_MINUTES)
        res = (
            self.client.table("pipeline_state")
            .update({"status": "pending", "locked_at": None, "updated_at": _now()})
            .eq("status", "running")
            .lt("locked_at", cutoff)
            .execute()
        )
        count = len(res.data or [])
        if count:
            logger.warning(f"[StateStore] {count} estado(s) desbloqueado(s) por timeout.")
        return count

    # ── Consultas: fase de enriquecimiento ────────────────────────────────────

    def get_next_for_enrichment(self) -> dict | None:
        """
        Devuelve el siguiente estado pendiente o fallido para enriquecer,
        en orden alfabético por state_code.
        """
        res = (
            self.client.table("pipeline_state")
            .select("*")
            .in_("status", ["pending", "failed"])
            .order("state_code", desc=False)
            .limit(1)
            .execute()
        )
        return (res.data or [None])[0]

    def all_enrichment_complete(self) -> bool:
        """True cuando no quedan estados pending ni failed."""
        res = (
            self.client.table("pipeline_state")
            .select("id", count="exact")
            .in_("status", ["pending", "failed"])
            .execute()
        )
        return (res.count or 0) == 0

    # ── Consultas: fase de mantenimiento ──────────────────────────────────────

    def get_next_for_maintenance(self) -> dict | None:
        """
        Devuelve el siguiente estado que necesita mantenimiento:
          - next_maintenance_run IS NULL (nunca mantenido) — primero
          - next_maintenance_run <= NOW()                  — vencido
        Excluye estados en ejecución activa.
        ORDER BY state_code ASC.
        """
        now = _now()

        # 1. Estados que nunca han sido mantenidos
        res = (
            self.client.table("pipeline_state")
            .select("*")
            .is_("next_maintenance_run", "null")
            .neq("status", "running")
            .order("state_code", desc=False)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]

        # 2. Estados con mantenimiento vencido
        res = (
            self.client.table("pipeline_state")
            .select("*")
            .lte("next_maintenance_run", now)
            .neq("status", "running")
            .order("state_code", desc=False)
            .limit(1)
            .execute()
        )
        return (res.data or [None])[0]

    def seconds_until_next_maintenance(self) -> float:
        """
        Segundos hasta el próximo next_maintenance_run programado.
        Devuelve 0 si no hay ninguno agendado.
        """
        res = (
            self.client.table("pipeline_state")
            .select("next_maintenance_run")
            .not_.is_("next_maintenance_run", "null")
            .order("next_maintenance_run", desc=False)
            .limit(1)
            .execute()
        )
        if not res.data:
            return 0
        ts_str = res.data[0]["next_maintenance_run"]
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return max((ts - datetime.now(timezone.utc)).total_seconds(), 0)

    # ── Transiciones de estado ────────────────────────────────────────────────

    def mark_running(self, state_code: str) -> None:
        now = _now()
        self.client.table("pipeline_state").update({
            "status":          "running",
            "last_started_at": now,
            "locked_at":       now,
            "last_error":      None,
            "updated_at":      now,
        }).eq("state_code", state_code).execute()

    def mark_completed(self, state_code: str) -> None:
        now = _now()
        self.client.table("pipeline_state").update({
            "status":            "completed",
            "last_completed_at": now,
            "locked_at":         None,
            "updated_at":        now,
        }).eq("state_code", state_code).execute()

    def mark_failed(self, state_code: str, error: str) -> None:
        retry = self._get_retry_count(state_code) + 1
        self.client.table("pipeline_state").update({
            "status":      "failed",
            "last_error":  error[:2000],
            "retry_count": retry,
            "locked_at":   None,
            "updated_at":  _now(),
        }).eq("state_code", state_code).execute()

    def mark_maintenance_done(self, state_code: str) -> None:
        """Marca el mantenimiento como completado y programa el próximo ciclo."""
        now = _now()
        nxt = _now_plus_days(MAINTENANCE_INTERVAL_DAYS)
        self.client.table("pipeline_state").update({
            "status":               "completed",
            "last_completed_at":    now,
            "locked_at":            None,
            "last_maintenance_run": now,
            "next_maintenance_run": nxt,
            "last_error":           None,
            "updated_at":           now,
        }).eq("state_code", state_code).execute()

    def mark_maintenance_failed(self, state_code: str, error: str) -> None:
        """Registra el fallo y reprograma para reintento en MAINTENANCE_RETRY_HOURS."""
        retry = self._get_retry_count(state_code) + 1
        soon  = _now_plus_hours(MAINTENANCE_RETRY_HOURS)
        self.client.table("pipeline_state").update({
            "status":               "failed",
            "last_error":           error[:2000],
            "retry_count":          retry,
            "locked_at":            None,
            "next_maintenance_run": soon,  # reintento en 1 hora
            "updated_at":           _now(),
        }).eq("state_code", state_code).execute()

    # ── Resumen ───────────────────────────────────────────────────────────────

    def get_summary(self) -> dict[str, int]:
        """Devuelve un dict {status: count} con el resumen de todos los estados."""
        rows = (
            self.client.table("pipeline_state")
            .select("status")
            .execute()
            .data or []
        )
        counts: dict[str, int] = {}
        for row in rows:
            s = row["status"]
            counts[s] = counts.get(s, 0) + 1
        return counts

    # ── Helpers internos ──────────────────────────────────────────────────────

    def _get_retry_count(self, state_code: str) -> int:
        res = (
            self.client.table("pipeline_state")
            .select("retry_count")
            .eq("state_code", state_code)
            .limit(1)
            .execute()
        )
        return (res.data or [{}])[0].get("retry_count", 0)


# ── Detección de cambios reales ───────────────────────────────────────────────

def detect_real_changes(before: list[dict], after_map: dict[str, dict]) -> bool:
    """
    Compara los campos de cada lounge antes y después del enriquecimiento.
    Devuelve True si al menos un campo de un lounge recibió un valor nuevo y válido
    (no vacío, no None) diferente al valor anterior.

    before    — lista de dicts con los campos antes del enriquecimiento
    after_map — {lounge_id: lounge_dict} recargado de Supabase tras enriquecer
    """
    for lounge in before:
        lid   = lounge.get("id")
        after = after_map.get(lid)
        if not after:
            continue
        for field in CHANGE_DETECTION_FIELDS:
            old_val = lounge.get(field)
            new_val = after.get(field)
            # Cambio real: el nuevo valor existe, no está vacío, y es diferente
            if new_val and new_val != old_val:
                return True
    return False


def reload_lounges(db: SupabaseClient, ids: list[str]) -> dict[str, dict]:
    """
    Recarga los campos de CHANGE_DETECTION_FIELDS desde Supabase para los IDs
    indicados. Devuelve {lounge_id: lounge_dict}.
    """
    if not ids:
        return {}
    cols = "id," + ",".join(CHANGE_DETECTION_FIELDS)
    result: dict[str, dict] = {}
    CHUNK = 50
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i : i + CHUNK]
        rows = (
            db.client.table("cigar_lounges")
            .select(cols)
            .in_("id", chunk)
            .execute()
            .data or []
        )
        for row in rows:
            result[row["id"]] = row
    return result


# ── Helpers de fecha/hora ─────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _now_minus_minutes(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()

def _now_plus_hours(hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()

def _now_plus_days(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
