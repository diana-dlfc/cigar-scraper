# pipeline/run_all_states.py
#
# Multi-state sequential pipeline.
# Para cada estado: enriquecimiento → exportación a Sheets → pausa 2 min → siguiente.
#
# Reutiliza completamente run_state_pipeline() de run_pipeline.py.
# No modifica ningún archivo existente.
#
# Run:
#   venv\Scripts\python pipeline\run_all_states.py              # usa la lista STATES de abajo
#   venv\Scripts\python pipeline\run_all_states.py TX FL CA     # override desde CLI

import asyncio
import sys
import time
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

from pipeline.run_pipeline import run_state_pipeline

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURA AQUÍ la lista de estados a procesar y el orden.
# También puedes pasarlos como argumentos CLI para un run puntual.
# ─────────────────────────────────────────────────────────────────────────────

STATES: list[str] = [
    "FL",
    "TX",
    "CA",
    "NY",
    "GA",
    # Agrega o reordena estados aquí
]

PAUSE_BETWEEN_STATES: int = 120  # segundos entre estados


# ── Countdown visible ─────────────────────────────────────────────────────────

def _countdown(seconds: int):
    """Muestra una cuenta regresiva en la misma línea."""
    step = 10
    remaining = seconds
    while remaining > 0:
        print(f"\r  ⏳ Siguiente estado en {remaining:>3}s...   ", end="", flush=True)
        time.sleep(min(step, remaining))
        remaining -= step
    print(f"\r  ✓ Pausa completada.                  ")


# ── Resumen final ─────────────────────────────────────────────────────────────

def _final_summary(
    results:       dict,
    states:        list[str],
    total_elapsed: float,
):
    ok     = [s for s in states if results.get(s, {}).get("status") == "ok"]
    failed = [s for s in states if results.get(s, {}).get("status") == "error"]

    print(f"\n{'='*58}")
    print(f"  MULTI-STATE PIPELINE — RESUMEN FINAL")
    print(f"  Fin: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*58}")
    print(f"  Procesados  : {len(states)}")
    print(f"  Completados : {len(ok)}")
    print(f"  Con errores : {len(failed)}")

    if ok:
        print(f"\n  ✓ OK    : {', '.join(ok)}")
    if failed:
        print(f"  ✗ Error : {', '.join(failed)}")
        for s in failed:
            print(f"    {s}: {results[s].get('error', '(sin detalle)')}")

    mins  = int(total_elapsed // 60)
    secs  = int(total_elapsed % 60)
    print(f"\n  Tiempo total: {mins}m {secs}s")
    print(f"{'='*58}\n")


# ── Runner principal ──────────────────────────────────────────────────────────

async def run_all_states(
    states: list[str],
    pause:  int = PAUSE_BETWEEN_STATES,
) -> dict:
    """
    Procesa una lista de estados de forma secuencial.

    Por cada estado:
      1. Enriquecimiento social (reutiliza enrich_batch_async vía run_state_pipeline)
      2. Exportación a Google Sheets (reutiliza export_all_states_to_sheets)
      3. Espera `pause` segundos
      4. Continúa con el siguiente (incluso si el anterior falló)

    Devuelve un dict {state: {"status": "ok"|"error", ...}}.
    """
    total   = len(states)
    results: dict = {}
    t_start = time.monotonic()

    print(f"\n{'='*58}")
    print(f"  Multi-State Pipeline")
    print(f"  {total} estado(s) en cola: {', '.join(states)}")
    print(f"  Pausa entre estados: {pause}s")
    print(f"  Inicio: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*58}")

    for idx, state in enumerate(states, 1):
        # ── Cabecera de progreso ──────────────────────────────────────────────
        next_state    = states[idx] if idx < total else "—"
        elapsed_total = time.monotonic() - t_start
        mins = int(elapsed_total // 60)
        secs = int(elapsed_total % 60)

        print(f"\n{'─'*58}")
        print(f"  [{idx}/{total}]  Procesando : {state}")
        print(f"           Siguiente  : {next_state}")
        print(f"           Tiempo acum: {mins}m {secs}s")
        print(f"{'─'*58}")

        # ── Ejecutar pipeline del estado ──────────────────────────────────────
        t_state = time.monotonic()
        try:
            stats = await run_state_pipeline(
                state          = state,
                skip_scraping  = True,   # esta etapa no hace scraping
                skip_export    = False,  # exporta a Sheets al terminar
            )
            elapsed_state = time.monotonic() - t_state
            results[state] = {"status": "ok", "stats": stats, "elapsed": elapsed_state}
            print(f"\n  ✓ {state} completado en {elapsed_state/60:.1f} min.")

        except Exception as e:
            elapsed_state = time.monotonic() - t_state
            results[state] = {"status": "error", "error": str(e), "elapsed": elapsed_state}
            print(f"\n  ✗ {state} FALLÓ ({elapsed_state:.0f}s): {e}")

        # ── Pausa entre estados (no después del último) ───────────────────────
        if idx < total:
            _countdown(pause)

    _final_summary(results, states, time.monotonic() - t_start)
    return results


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Si se pasan argumentos, usan esos estados en lugar de la lista STATES.
    # Ejemplo: python pipeline\run_all_states.py TX FL CA
    states_to_run = [s.upper() for s in sys.argv[1:]] if sys.argv[1:] else STATES

    if not states_to_run:
        print("Error: la lista STATES está vacía. Edítala en pipeline/run_all_states.py.")
        sys.exit(1)

    asyncio.run(run_all_states(states_to_run))
