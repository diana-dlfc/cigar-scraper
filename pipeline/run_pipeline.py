# pipeline/run_pipeline.py
#
# Pipeline Manager — orquestador del flujo completo para un estado.
#
# Reutiliza funciones existentes SIN duplicar su lógica:
#   · Scraping  → scrapers.yelp.search_city / scrapers.google_maps.search_city
#   · Upsert    → database.supabase_client.SupabaseClient.upsert_lounge / insert_source
#   · Enrich    → enrichment.social_enricher.enrich_batch_async
#   · Export    → sheets.sync.export_all_states_to_sheets
#
# NINGÚN archivo existente fue modificado.
#
# Run:
#   venv\Scripts\python pipeline\run_pipeline.py TX
#   venv\Scripts\python pipeline\run_pipeline.py TX --source google
#   venv\Scripts\python pipeline\run_pipeline.py TX --skip-scraping
#   venv\Scripts\python pipeline\run_pipeline.py TX --skip-scraping --skip-export
#   venv\Scripts\python pipeline\run_pipeline.py TX --limit 50    # solo 50 lounges en enrichment
#   venv\Scripts\python pipeline\run_pipeline.py TX --only-enrichment

import sys
import asyncio
import time
from dotenv import load_dotenv
load_dotenv()

from loguru import logger

from database.supabase_client import SupabaseClient
from config.cities.all_states import STATE_CITIES
from config.settings import GOOGLE_SHEETS_SPREADSHEET_ID
from pipeline.state_manager import StateManager
from pipeline.pipeline_logger import PipelineLogger


# ── Fase 1: Scraping ──────────────────────────────────────────────────────────

def _run_scraping(state: str, source: str, db: SupabaseClient, log: PipelineLogger) -> dict:
    """
    Scrape cigar lounges para el estado usando Yelp o Google Maps.
    Reutiliza search_city() del scraper correspondiente y db.upsert_lounge().
    No duplica la lógica de upsert — delega en SupabaseClient.
    """
    token = log.phase_start(f"Scraping [{source}] — {state}")

    # Importar el scraper correcto según la fuente
    if source == "google":
        from scrapers.google_maps import search_city
        source_name = "google_maps"
        city_pause  = 2.0
    else:
        from scrapers.yelp import search_city
        source_name = "yelp"
        city_pause  = 1.0

    cities = STATE_CITIES.get(state, [])
    if not cities:
        log.phase_end(token, {"error": f"Sin ciudades configuradas para {state}"})
        return {"saved": 0, "errors": 0, "cities": 0}

    saved       = 0
    errors      = 0
    seen_ids: set[str] = set()

    for city in cities:
        print(f"    {city}...", end=" ", flush=True)
        try:
            results = search_city(city, state)
            print(f"{len(results)} encontrados", end=" ", flush=True)
        except Exception as e:
            print(f"ERROR: {e}")
            logger.warning(f"[Pipeline] scrape error {city}, {state}: {e}")
            errors += 1
            time.sleep(city_pause)
            continue

        city_saved = 0
        for lounge in results:
            source_id = lounge.pop("_source_id", None)
            lounge.pop("_source", None)

            if source_id and source_id in seen_ids:
                continue
            if source_id:
                seen_ids.add(source_id)

            try:
                row = db.upsert_lounge(lounge)
                if row and source_id:
                    try:
                        db.insert_source({
                            "lounge_id":  row["id"],
                            "source":     source_name,
                            "source_id":  source_id,
                            "source_url": lounge.get("google_maps_url") or lounge.get("source_url"),
                            "raw_data":   lounge,
                        })
                    except Exception:
                        pass
                city_saved += 1
                saved += 1
            except Exception as e:
                errors += 1
                logger.warning(f"[Pipeline] upsert error ({city}, {state}): {e}")

        print(f"→ {city_saved} guardados")
        time.sleep(city_pause)

    stats = {"ciudades": len(cities), "guardados": saved, "errores": errors}
    log.phase_end(token, stats)
    return stats


# ── Fase 2: Enriquecimiento social ────────────────────────────────────────────

async def _run_enrichment(
    state: str,
    db:    SupabaseClient,
    log:   PipelineLogger,
    limit: int | None = None,
) -> dict:
    """
    Enriquece los lounges con campos sociales vacíos.
    Delega completamente en enrichment.social_enricher.enrich_batch_async().
    """
    from enrichment.social_enricher import enrich_batch_async

    token = log.phase_start("Enriquecimiento social")

    mgr     = StateManager(db, state)
    lounges = mgr.load_for_enrichment(limit=limit)

    if not lounges:
        log.phase_end(token, {"lounges": 0, "nota": "ninguno necesita enriquecimiento"})
        return {"lounges": 0}

    print(f"    {len(lounges)} lounge(s) a enriquecer")

    await enrich_batch_async(lounges, db)

    # Contar cuántos quedaron completos después del enriquecimiento
    remaining = mgr.count_needing_enrichment()
    stats = {
        "procesados":  len(lounges),
        "aún faltan":  remaining,
    }
    log.phase_end(token, stats)
    return stats


# ── Fase 3: Exportación a Google Sheets ──────────────────────────────────────

def _run_export(state: str, db: SupabaseClient, log: PipelineLogger) -> dict:
    """
    Exporta todos los estados a Google Sheets.
    Delega en sheets.sync.export_all_states_to_sheets().
    """
    from sheets.sync import export_all_states_to_sheets

    token = log.phase_start("Exportación a Google Sheets")

    if not GOOGLE_SHEETS_SPREADSHEET_ID:
        log.phase_end(token, {"error": "GOOGLE_SHEETS_SPREADSHEET_ID no configurado en .env"})
        return {"exported": 0}

    try:
        result = export_all_states_to_sheets(
            spreadsheet_id=GOOGLE_SHEETS_SPREADSHEET_ID,
            db=db,
        )
        stats = {
            "filas exportadas": result.get("total_rows", "?"),
            "estados":          result.get("states_exported", "?"),
            "url":              result.get("url", ""),
        }
        log.phase_end(token, stats)
        return stats
    except Exception as e:
        logger.error(f"[Pipeline] export error: {e}")
        log.phase_end(token, {"error": str(e)})
        return {"exported": 0, "error": str(e)}


# ── Orquestador principal ─────────────────────────────────────────────────────

async def run_state_pipeline(
    state:           str,
    source:          str  = "yelp",   # "yelp" | "google"
    limit:           int  | None = None,
    skip_scraping:   bool = False,
    skip_enrichment: bool = False,
    skip_export:     bool = False,
) -> dict:
    """
    Ejecuta el pipeline completo para un estado.

    Parámetros:
        state           Código de estado (ej. "TX", "FL")
        source          Motor de scraping: "yelp" (default) o "google"
        limit           Limitar cuántos lounges entran al enriquecimiento
        skip_scraping   Omitir fase 1
        skip_enrichment Omitir fase 2
        skip_export     Omitir fase 3

    Devuelve un dict con los stats de cada fase completada.
    """
    db  = SupabaseClient()
    mgr = StateManager(db, state)

    if not mgr.is_valid_state():
        print(f"[Pipeline] Estado inválido: '{state}'")
        sys.exit(1)

    log = PipelineLogger(mgr.state)

    # Conteo inicial
    total_before = mgr.count_total()
    print(f"\n  Lounges en DB antes del pipeline: {total_before}")

    results: dict = {}

    # ── Fase 1 ──────────────────────────────────────────────────────────────
    if skip_scraping:
        log.phase_skip("Scraping", "--skip-scraping")
    else:
        results["scraping"] = _run_scraping(mgr.state, source, db, log)

    # ── Fase 2 ──────────────────────────────────────────────────────────────
    if skip_enrichment:
        log.phase_skip("Enriquecimiento social", "--skip-enrichment")
    else:
        results["enrichment"] = await _run_enrichment(mgr.state, db, log, limit=limit)

    # ── Fase 3 ──────────────────────────────────────────────────────────────
    if skip_export:
        log.phase_skip("Exportación a Google Sheets", "--skip-export")
    else:
        results["export"] = _run_export(mgr.state, db, log)

    log.summary()
    return results


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args(argv: list[str]) -> dict:
    """
    Parsea argumentos de línea de comandos.

    Uso:
        pipeline/run_pipeline.py <STATE> [--source yelp|google]
                                         [--limit N]
                                         [--skip-scraping]
                                         [--skip-enrichment]
                                         [--skip-export]
                                         [--only-enrichment]
    """
    if not argv:
        print("Uso: venv\\Scripts\\python pipeline\\run_pipeline.py <STATE> [opciones]")
        print("       --source yelp|google   Motor de scraping (default: yelp)")
        print("       --limit N              Máximo de lounges a enriquecer")
        print("       --skip-scraping        Omitir fase de scraping")
        print("       --skip-enrichment      Omitir fase de enriquecimiento")
        print("       --skip-export          Omitir exportación a Sheets")
        print("       --only-enrichment      Alias: --skip-scraping --skip-export")
        sys.exit(0)

    cfg = {
        "state":           argv[0].upper(),
        "source":          "yelp",
        "limit":           None,
        "skip_scraping":   False,
        "skip_enrichment": False,
        "skip_export":     False,
    }

    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--source" and i + 1 < len(argv):
            cfg["source"] = argv[i + 1].lower()
            i += 1
        elif arg == "--limit" and i + 1 < len(argv):
            cfg["limit"] = int(argv[i + 1])
            i += 1
        elif arg == "--skip-scraping":
            cfg["skip_scraping"] = True
        elif arg == "--skip-enrichment":
            cfg["skip_enrichment"] = True
        elif arg == "--skip-export":
            cfg["skip_export"] = True
        elif arg == "--only-enrichment":
            cfg["skip_scraping"] = True
            cfg["skip_export"]   = True
        i += 1

    if cfg["source"] not in ("yelp", "google"):
        print(f"[Pipeline] --source debe ser 'yelp' o 'google', no '{cfg['source']}'")
        sys.exit(1)

    return cfg


if __name__ == "__main__":
    cfg = _parse_args(sys.argv[1:])
    asyncio.run(run_state_pipeline(**cfg))
