# pipeline/orchestrator.py
#
# Pipeline Orchestrator — proceso persistente para Railway.
#
# Lee el modo global desde pipeline_config y ejecuta el bucle correcto:
#
#   Modo ENRICHMENT  → enriquece estados en orden alfabético, exporta al terminar cada uno.
#                      Cuando todos los estados están completos, cambia permanentemente a MAINTENANCE.
#
#   Modo MAINTENANCE → ciclo semanal por estado: scraping + enriquecimiento + exportación
#                      condicional (solo si hay cambios reales).
#                      Usa next_maintenance_run por estado; nunca resetea todos a la vez.
#
# No modifica scrapers/, enrichment/ ni ningún archivo existente.
#
# Prerequisito: ejecutar pipeline/setup.sql en Supabase antes de la primera ejecución.
#
# Run:
#   venv\Scripts\python pipeline\orchestrator.py

import asyncio
import sys
from pathlib import Path
from datetime import datetime

# Asegura que la raíz del proyecto esté en sys.path cuando se corre como script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from loguru import logger

from database.supabase_client import SupabaseClient
from config.cities.all_states import STATE_CITIES
from pipeline.pipeline_logger import PipelineLogger
from pipeline.state_manager import StateManager
from pipeline.state_store import StateStore, detect_real_changes, reload_lounges
from pipeline.run_pipeline import _run_scraping, _run_enrichment, _run_export

PAUSE_BETWEEN_STATES = 120   # segundos de pausa entre estados


# ── Bucle de enriquecimiento (Fase 1) ─────────────────────────────────────────

async def run_enrichment_loop(db: SupabaseClient, store: StateStore) -> None:
    """
    Procesa todos los estados pendientes en orden alfabético.
    Por cada estado: enriquecimiento → exportación.
    Al completar todos, cambia el modo global a 'maintenance'.
    """
    _header("ENRICHMENT")

    while True:
        row = store.get_next_for_enrichment()
        if not row:
            break

        state = row["state_code"]
        log   = PipelineLogger(state)
        store.mark_running(state)

        try:
            await _run_enrichment(state, db, log)
            _run_export(state, db, log)
            store.mark_completed(state)
            print(f"\n  ✓ {state} completado.")
        except Exception as exc:
            error = str(exc)
            logger.error(f"[Orchestrator] enrichment {state} FAILED: {error}")
            store.mark_failed(state, error)
            print(f"\n  ✗ {state} falló: {error}")

        # Pausa antes del siguiente estado (no después del último)
        if store.get_next_for_enrichment():
            await _sleep(PAUSE_BETWEEN_STATES, "Siguiente estado en")

    # Verificar que todos completaron antes de cambiar modo
    if store.all_enrichment_complete():
        summary = store.get_summary()
        print(f"\n  ✓ Enriquecimiento inicial completo. Resumen: {summary}")
        print("  Cambiando a modo MAINTENANCE de forma permanente...")
        store.set_mode("maintenance")
    else:
        # Algunos estados siguen en failed — lo indicamos pero no bloqueamos
        summary = store.get_summary()
        print(f"\n  ⚠ Algunos estados quedaron con error: {summary}")
        print("  Continuando a MAINTENANCE; los estados fallidos serán reintentados.")
        store.set_mode("maintenance")


# ── Bucle de mantenimiento (Fase 2) ──────────────────────────────────────────

async def run_maintenance_loop(db: SupabaseClient, store: StateStore) -> None:
    """
    Ciclo permanente: para cada estado cuyo next_maintenance_run ha vencido (o es NULL):
      1. Scraping → nuevos negocios
      2. Snapshot antes de enriquecer
      3. Enriquecimiento incremental (nunca sobreescribe datos válidos)
      4. Comparar antes/después campo a campo
      5. Exportar solo si hay cambios reales
      6. Registrar last_maintenance_run y next_maintenance_run (+7 días)

    Cuando todos los estados están al día, duerme hasta que venza el próximo.
    """
    _header("MAINTENANCE")

    while True:
        row = store.get_next_for_maintenance()

        if not row:
            # Todos los estados están al día — calcular cuánto esperar
            wait_secs = store.seconds_until_next_maintenance()
            if wait_secs <= 0:
                # No hay ninguno programado aún (raro); esperar 1 hora
                wait_secs = 3600
            wait_mins = int(wait_secs // 60)
            print(f"\n  Todos los estados están al día.")
            print(f"  Próximo ciclo de mantenimiento en {wait_mins} min.")
            # Dormir en intervalos de 1 hora para detectar cambios
            await _sleep(min(wait_secs, 3600), "Próximo chequeo en")
            continue

        state = row["state_code"]
        log   = PipelineLogger(state)
        store.mark_running(state)

        try:
            # ── 1. Scraping ────────────────────────────────────────────────
            scraping_stats = _run_scraping(state, "yelp", db, log)
            new_businesses = scraping_stats.get("guardados", 0)

            # ── 2. Snapshot antes de enriquecer ───────────────────────────
            mgr     = StateManager(db, state)
            lounges = mgr.load_for_enrichment()
            before  = [dict(l) for l in lounges]
            ids     = [l["id"] for l in lounges]

            # ── 3. Enriquecimiento incremental ────────────────────────────
            await _run_enrichment(state, db, log)

            # ── 4. Detectar cambios reales campo a campo ──────────────────
            after_map         = reload_lounges(db, ids)
            enrichment_changed = detect_real_changes(before, after_map)
            has_changes       = new_businesses > 0 or enrichment_changed

            # ── 5. Exportar solo si hay cambios ───────────────────────────
            if has_changes:
                why = []
                if new_businesses > 0:
                    why.append(f"{new_businesses} negocios nuevos")
                if enrichment_changed:
                    why.append("campos actualizados")
                print(f"  Cambios detectados ({', '.join(why)}) → exportando...")
                _run_export(state, db, log)
            else:
                print(f"  Sin cambios en {state} → exportación omitida.")

            # ── 6. Programar próximo ciclo ────────────────────────────────
            store.mark_maintenance_done(state)
            print(f"\n  ✓ Mantenimiento {state} completado.")

        except Exception as exc:
            error = str(exc)
            logger.error(f"[Orchestrator] maintenance {state} FAILED: {error}")
            store.mark_maintenance_failed(state, error)
            print(f"\n  ✗ {state} falló (reintento en 1h): {error}")

        # Pausa antes del siguiente estado (no si no hay más pendientes)
        if store.get_next_for_maintenance():
            await _sleep(PAUSE_BETWEEN_STATES, "Siguiente estado en")


# ── Entrada principal ─────────────────────────────────────────────────────────

async def main() -> None:
    print(f"\n{'='*58}")
    print(f"  Pipeline Orchestrator")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*58}")

    db    = SupabaseClient()
    store = StateStore(db)

    # 1. Registrar todos los estados conocidos (idempotente)
    all_states = sorted(STATE_CITIES.keys())
    inserted   = store.initialize(all_states)
    if inserted:
        print(f"\n  Inicializado: {inserted} estado(s) nuevos añadidos.")

    # 2. Recuperar estados bloqueados por reinicios anteriores
    recovered = store.recover_stale_locks()
    if recovered:
        print(f"  Recuperados: {recovered} estado(s) desbloqueados.")

    # 3. Mostrar resumen actual
    summary = store.get_summary()
    mode    = store.get_mode()
    print(f"\n  Estado actual: {summary}")
    print(f"  Modo: {mode.upper()}")

    # 4. Despachar al bucle correcto
    if mode == "enrichment":
        await run_enrichment_loop(db, store)
        # Al terminar enrichment, el modo ya cambió a 'maintenance'
        # Continuar directamente al bucle de mantenimiento
        await run_maintenance_loop(db, store)
    else:
        await run_maintenance_loop(db, store)


# ── Utilidades ────────────────────────────────────────────────────────────────

def _header(mode: str) -> None:
    print(f"\n{'='*58}")
    print(f"  Modo: {mode}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*58}")


async def _sleep(seconds: float, label: str = "Esperando") -> None:
    """Pausa async con contador regresivo visible en la misma línea."""
    remaining = int(seconds)
    step      = 10
    while remaining > 0:
        wait = min(step, remaining)
        print(f"\r  ⏳ {label} {remaining}s...   ", end="", flush=True)
        await asyncio.sleep(wait)
        remaining -= wait
    print(f"\r  ✓ Continuando.                    ")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n  Orchestrator detenido manualmente.")
        sys.exit(0)
