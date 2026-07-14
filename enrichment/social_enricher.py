# enrichment/social_enricher.py
#
# Enriquecimiento optimizado: email, Facebook, Instagram, TikTok.
#
# Prioridades:
#   1. Scrape del website oficial (si existe)
#   2. Búsqueda por motor para cada red social faltante:
#      Bing → DuckDuckGo → Brave Search
#      Consulta: "Business Name" City State site:platform.com
#
# Nunca sobreescribe campos existentes. Valida antes de guardar.
# Modo debug: establecer DEBUG = True desde enrich_social.py --debug

import asyncio
import base64
import random
import re
import time
from urllib.parse import urlparse, parse_qs
from loguru import logger
from bs4 import BeautifulSoup

CONCURRENCY = 1
_PAGE_DEAD = object()  # sentinel: página muerta, saltar queries restantes
PAUSE_MIN   = 0.3
PAUSE_MAX   = 0.8

# Activar desde enrich_social.py con --debug
DEBUG = False

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.I)

SOCIAL_RE = {
    "facebook_url":  re.compile(r"facebook\.com/([A-Za-z0-9_.@\-]{2,60})(?:[/?]|$)", re.I),
    "instagram_url": re.compile(r"instagram\.com/([A-Za-z0-9_.]{2,30})(?:[/?]|$)", re.I),
    "tiktok_url":    re.compile(r"tiktok\.com/@([A-Za-z0-9_.]{2,30})(?:[/?]|$)", re.I),
    "youtube_url":   re.compile(r"youtube\.com/(?:@|channel/|user/|c/)?([A-Za-z0-9_\-.]{2,80})(?:[/?]|$)", re.I),
}

SKIP_HANDLES = {
    "sharer", "share", "intent", "dialog", "login", "signup", "home",
    "pages", "groups", "events", "p", "reel", "stories", "reels",
    "hashtag", "explore", "about", "watch", "shorts", "create",
}

# Primer segmento del path que indica una ruta funcional, no un perfil.
# Se compara contra el segmento RAW (antes de normalizar) para capturar
# tanto "sharer" como "sharer.php", "embed" como "embed.js", etc.
BLOCKED_SOCIAL_PATHS = frozenset({
    # Ayuda y legales
    "docs", "help", "settings", "privacy", "terms", "policy",
    "recover", "login", "signup",
    # Funciones de plataforma
    "dialog", "plugins", "share", "sharer.php", "profile.php",
    "accounts", "business", "developer", "developers",
    # Contenido (no perfiles)
    "about", "watch", "marketplace", "gaming", "reels", "stories",
    "groups", "events", "blog", "embed", "embed.js", "explore",
    "tr",
})

# Rutas adicionales bloqueadas por plataforma (complementan BLOCKED_SOCIAL_PATHS).
# Usar estas listas para paths que solo aplican a una red especifica.
BLOCKED_FACEBOOK_PATHS = frozenset({
    # Todos los paths de Facebook actualmente bloqueados ya estan en
    # BLOCKED_SOCIAL_PATHS. Agregar aqui exclusivos de Facebook si aparecen.
})

BLOCKED_INSTAGRAM_PATHS = frozenset({
    "reel",   # post individual (/reel/<id>) — distinto de "reels" (seccion)
    "p",      # post individual (/p/<shortcode>)
    "legal",  # pagina legal de la plataforma
})

BLOCKED_YOUTUBE_PATHS = frozenset({
    "results", "feed", "trending", "playlist", "premium",
    "studio", "live", "clips", "hashtag", "creators",
    "copyright", "ads", "policies", "howyoutubeworks",
})

# Mapa host -> set de paths extra bloqueados para esa plataforma.
# Se consulta dentro de _validate_social() despues de BLOCKED_SOCIAL_PATHS.
_PLATFORM_PATH_EXTRAS: dict = {
    "facebook.com":  BLOCKED_FACEBOOK_PATHS,
    "fb.com":        BLOCKED_FACEBOOK_PATHS,
    "instagram.com": BLOCKED_INSTAGRAM_PATHS,
    "youtube.com":   BLOCKED_YOUTUBE_PATHS,
}

# Usernames de plataformas de construccion web y servicios conocidos que
# aparecen como falsos positivos en redes sociales.
# Se compara despues de extraer y normalizar el handle (solo minusculas).
BLOCKED_SOCIAL_USERNAMES = frozenset({
    "wix",
    "wordpress",
    "wordpresscom",
    "shopify",
    "elementor",
    "webflow",
    "weebly",
    "godaddy",
    "helium",
    "squarespace",
    "wixpress",
    "jimdo",
    "bigcommerce",
    "magento",
})

# Formato valido de handle social: empieza con alfanumerico,
# luego letras/digitos/punto/guion/guion_bajo, max 60 chars.
_HANDLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@\-]{0,59}$")

# Extensiones de archivo que aparecen como TLD en falsos positivos del EMAIL_RE
# (ej: "user@image.png", "style@theme.css" captados en texto de recursos)
_JUNK_EMAIL_TLDS = frozenset({
    "png", "jpg", "jpeg", "gif", "svg", "webp", "ico", "bmp", "tiff",
    "css", "scss", "less",
    "js", "ts", "jsx", "tsx", "mjs", "cjs", "min",
    "map", "json", "xml", "yaml", "yml",
    "woff", "woff2", "ttf", "eot", "otf",
    "pdf", "zip", "gz", "tar",
})

# Partes locales siempre basura (independiente del dominio)
_JUNK_EMAIL_LOCALS = frozenset({
    "example", "user", "test", "noreply", "no-reply",
    "donotreply", "do-not-reply",
})

# Dominios basura adicionales (se unen a JUNK_EMAIL_DOMAINS en la validacion)
_JUNK_EMAIL_DOMAINS_EXTRA = frozenset({
    "heliumdev.com", "company.site",
    "sentry-next.wixpress.com",
})

JUNK_EMAIL_DOMAINS = {
    "example.com", "sentry.io", "wixpress.com", "squarespace.com",
    "wordpress.com", "godaddy.com", "cloudflare.com", "w3.org",
    "schema.org", "google.com", "gmail.com", "yahoo.com",
}

STOP_WORDS = {
    "the", "a", "an", "and", "or", "of", "in", "at", "on", "for",
    "cigar", "lounge", "bar", "club", "smoke", "shop", "tobacco",
    "cigars", "smoking",
}


# Dominio objetivo por campo social
_PLATFORM_SITE = {
    "facebook_url":  "facebook.com",
    "instagram_url": "instagram.com",
    "tiktok_url":    "tiktok.com",
    "youtube_url":   "youtube.com",
}

# Etiqueta en lenguaje natural para cada campo — usada en la query de Google
_PLATFORM_QUERY_LABEL = {
    "facebook_url":  "Facebook",
    "instagram_url": "Instagram",
    "tiktok_url":    "TikTok",
    "youtube_url":   "YouTube",
    "email":         "email",
}

# Stats globales (se resetean en enrich_batch_async)
_stats: dict = {}

# Contador atómico de llamadas a _search_google por (lounge_id, field).
# Keyed por (lounge.get("id"), field) — asyncio es single-threaded, no hay race.
_search_call_counts: dict[tuple, int] = {}

# ── Cachés en memoria (solo duran lo que dura el proceso) ─────────────────────
# search_cache : query exacta  → resultado de _search_google()
# website_cache: URL website   → resultado de _scrape_website()
# maps_cache   : URL maps      → resultado de _enrich_from_maps()
_search_cache:  dict[str, str | None] = {}
_website_cache: dict[str, dict]       = {}
_maps_cache:    dict[str, dict]       = {}


# ── Debug helper ──────────────────────────────────────────────────────────────

def _dbg(msg: str):
    if DEBUG:
        print(msg)


# ── Utilidades ────────────────────────────────────────────────────────────────

def _reset_stats():
    _stats.clear()
    _stats.update({
        "processed":         0,
        "website_reused":    0,
        # campos encontrados durante esta ejecución
        "email_found":       0,
        "facebook_found":    0,
        "instagram_found":   0,
        "tiktok_found":      0,
        "youtube_found":     0,
        # campos que estaban vacíos al inicio (se pueblan en enrich_batch_async)
        "email_missing":     0,
        "facebook_missing":  0,
        "instagram_missing": 0,
        "tiktok_missing":    0,
        "youtube_missing":   0,
        "engine_searches":   0,
        "maps_queries":      0,
        "discarded":         0,
        "start_time":        time.time(),
    })
    # Limpiar cachés al inicio de cada ejecución
    _search_cache.clear()
    _website_cache.clear()
    _maps_cache.clear()


def _missing_fields(lounge: dict) -> set[str]:
    fields = {"email", "facebook_url", "instagram_url", "tiktok_url", "youtube_url"}
    return {f for f in fields if not lounge.get(f)}


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _name_tokens(name: str) -> list[str]:
    words = re.findall(r"[a-zA-Z0-9]+", name.lower())
    return [w for w in words if w not in STOP_WORDS and len(w) > 2]


# ── Validación ────────────────────────────────────────────────────────────────

def _validate_social(url: str, lounge: dict, source: str = "organic") -> bool:
    """
    Verifica que la URL social sea un perfil real y pertenezca a este negocio.

    Orden de comprobaciones:
      1. Normalizar y parsear la URL (quitar query, fragment, espacios)
      2a. Rechazar rutas comunes no-perfil (BLOCKED_SOCIAL_PATHS)
      2b. Rechazar rutas especificas de la plataforma (_PLATFORM_PATH_EXTRAS)
      3. Quitar prefijos de plataforma (@, pages/, etc.) para aislar el handle
      4. Validar formato del handle con _HANDLE_RE
      5. Rechazar handles en SKIP_HANDLES o BLOCKED_SOCIAL_USERNAMES
      6. Coincidencia de tokens del nombre del negocio contra el handle
    """
    name = lounge.get("name", "?")

    # 1. Normalizar y parsear
    try:
        # Quitar espacios, asegurar esquema para que urlparse funcione bien
        clean_url = url.strip()
        if not clean_url.startswith(("http://", "https://")):
            clean_url = "https://" + clean_url
        parsed = urlparse(clean_url)
        # Path limpio: sin query ni fragment, en minusculas
        path = parsed.path.lower().strip("/")
    except Exception:
        _dbg(f"    x RECHAZADO -- URL invalida: {url}")
        _stats["discarded"] += 1
        return False

    # 2a. Rechazar rutas funcionales comunes (aplican a todas las plataformas)
    #     Comprobamos el primer segmento raw para capturar "sharer.php", "embed.js", etc.
    raw_first = path.split("/")[0] if path else ""
    raw_first_no_ext = raw_first.split(".")[0]   # "sharer.php" -> "sharer"
    if raw_first in BLOCKED_SOCIAL_PATHS or raw_first_no_ext in BLOCKED_SOCIAL_PATHS:
        _dbg(f"    x RECHAZADO -- ruta funcional comun '{raw_first}': {url}")
        _stats["discarded"] += 1
        return False

    # 2b. Rechazar rutas especificas de la plataforma
    host = re.sub(r"^(www\.|m\.)", "", parsed.netloc.lower())
    platform_paths = _PLATFORM_PATH_EXTRAS.get(host, frozenset())
    if raw_first in platform_paths or raw_first_no_ext in platform_paths:
        _dbg(f"    x RECHAZADO -- ruta funcional de {host} '{raw_first}': {url}")
        _stats["discarded"] += 1
        return False

    # 3. Quitar prefijos de plataforma para aislar el handle
    path = re.sub(r"^(@|pages/|people/|company/|biz/|user/|channel/)", "", path)
    raw_handle = path.split("/")[0].lstrip("@")   # sin normalizar aun

    # 4. Validar formato del handle (caracteres permitidos, longitud)
    if not raw_handle or len(raw_handle) < 2:
        _dbg(f"    x RECHAZADO -- handle vacio o demasiado corto: '{raw_handle}' ({url})")
        _stats["discarded"] += 1
        return False
    if not _HANDLE_RE.match(raw_handle):
        _dbg(f"    x RECHAZADO -- handle con caracteres invalidos: '{raw_handle}' ({url})")
        _stats["discarded"] += 1
        return False

    # 5. Normalizar handle y comprobar listas negras
    handle = _normalize(raw_handle)
    if handle in SKIP_HANDLES:
        _dbg(f"    x RECHAZADO -- handle '{handle}' en SKIP_HANDLES ({url})")
        _stats["discarded"] += 1
        return False
    if handle in BLOCKED_SOCIAL_USERNAMES:
        _dbg(f"    x RECHAZADO -- handle '{handle}' en BLOCKED_SOCIAL_USERNAMES ({url})")
        _stats["discarded"] += 1
        return False

    # 6. Coincidencia de tokens del nombre del negocio
    tokens = _name_tokens(name)

    _dbg(f"    ? Validando [{source}]: {url}")
    _dbg(f"      handle='{handle}' (raw: '{raw_handle}')")
    _dbg(f"      tokens del nombre '{name}': {tokens}")

    if not tokens:
        _dbg(f"      + ACEPTADO -- sin tokens significativos, no se puede refutar")
        return True

    for token in tokens:
        norm_token = _normalize(token)
        if norm_token in handle:
            _dbg(f"      + ACEPTADO -- token '{token}' -> '{norm_token}' en handle '{handle}'")
            return True

    name_norm = _normalize(name)
    if handle in name_norm:
        _dbg(f"      + ACEPTADO -- handle '{handle}' dentro del nombre '{name_norm}'")
        return True

    _dbg(f"      x RECHAZADO -- ningun token {tokens} coincide con handle '{handle}'")
    _dbg(f"        nombre normalizado='{name_norm}'")
    _stats["discarded"] += 1
    return False


# ── Validación de email ───────────────────────────────────────────────────────

def _validate_email(email: str) -> bool:
    """
    Filtro de ultimo nivel para emails antes de guardarlos en DB.
    Complementa JUNK_EMAIL_DOMAINS (que ya se aplica durante la extraccion).

    Rechaza:
      - TLD que son extensiones de archivo (.png, .js, .css, etc.)
      - Dominios basura conocidos (_JUNK_EMAIL_DOMAINS_EXTRA y subdominios)
      - Partes locales siempre basura (noreply, test, example, user, ...)
      - admin@<dominio con "example">
      - Emails sin punto en el dominio o con TLD de 1 caracter
    """
    if not email or "@" not in email:
        return False

    local, _, domain = email.lower().partition("@")
    tld = domain.rsplit(".", 1)[-1] if "." in domain else ""

    # TLD es una extension de archivo — falso positivo del regex
    if tld in _JUNK_EMAIL_TLDS:
        _dbg(f"  [email] rechazado — TLD de archivo: '{tld}' en '{email}'")
        return False

    # Dominio basura (lista ampliada + subdominos)
    all_junk_domains = JUNK_EMAIL_DOMAINS | _JUNK_EMAIL_DOMAINS_EXTRA
    if domain in all_junk_domains:
        _dbg(f"  [email] rechazado — dominio basura: '{domain}'")
        return False
    if any(domain.endswith("." + d) for d in all_junk_domains):
        _dbg(f"  [email] rechazado — subdominio basura: '{domain}'")
        return False

    # Parte local siempre basura
    if local in _JUNK_EMAIL_LOCALS:
        _dbg(f"  [email] rechazado — local basura: '{local}'")
        return False

    # admin@<dominio de ejemplo>
    if local == "admin" and "example" in domain:
        _dbg(f"  [email] rechazado — admin@example: '{email}'")
        return False

    # Debe tener punto en el dominio y TLD de al menos 2 caracteres
    if "." not in domain or len(tld) < 2:
        _dbg(f"  [email] rechazado — dominio mal formado: '{domain}'")
        return False

    return True


# ── Normalización de website ──────────────────────────────────────────────────

# Hosts que indican que el campo `website` es en realidad una red social.
# Se compara contra el host sin "www." ni "m.".
_SOCIAL_HOSTS: dict[str, str] = {
    "facebook.com":   "facebook_url",
    "fb.com":         "facebook_url",
    "instagram.com":  "instagram_url",
    "tiktok.com":     "tiktok_url",
    "vm.tiktok.com":  "tiktok_url",
    "youtube.com":    "youtube_url",
    "youtu.be":       "youtube_url",
}


def _social_field_for_url(url: str) -> str | None:
    """
    Devuelve el nombre del campo social si la URL pertenece a una red social,
    o None si parece ser un sitio web real del negocio.
    """
    if not url:
        return None
    try:
        raw = url if "://" in url else "https://" + url
        host = urlparse(raw).netloc.lower()
        host = re.sub(r"^(www\.|m\.)", "", host)
    except Exception:
        return None
    return _SOCIAL_HOSTS.get(host)


async def _normalize_website(lounge: dict, db) -> dict:
    """
    Si `website` es una URL de red social, la reclasifica al campo correcto:
      - Mueve el valor a facebook_url / instagram_url / tiktok_url
        (solo si ese campo estaba vacío; no sobreescribe)
      - Limpia el campo `website`
      - Persiste el cambio en Supabase de inmediato

    Debe llamarse al inicio de enrich_one(), antes de calcular missing_fields.
    """
    website = lounge.get("website")
    if not website:
        return lounge

    field = _social_field_for_url(website)
    if field is None:
        return lounge  # es un sitio web real, no tocar

    name = lounge.get("name", "?")
    lounge = dict(lounge)  # copia para no mutar el original

    if lounge.get(field):
        # El campo social ya tiene un valor — solo limpiamos website
        logger.info(
            f"[NORM] {name!r}: website={website!r} es {field} "
            f"pero ya tiene valor — solo limpiando website"
        )
        db_update = {"website": None}
    else:
        # Mover website → campo social
        logger.info(f"[NORM] {name!r}: reclasificando website → {field} = {website!r}")
        lounge[field] = website
        db_update = {"website": None, field: website}
        if DEBUG:
            print(f"  [NORM] website reclasificado → {field}: {website}")

    lounge["website"] = None

    if lounge.get("id"):
        try:
            db.client.table("cigar_lounges").update(db_update).eq("id", lounge["id"]).execute()
        except Exception as exc:
            logger.warning(f"[NORM] {name!r}: error actualizando DB: {exc}")

    return lounge


# ── Extracción ────────────────────────────────────────────────────────────────

def _build_social_url(field: str, handle: str) -> str | None:
    if "instagram" in field:
        return f"https://instagram.com/{handle}"
    if "facebook" in field:
        return f"https://facebook.com/{handle}"
    if "tiktok" in field:
        return f"https://tiktok.com/@{handle}"
    if "youtube" in field:
        # Channel IDs start with UC + 22 chars; everything else gets @handle
        if re.match(r"^UC[A-Za-z0-9_\-]{22}$", handle):
            return f"https://youtube.com/channel/{handle}"
        return f"https://youtube.com/@{handle}"
    return None


# ── Playwright: scrape website ────────────────────────────────────────────────

async def _scrape_website(page, url: str, missing: set[str], lounge: dict) -> dict:
    """
    Delega al crawler multi-página de browser_enricher.
    Visita hasta MAX_PAGES páginas internas buscando los campos faltantes.
    La URL normalizada (sin trailing slash, lowercase) se usa como clave de caché.
    """
    from enrichment.browser_enricher import scrape_website as _crawl

    _cache_key = url.rstrip("/").lower()
    if _cache_key in _website_cache:
        print(f"[CACHE] WEBSITE HIT  | {url}")
        return _website_cache[_cache_key]
    print(f"[CACHE] WEBSITE MISS | {url}")

    _dbg(f"  [P1] Crawler iniciado en: {url}")
    logger.info(f"[PW] browser_enricher → scrape_website({url!r}) ...")
    _t_crawl = time.monotonic()
    try:
        result = await asyncio.wait_for(_crawl(url, page, missing=missing), timeout=90)
        logger.info(f"[PW] browser_enricher → scrape_website() completado en {time.monotonic()-_t_crawl:.1f}s")
    except asyncio.TimeoutError:
        logger.warning(f"[PW] scrape_website() TIMEOUT 90s en {url} — página probablemente crasheada, devolviendo {{}}")
        result = {}
    except Exception as e:
        # logger.warning (no debug) para que siempre aparezca aunque no haya --debug
        logger.warning(f"[DIAG] scrape_website EXCEPCIÓN en {url}: {type(e).__name__}: {e}")
        logger.info(f"[PW] browser_enricher → scrape_website() EXCEPCIÓN en {time.monotonic()-_t_crawl:.1f}s")
        result = {}

    _website_cache[_cache_key] = result

    if DEBUG:
        print(f"\n  ┌─ scrape_website() devolvió ──────────────────────────")
        if result:
            for k, v in result.items():
                print(f"  │  {k:<20} = {v}")
        else:
            print(f"  │  (dict vacío)")
        print(f"  └──────────────────────────────────────────────────────")

    return result


# ── P2: Búsqueda en Google Search ─────────────────────────────────────────────

async def _google_search_hrefs(page, query: str) -> list[str]:
    """
    Ejecuta una búsqueda en Google y devuelve todos los <a href> absolutos del DOM.

    Optimizaciones vs versión anterior:
    - Stealth y goto() solo en la primera query (la página ya está en Google después).
    - Cookie dialog solo en primera carga.
    - Sleeps reducidos al mínimo funcional.
    - TIMING logs para identificar cuellos de botella.
    """
    from playwright_stealth import Stealth

    _t_start = time.monotonic()

    try:
        # ── Decidir si hay que navegar a Google o reutilizar la pestaña ────────
        # page.url es "about:blank" tras reset del pool, o una URL de google.com
        # si venimos de una búsqueda anterior.
        _on_google = "google.com" in page.url and "about:blank" not in page.url

        if not _on_google:
            # Primera búsqueda de este negocio (o tras reset): cargar Google.
            # Stealth se registra como init_script → persiste en navegaciones
            # posteriores dentro de la misma página sin llamarlo de nuevo.
            _t0 = time.monotonic()
            await Stealth().apply_stealth_async(page)
            await page.goto(
                "https://www.google.com",
                timeout=20000,
                wait_until="domcontentloaded",
            )
            print(f"[TIMING] goto google.com: {time.monotonic()-_t0:.2f}s")

            # Pausa breve: la homepage necesita un momento antes de ser interactiva
            await asyncio.sleep(random.uniform(0.35, 0.55))

            # Diálogo de cookies — solo aparece en la primera carga
            for _sel in (
                'button[id="L2AGLb"]',
                'button:has-text("Accept all")',
                'button:has-text("Aceptar todo")',
            ):
                try:
                    btn = page.locator(_sel)
                    if await btn.is_visible(timeout=600):
                        await btn.click()
                        await asyncio.sleep(0.20)
                        break
                except Exception:
                    pass
        # else: ya estamos en resultados de Google → solo reutilizar el cuadro
        #        de búsqueda.  Sin goto, sin stealth, sin cookie check.

        # ── Localizar el cuadro de búsqueda visible y enviar ──────────────────
        # Google tiene varios input[name="q"] en el DOM (uno visible, otros
        # hidden). El selector excluye explícitamente type="hidden" y además
        # filtra con .filter(has_text=...) → visible() para evitar cualquier
        # elemento no interactivo.
        #
        # Estrategia:
        #   1. Buscar un campo visible con name="q" que NO sea hidden.
        #   2. Si no existe (página en estado extraño), navegar a google.com y
        #      repetir la búsqueda del campo.  Nunca aumentar el timeout.
        _BOX_SEL = 'textarea[name="q"], input[name="q"]:not([type="hidden"])'

        # ── TIMING granular: localizar ────────────────────────────────────────
        # page.locator() es síncrono (solo crea un objeto Locator, no toca el DOM)
        _t0 = time.monotonic()
        box = page.locator(_BOX_SEL).filter(visible=True).first
        print(f"[TIMING]   locator():    {time.monotonic()-_t0:.3f}s")

        # ── TIMING granular: is_visible() ────────────────────────────────────
        _t0 = time.monotonic()
        try:
            _box_visible = await asyncio.wait_for(box.is_visible(), timeout=2)
        except asyncio.TimeoutError:
            logger.warning("[google] is_visible() TIMEOUT 2s — página no responde, intentando soft reset")
            try:
                await asyncio.wait_for(
                    page.goto("about:blank", timeout=3000, wait_until="commit"),
                    timeout=5,
                )
                logger.info("[google] soft reset OK — página volvió a about:blank")
                return []   # reset OK, página usable pero vacía
            except Exception as _rst_exc:
                logger.warning(f"[google] soft reset FALLÓ ({type(_rst_exc).__name__}) — página permanece muerta")
                return None  # señal de página muerta: saltar queries restantes
        print(f"[TIMING]   is_visible(): {time.monotonic()-_t0:.3f}s  → {_box_visible}")

        if not _box_visible:
            _dbg("[google] search box no visible, recargando google.com")
            await page.goto(
                "https://www.google.com",
                timeout=20000,
                wait_until="domcontentloaded",
            )
            await asyncio.sleep(random.uniform(0.35, 0.55))
            box = page.locator(_BOX_SEL).filter(visible=True).first

        # ── TIMING granular: click() ──────────────────────────────────────────
        _t0 = time.monotonic()
        await box.click(timeout=3000)
        print(f"[TIMING]   click():      {time.monotonic()-_t0:.3f}s")

        # ── TIMING granular: fill() ───────────────────────────────────────────
        _t0 = time.monotonic()
        await box.fill(query)
        print(f"[TIMING]   fill():       {time.monotonic()-_t0:.3f}s")

        await asyncio.sleep(random.uniform(0.12, 0.25))   # simular escritura humana

        _t0 = time.monotonic()
        await box.press("Enter")
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        print(f"[TIMING] Enter + domcontentloaded: {time.monotonic()-_t0:.2f}s")

        # Pausa mínima: los href de resultados ya están en el DOM tras
        # domcontentloaded; Google no usa lazy-load crítico para links de texto.
        await asyncio.sleep(random.uniform(0.20, 0.40))

    except Exception as e:
        logger.debug(f"[google] navegacion fallo '{query}': {e}")
        return []

    print(f"[TIMING] total nav+wait: {time.monotonic()-_t_start:.2f}s  query={query!r}")

    try:
        return await asyncio.wait_for(
            page.evaluate("""
                () => {
                    const seen = new Set();
                    const out  = [];
                    for (const a of document.querySelectorAll('a[href]')) {
                        const h = a.href;
                        if (h && h.startsWith('http') && !seen.has(h)) {
                            seen.add(h);
                            out.push(h);
                        }
                    }
                    return out;
                }
            """),
            timeout=15,
        )
    except asyncio.TimeoutError:
        logger.warning("[google] page.evaluate() TIMEOUT 15s extrayendo hrefs — página probablemente rota")
        return []
    except Exception as e:
        logger.debug(f"[google] evaluate fallo: {e}")
        return []


async def _search_google(
    page,
    query:         str,
    target_domain: str,
    field:         str,
    lounge:        dict,
) -> str | None:
    """
    Ejecuta UNA query en Google y devuelve el primer enlace válido de
    `target_domain`, o None.  Imprime en DEBUG los primeros 10 hrefs con
    el motivo de aceptación/rechazo para los que pertenecen al dominio.
    """
    # ── Caché de búsqueda ────────────────────────────────────────────────────
    if query in _search_cache:
        print(f"[CACHE] SEARCH HIT  | campo={field} | query={query!r}")
        return _search_cache[query]
    print(f"[CACHE] SEARCH MISS | campo={field} | query={query!r}")

    _stats["engine_searches"] += 1

    # ── Log de auditoría por llamada ────────────────────────────────────────
    _lounge_id = lounge.get("id", lounge.get("name", "?"))
    _key = (_lounge_id, field)
    _search_call_counts[_key] = _search_call_counts.get(_key, 0) + 1
    _call_n = _search_call_counts[_key]
    print(
        f"[SEARCH] call={_call_n}"
        f" | {lounge.get('name', '?')!r}"
        f" | campo={field}"
        f" | query={query!r}"
    )

    _dbg(f"  [google] ── query: {query!r}")

    hrefs = await _google_search_hrefs(page, query)
    if hrefs is None:
        # Página muerta — no cachear, propagar sentinel para saltar queries restantes
        return _PAGE_DEAD
    _dbg(f"  [google]    {len(hrefs)} enlaces encontrados en el DOM")

    if DEBUG:
        _dbg(f"  [google]    primeros 10 enlaces:")
        for _i, _h in enumerate(hrefs[:10], 1):
            _dbg(f"    {_i:2d}. {_h}")

    # ── Email ─────────────────────────────────────────────────────────────────
    if field == "email":
        for href in hrefs:
            if href.startswith("mailto:"):
                em = href[7:].split("?")[0].strip().lower()
                ok = _validate_email(em)
                _dbg(f"  [google]    {'✓' if ok else '✗'} mailto:{em} — {'OK' if ok else 'rechazado por _validate_email'}")
                if ok:
                    return em
        try:
            body_text = await page.inner_text("body")
        except Exception:
            body_text = ""
        for m in EMAIL_RE.finditer(body_text):
            em = m.group(0).lower()
            ok = _validate_email(em)
            _dbg(f"  [google]    {'✓' if ok else '✗'} {em} — {'OK' if ok else 'rechazado por _validate_email'}")
            if ok:
                _search_cache[query] = em
                return em
        _dbg(f"  [google]    ✗ email — no encontrado")
        _search_cache[query] = None
        return None

    # ── Sociales ──────────────────────────────────────────────────────────────
    found = None
    for url in hrefs:
        if target_domain not in url.lower():
            continue                                  # silencio: no es el dominio objetivo

        m = SOCIAL_RE[field].search(url)
        if not m:
            _dbg(f"  [google]    ✗ {url[:90]} — sin match de regex ({field})")
            continue

        handle = m.group(1).strip("/")

        if len(handle) <= 1:
            _dbg(f"  [google]    ✗ {url[:90]} — handle demasiado corto: '{handle}'")
            continue

        if handle.lower() in SKIP_HANDLES:
            _dbg(f"  [google]    ✗ {url[:90]} — handle '{handle}' en SKIP_HANDLES")
            continue

        built = _build_social_url(field, handle)
        if not built:
            _dbg(f"  [google]    ✗ {url[:90]} — _build_social_url devolvió None")
            continue

        if not _validate_social(built, lounge, "google"):
            _dbg(f"  [google]    ✗ {url[:90]} — no pasa _validate_social")
            continue

        _dbg(f"  [google]    ✓ {field}: {built}")
        found = built
        break

    if not found:
        _dbg(f"  [google]    ✗ {field} — ningún enlace válido en esta query")
    _search_cache[query] = found
    return found


async def _find_via_google(page, field: str, lounge: dict) -> str | None:
    """
    Prueba hasta 4 queries en Google para encontrar `field`.

    Para redes sociales (facebook_url / instagram_url / tiktok_url):
      1. "{name} {city} {state} {label}"
      2. "{name} {label}"
      3. "site:{domain} \"{name}\""
      4. "site:{domain} {domain_root}"   (solo si hay website)

    Para email:
      1. "{name} {city} email"

    Se detiene en la primera query que devuelva un resultado válido.
    """
    name   = lounge.get("name", "")
    city   = lounge.get("city", "")
    state  = lounge.get("state", "")
    label  = _PLATFORM_QUERY_LABEL.get(field, field.replace("_url", ""))
    domain = _PLATFORM_SITE.get(field, "")

    # Raíz del dominio del website del negocio (para query 4)
    domain_root = ""
    website = lounge.get("website") or ""
    if website:
        try:
            host = urlparse(website).netloc.lower()
            host = re.sub(r"^www\.", "", host)
            root = host.split(".")[0]
            if len(root) >= 3:
                domain_root = root
        except Exception:
            pass

    if field == "email":
        queries = [
            f"{name} {city} email",
            f'"{name}" {state} email OR contact',
            f"{name} {city} {state} chamber commerce",
            f'"{name}" {city} site:yellowpages.com OR site:bbb.org',
        ]
        # Filtrar queries vacías si city/state no están disponibles
        queries = [q for q in queries if q.strip()]
    else:
        queries = [
            " ".join(filter(None, [name, city, state, label])),
            f"{name} {label}",
            f'site:{domain} "{name}"',
        ]
        if domain_root:
            queries.append(f"site:{domain} {domain_root}")

    _dbg(f"  [P2] {field}: {len(queries)} quier{'y' if len(queries) == 1 else 'ies'} a intentar")

    for idx, q in enumerate(queries, 1):
        _dbg(f"  [P2] {field} [{idx}/{len(queries)}]: {q!r}")
        result = await _search_google(page, q, domain, field, lounge)
        if result is _PAGE_DEAD:
            logger.warning(f"[P2] página muerta en campo={field} query={idx} — saltando queries restantes")
            return _PAGE_DEAD
        if result:
            _dbg(f"  [P2] ✓ {field} encontrado con query {idx}: {result}")
            return result
        await asyncio.sleep(random.uniform(PAUSE_MIN, PAUSE_MAX))

    _dbg(f"  [P2] ✗ {field} — agotadas las {len(queries)} queries")
    return None








# ── P1.5: Inspeccion de Google Maps ──────────────────────────────────────────

async def _enrich_from_maps(page, lounge: dict, missing: set[str]) -> dict:
    """
    Fase P1.5: extrae links de la ficha de Google Maps del negocio.

    Condicion de uso: el lounge tiene google_maps_url Y aun faltan campos.

    Reutiliza exactamente los mismos validadores del pipeline:
      - _validate_social() para Facebook, Instagram, TikTok
      - _validate_email()  para email
      - SOCIAL_RE / EMAIL_RE para extraccion
      - _build_social_url() para construir URL canonica

    Los resultados se devuelven con el mismo formato que P1 (scrape website)
    para que el caller aplique la misma logica de merge.
    """
    from playwright_stealth import Stealth

    maps_url = lounge.get("google_maps_url")
    if not maps_url or not missing:
        return {}

    # ── Caché de Maps ────────────────────────────────────────────────────────
    _maps_key = maps_url.strip().rstrip("/")
    if _maps_key in _maps_cache:
        print(f"[CACHE] MAPS HIT  | {maps_url}")
        return _maps_cache[_maps_key]
    print(f"[CACHE] MAPS MISS | {maps_url}")

    name = lounge.get("name", "?")
    logger.info(f"[P1.5] {name!r} | maps={maps_url!r} | missing={sorted(missing)}")

    # --- DIAG: URL abierta ---
    print(f"[MAPS-DIAG] ============================================================")
    print(f"[MAPS-DIAG] Negocio : {name!r}")
    print(f"[MAPS-DIAG] URL     : {maps_url}")
    print(f"[MAPS-DIAG] Faltan  : {sorted(missing)}")

    try:
        await Stealth().apply_stealth_async(page)
        logger.info(f"[PW] _enrich_from_maps → page.goto({maps_url!r}) ...")
        await page.goto(maps_url, timeout=9000, wait_until="domcontentloaded")
        logger.info(f"[PW] _enrich_from_maps → page.goto() OK")
        print(f"[MAPS-DIAG] page.goto() completado -- esperando 2.5s...")
        await asyncio.sleep(2.5)
        logger.info(f"[PW] _enrich_from_maps → sleep(2.5) OK — entrando a page.evaluate() scroll ...")

        # Scroll dentro del panel para revelar secciones (website, redes, etc.)
        try:
            scroll_result = await asyncio.wait_for(
                page.evaluate("""
                    () => {
                        const candidates = [
                            ['div[role="main"]',  document.querySelector('div[role="main"]')],
                            ['.m6QErb',           document.querySelector('.m6QErb')],
                            ['#QA0Szd',           document.querySelector('#QA0Szd')],
                            ['.siAUzd-neVct',     document.querySelector('.siAUzd-neVct')],
                        ];
                        for (const [sel, el] of candidates) {
                            if (el) {
                                el.scrollTop = 3000;
                                return 'scrolled:' + sel;
                            }
                        }
                        window.scrollTo(0, 3000);
                        return 'scrolled:window';
                    }
                """),
                timeout=15,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"[PW] _enrich_from_maps → page.evaluate() TIMEOUT 15s para {name!r} "
                f"— página probablemente rota, _reset() la reemplazará al liberar"
            )
            return {}
        logger.info(f"[PW] _enrich_from_maps → page.evaluate() scroll OK: {scroll_result}")
        print(f"[MAPS-DIAG] Scroll  : {scroll_result}")
        logger.info(f"[PW] _enrich_from_maps → sleep(1.0) ...")
        await asyncio.sleep(1.0)
        logger.info(f"[PW] _enrich_from_maps → sleep(1.0) OK — entrando a page.content() ...")
        try:
            html = await asyncio.wait_for(page.content(), timeout=20)
        except asyncio.TimeoutError:
            logger.warning(
                f"[PW] _enrich_from_maps → page.content() TIMEOUT 20s para {name!r} "
                f"— página probablemente rota, _reset() la reemplazará al liberar"
            )
            return {}
        logger.info(f"[PW] _enrich_from_maps → page.content() OK — {len(html)} chars")
        print(f"[MAPS-DIAG] HTML len: {len(html)} chars")
    except Exception as exc:
        print(f"[MAPS-DIAG] ERROR navegando Maps: {exc}")
        logger.warning(f"[P1.5] Error navegando Maps para {name!r}: {exc}")
        return {}

    _stats["maps_queries"] += 1
    soup = BeautifulSoup(html, "lxml")

    # --- Contar hrefs RAW en el DOM (antes de filtrar) ---
    all_dom_hrefs = soup.find_all("a", href=True)
    http_dom_hrefs = [a.get("href","") for a in all_dom_hrefs if a.get("href","").startswith("http")]
    print(f"[MAPS-DIAG] <a href> totales en DOM : {len(all_dom_hrefs)}")
    print(f"[MAPS-DIAG] <a href> con http       : {len(http_dom_hrefs)}")

    # Recopilar y deduplicar todos los hrefs del panel
    raw: list[str] = []

    for a in all_dom_hrefs:
        href = a.get("href", "").strip()
        if not href.startswith("http"):
            continue
        # Decodificar redirects de Google (/url?q=https://...)
        if "google.com/url" in href or "/url?q=" in href:
            try:
                qs   = parse_qs(urlparse(href).query)
                real = qs.get("q", qs.get("url", [""]))[0]
                if real.startswith("http"):
                    href = real
            except Exception:
                pass
        raw.append(href)

    # Complementar con URLs capturadas por regex en data-attributes del HTML
    regex_urls = []
    for m in re.finditer(r'https?://[A-Za-z0-9.\-_%/?#=&@:,;+!~*\'()]{6,}', html):
        regex_urls.append(m.group(0).rstrip(".,;)'\""))
    raw.extend(regex_urls)
    print(f"[MAPS-DIAG] URLs por regex en HTML  : {len(regex_urls)}")

    # Deduplicar conservando el primer orden de aparicion
    seen_keys: set[str] = set()
    links: list[str] = []
    for lnk in raw:
        key = lnk.split("?")[0].rstrip("/#").lower()
        if key not in seen_keys:
            seen_keys.add(key)
            links.append(lnk)

    print(f"[MAPS-DIAG] Total links unicos      : {len(links)}")

    # --- DIAG: primeros 30 links ---
    print(f"[MAPS-DIAG] Primeros 30 links:")
    for _i, _lnk in enumerate(links[:30], 1):
        print(f"[MAPS-DIAG]   {_i:2}. {_lnk}")

    # --- DIAG: si menos de 5 links, volcar inicio del HTML ---
    if len(links) < 5:
        print(f"[MAPS-DIAG] ALERTA: menos de 5 links -- primeros 3000 chars del HTML:")
        print(html[:3000])

    # --- Conteos por categoria (sin ejecutar validadores aun) ---
    _cnt_fb  = sum(1 for lnk in links if "facebook.com" in lnk.lower())
    _cnt_ig  = sum(1 for lnk in links if "instagram.com" in lnk.lower())
    _cnt_tt  = sum(1 for lnk in links if "tiktok.com"    in lnk.lower())
    _cnt_yt  = sum(1 for lnk in links if "youtube.com"   in lnk.lower() or "youtu.be" in lnk.lower())
    _cnt_em  = len(list(EMAIL_RE.finditer(html)))
    _WS_SKIP_HOSTS = {
        "google.", "goo.gl", "maps.app", "facebook.", "fb.com",
        "instagram.", "tiktok.", "youtube.", "youtu.be", "yelp.", "tripadvisor.",
    }
    _cnt_ws = sum(
        1 for lnk in links
        if "." in urlparse(lnk).netloc
        and not any(s in urlparse(lnk).netloc.lower() for s in _WS_SKIP_HOSTS)
        and "google.com" not in urlparse(lnk).netloc.lower()
    )
    print(f"[MAPS-DIAG] Conteos por categoria:")
    print(f"[MAPS-DIAG]   facebook  : {_cnt_fb}")
    print(f"[MAPS-DIAG]   instagram : {_cnt_ig}")
    print(f"[MAPS-DIAG]   tiktok    : {_cnt_tt}")
    print(f"[MAPS-DIAG]   youtube   : {_cnt_yt}")
    print(f"[MAPS-DIAG]   email     : {_cnt_em}")
    print(f"[MAPS-DIAG]   website   : {_cnt_ws}")

    found: dict = {}

    # Email
    if "email" in missing:
        _email_candidates = [m.group(0).lower() for m in EMAIL_RE.finditer(html)]
        for _ec in _email_candidates[:10]:
            _ok = _validate_email(_ec)
            print(f"[MAPS-DIAG]   email {'ACEPTADO' if _ok else 'RECHAZADO'}: {_ec}")
            if _ok and "email" not in found:
                found["email"] = _ec
    else:
        _email_candidates = []

    # Redes sociales (Facebook, Instagram, TikTok)
    for field in ("facebook_url", "instagram_url", "tiktok_url", "youtube_url"):
        if field not in missing or field in found:
            continue
        domain = _PLATFORM_SITE[field]
        _platform_links = [lnk for lnk in links if domain in lnk.lower()]
        for lnk in _platform_links:
            m = SOCIAL_RE[field].search(lnk)
            if not m:
                print(f"[MAPS-DIAG]   {field} SKIP (sin regex match): {lnk}")
                continue
            handle = m.group(1).strip("/")
            url = _build_social_url(field, handle)
            if url and _validate_social(url, lounge, "maps"):
                found[field] = url
                print(f"[MAPS-DIAG]   {field} ACEPTADO: {url}")
                break
            else:
                print(f"[MAPS-DIAG]   {field} RECHAZADO por _validate_social: {url}")

    # Website (solo si el lounge no tenia ninguno)
    if "website" in missing:
        _WS_SKIP = {
            "google.", "goo.gl", "maps.app", "maps.google.",
            "facebook.", "fb.com", "instagram.", "tiktok.",
            "youtube.", "youtu.be", "yelp.", "tripadvisor.",
        }
        for lnk in links:
            try:
                host = urlparse(lnk).netloc.lower()
            except Exception:
                continue
            if not host or "." not in host:
                continue
            if any(s in host for s in _WS_SKIP):
                continue
            if "google.com" in host:
                continue
            found["website"] = lnk.split("?")[0].rstrip("/")
            print(f"[MAPS-DIAG]   website ACEPTADO: {found['website']}")
            break

    if found:
        logger.info(f"[P1.5] aporte de Maps para {name!r}: {list(found.keys())}")
        print(f"[MAPS-DIAG] RESULTADO: {list(found.keys())}")
    else:
        print(f"[MAPS-DIAG] RESULTADO: sin datos")

    print(f"[MAPS-DIAG] ============================================================")
    _maps_cache[_maps_key] = found
    return found


# ── Enriquecimiento de un lounge ──────────────────────────────────────────────

async def enrich_one(lounge: dict, browser, db, cache: dict) -> dict:
    # Fase 0: si `website` es una red social, reclasificar antes de todo
    lounge = await _normalize_website(lounge, db)

    name    = lounge.get("name", "")
    missing         = _missing_fields(lounge)
    initial_missing = missing.copy()   # para filtrar el UPDATE final sin mutaciones

    # Limpia los contadores de _search_google para este lounge (evita acumulación
    # entre corridas si se reutiliza el proceso).
    _lounge_id = lounge.get("id", name)
    for _f in ("facebook_url", "instagram_url", "tiktok_url", "email"):
        _search_call_counts.pop((_lounge_id, _f), None)

    if not missing:
        return {}

    if DEBUG:
        print(f"\n{'─'*55}")
        print(f"  {name} | {lounge.get('city')}, {lounge.get('state')}")
        print(f"  Faltan: {sorted(missing)}")
        print(f"  Website: {lounge.get('website') or '(ninguno)'}")

    found   = {}
    website = lounge.get("website")

    # ── [DIAG] Checkpoint 0: estado inicial ──────────────────────────────────
    logger.info(f"[DIAG] ▶ {name!r} | missing={sorted(missing)} | website={website!r}")

    crawler_page = None
    maps_page    = None
    google_page  = None
    try:
        logger.info(f"[PW] {name!r} → acquire_crawler() ...")
        crawler_page = await browser.acquire_crawler()
        logger.info(f"[PW] {name!r} → acquire_crawler() OK")
        logger.info(f"[PW] {name!r} → acquire_maps() ...")
        maps_page    = await browser.acquire_maps()
        logger.info(f"[PW] {name!r} → acquire_maps() OK")
        logger.info(f"[PW] {name!r} → acquire_google() ...")
        google_page  = await browser.acquire_google()
        logger.info(f"[PW] {name!r} → acquire_google() OK")
        # ── PRIORIDAD 1: Scrape del website existente ────────────────────────
        if website:
            _stats["website_reused"] += 1
            _dbg(f"  [P1] Scrapeando website: {website}")
            scraped = await _scrape_website(crawler_page, website, missing, lounge)

            # ── [DIAG] Checkpoint 1: lo que devolvió scrape_website() ─────────
            logger.info(f"[DIAG] scrape_website() → {scraped}")

            for k, v in scraped.items():
                if not v:
                    continue
                # Validacion extra para email
                if k == "email" and not _validate_email(v):
                    logger.info(f"[DIAG] campo 'email' descartado del scrape — rechazado por _validate_email: '{v}'")
                    if DEBUG:
                        _dbg(f"  [P1] 'email' ignorado — no pasa _validate_email: {v}")
                    continue
                # Guardar todo (no filtrar por missing aqui — el filtro va en el UPDATE)
                if k not in found:
                    found[k] = v
                    if DEBUG:
                        in_missing = k in missing
                        _dbg(f"  [P1] '{k}' guardado{'' if in_missing else ' (campo ya existia en DB, se conserva para referencia)'}: {v}")

            # ── [DIAG] Checkpoint 2: found después de fusionar P1 ─────────────
            logger.info(f"[DIAG] found después de P1 = {found} | siguen faltando: {sorted(missing - set(found))}")
            missing -= set(found.keys())
            _dbg(f"  [missing] Después de Website:     missing={sorted(missing)}")

            if DEBUG:
                print(f"\n  ┌─ found después de P1 ────────────────────────────────")
                if found:
                    for k, v in found.items():
                        print(f"  │  {k:<20} = {v}")
                else:
                    print(f"  │  (vacío — scrape no aportó nada nuevo)")
                print(f"  │  Siguen faltando: {sorted(missing)}")
                print(f"  └──────────────────────────────────────────────────────")
        else:
            logger.info(f"[DIAG] P1 omitida — lounge sin website")

        # ── PRIORIDAD 1.5: Inspeccion de Google Maps ────────────────────────
        if missing and lounge.get("google_maps_url"):
            # Incluir website en la busqueda de Maps si el lounge no tenia ninguno
            maps_target = set(missing)
            if not lounge.get("website"):
                maps_target.add("website")

            _dbg(f"  [missing] Antes de Google Maps:   missing={sorted(missing)}")
            _dbg(f"  [P1.5] Consultando Google Maps...")
            maps_result = await _enrich_from_maps(maps_page, lounge, maps_target)
            logger.info(f"[DIAG] Maps → {maps_result}")

            for k, v in maps_result.items():
                if not v:
                    continue
                if k == "website":
                    # Website no esta en missing_fields pero lo guardamos si faltaba
                    if not lounge.get("website") and "website" not in found:
                        found["website"] = v
                        lounge = {**lounge, "website": v}
                elif k in missing:
                    if k == "email" and not _validate_email(v):
                        _dbg(f"  [P1.5] email descartado por _validate_email: {v}")
                        continue
                    found[k] = v

            missing -= set(found.keys())
            _dbg(f"  [missing] Después de Google Maps: missing={sorted(missing)}")

            if DEBUG and maps_result:
                maps_found = {k: v for k, v in found.items() if k in maps_result}
                print(f"\n  ┌─ found despues de P1.5 (Maps) ───────────────────────")
                if maps_found:
                    for k, v in maps_found.items():
                        print(f"  │  {k:<20} = {v}")
                else:
                    print(f"  │  (Maps no aperto datos nuevos)")
                print(f"  │  Siguen faltando: {sorted(missing)}")
                print(f"  └──────────────────────────────────────────────────────")

        # ── PRIORIDAD 2: Google Search para campos faltantes ─────────────────
        _P2_FIELDS = ("facebook_url", "instagram_url", "tiktok_url", "youtube_url", "email")
        _dbg(f"  [missing] Antes de Google Search: missing={sorted(missing)}")

        # Lista exacta de campos que aún faltan — se calcula DESPUÉS de Website y
        # Maps para reflejar su estado real. El loop itera solo sobre estos campos;
        # no hay snapshots ni guards internos que puedan desincronizarse.
        p2_needed = [f for f in _P2_FIELDS if f in missing]

        # ── AUDITORÍA: estado justo antes de P2 ──────────────────────────────
        _p2_searches_start = _stats["engine_searches"]
        print(
            f"[AUDIT] ▶ {name!r}"
            f" | missing_pre_P2={sorted(missing)}"
            f" | campos_a_buscar={p2_needed}"
        )

        if not p2_needed:
            _dbg(f"  [P2] omitido — todos los campos P2 ya cubiertos por Website/Maps")
        else:
            _dbg(f"  [P2] {len(p2_needed)} campo(s) a buscar: {p2_needed}")

            for idx, field in enumerate(p2_needed):
                _dbg(f"  [P2] [{idx + 1}/{len(p2_needed)}] → {field} | missing={sorted(missing)}")

                # ── AUDITORÍA: estado de missing justo antes de la búsqueda ─
                # El conteo real de queries se lee de _search_call_counts DESPUÉS
                # de la llamada — es por (lounge_id, field) y no se contamina con
                # workers paralelos.
                _lounge_key = (lounge.get("id", lounge.get("name")), field)
                _calls_before = _search_call_counts.get(_lounge_key, 0)
                print(
                    f"[AUDIT]   campo={field}"
                    f" | missing_antes={sorted(missing)}"
                )

                result = await _find_via_google(google_page, field, lounge)
                if result is _PAGE_DEAD:
                    logger.warning(f"[P2] página muerta detectada en campo={field} — saltando todos los campos restantes")
                    break

                _queries_this_field = _search_call_counts.get(_lounge_key, 0) - _calls_before
                print(
                    f"[AUDIT]   campo={field}"
                    f" | queries_lanzadas={_queries_this_field}"
                    f" | resultado={'✓ ' + result if result else '✗ no encontrado'}"
                )

                if result:
                    found[field] = result
                    missing.discard(field)
                    _dbg(f"  [P2] ✓ {field} encontrado → missing={sorted(missing)}")
                else:
                    _dbg(f"  [P2] ✗ {field} no encontrado → missing={sorted(missing)}")
                # Pausar entre campos, pero NO después del último
                if idx < len(p2_needed) - 1:
                    await asyncio.sleep(random.uniform(PAUSE_MIN, PAUSE_MAX))

            _dbg(f"  [missing] Después de Google Search: missing={sorted(missing)}")

        # ── AUDITORÍA: resumen por negocio ────────────────────────────────────
        _p2_total = _stats["engine_searches"] - _p2_searches_start
        print(
            f"[AUDIT] ◀ {name!r}"
            f" | búsquedas_P2={_p2_total}"
            f" | encontrados={[f for f in p2_needed if f in found]}"
            f" | missing_post_P2={sorted(missing)}"
        )

        # ── [DIAG] Checkpoint 3: found despues de P2 ──────────────────────
        p2_all_fields = {"facebook_url", "instagram_url", "tiktok_url", "email"}
        p2_keys = set(found) & p2_all_fields
        logger.info(f"[DIAG] found despues de P2 = { {k: found[k] for k in p2_keys} } | discarded_total={_stats['discarded']}")

        if DEBUG:
            print(f"\n  ┌─ found despues de P2 ────────────────────────────────")
            p2_found = {k: v for k, v in found.items() if k in p2_all_fields}
            if p2_found:
                for k, v in p2_found.items():
                    print(f"  │  {k:<20} = {v}")
            else:
                print(f"  │  (vacio -- motores no aportaron nada)")
            print(f"  │  Siguen faltando: {sorted(missing)}")
            print(f"  └──────────────────────────────────────────────────────")

    finally:
        if crawler_page is not None:
            logger.info(f"[PW] {name!r} → release_crawler() ...")
            await browser.release_crawler(crawler_page)
            logger.info(f"[PW] {name!r} → release_crawler() OK")
        if maps_page is not None:
            logger.info(f"[PW] {name!r} → release_maps() ...")
            await browser.release_maps(maps_page)
            logger.info(f"[PW] {name!r} → release_maps() OK")
        if google_page is not None:
            logger.info(f"[PW] {name!r} → release_google() ...")
            await browser.release_google(google_page)
            logger.info(f"[PW] {name!r} → release_google() OK")

    # ── Guardar en DB ─────────────────────────────────────────────────────────
    if found:
        # Solo escribir columnas que faltaban originalmente (no sobreescribir datos existentes)
        update = {k: v for k, v in found.items() if v and (k in initial_missing or k == "website")}
        # Descartar URLs de schema.org — son metadatos de JSON-LD, no sitios reales
        if update.get("website") and "schema.org" in update.get("website", ""):
            logger.warning(f"[FILTER] website descartado por ser schema.org: {update['website']!r}")
            del update["website"]
        # Descartar URLs genéricas/pixel de Facebook/Instagram/TikTok — no son páginas de negocio
        _FAKE_SOCIAL = {
            "facebook_url":  ["facebook.com/tr", "facebook.com/recover", "facebook.com/login",
                               "facebook.com/help", "facebook.com/policies", "facebook.com/privacy"],
            "instagram_url": ["instagram.com/accounts", "instagram.com/legal"],
            "tiktok_url":    ["tiktok.com/legal", "tiktok.com/privacy"],
            "youtube_url":   ["youtube.com/results", "youtube.com/feed", "youtube.com/watch",
                               "youtube.com/trending", "youtube.com/premium", "youtube.com/about"],
        }
        for _field, _bad_patterns in _FAKE_SOCIAL.items():
            _val = update.get(_field, "")
            if _val and any(p in _val for p in _bad_patterns):
                logger.warning(f"[FILTER] {_field} descartado por ser URL genérica: {_val!r}")
                del update[_field]
        if update:
            from datetime import datetime, timezone
            update["enriched"] = True
            update["last_enriched_at"] = datetime.now(timezone.utc).isoformat()

        # ── [DIAG] Checkpoint 4: dict que va a Supabase ───────────────────────
        logger.info(f"[DIAG] update → Supabase = {update} | lounge_id={lounge.get('id')!r}")

        if DEBUG:
            print(f"\n  ┌─ update enviado a Supabase ───────────────────────────")
            for k, v in update.items():
                print(f"  │  {k:<20} = {v}")
            print(f"  │  Total columnas en el update: {len(update)}")
            print(f"  └──────────────────────────────────────────────────────")

        try:
            logger.info(f"[PW] Supabase update → id={lounge.get('id')!r} campos={list(update.keys())} ...")
            try:
                resp = (
                    db.client.table("cigar_lounges")
                    .update(update)
                    .eq("id", lounge["id"])
                    .execute()
                )
            except Exception as _db_exc:
                _exc_str = str(_db_exc)
                if "last_enriched_at" in _exc_str or "PGRST204" in _exc_str:
                    logger.warning(
                        f"[PW] Columna 'last_enriched_at' no existe en Supabase "
                        f"— reintentando sin ella para {name!r}"
                    )
                    _update_retry = {k: v for k, v in update.items() if k != "last_enriched_at"}
                    resp = (
                        db.client.table("cigar_lounges")
                        .update(_update_retry)
                        .eq("id", lounge["id"])
                        .execute()
                    )
                else:
                    raise
            logger.info(f"[PW] Supabase update → OK  filas={len(resp.data or [])}")
            # ── [DIAG] Checkpoint 5: respuesta de Supabase ───────────────────

            logger.info(f"[DIAG] Supabase resp.data = {resp.data!r}")

            if DEBUG:
                rows = resp.data or []
                print(f"  Supabase respondio: {len(rows)} fila(s) afectada(s)")
                if rows:
                    updated_row = rows[0]
                    written = {k: updated_row.get(k) for k in update if k in updated_row}
                    for k, v in written.items():
                        match_sym = "OK" if str(v) == str(update[k]) else "DIFIERE"
                        print(f"  {k:<20} = {v}  {match_sym}")
                    if not written:
                        print("  (Supabase no devolvio el registro -- normal sin .select())")
        except Exception as e:
            logger.warning(f"DB update failed for {name}: {e}")
            if DEBUG:
                print(f"  ERROR en DB update: {e}")
    else:
        logger.info(f"[DIAG] found vacio -- Supabase update omitido para {name!r}")
        if DEBUG:
            print("  Sin datos nuevos -- no se envio nada a Supabase")

    # Actualizar estadisticas
    _stats["processed"] += 1
    for field in ("email", "facebook_url", "instagram_url", "tiktok_url", "youtube_url"):
        if found.get(field):
            key = field.split("_")[0] + "_found"
            _stats[key] += 1

    return found


# ── Pool de Playwright ────────────────────────────────────────────────────────

class PlaywrightPool:
    """
    Browser, Context y páginas reutilizables para toda la corrida.

    Mantiene `concurrency` páginas para cada rol:
      • crawler  — P1: website scraping
      • maps     — P1.5: Google Maps
      • google   — P2: Google Search

    Las páginas se resetean a about:blank entre negocios en lugar de
    cerrarse y recrearse, ahorrando ~100 ms por lounge.
    """

    _LAUNCH_ARGS = [
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",   # Docker: usa /tmp en vez de /dev/shm (64 MB)
        "--disable-gpu",
        "--disable-software-rasterizer",
    ]

    def __init__(self, pw, browser, context, concurrency: int):
        self._pw          = pw
        self._browser     = browser
        self._context     = context
        self._concurrency = concurrency
        self._crawlers    = asyncio.Queue()
        self._maps        = asyncio.Queue()
        self._googles     = asyncio.Queue()

    @classmethod
    async def create(cls, pw, concurrency: int) -> "PlaywrightPool":
        browser = await pw.chromium.launch(
            headless=True,
            args=cls._LAUNCH_ARGS,
        )
        _dbg("[PW] Browser creado")

        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
        )
        _dbg("[PW] Context creado")

        pool = cls(pw, browser, context, concurrency)

        for _ in range(concurrency):
            pool._crawlers.put_nowait(await context.new_page())
            pool._maps.put_nowait(await context.new_page())
            pool._googles.put_nowait(await context.new_page())

        _dbg(
            f"[PW] {concurrency * 3} páginas pre-creadas "
            f"({concurrency} × crawler + maps + google)"
        )
        return pool

    async def _full_restart(self) -> None:
        """
        Cierra el browser completo y crea uno nuevo desde cero.
        Se usa cuando el proceso Chromium está muerto (todas las páginas
        crashean inmediatamente al navegar, incluso las recién creadas).
        Las colas de páginas quedan vacías — _reset() repoblará con
        la página de reemplazo que devuelve.
        """
        logger.warning("[PW] FULL RESTART — el proceso Chromium está muerto, reiniciando browser completo")
        try:
            await self._browser.close()
        except Exception as _e:
            logger.debug(f"[PW] full_restart: error cerrando browser muerto: {_e}")
        try:
            self._browser = await self._pw.chromium.launch(
                headless=True,
                args=self._LAUNCH_ARGS,
            )
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 800},
            )
            logger.info("[PW] FULL RESTART OK — browser y context recreados")
        except Exception as _e:
            logger.error(f"[PW] FULL RESTART FALLÓ: {_e}")
            raise

    async def _reset(self, page, pool_name: str = "?"):
        """
        Navega a about:blank para limpiar la página entre usos.
        Si la página crasheada, cierra y crea reemplazo.
        Si el context/browser está muerto, hace full restart primero.
        Siempre devuelve una página utilizable.
        """
        try:
            await page.goto("about:blank", timeout=5000, wait_until="commit")
            return page
        except Exception as exc:
            logger.warning(
                f"[PW] pool '{pool_name}' página crasheada "
                f"({type(exc).__name__}) — cerrando y creando reemplazo"
            )
            try:
                await page.close()
            except Exception:
                pass
            try:
                new_page = await asyncio.wait_for(self._context.new_page(), timeout=10)
                # Verificar que la nueva página funcione
                await asyncio.wait_for(
                    new_page.goto("about:blank", timeout=3000, wait_until="commit"),
                    timeout=5,
                )
                logger.info(f"[PW] pool '{pool_name}' → página de reemplazo creada OK")
                return new_page
            except Exception as _new_exc:
                # Context/browser muerto — full restart
                logger.warning(
                    f"[PW] pool '{pool_name}' — new_page() falló ({type(_new_exc).__name__}) "
                    f"— el browser está muerto, ejecutando full restart"
                )
                await self._full_restart()
                new_page = await self._context.new_page()
                logger.info(f"[PW] pool '{pool_name}' → página de reemplazo post-restart OK")
                return new_page

    async def acquire_crawler(self):
        try:
            page = await asyncio.wait_for(self._crawlers.get(), timeout=60)
        except asyncio.TimeoutError:
            logger.error("[PW] TIMEOUT: pool 'crawler' sin páginas disponibles tras 60s")
            raise
        _dbg("[PW] Reutilizando página crawler")
        return page

    async def release_crawler(self, page) -> None:
        clean = await self._reset(page, "crawler")
        self._crawlers.put_nowait(clean)

    async def acquire_maps(self):
        try:
            page = await asyncio.wait_for(self._maps.get(), timeout=60)
        except asyncio.TimeoutError:
            logger.error("[PW] TIMEOUT: pool 'maps' sin páginas disponibles tras 60s")
            raise
        _dbg("[PW] Reutilizando página Maps")
        return page

    async def release_maps(self, page) -> None:
        clean = await self._reset(page, "maps")
        self._maps.put_nowait(clean)

    async def acquire_google(self):
        try:
            page = await asyncio.wait_for(self._googles.get(), timeout=60)
        except asyncio.TimeoutError:
            logger.error("[PW] TIMEOUT: pool 'google' sin páginas disponibles tras 60s")
            raise
        _dbg("[PW] Reutilizando página Google")
        return page

    async def release_google(self, page) -> None:
        clean = await self._reset(page, "google")
        self._googles.put_nowait(clean)

    async def close(self) -> None:
        _dbg("[PW] Cerrando Browser")
        await self._browser.close()


# Batch principal

async def enrich_batch_async(lounges: list[dict], db, concurrency: int = CONCURRENCY):
    from playwright.async_api import async_playwright

    _reset_stats()
    total     = len(lounges)
    done      = 0
    semaphore = asyncio.Semaphore(concurrency)

    _MISSING_KEYS = {
        "email":         "email_missing",
        "facebook_url":  "facebook_missing",
        "instagram_url": "instagram_missing",
        "tiktok_url":    "tiktok_missing",
        "youtube_url":   "youtube_missing",
    }
    for lounge in lounges:
        for field, stat_key in _MISSING_KEYS.items():
            if not lounge.get(field):
                _stats[stat_key] += 1

    if DEBUG:
        concurrency = 1
        semaphore = asyncio.Semaphore(concurrency)

    cache: dict = {}

    # Reiniciar el browser cada N negocios para liberar memoria acumulada de Chromium
    BROWSER_RESTART_EVERY = 20

    async with async_playwright() as pw:
        pool_box = [await PlaywrightPool.create(pw, concurrency)]

        async def _run_all():
            nonlocal done
            for i, lounge in enumerate(lounges):
                # Reinicio periódico — previene crashes por memoria acumulada en Railway/Docker
                if i > 0 and i % BROWSER_RESTART_EVERY == 0:
                    logger.info(
                        f"[PW] Reiniciando browser (negocio {i}/{total}) "
                        f"— liberando memoria acumulada de {BROWSER_RESTART_EVERY} negocios"
                    )
                    try:
                        await pool_box[0].close()
                    except Exception as _e:
                        logger.warning(f"[PW] Error cerrando pool al reiniciar: {_e}")
                    pool_box[0] = await PlaywrightPool.create(pw, concurrency)
                    logger.info(f"[PW] Browser reiniciado OK → {lounge.get('name', '?')}")

                try:
                    await enrich_one(lounge, pool_box[0], db, cache)
                except Exception as exc:
                    logger.warning(f"[QUEUE] enrich_one excepcion para {lounge.get('name')}: {exc}")
                done += 1
                if not DEBUG and (done % 5 == 0 or done == total):
                    elapsed = time.time() - _stats["start_time"]
                    avg     = elapsed / done if done else 0
                    print(
                        f"  [{done}/{total}] "
                        f"email:{_stats['email_found']} "
                        f"fb:{_stats['facebook_found']} "
                        f"ig:{_stats['instagram_found']} "
                        f"tt:{_stats['tiktok_found']} "
                        f"yt:{_stats['youtube_found']} "
                        f"searches:{_stats['engine_searches']} "
                        f"avg:{avg:.1f}s",
                        end="\r",
                        flush=True,
                    )

        try:
            await asyncio.wait_for(_run_all(), timeout=7200)
        except asyncio.TimeoutError:
            logger.error("[QUEUE] TIMEOUT GLOBAL: el lote superó 2h — continuando con el siguiente estado")

        try:
            await pool_box[0].close()
        except Exception:
            pass

    if not DEBUG:
        print()
    _print_stats(total)


def _print_stats(total: int):
    elapsed = time.time() - _stats["start_time"]
    avg     = elapsed / _stats["processed"] if _stats["processed"] else 0

    print("\n" + "=" * 55)
    print("  RESULTADOS FINALES")
    print("=" * 55)
    print(f"  Procesados               : {_stats['processed']}")
    print(f"  Website reutilizados     : {_stats['website_reused']}")
    print(f"  Emails encontrados       : {_stats['email_found']}")
    print(f"  Facebook encontrados     : {_stats['facebook_found']}")
    print(f"  Instagram encontrados    : {_stats['instagram_found']}")
    print(f"  TikTok encontrados       : {_stats['tiktok_found']}")
    print(f"  YouTube encontrados      : {_stats['youtube_found']}")
    print(f"  Consultas Google Maps    : {_stats['maps_queries']}")
    print(f"  Busquedas por motor      : {_stats['engine_searches']}")
    print(f"  Coincidencias descartadas: {_stats['discarded']}")
    print(f"  Tiempo promedio/negocio  : {avg:.1f}s")
    print("=" * 55)

    _COVERAGE = [
        ("Facebook ", "facebook_found", "facebook_missing"),
        ("Instagram", "instagram_found", "instagram_missing"),
        ("TikTok   ", "tiktok_found",    "tiktok_missing"),
        ("YouTube  ", "youtube_found",   "youtube_missing"),
        ("Email    ", "email_found",     "email_missing"),
    ]
    print("\n  Cobertura del enriquecimiento")
    print("  " + "-" * 31)
    for label, found_key, missing_key in _COVERAGE:
        found   = _stats[found_key]
        missing = _stats[missing_key]
        pct     = (found / missing * 100) if missing else 0.0
        print(f"  {label}: {found}/{missing} ({pct:.1f}%)")
    print()
