# Arquitectura del Proyecto cigar-scraper

> Documento de análisis — solo lectura, sin cambios en el código.
> Generado: julio 2026

---

## Índice

1. [Mapa de módulos](#1-mapa-de-módulos)
2. [Módulo Discovery](#2-módulo-discovery)
3. [Módulo Website Enricher](#3-módulo-website-enricher)
4. [Módulo Validación](#4-módulo-validación)
5. [Módulo Exportación](#5-módulo-exportación)
6. [Módulo Persistencia](#6-módulo-persistencia)
7. [Módulo API](#7-módulo-api)
8. [Utilidades y configuración](#8-utilidades-y-configuración)
9. [Herramientas de mantenimiento y debug](#9-herramientas-de-mantenimiento-y-debug)
10. [Mapa de dependencias](#10-mapa-de-dependencias)
11. [Archivos con responsabilidades mezcladas](#11-archivos-con-responsabilidades-mezcladas)
12. [Recomendaciones de desacoplamiento](#12-recomendaciones-de-desacoplamiento)

---

## 1. Mapa de módulos

```
┌─────────────────────────────────────────────────────────────────┐
│                        ENTRY POINTS                             │
│                                                                 │
│  CLI Scrapers          CLI Enrichers           API              │
│  ─────────────         ─────────────           ───              │
│  scrape_all_states     enrich_social           api/main.py      │
│  scrape_google_states  enrich_all              api/routes/*     │
│                        enrich_google_maps                       │
│                        enrich_categories                        │
│                        test_sheets                              │
└──────────────────────────────────────────────────────────────────
           │                    │                    │
           ▼                    ▼                    ▼
┌──────────────┐   ┌─────────────────────┐   ┌──────────────────┐
│  Discovery   │   │  Website Enricher   │   │  Enricher (sync) │
│              │   │                     │   │  (solo vía API)  │
│ scrapers/    │   │  social_enricher.py │   │                  │
│  yelp        │   │  browser_enricher   │   │  pipeline.py     │
│  google_maps │   │  google_places_     │   │  email_finder    │
│  google_     │   │    finder           │   │  social_finder   │
│    places    │   │                     │   │  owner_finder    │
│  grid_search │   │                     │   └──────────────────┘
└──────────────┘   └─────────────────────┘
           │                    │
           ▼                    ▼
┌──────────────────────────────────────────┐
│              Validación                   │
│    validators.py  ←  search_config.py    │
└──────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────┐
│              Persistencia                 │
│         database/supabase_client.py      │
└──────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────┐
│              Exportación                  │
│              sheets/sync.py              │
└──────────────────────────────────────────┘
```

---

## 2. Módulo Discovery

Responsabilidad: encontrar nuevos registros de cigar lounges y guardarlos en Supabase.

| Archivo | Rol | Tecnología |
|---|---|---|
| `scrapers/yelp.py` | Búsqueda por ciudad/estado vía Yelp Fusion API | requests + tenacity |
| `scrapers/google_maps.py` | Scraping de Google Maps (scroll + visita por lugar) | Playwright async |
| `scrapers/google_places.py` | Búsqueda textual + detalles via Google Places API v1 | requests |
| `scrapers/grid_search.py` | Cuadrícula geográfica sobre Places API Nearby Search | requests |
| `scrape_all_states.py` | Runner CLI: itera estados → `yelp.search_city()` | standalone |
| `scrape_google_states.py` | Runner CLI: itera estados → `google_maps.search_city()` | standalone |

Los runners CLI hacen su propia paginación por ciudad y deduplicación en memoria con `seen_source_ids`. No usan `DatabaseDeduplicator`.

La API (`api/routes/scraper.py`) ofrece los mismos scrapers bajo `/scrape/city` y `/scrape/state`, pero a través de FastAPI BackgroundTasks y con `DatabaseDeduplicator`.

---

## 3. Módulo Website Enricher

Responsabilidad: tomar registros ya existentes en la base de datos y completar los campos vacíos: `email`, `facebook_url`, `instagram_url`, `tiktok_url`, `website`, `google_maps_url`, `category`.

Este módulo tiene **dos implementaciones paralelas** con distinto nivel de calidad.

### 3a. Pipeline async (CLI) — implementación activa

| Archivo | Rol |
|---|---|
| `enrichment/social_enricher.py` | Orquestador principal: P1→P2→P3, cache de búsquedas, validación, stats, escritura a DB |
| `enrichment/browser_enricher.py` | Crawler multi-página: extracción de HTML, bypass de overlays, discovery de páginas internas |
| `enrichment/google_places_finder.py` | Enriquecimiento vía Google Places API (website + maps URL) |
| `enrich_social.py` | Runner CLI → `social_enricher.enrich_batch_async()` |
| `enrich_all.py` | Runner CLI legacy: `google_places_finder` + `browser_enricher.scrape_website()` directamente |
| `enrich_google_maps.py` | Runner CLI: navega Maps por nombre/ciudad, extrae URL + categoría |
| `enrich_categories.py` | Runner CLI: navega `google_maps_url` y extrae el label de categoría |

Flujo P1→P2→P3 de `social_enricher.enrich_one()`:
```
1. ¿Tiene website? → scrape_website() en browser_enricher
2. ¿Faltan campos? → una búsqueda en Google → parsear KP + orgánicos
3. ¿Google devolvió un website nuevo? → scrape_website() de ese nuevo URL
```

### 3b. Pipeline sync (API) — implementación secundaria

Usada únicamente por `api/routes/enrichment.py`. No comparte código con el pipeline async.

| Archivo | Rol |
|---|---|
| `enrichment/pipeline.py` | Orquestador: llama a los tres finders síncronos y guarda en DB |
| `enrichment/email_finder.py` | Scrape de website + Hunter.io API para email |
| `enrichment/social_finder.py` | Scrape de website con `requests` para FB/IG/TikTok |
| `enrichment/owner_finder.py` | Scraping de páginas about/team + Google para el nombre del dueño |

---

## 4. Módulo Validación

Responsabilidad: determinar si un lugar es un cigar lounge válido y normalizar sus campos.

| Archivo | Función | Quién lo llama |
|---|---|---|
| `utils/validators.py` | `is_cigar_venue()` — filtra por palabras clave | todos los scrapers, `debug_yelp` |
| `utils/validators.py` | `sanitize_lounge_data()` — limpia y normaliza tipos | todos los scrapers |
| `config/search_config.py` | `CIGAR_KEYWORDS`, `EXCLUDE_KEYWORDS` — las listas de palabras | `validators.py`, scrapers directamente |

La validación está correctamente separada del scraping. Los scrapers importan `is_cigar_venue()` y `sanitize_lounge_data()` para filtrar antes de upsert.

---

## 5. Módulo Exportación

Responsabilidad: exportar la base de datos a Google Sheets.

| Archivo | Rol |
|---|---|
| `sheets/sync.py` | Exportación a Google Sheets via gspread, una hoja por estado. Columnas: nombre, ciudad, estado, dirección, teléfono, website, email, rating, reviews, Google Maps, Instagram, Facebook, TikTok, owner |
| `test_sheets.py` | Runner CLI → `sheets.sync.export_all_states_to_sheets()` |
| `api/routes/sheets.py` | Endpoint `POST /sheets/sync` → `sheets.sync.export_to_sheets()` |

---

## 6. Módulo Persistencia

Responsabilidad: toda la comunicación con Supabase.

| Archivo | Métodos clave |
|---|---|
| `database/supabase_client.py` | `upsert_lounge()`, `get_lounges()`, `get_lounge_by_slug()`, `insert_source()`, `source_exists()`, `create_job()`, `update_job()`, `get_job()` |

Este módulo es el único que toca Supabase directamente. Sin embargo, varios archivos también acceden directamente a `db.client.table(...)` sin pasar por los métodos del cliente, lo que crea acoplamiento directo con el schema de Supabase:

- `enrich_social.py` — `db.client.table("cigar_lounges").select(...)` inline
- `social_enricher.py` — `db.client.table("cigar_lounges").update(...)` inline
- `clean_categories.py` — `db.client.table("cigar_lounges").delete(...)` inline
- `api/routes/enrichment.py` — `db.client.table("cigar_lounges").select(...)` inline

---

## 7. Módulo API

Responsabilidad: exponer las funcionalidades del proyecto como REST API (FastAPI).

| Archivo | Endpoints |
|---|---|
| `api/main.py` | App principal, CORS, health check |
| `api/deps.py` | Singleton `SupabaseClient` como dependencia FastAPI |
| `api/routes/scraper.py` | `GET /scrape/states`, `POST /scrape/city`, `POST /scrape/state` |
| `api/routes/enrichment.py` | `POST /enrich/batch`, `POST /enrich/lounge/{slug}` |
| `api/routes/lounges.py` | `GET /lounges`, `GET /lounges/stats`, `GET /lounges/{slug}` |
| `api/routes/jobs.py` | `GET /jobs/{job_id}` |
| `api/routes/sheets.py` | `POST /sheets/sync` |
| `api/models/requests.py` | Pydantic models para requests |
| `api/models/responses.py` | Pydantic models para responses |

**Bug conocido:** `api/routes/scraper.py` (líneas 94 y 104) importa `from config.cities.florida import FLORIDA_CITIES` — ese archivo no existe. Cualquier llamada a `POST /scrape/state` con fuente `google` o `yelp` falla con `ModuleNotFoundError`. El fix es usar `from config.cities.all_states import STATE_CITIES` y `STATE_CITIES[req.state]`.

---

## 8. Utilidades y configuración

| Archivo | Responsabilidad |
|---|---|
| `config/settings.py` | Variables de entorno: Supabase, Google Places API, Yelp, Sheets |
| `config/states.py` | `US_STATES` dict con nombres y bounding boxes. Helpers `get_state_name()`, `get_state_bbox()`, `get_all_state_abbrs()` |
| `config/cities/all_states.py` | `STATE_CITIES` dict: 51 estados → lista de ciudades principales |
| `config/search_config.py` | Constantes de búsqueda: `CIGAR_KEYWORDS`, `EXCLUDE_KEYWORDS`, parámetros de grid, categorías Yelp |
| `utils/helpers.py` | `slugify()`, `make_slug()`, `normalize_phone()`, `normalize_url()`, `now_utc()`, `safe_float()`, `safe_int()` |
| `utils/deduplicator.py` | `DatabaseDeduplicator`: verifica slugs y source_ids contra Supabase para evitar re-inserción |

---

## 9. Herramientas de mantenimiento y debug

Scripts de uso puntual, sin importadores. Ninguno modifica el código principal.

| Archivo | Función |
|---|---|
| `audit_categories.py` | Estadísticas read-only por categoría |
| `audit_invalid_states.py` | Registros con estado fuera de los 51 válidos |
| `audit_non_cigar.py` | Detección de negocios que no son cigar lounges |
| `clean_categories.py` | Eliminación interactiva por categoría (s/n por categoría) |
| `clean_database.py` | Eliminación batch: nombres junk, direcciones extranjeras, estados inválidos |
| `check_missing_states.py` | Qué estados tienen cero registros en DB |
| `debug_crawler.py` | Debug visual del crawler para un solo negocio (headless=False) |

---

## 10. Mapa de dependencias

Las flechas apuntan desde quien importa hacia quien es importado.

```
scrape_all_states.py     ──► scrapers/yelp.py
                         ──► database/supabase_client.py
                         ──► config/states.py
                         ──► config/cities/all_states.py

scrape_google_states.py  ──► scrapers/google_maps.py
                         ──► database/supabase_client.py
                         ──► config/states.py
                         ──► config/cities/all_states.py

scrapers/google_maps.py  ──► utils/helpers.py
                         ──► utils/validators.py
                         ──► playwright + playwright_stealth

scrapers/yelp.py         ──► utils/helpers.py
                         ──► utils/validators.py
                         ──► config/settings.py
                         ──► config/search_config.py

scrapers/google_places.py ──► utils/helpers.py
                          ──► utils/validators.py
                          ──► config/settings.py
                          ──► config/search_config.py

scrapers/grid_search.py   ──► utils/helpers.py
                          ──► utils/validators.py
                          ──► config/settings.py
                          ──► config/search_config.py
                          ──► config/states.py

─────────────────────────────────────────────────────────

enrich_social.py         ──► enrichment/social_enricher.py
                         ──► database/supabase_client.py
                         ──► [module-level] enrichment/browser_enricher.py (para --debug)

enrichment/social_enricher.py ──► enrichment/browser_enricher.py (deferred import)
                               ──► playwright + playwright_stealth

enrichment/browser_enricher.py ──► playwright + playwright_stealth
                                ──► bs4

enrich_all.py            ──► enrichment/google_places_finder.py
                         ──► enrichment/browser_enricher.py
                         ──► database/supabase_client.py

─────────────────────────────────────────────────────────

api/routes/enrichment.py ──► enrichment/pipeline.py (deferred)
enrichment/pipeline.py   ──► enrichment/email_finder.py
                         ──► enrichment/social_finder.py
                         ──► enrichment/owner_finder.py
                         ──► utils/helpers.py

─────────────────────────────────────────────────────────

sheets/sync.py           ──► config/settings.py
                         ──► config/states.py
                         ──► database/supabase_client.py (deferred)
                         ──► gspread + google.oauth2

utils/validators.py      ──► config/search_config.py
utils/deduplicator.py    (sin dependencias del proyecto)
```

---

## 11. Archivos con responsabilidades mezcladas

### `enrichment/social_enricher.py` — 6 responsabilidades en un mismo archivo

Este es el archivo con mayor mezcla de responsabilidades en el proyecto:

| Responsabilidad | Funciones |
|---|---|
| Orquestación del flujo P1→P2→P3 | `enrich_one()`, `enrich_batch_async()` |
| Extracción de HTML genérico | `_extract_from_html()`, `_build_social_url()` |
| Parsing de páginas de Google | `_extract_from_google_page()`, `_debug_google_organic()` |
| Validación de handles sociales | `_validate_social()`, `_name_tokens()` |
| Escritura directa a Supabase | `db.client.table(...)` dentro de `enrich_one()` |
| Estadísticas y reporte | `_reset_stats()`, `_print_stats()`, `_stats` global |

Además define sus propias versiones de constantes que ya existen en `browser_enricher.py`: `EMAIL_RE`, `SOCIAL_RE`, `SKIP_HANDLES`, `JUNK_EMAIL_DOMAINS`, `_build_social_url()`.

### `enrichment/browser_enricher.py` — 4 responsabilidades

| Responsabilidad | Funciones |
|---|---|
| Extracción de datos desde HTML | `_extract_from_html()` |
| Estrategia de crawl multi-página | `scrape_website()`, `_visit()` |
| Descubrimiento de páginas internas | `_find_priority_links()` |
| Bypass de overlays (cookies, age gates) | `_bypass_overlays()`, `_try_click_text()`, `_try_dob_form()` |

### `scrapers/google_maps.py` — Discovery + Validación mezclados

El scraper llama a `is_cigar_venue()` directamente dentro de su lógica de scroll para pre-filtrar tarjetas antes de visitar cada lugar. Esta validación debería aplicarse en una capa separada (post-scrape, pre-upsert), no dentro del scraper mismo.

### `api/routes/scraper.py` — Routing + Lógica de negocio + Bug

El route handler contiene un bucle `for lounge in all_lounges` con lógica de upsert y manejo de source IDs que debería estar en `SupabaseClient` o en una capa de servicio. Además tiene el bug de `from config.cities.florida import FLORIDA_CITIES` (archivo inexistente).

### Runner scripts (`enrich_social.py`, `scrape_all_states.py`, etc.)

Cada runner implementa su propio bucle de paginación con `.range(offset, offset + PAGE_SIZE)` y su propio filtro de carga inicial. Esta lógica de "cargar lounges con campos vacíos" se repite con variaciones en varios scripts y podría estar en `SupabaseClient`.

### Constantes duplicadas

Las mismas constantes están definidas por separado en `browser_enricher.py` y `social_enricher.py`:

| Constante | En browser_enricher | En social_enricher |
|---|---|---|
| `EMAIL_RE` | ✓ | ✓ |
| `SOCIAL_RE` | ✓ | ✓ |
| `SKIP_HANDLES` | ✓ | ✓ |
| `JUNK_EMAIL_DOMAINS` | ✓ | ✓ |
| `SKIP_DOMAINS` / `SKIP_WEBSITE_DOMAINS` | ✓ | ✓ |
| `_build_social_url()` | ✓ | ✓ |

---

## 12. Recomendaciones de desacoplamiento

### Prioridad alta

**A. Crear `enrichment/constants.py`**

Extraer todas las constantes compartidas hacia un solo módulo:
```python
# enrichment/constants.py
EMAIL_RE = re.compile(...)
SOCIAL_RE = { ... }
SKIP_HANDLES = { ... }
JUNK_EMAIL_DOMAINS = { ... }
SKIP_DOMAINS = { ... }

def build_social_url(field, handle): ...
```
`browser_enricher.py` y `social_enricher.py` importan desde aquí. Elimina la duplicación y garantiza que los dos enrichers usen exactamente los mismos patrones.

**B. Extraer `enrichment/html_extractor.py`**

La función `_extract_from_html()` existe con lógica similar en `browser_enricher.py` (versión completa con JSON-LD, meta tags, Google Maps) y en `social_enricher.py` (versión más simple). Deben converger en un solo módulo puro de extracción de HTML:
```python
# enrichment/html_extractor.py
def extract_from_html(html, base_url, missing, lounge=None, source="website") -> dict: ...
def extract_from_google_page(html, missing, lounge) -> dict: ...
```
Ambos enrichers importan de aquí. `social_enricher.py` elimina su propio `_extract_from_html`.

**C. Corregir el bug de `api/routes/scraper.py`**

Reemplazar:
```python
from config.cities.florida import FLORIDA_CITIES
cities = FLORIDA_CITIES if req.state == "FL" else []
```
Por:
```python
from config.cities.all_states import STATE_CITIES
cities = STATE_CITIES.get(req.state, [])
```

---

### Prioridad media

**D. Separar el bypass de overlays**

`_try_click_text()`, `_try_dob_form()` y `_bypass_overlays()` son autocontenidos y podrían vivir en `enrichment/overlay_bypass.py`. Esto haría `browser_enricher.py` más enfocado en el crawl y facilitaría testear el bypass de forma independiente.

**E. Mover la escritura a DB fuera de `social_enricher.enrich_one()`**

Actualmente `enrich_one()` ejecuta `db.client.table(...).update(...)` directamente. Esto mezcla orquestación con persistencia. Alternativa: retornar el dict de datos encontrados y que el caller (`enrich_batch_async`) o un método de `SupabaseClient` se encargue de guardar.

**F. Añadir métodos de consulta a `SupabaseClient`**

Los runners repiten este patrón cada vez:
```python
while True:
    res = db.client.table("cigar_lounges").select(...).range(offset, offset + PAGE_SIZE - 1).execute()
    ...
```
Centralizarlo en `SupabaseClient.get_lounges_missing_fields(states, fields)` elimina 4 implementaciones casi idénticas.

---

### Prioridad baja

**G. Unificar los dos pipelines de enriquecimiento**

El pipeline sync (`pipeline.py` → `email_finder` + `social_finder` + `owner_finder`) y el pipeline async (`social_enricher` → `browser_enricher`) tienen objetivos solapados pero implementaciones completamente distintas. Opciones:

- Opción 1 (conservadora): mantener ambos, documentar claramente que el async es el activo y el sync solo existe para la API.
- Opción 2 (deseable a futuro): hacer que `api/routes/enrichment.py` llame a `social_enricher.enrich_batch_async()` también. Implicaría ejecutar Playwright dentro del proceso FastAPI, lo cual requiere manejo cuidadoso del event loop.

**H. Mover la lógica de deduplicación de los runners a `SupabaseClient`**

Los runners CLI (`scrape_all_states.py`, `scrape_google_states.py`) deducan con un `set()` local mientras que la API usa `DatabaseDeduplicator`. El resultado varía: el CLI puede insertar duplicados si se ejecuta en dos sesiones distintas. Unificar con `DatabaseDeduplicator` en ambos.
