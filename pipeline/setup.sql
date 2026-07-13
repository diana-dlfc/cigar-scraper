-- pipeline/setup.sql
--
-- Crea las tablas necesarias para el Pipeline Orchestrator.
-- Ejecuta este script en Supabase → SQL Editor antes de correr orchestrator.py.
--
-- IMPORTANTE: ejecutar una sola vez. Las cláusulas IF NOT EXISTS evitan errores
-- si ya existen las tablas, así que es seguro volver a correr el script.

-- ── pipeline_state ─────────────────────────────────────────────────────────────
-- Una fila por estado. Registra status, bloqueo, historial y ciclos de mantenimiento.

CREATE TABLE IF NOT EXISTS pipeline_state (
    id                   UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    state_code           TEXT        NOT NULL UNIQUE,
    status               TEXT        NOT NULL DEFAULT 'pending',
                         -- valores posibles: pending | running | completed | failed
    last_started_at      TIMESTAMPTZ,
    last_completed_at    TIMESTAMPTZ,
    locked_at            TIMESTAMPTZ,  -- fijado en NOW() al marcar 'running'; limpiado al terminar
    last_error           TEXT,
    retry_count          INTEGER     NOT NULL DEFAULT 0,
    last_maintenance_run TIMESTAMPTZ, -- última vez que se ejecutó mantenimiento
    next_maintenance_run TIMESTAMPTZ, -- próximo mantenimiento programado (NULL = nunca ejecutado)
    updated_at           TIMESTAMPTZ DEFAULT NOW()
);

-- ── pipeline_config ────────────────────────────────────────────────────────────
-- Fila única (id=1). Controla el modo global del pipeline.

CREATE TABLE IF NOT EXISTS pipeline_config (
    id         INTEGER     PRIMARY KEY DEFAULT 1,
    mode       TEXT        NOT NULL DEFAULT 'enrichment',
               -- valores posibles: enrichment | maintenance
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Inicializar la fila de configuración si no existe
INSERT INTO pipeline_config (id, mode)
VALUES (1, 'enrichment')
ON CONFLICT DO NOTHING;

-- ── Índices ────────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_pipeline_state_status_code
    ON pipeline_state (status, state_code);

CREATE INDEX IF NOT EXISTS idx_pipeline_state_next_maintenance
    ON pipeline_state (next_maintenance_run);
