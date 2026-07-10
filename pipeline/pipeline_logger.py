# pipeline/pipeline_logger.py
#
# Logging estructurado para el Pipeline Manager.
# Solo responsable de presentación y tiempos — sin lógica de dominio.

import time
from datetime import datetime


class PipelineLogger:
    """
    Registra el inicio/fin de cada fase del pipeline y genera un resumen final.

    Uso:
        log = PipelineLogger("TX")
        token = log.phase_start("Scraping")
        # ... trabajo ...
        log.phase_end(token, {"saved": 42, "errors": 0})
        log.summary()
    """

    def __init__(self, state: str):
        self.state     = state
        self.started   = time.monotonic()
        self._phases: list[dict] = []
        self._print_header()

    # ── Header ────────────────────────────────────────────────────────────────

    def _print_header(self):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n{'='*58}")
        print(f"  Pipeline Manager — Estado: {self.state}")
        print(f"  Inicio: {ts}")
        print(f"{'='*58}")

    # ── Fases ─────────────────────────────────────────────────────────────────

    def phase_start(self, name: str) -> dict:
        """Marca el inicio de una fase. Devuelve un token para phase_end."""
        print(f"\n▶  {name}...")
        token = {"name": name, "_t0": time.monotonic()}
        return token

    def phase_end(self, token: dict, stats: dict | None = None) -> float:
        """
        Cierra una fase y muestra sus estadísticas.

        stats: dict libre de claves → valores para mostrar.
               Ejemplo: {"guardados": 42, "errores": 0}
        """
        elapsed = time.monotonic() - token["_t0"]
        name    = token["name"]

        print(f"   ✓ {name} completado en {elapsed:.1f}s")
        if stats:
            for k, v in stats.items():
                print(f"     {k:<22}: {v}")

        self._phases.append({
            "name":    name,
            "elapsed": elapsed,
            "stats":   stats or {},
        })
        return elapsed

    def phase_skip(self, name: str, reason: str = ""):
        """Registra una fase que fue omitida."""
        msg = f"   — {name} omitido"
        if reason:
            msg += f" ({reason})"
        print(msg)
        self._phases.append({"name": name, "elapsed": 0, "stats": {"omitido": True}})

    # ── Resumen final ─────────────────────────────────────────────────────────

    def summary(self):
        total = time.monotonic() - self.started
        print(f"\n{'='*58}")
        print(f"  PIPELINE COMPLETADO — {self.state}")
        print(f"{'='*58}")
        for phase in self._phases:
            omitido = phase["stats"].get("omitido", False)
            status  = "–" if omitido else f"{phase['elapsed']:.1f}s"
            print(f"  {phase['name']:<28} {status:>8}")
        print(f"  {'─'*38}")
        print(f"  Total                        {total:>7.1f}s")
        print(f"{'='*58}\n")
