# enrichment/browser_enricher.py
#
# Crawler inteligente del website oficial.
# Visita hasta MAX_PAGES páginas internas (contact, about, etc.)
# extrayendo email, Facebook, Instagram, TikTok y Google Maps URL.
#
# ── Fuentes estáticas (HTML parseado con BeautifulSoup) ─────────────────────
#   0. Footer region — se escanea primero (canonical socials suelen estar aqui)
#   1. JSON-LD — sameAs, email, @graph, contactPoint, url, mainEntityOfPage
#   2. OpenGraph / meta tags — og:see_also, og:url, profile:username
#   3. mailto: en hrefs
#   4. Redes sociales en hrefs <a>
#   5. onclick / data-href / data-url / data-link / data-share / data-network
#   6. SVG links — href y xlink:href en elementos dentro de <svg>
#   7. Scripts inline — window.open(), variables JS, asignaciones de URL
#   8. Regex en texto completo (fallback)
#
# ── Fuentes dinámicas (Playwright JS eval tras esperar networkidle) ───────────
#   • DOM renderizado completo (hrefs inyectados dinámicamente)
#   • onclick / data-* en todos los elementos del DOM renderizado
#   • Scripts inline completos del DOM vivo
#   • SVG links del DOM vivo
#
# ── Estrategia de extracción por página (4 fases) ────────────────────────────
#   Fase 1: Extraer del HTML de carga inicial
#   Fase 2: Esperar networkidle (homepage ≤5 s, internas ≤3 s),
#           si el DOM cambió volver a parsear HTML
#   Fase 3: Extracción dinámica vía page.evaluate() en el DOM vivo
#   Fase 4: Iframes del mismo dominio

import re
import json
import asyncio
from urllib.parse import urlparse, urljoin
from loguru import logger
from bs4 import BeautifulSoup

CONCURRENCY = 5
MAX_PAGES   = 6    # máximo de páginas internas a visitar por sitio

# Dominios cuyo contenido embebido bloquear via Playwright route interception.
# Widgets, videos, badges y trackers de analitica retrasan networkidle
# sin aportar datos utiles al enriquecimiento.
_BLOCK_DOMAINS = {
    # Redes que no son objetivo — sus embeds retrasan networkidle
    "twitter.com", "x.com", "t.co", "twimg.com",
    "youtube.com", "youtu.be", "ytimg.com", "googlevideo.com",
    "linkedin.com", "licdn.com",
    "pinterest.com", "pinimg.com",
    "snapchat.com",
    "reddit.com", "redditmedia.com", "redd.it",
    # Analitica y ads — nunca aportan contenido
    "doubleclick.net", "googlesyndication.com",
    "google-analytics.com", "googletagmanager.com",
    "hotjar.com", "mixpanel.com", "segment.io", "segment.com",
    "clarity.ms", "mouseflow.com",
    # SDKs de Meta que no son la pagina objetivo
    "connect.facebook.net",
}

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.I)

SOCIAL_RE = {
    # (?=...) lookahead: no consume el terminador.
    # Acepta: / ? ' " ) > < & whitespace y fin de cadena.
    # Necesario para capturar URLs dentro de onclick="window.open('url')",
    # data-href="url", atributos HTML, y hrefs normales.
    "instagram_url": re.compile(r"instagram\.com/([A-Za-z0-9_.]{2,30})(?=[/?'\")<>&\s]|$)", re.I),
    "facebook_url":  re.compile(r"facebook\.com/([A-Za-z0-9_.@\-]{2,60})(?=[/?'\")<>&\s]|$)", re.I),
    "tiktok_url":    re.compile(r"tiktok\.com/@([A-Za-z0-9_.]{2,30})(?=[/?'\")<>&\s]|$)", re.I),
}


SKIP_HANDLES = {
    "sharer", "share", "intent", "dialog", "login", "signup", "home",
    "pages", "groups", "events", "p", "reel", "stories", "reels",
    "hashtag", "explore", "about", "watch", "shorts", "create",
}

JUNK_EMAIL_DOMAINS = {
    "example.com", "sentry.io", "wixpress.com", "squarespace.com",
    "wordpress.com", "godaddy.com", "cloudflare.com", "w3.org", "schema.org",
    "google.com", "gmail.com",
}

# Weighted page scores: how likely this type of page contains contact/social info.
# Higher score = visited first by _find_priority_links().
_PAGE_SCORES: dict[str, int] = {
    # Tier 1 -- contact pages almost always have email + socials
    "contact":       10, "contacto":     10, "contact-us":   10,
    "get-in-touch":   9, "reach-us":      9,
    "about-us":       9, "about":         8, "acerca":        8,
    "our-story":      8, "story":         7, "historia":      7,
    # Tier 2 -- team/location pages often link to socials
    "team":           7, "staff":         7, "equipo":        7,
    "our-team":       7, "nosotros":      7,
    "locations":      6, "location":      6,
    # Tier 3 -- connect/info pages
    "connect":        5, "social":        5, "reach":         5,
    "info":           4, "help":          3,
    # Tier 4 -- sitemap (solo si faltan campos tras Tier A)
    "sitemap":        3,
}

# Backward-compat alias (imported by debug_crawler.py and others)
PRIORITY_KEYWORDS = list(_PAGE_SCORES.keys())

# URL path segments that signal low-value pages; skip these to save page budget
_SKIP_PATHS = {
    # Blog / contenido editorial
    "blog", "news", "article", "articles", "post", "posts",
    # E-commerce y catálogos
    "product", "products", "product-category", "shop", "store", "tienda",
    "cart", "checkout", "collections", "collection", "category",
    # Legal / políticas
    "terms", "privacy", "refund", "returns", "legal",
    # Técnicos
    "tag", "author", "search", "archive", "cdn-cgi", "wp-json", "feed", "rss",
}

# Páginas siempre visitadas (si existen) después del homepage — Tier A
_TIER_A_PATHS = frozenset({
    "contact", "contacto", "contact-us", "get-in-touch", "reach-us",
    "about", "about-us", "acerca", "our-story", "historia", "nosotros",
    "team", "our-team", "staff", "equipo",
    "locations", "location", "connect", "social", "reach", "info", "story",
})

# Visitadas SOLO si todavía faltan campos tras Tier A — Tier B
_TIER_B_PATHS = frozenset({"sitemap"})

# Activar desde social_enricher.py o cualquier runner con --debug
DEBUG = False


def _log_debug(msg: str):
    if DEBUG:
        print(f"  [crawler] {msg}")


# ── JS para extracción dinámica via page.evaluate() ──────────────────────────

_JS_INTERNAL_LINKS = """
() => {
    // Usa a.href (no a.getAttribute) para que el browser resuelva URLs relativas
    // a absolutas — mas fiable en SPAs que serializan el DOM con rutas relativas.
    const results = [];
    for (const a of document.querySelectorAll("a[href]")) {
        try {
            const abs = a.href;
            const txt = (a.innerText || a.textContent || "").trim().slice(0, 80);
            if (abs && (abs.startsWith("http://") || abs.startsWith("https://")))
                results.push([abs, txt]);
        } catch(e) {}
    }
    return results;
}
"""

_JS_EXTRACT = """
() => {
    const out = { hrefs: [], data_vals: [], script_text: "", svg_links: [] };

    // 1. Todos los <a href> ya renderizados (incluye los inyectados por JS)
    for (const a of document.querySelectorAll("a[href]")) {
        try {
            const h = a.href;
            if (h && (h.startsWith("http://") || h.startsWith("https://"))) {
                out.hrefs.push(h);
            }
        } catch(e) {}
    }

    // 2. onclick / data-* en elementos que típicamente llevan URLs sociales
    const DATA_SEL = [
        "[onclick]","[data-href]","[data-url]","[data-link]",
        "[data-share]","[data-share-url]","[data-action]",
        "[data-network]","[data-social-url]","[data-feed]",
        "[data-instagram]","[data-facebook]","[data-tiktok]"
    ].join(",");
    const DATA_NAMES = [
        "onclick","data-href","data-url","data-link",
        "data-share","data-share-url","data-action",
        "data-network","data-social-url","data-feed",
        "data-instagram","data-facebook","data-tiktok"
    ];
    for (const el of document.querySelectorAll(DATA_SEL)) {
        for (const name of DATA_NAMES) {
            const val = el.getAttribute(name);
            if (val && val.length > 5) out.data_vals.push(val);
        }
    }

    // 3. Scripts inline completos (window.open, vars JS, asignaciones de URL)
    const parts = [];
    for (const s of document.querySelectorAll("script:not([src])")) {
        const t = (s.textContent || "").trim();
        if (t.length > 0) parts.push(t);
    }
    out.script_text = parts.join(" ");

    // 4. SVG links — href y xlink:href en elementos hijo de <svg>
    for (const svg of document.querySelectorAll("svg")) {
        for (const el of svg.querySelectorAll("*")) {
            const h1 = el.getAttribute("href");
            const h2 = el.getAttribute("xlink:href");
            if (h1 && h1.startsWith("http")) out.svg_links.push(h1);
            if (h2 && h2.startsWith("http")) out.svg_links.push(h2);
        }
    }

    return out;
}
"""


# ── Helpers de extracción ─────────────────────────────────────────────────────

def _build_social_url(field: str, handle: str) -> str | None:
    if "instagram" in field:
        return f"https://instagram.com/{handle}"
    if "facebook" in field:
        return f"https://facebook.com/{handle}"
    if "tiktok" in field:
        return f"https://tiktok.com/@{handle}"
    return None


def _apply_social_re(text: str, result: dict) -> None:
    """Aplica SOCIAL_RE contra `text` y escribe en `result` los campos aún vacíos."""
    for field, pattern in SOCIAL_RE.items():
        if result.get(field):
            continue
        match = pattern.search(text)
        if match:
            handle = match.group(1).strip("/")
            if handle.lower() not in SKIP_HANDLES and len(handle) > 1:
                result[field] = _build_social_url(field, handle)


def _apply_email_re(text: str, result: dict) -> None:
    """Busca el primer email válido en `text` y lo escribe en result si falta."""
    if result.get("email"):
        return
    for e in EMAIL_RE.findall(text):
        domain = e.split("@")[-1].lower()
        if domain not in JUNK_EMAIL_DOMAINS and "." in domain:
            result["email"] = e.lower()
            break


# ── Extracción estática desde HTML ────────────────────────────────────────────

def _extract_from_html(html: str, base_url: str = "") -> dict:
    """
    Extracción completa desde HTML estático.
    9 fuentes: JSON-LD · meta/OG · mailto · hrefs · onclick/data-* ·
               SVG links · scripts inline · Google Maps · regex fulltext.
    """
    soup = BeautifulSoup(html, "lxml")
    result: dict = {
        "email":         None,
        "instagram_url": None,
        "facebook_url":  None,
        "tiktok_url":    None,
    }

    all_hrefs = [a.get("href", "") for a in soup.find_all("a", href=True)]

    # ── 0. Footer region — scan first so footer links take priority ───────────
    # Most sites put canonical social links in the footer, not the body.
    footer_els = (
        soup.find_all("footer")
        + soup.select("[id*='footer'],[class*='footer']")
        + soup.select("[id*='bottom-bar'],[class*='bottom-bar']")
        + soup.select("[id*='site-bottom'],[class*='site-bottom']")
    )
    if footer_els:
        footer_html = " ".join(str(el) for el in footer_els)
        _apply_social_re(footer_html, result)
        _apply_email_re(footer_html, result)

    # ── 1. JSON-LD ────────────────────────────────────────────────────────────
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            raw  = script.string or ""
            data = json.loads(raw)
            # Soportar tanto lista plana como objeto con @graph
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = data.get("@graph", [data])
            else:
                items = []

            for item in items:
                if not isinstance(item, dict):
                    continue

                # Email en JSON-LD (campo directo)
                if not result["email"]:
                    email_raw = item.get("email", "")
                    if email_raw and "@" in str(email_raw):
                        domain = str(email_raw).split("@")[-1].lower()
                        if domain not in JUNK_EMAIL_DOMAINS:
                            result["email"] = str(email_raw).lower().strip()

                # Email en Schema.org contactPoint
                if not result["email"]:
                    cp = item.get("contactPoint", {})
                    if isinstance(cp, list):
                        cp = cp[0] if cp else {}
                    if isinstance(cp, dict):
                        cp_email = cp.get("email", "")
                        if cp_email and "@" in str(cp_email):
                            domain = str(cp_email).split("@")[-1].lower()
                            if domain not in JUNK_EMAIL_DOMAINS:
                                result["email"] = str(cp_email).lower().strip()

                # sameAs → perfiles de redes sociales
                for same_as in item.get("sameAs", []):
                    _apply_social_re(str(same_as), result)

                # url / mainEntityOfPage pueden apuntar a redes sociales
                for key in ("url", "mainEntityOfPage"):
                    val = item.get(key, "")
                    if isinstance(val, dict):
                        val = val.get("@id", "")
                    if val:
                        _apply_social_re(str(val), result)

        except Exception:
            pass

    # ── 2. OpenGraph / Meta tags ──────────────────────────────────────────────
    for meta in soup.find_all("meta"):
        prop    = (meta.get("property") or meta.get("name") or "").lower()
        content = (meta.get("content") or "").strip()
        if not content:
            continue

        # Email en meta
        if not result["email"] and "email" in prop and "@" in content:
            domain = content.split("@")[-1].lower()
            if domain not in JUNK_EMAIL_DOMAINS:
                result["email"] = content.lower()

        # Redes sociales en meta (og:see_also, profile:username, og:url, etc.)
        _apply_social_re(content, result)

    # ── 3. mailto: en hrefs ───────────────────────────────────────────────────
    if not result["email"]:
        for href in all_hrefs:
            if href.startswith("mailto:"):
                email = href.replace("mailto:", "").split("?")[0].strip().lower()
                domain = email.split("@")[-1] if "@" in email else ""
                if domain and domain not in JUNK_EMAIL_DOMAINS:
                    result["email"] = email
                    break

    # ── 4. Redes sociales en hrefs <a> ────────────────────────────────────────
    for href in all_hrefs:
        _apply_social_re(href, result)

    # ── 5. onclick / data-href / data-url / data-link / data-share / etc. ─────
    _DATA_CSS = (
        "[onclick],[data-href],[data-url],[data-link],[data-share],"
        "[data-share-url],[data-action],[data-network],[data-social-url],"
        "[data-instagram],[data-facebook],[data-tiktok]"
    )
    _DATA_NAMES = (
        "onclick", "data-href", "data-url", "data-link", "data-share",
        "data-share-url", "data-action", "data-network", "data-social-url",
        "data-instagram", "data-facebook", "data-tiktok",
    )
    for el in soup.select(_DATA_CSS):
        for attr in _DATA_NAMES:
            val = el.get(attr)
            if not val:
                continue
            _apply_social_re(val, result)
            _apply_email_re(val, result)

    # ── 6. SVG links — href y xlink:href en hijos de <svg> ───────────────────
    for svg in soup.find_all("svg"):
        for el in svg.find_all(True):
            for attr_name, attr_val in el.attrs.items():
                if "href" in attr_name and isinstance(attr_val, str):
                    _apply_social_re(attr_val, result)

    # ── 7. Scripts inline ─────────────────────────────────────────────────────
    inline_script = " ".join(
        (s.string or "")
        for s in soup.find_all("script")
        if not s.get("src") and s.string
    )
    _apply_social_re(inline_script, result)
    _apply_email_re(inline_script, result)

    # ── 8. Regex en texto completo (fallback general) ─────────────────────────
    full_text = html
    _apply_social_re(full_text, result)
    _apply_email_re(full_text, result)

    return result


# ── Extracción dinámica vía JavaScript en el DOM renderizado ──────────────────

async def _extract_dynamic(page) -> dict:
    """
    Extrae URLs sociales evaluando JavaScript en el contexto del navegador.
    Accede a fuentes no disponibles en el HTML estático:
      - hrefs inyectados dinámicamente
      - onclick / data-* en el DOM renderizado
      - scripts inline completos
      - SVG links (href y xlink:href)
    """
    result: dict = {}
    try:
        js_out = await page.evaluate(_JS_EXTRACT)
    except Exception as e:
        _log_debug(f"JS evaluate error: {e}")
        return result

    # URLs del DOM renderizado (hrefs + SVG links)
    for href in js_out.get("hrefs", []) + js_out.get("svg_links", []):
        _apply_social_re(href, result)

    # Texto de atributos data-* / onclick + scripts inline
    combined_text = " ".join(js_out.get("data_vals", [])) + " " + js_out.get("script_text", "")
    _apply_social_re(combined_text, result)
    _apply_email_re(combined_text, result)

    if result:
        _log_debug(f"[dynamic] encontrado: {list(result.keys())}")

    return {k: v for k, v in result.items() if v}


# ── Extraccion desde iframes del mismo dominio ────────────────────────────────

async def _extract_from_iframes(page, base_url: str) -> dict:
    """
    Checks same-domain iframes for social/email content.
    Contact widgets and embedded forms often live inside iframes.
    """
    result: dict = {}
    try:
        base_domain = urlparse(base_url).netloc.lower()
        for frame in page.frames:
            frame_url = frame.url or ""
            if not frame_url or frame_url == "about:blank":
                continue
            try:
                frame_domain = urlparse(frame_url).netloc.lower()
            except Exception:
                continue
            # Only same-domain or relative (no domain) frames
            if frame_domain and frame_domain != base_domain:
                continue
            try:
                html = await frame.content()
                found = _extract_from_html(html, base_url=frame_url or base_url)
                for k, v in found.items():
                    if v and not result.get(k):
                        result[k] = v
            except Exception:
                pass
    except Exception as e:
        _log_debug(f"iframe extraction error: {e}")

    if result:
        _log_debug(f"[iframes] encontrado: {list(result.keys())}")

    return {k: v for k, v in result.items() if v}


# ── Descubrimiento de páginas internas ───────────────────────────────────────

async def _find_priority_links(html: str, base_url: str, page=None) -> list[str]:
    """
    Recopila enlaces internos de la pagina, los puntua por relevancia
    (contact, about, etc.) y retorna hasta MAX_PAGES URLs en orden de prioridad.

    Fuentes:
      1. BeautifulSoup sobre el HTML serializado (cubre sitios estaticos y SSR)
      2. DOM vivo via page.evaluate(_JS_INTERNAL_LINKS) — imprescindible en SPAs
         donde React/Vue inyecta <a> tags despues de la serializacion inicial

    En modo DEBUG imprime: total de hrefs encontrados, descartes por motivo
    (externo, duplicado, ruta baja prioridad, esquema invalido) y la lista final.
    """
    base_domain = urlparse(base_url).netloc.lower()
    base_clean  = base_url.rstrip("/")
    seen: set[str] = {base_clean}

    # (abs_url, link_text)
    candidates: list[tuple[str, str]] = []

    # -- Fuente 1: BeautifulSoup sobre el HTML serializado --------------------
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        text = a.get_text(strip=True).lower()
        try:
            abs_url = urljoin(base_url, href)
            candidates.append((abs_url, text))
        except Exception:
            pass

    html_count = len(candidates)

    # -- Fuente 2: DOM vivo via page.evaluate() ------------------------------
    live_count = 0
    if page is not None:
        try:
            live_pairs = await page.evaluate(_JS_INTERNAL_LINKS)
            for abs_url, text in live_pairs:
                candidates.append((abs_url, text.lower()))
                live_count += 1
        except Exception as exc:
            _log_debug(f"[links] page.evaluate error: {exc}")

    total_raw = len(candidates)
    _log_debug(
        f"[links] fuentes: HTML={html_count}  DOM_vivo={live_count}  "
        f"total_raw={total_raw}"
    )

    # -- Filtrar, deduplicar y puntuar ----------------------------------------
    cnt_ext  = 0   # dominio externo
    cnt_sch  = 0   # esquema invalido (mailto, tel, etc.)
    cnt_dup  = 0   # duplicado
    cnt_skip = 0   # _SKIP_PATHS

    scored: list[tuple[int, str]] = []

    for abs_url, text in candidates:
        try:
            parsed = urlparse(abs_url)
        except Exception:
            cnt_sch += 1
            continue

        if parsed.scheme not in ("http", "https"):
            cnt_sch += 1
            continue

        if parsed.netloc.lower() != base_domain:
            cnt_ext += 1
            continue

        # Normalizar: quitar fragmento, strip trailing slash
        clean = abs_url.split("#")[0].rstrip("/")
        if clean in seen:
            cnt_dup += 1
            continue
        seen.add(clean)

        path_parts = set(parsed.path.lower().strip("/").split("/"))
        if path_parts & _SKIP_PATHS:
            cnt_skip += 1
            _log_debug(f"[links] SKIP (ruta baja prioridad): {clean}")
            continue

        # Puntuar por coincidencia en la ruta o el texto del enlace
        path_lower = parsed.path.lower()
        score      = 0
        matched_kw = None
        for keyword, kw_score in _PAGE_SCORES.items():
            if keyword in path_lower or keyword in text:
                if kw_score > score:
                    score      = kw_score
                    matched_kw = keyword

        scored.append((score, clean))
        if DEBUG:
            kw_info = f"kw={matched_kw!r} score={score}" if matched_kw else "score=0"
            _log_debug(f"[links] OK  [{kw_info}]: {clean}")

    _log_debug(
        f"[links] descartes: dominio_externo={cnt_ext}  duplicado={cnt_dup}  "
        f"ruta_skip={cnt_skip}  esquema_invalido={cnt_sch}  "
        f"utiles={len(scored)}"
    )

    # Ordenar: mayor puntuacion primero; sin puntuar (0) al final
    scored.sort(key=lambda x: -x[0])
    result = [u for _, u in scored[:MAX_PAGES]]
    _log_debug(f"[links] paginas a visitar (max {MAX_PAGES}): {result}")
    return result


# ── Cookie banners y Age Gates ───────────────────────────────────────────────

# Textos de botones de cookies (case-insensitive, coincidencia parcial)
_COOKIE_TEXTS = [
    "accept all cookies", "accept all", "accept cookies",
    "allow all cookies", "allow all", "allow cookies",
    "i agree", "agree to all", "agree",
    "got it", "okay", "ok",
    "consent", "accept",
]

# Textos de botones de age gate (más específico — se evalúa después)
_AGE_GATE_TEXTS = [
    "i'm 21 or older", "i am 21 or older",
    "i'm 21+", "i am 21+",
    "i am of legal smoking age", "i am of legal age",
    "i am old enough",
    "yes, i am", "yes i am",
    "yes, enter", "enter site",
    "verify age",
    "i am an adult",
    "enter",
    "yes",
]


async def _try_click_text(page, texts: list[str]) -> str | None:
    """
    Intenta hacer click en el primer elemento visible que contenga
    alguno de los textos dados. Devuelve el texto clickeado o None.
    """
    selector = (
        "button, a[href], [role='button'], "
        "input[type='submit'], input[type='button'], label"
    )
    for text in texts:
        try:
            loc = page.locator(selector).filter(
                has_text=re.compile(rf"\b{re.escape(text)}\b", re.I)
            )
            if await loc.count() > 0:
                el = loc.first
                # Verificar que esté visible
                if await el.is_visible():
                    await el.click(timeout=3000)
                    return text
        except Exception:
            pass
    return None


async def _try_dob_form(page) -> bool:
    """
    Detecta y completa formularios de fecha de nacimiento (age gate).
    Soporta: input[type=date], tres <select> month/day/year,
    y tres <input type=text> para mes/día/año.
    Devuelve True si encontró y completó el formulario.
    """
    try:
        # Caso 1: un solo input[type="date"]
        date_inp = page.locator("input[type='date']")
        if await date_inp.count() > 0 and await date_inp.first.is_visible():
            await date_inp.first.fill("1980-01-01")
            _log_debug("DOB input[type=date] completado con 01/01/1980")
            submit = page.locator(
                "button[type='submit'], input[type='submit'], "
                "button:has-text('Enter'), button:has-text('Continue'), "
                "button:has-text('Submit'), button:has-text('Verify')"
            )
            if await submit.count() > 0:
                await submit.first.click(timeout=3000)
                return True

        # Caso 2: tres <select> month/day/year
        month_sel = page.locator(
            "select[name*='month' i], select[id*='month' i], "
            "select[name*='Month'], select[class*='month' i]"
        )
        year_sel  = page.locator(
            "select[name*='year' i], select[id*='year' i], "
            "select[name*='Year'], select[class*='year' i]"
        )
        day_sel   = page.locator(
            "select[name*='day' i], select[id*='day' i], "
            "select[name*='Day'], select[class*='day' i]"
        )

        if await month_sel.count() > 0 and await year_sel.count() > 0:
            await month_sel.first.select_option(value="1")
            if await day_sel.count() > 0:
                await day_sel.first.select_option(value="1")
            # Intentar valor "1980" o el más cercano disponible
            for yr in ("1980", "1981", "1979", "1975"):
                try:
                    await year_sel.first.select_option(value=yr)
                    break
                except Exception:
                    pass
            _log_debug("Formulario DOB (selects) completado con 01/01/1980")
            submit = page.locator(
                "button[type='submit'], input[type='submit'], "
                "button:has-text('Enter'), button:has-text('Continue'), "
                "button:has-text('Submit'), button:has-text('Verify')"
            )
            if await submit.count() > 0:
                await submit.first.click(timeout=3000)
                return True

        # Caso 3: inputs de texto para mes/día/año (formato MM/DD/YYYY)
        text_inputs = page.locator(
            "input[placeholder*='MM'], input[placeholder*='month' i], "
            "input[name*='month' i], input[id*='month' i]"
        )
        if await text_inputs.count() > 0 and await text_inputs.first.is_visible():
            await text_inputs.first.fill("01")
            day_inp = page.locator(
                "input[placeholder*='DD'], input[name*='day' i], input[id*='day' i]"
            )
            yr_inp  = page.locator(
                "input[placeholder*='YYYY'], input[placeholder*='year' i], "
                "input[name*='year' i], input[id*='year' i]"
            )
            if await day_inp.count() > 0:
                await day_inp.first.fill("01")
            if await yr_inp.count() > 0:
                await yr_inp.first.fill("1980")
            _log_debug("Formulario DOB (text inputs) completado con 01/01/1980")
            submit = page.locator(
                "button[type='submit'], input[type='submit'], "
                "button:has-text('Enter'), button:has-text('Continue')"
            )
            if await submit.count() > 0:
                await submit.first.click(timeout=3000)
                return True

    except Exception as e:
        logger.debug(f"DOB form error: {e}")

    return False


async def _bypass_overlays(page) -> None:
    """
    Detecta y supera automáticamente:
    - Banners de cookies (Accept, I Agree, etc.)
    - Age Gates (Yes, Enter, I'm 21+, formulario DOB)
    Debe llamarse justo después de cargar la página.
    """
    # Dar tiempo a que aparezcan los overlays
    await asyncio.sleep(0.8)

    # ── 1. Cookie banner ──────────────────────────────────────────────────────
    clicked = await _try_click_text(page, _COOKIE_TEXTS)
    if clicked:
        _log_debug(f"Cookies aceptadas ('{clicked}')")
        await asyncio.sleep(0.4)

    # ── 2. Age Gate — botones ─────────────────────────────────────────────────
    age_clicked = await _try_click_text(page, _AGE_GATE_TEXTS)
    if age_clicked:
        _log_debug(f"Age Gate superado (botón '{age_clicked}')")
        await asyncio.sleep(0.4)
        return

    # ── 3. Age Gate — formulario DOB ─────────────────────────────────────────
    dob_done = await _try_dob_form(page)
    if dob_done:
        _log_debug("Age Gate superado (formulario DOB)")
        await asyncio.sleep(0.4)


# ── Crawler principal ────────────────────────────────────────────────────────

async def scrape_website(url: str, page, missing: set[str] | None = None) -> dict:
    """
    Crawler multi-página del website oficial con extracción en 3 fases.

    Visita la página principal y hasta MAX_PAGES páginas internas
    (priorizando contact, about, etc.). Se detiene en cuanto encuentra
    todos los campos requeridos.

    Cada pagina se procesa en 4 fases:
      1. Extraccion del HTML tras carga inicial (fuentes estaticas)
      2. Espera de networkidle + re-extraccion si el DOM cambio
      3. Extraccion dinamica via page.evaluate() en el DOM vivo
      4. Iframes del mismo dominio

    Args:
        url:     URL del website.
        page:    Playwright page ya abierta.
        missing: Campos que todavia hacen falta. Si es None busca todos.

    Returns:
        dict con los campos encontrados (solo los que tienen valor).
    """
    from playwright_stealth import Stealth

    ALL_FIELDS = {"email", "facebook_url", "instagram_url", "tiktok_url"}
    if missing is None:
        missing = ALL_FIELDS

    await Stealth().apply_stealth_async(page)

    # Bloquear recursos de dominios irrelevantes para acelerar networkidle.
    # Se abortan ANTES de que el navegador los descargue — sin impacto en el HTML.
    async def _route_handler(route):
        try:
            host = urlparse(route.request.url).netloc.lower()
            if any(bd in host for bd in _BLOCK_DOMAINS):
                await route.abort()
            else:
                await route.continue_()
        except Exception:
            try:
                await route.continue_()
            except Exception:
                pass

    await page.route("**/*", _route_handler)

    result:  dict      = {}
    visited: set[str]  = set()

    _log_debug(f"[scrape] START  url={url}  missing={sorted(missing)}")

    def _remaining() -> set[str]:
        return missing - {k for k, v in result.items() if v}

    def _merge(found: dict, label: str) -> None:
        """
        Escribe en result TODOS los campos encontrados que no esten ya escritos.
        No filtra por missing -- el filtrado de que guardar en DB ocurre en enrich_one().
        label identifica la fuente en los logs (fase1, fase2, fase3-dynamic, etc.)
        """
        non_empty_in = {k: v for k, v in found.items() if v}

        _log_debug(
            f"[merge:{label}]  recibido={sorted(non_empty_in.keys()) or '(vacio)'}  "
            f"result_actual={sorted(k for k,v in result.items() if v)}"
        )

        for field, value in found.items():
            has_value = bool(value)
            not_dupe  = not result.get(field)

            if has_value and not_dupe:
                result[field] = value
                _log_debug(f"[merge:{label}]  ESCRITO   {field} = {value}")
            elif has_value:
                _log_debug(f"[merge:{label}]  IGNORADO  {field}  [ya_existe_en_result]")

        _log_debug(
            f"[merge:{label}]  result_despues={sorted(k for k,v in result.items() if v)}"
        )

    async def _visit(visit_url: str, is_homepage: bool = False) -> str | None:
        """
        Visita una URL y extrae datos en 4 fases. Devuelve el HTML final.
        """
        if visit_url in visited:
            _log_debug(f"[visit] SKIP (ya visitado): {visit_url}")
            return None
        visited.add(visit_url)

        if not _remaining():
            _log_debug(f"[visit] SKIP (nada que buscar): {visit_url}")
            return None

        page_label = "homepage" if is_homepage else visit_url.split("/")[-1] or visit_url
        _log_debug(f"[visit] INICIO {page_label}  rem={sorted(_remaining())}")

        html_final: str | None = None

        try:
            await page.goto(visit_url, timeout=20000, wait_until="domcontentloaded")

            if is_homepage:
                _log_debug(f"Comenzando crawler en: {visit_url}")
                await _bypass_overlays(page)
                await asyncio.sleep(0.3)
            else:
                await asyncio.sleep(0.5)

            # -- Fase 1: HTML de carga inicial ---------------------------------
            html_initial = await page.content()
            _log_debug(f"[visit] {page_label} fase1 -- html_initial {len(html_initial)} chars")
            _merge(_extract_from_html(html_initial, base_url=visit_url), f"fase1:{page_label}")

            # -- Fase 2: esperar JS + re-parsear si el DOM cambio --------------
            if _remaining():
                wait_ms = 5000 if is_homepage else 3000
                try:
                    await page.wait_for_load_state("networkidle", timeout=wait_ms)
                except Exception:
                    await asyncio.sleep(1.5 if not is_homepage else 2.0)

                html_final = await page.content()

                if html_final != html_initial:
                    _log_debug(f"[visit] {page_label} fase2 -- DOM cambio ({len(html_final)} chars), re-parseando")
                    _merge(_extract_from_html(html_final, base_url=visit_url), f"fase2:{page_label}")
                else:
                    _log_debug(f"[visit] {page_label} fase2 -- DOM sin cambio, skip re-parse")

                # -- Fase 3: extraccion dinamica via JS eval ------------------
                if _remaining():
                    _log_debug(f"[visit] {page_label} fase3-dynamic INICIO")
                    dyn = await _extract_dynamic(page)
                    _log_debug(f"[visit] {page_label} fase3-dynamic retorno={sorted(dyn.keys()) if dyn else '(vacio)'}")
                    _merge(dyn, f"fase3-dynamic:{page_label}")
                else:
                    _log_debug(f"[visit] {page_label} fase3-dynamic SKIP (rem vacio tras fase2)")

                # -- Fase 4: iframes del mismo dominio -------------------------
                if _remaining():
                    _log_debug(f"[visit] {page_label} fase4-iframes INICIO")
                    ifr = await _extract_from_iframes(page, visit_url)
                    _log_debug(f"[visit] {page_label} fase4-iframes retorno={sorted(ifr.keys()) if ifr else '(vacio)'}")
                    _merge(ifr, f"fase4-iframes:{page_label}")
                else:
                    _log_debug(f"[visit] {page_label} fase4-iframes SKIP")
            else:
                _log_debug(f"[visit] {page_label} fase2/3/4 SKIP (rem vacio tras fase1)")

            html_final = html_final or html_initial

        except Exception as e:
            logger.debug(f"Crawler: fallo {visit_url}: {e}")
            _log_debug(f"[visit] {page_label} EXCEPCION: {e}  result_actual={dict(result)}")
            return None

        _log_debug(
            f"[visit] FIN {page_label}  "
            f"result={sorted(k for k,v in result.items() if v)}"
        )
        return html_final

    # -- Página principal --------------------------------------------------------
    homepage_html = await _visit(url, is_homepage=True)

    if not _remaining():
        _log_debug("[crawler] homepage completo, finalizando")
    else:
        _log_debug(f"[scrape] tras homepage  result={sorted(k for k,v in result.items() if v)}")

    # -- Páginas internas: Tier A (contact/about) → Tier B (sitemap) si falta --
    if homepage_html and _remaining():
        all_links = await _find_priority_links(homepage_html, url, page)
        _log_debug(f"[scrape] candidatos internos: {all_links}")

        def _path_kws(link_url: str) -> set[str]:
            return set(urlparse(link_url).path.lower().strip("/").split("/"))

        tier_a = [l for l in all_links if _path_kws(l) & _TIER_A_PATHS]
        tier_b = [l for l in all_links if _path_kws(l) & _TIER_B_PATHS]
        _log_debug(
            f"[crawler] tier-A: {[l.rstrip('/').split('/')[-1] for l in tier_a]}  "
            f"tier-B: {[l.rstrip('/').split('/')[-1] for l in tier_b]}"
        )

        # Tier A: contact + about (siempre, en orden descendente de score)
        for link in tier_a:
            if not _remaining():
                _log_debug("[crawler] missing vacío, terminando recorrido")
                break
            label = link.rstrip("/").split("/")[-1] or "página"
            _log_debug(f"[crawler] aún falta: {sorted(_remaining())}, visitando {label}")
            await _visit(link)

        # Tier B: sitemap — solo si todavía faltan campos tras Tier A
        if _remaining():
            for link in tier_b:
                if not _remaining():
                    _log_debug("[crawler] missing vacío, terminando recorrido")
                    break
                label = link.rstrip("/").split("/")[-1] or "página"
                _log_debug(f"[crawler] aún falta: {sorted(_remaining())}, visitando {label}")
                await _visit(link)

        if not _remaining():
            _log_debug("[crawler] missing vacío, terminando recorrido")

    elif not homepage_html:
        _log_debug("[scrape] homepage_html=None -- skip internas")
    else:
        _log_debug("[crawler] homepage completo, finalizando")

    final = {k: v for k, v in result.items() if v}
    pages_visited = len(visited)
    _log_debug(
        f"[scrape] RETURN  paginas={pages_visited}  "
        f"result_raw={dict(result)}  final={final}"
    )
    logger.debug(
        f"Crawler: {pages_visited} pagina(s) visitadas en {url} "
        f"-> {list(final.keys())}"
    )
    return final
