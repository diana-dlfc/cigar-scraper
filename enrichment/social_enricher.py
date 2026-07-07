# enrichment/social_enricher.py
#
# Enriquecimiento optimizado: email, Facebook, Instagram, TikTok.
#
# Prioridades:
#   1. Scrape del website oficial (si existe)
#   2. UNA búsqueda en Google → Knowledge Panel + resultados orgánicos
#   3. Visitar el website nuevo encontrado en Google
#
# Nunca sobreescribe campos existentes. Valida antes de guardar.
# Modo debug: establecer DEBUG = True desde enrich_social.py --debug

import asyncio
import random
import re
import time
from urllib.parse import urlparse, parse_qs, quote as url_quote
from loguru import logger
from bs4 import BeautifulSoup

CONCURRENCY = 5
PAUSE_MIN   = 0.3
PAUSE_MAX   = 0.8

# Activar desde enrich_social.py con --debug
DEBUG = False

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.I)

SOCIAL_RE = {
    "facebook_url":  re.compile(r"facebook\.com/([A-Za-z0-9_.@\-]{2,60})(?:[/?]|$)", re.I),
    "instagram_url": re.compile(r"instagram\.com/([A-Za-z0-9_.]{2,30})(?:[/?]|$)", re.I),
    "tiktok_url":    re.compile(r"tiktok\.com/@([A-Za-z0-9_.]{2,30})(?:[/?]|$)", re.I),
}

SKIP_HANDLES = {
    "sharer", "share", "intent", "dialog", "login", "signup", "home",
    "pages", "groups", "events", "p", "reel", "stories", "reels",
    "hashtag", "explore", "about", "watch", "shorts", "create",
}

JUNK_EMAIL_DOMAINS = {
    "example.com", "sentry.io", "wixpress.com", "squarespace.com",
    "wordpress.com", "godaddy.com", "cloudflare.com", "w3.org",
    "schema.org", "google.com", "gmail.com", "yahoo.com",
}

SKIP_WEBSITE_DOMAINS = {
    "yelp.com", "google.com", "facebook.com", "instagram.com", "tiktok.com",
    "twitter.com", "x.com", "youtube.com", "tripadvisor.com", "yellowpages.com",
    "mapquest.com", "foursquare.com", "apple.com", "linkedin.com", "bing.com",
    "wikipedia.org", "duckduckgo.com", "bbb.org", "thumbtack.com",
    "nextdoor.com", "groupon.com", "opentable.com", "maps.google.com",
}

STOP_WORDS = {
    "the", "a", "an", "and", "or", "of", "in", "at", "on", "for",
    "cigar", "lounge", "bar", "club", "smoke", "shop", "tobacco",
    "cigars", "smoking",
}

# Stats globales (se resetean en enrich_batch_async)
_stats: dict = {}


# ── Debug helper ──────────────────────────────────────────────────────────────

def _dbg(msg: str):
    if DEBUG:
        print(msg)


# ── Utilidades ────────────────────────────────────────────────────────────────

def _reset_stats():
    _stats.clear()
    _stats.update({
        "processed":        0,
        "website_found":    0,
        "website_reused":   0,
        "email_found":      0,
        "facebook_found":   0,
        "instagram_found":  0,
        "tiktok_found":     0,
        "google_searches":  0,
        "discarded":        0,
        "start_time":       time.time(),
    })


def _missing_fields(lounge: dict) -> set[str]:
    fields = {"email", "facebook_url", "instagram_url", "tiktok_url"}
    return {f for f in fields if not lounge.get(f)}


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _name_tokens(name: str) -> list[str]:
    words = re.findall(r"[a-zA-Z0-9]+", name.lower())
    return [w for w in words if w not in STOP_WORDS and len(w) > 2]


def _unwrap_google_url(href: str) -> str:
    if not href:
        return href
    if href.startswith("/url?") or ("google.com/url?" in href):
        try:
            params = parse_qs(urlparse(href).query)
            if "q" in params:
                return params["q"][0]
        except Exception:
            pass
    return href


# ── Validación ────────────────────────────────────────────────────────────────

def _validate_social(url: str, lounge: dict, source: str = "organic") -> bool:
    """
    Verifica que la URL social pertenezca a este negocio.
    En modo DEBUG imprime el razonamiento completo.
    """
    name = lounge.get("name", "?")

    # Knowledge Panel: Google ya verificó la correspondencia
    if source == "knowledge_panel":
        _dbg(f"    ✓ ACEPTADO [{source}] — Google KP es fuente de confianza: {url}")
        return True

    try:
        path = urlparse(url).path.lower().strip("/")
    except Exception:
        _dbg(f"    ✗ RECHAZADO — URL inválida: {url}")
        _stats["discarded"] += 1
        return False

    # Quitar prefijos comunes de plataforma
    path = re.sub(r"^(@|pages/|company/|biz/|user/|channel/)", "", path)
    handle = _normalize(path.split("/")[0])

    if not handle or len(handle) < 2:
        _dbg(f"    ✗ RECHAZADO — handle vacío o demasiado corto: '{handle}' ({url})")
        _stats["discarded"] += 1
        return False

    if handle in SKIP_HANDLES:
        _dbg(f"    ✗ RECHAZADO — handle '{handle}' está en lista negra ({url})")
        _stats["discarded"] += 1
        return False

    tokens = _name_tokens(name)

    _dbg(f"    ? Validando [{source}]: {url}")
    _dbg(f"      handle='{handle}'")
    _dbg(f"      tokens del nombre='{name}': {tokens}")

    if not tokens:
        _dbg(f"      ✓ ACEPTADO — sin tokens significativos, no se puede validar")
        return True

    # Comprobar token a token
    for token in tokens:
        norm_token = _normalize(token)
        if norm_token in handle:
            _dbg(f"      ✓ ACEPTADO — token '{token}' → '{norm_token}' encontrado en handle '{handle}'")
            return True

    # Comprobar si el handle está contenido en el nombre
    name_norm = _normalize(name)
    if handle in name_norm:
        _dbg(f"      ✓ ACEPTADO — handle '{handle}' está dentro del nombre normalizado '{name_norm}'")
        return True

    _dbg(f"      ✗ RECHAZADO — ningún token {tokens} coincide con handle '{handle}'")
    _dbg(f"        nombre normalizado='{name_norm}'")
    _stats["discarded"] += 1
    return False


# ── Extracción de HTML ────────────────────────────────────────────────────────

def _build_social_url(field: str, handle: str) -> str | None:
    if "instagram" in field:
        return f"https://instagram.com/{handle}"
    if "facebook" in field:
        return f"https://facebook.com/{handle}"
    if "tiktok" in field:
        return f"https://tiktok.com/@{handle}"
    return None


def _extract_from_html(html: str, missing: set[str], lounge: dict = None,
                       source: str = "website") -> dict:
    soup = BeautifulSoup(html, "lxml")
    result = {}
    all_hrefs = [a.get("href", "") for a in soup.find_all("a", href=True)]
    full_text = html

    # Email — priorizar mailto:
    if "email" in missing:
        for href in all_hrefs:
            if href.startswith("mailto:"):
                email = href.replace("mailto:", "").split("?")[0].strip().lower()
                domain = email.split("@")[-1] if "@" in email else ""
                if domain and domain not in JUNK_EMAIL_DOMAINS:
                    result["email"] = email
                    break
        if "email" not in result:
            for e in EMAIL_RE.findall(full_text):
                domain = e.split("@")[-1].lower()
                if domain not in JUNK_EMAIL_DOMAINS and "." in domain:
                    result["email"] = e.lower()
                    break

    # Redes sociales — buscar en hrefs primero
    for href in all_hrefs:
        real_href = _unwrap_google_url(href)
        for field, pattern in SOCIAL_RE.items():
            if field not in missing or field in result:
                continue
            match = pattern.search(real_href)
            if match:
                handle = match.group(1).strip("/")
                if handle.lower() in SKIP_HANDLES or len(handle) <= 1:
                    _dbg(f"    ✗ {field}: handle '{handle}' en lista negra — {real_href[:80]}")
                    continue
                url = _build_social_url(field, handle)
                if url and _validate_social(url, lounge or {}, source):
                    result[field] = url

    # Fallback: regex en texto completo
    for field, pattern in SOCIAL_RE.items():
        if field not in missing or field in result:
            continue
        match = pattern.search(full_text)
        if match:
            handle = match.group(1).strip("/")
            if handle.lower() in SKIP_HANDLES or len(handle) <= 1:
                continue
            url = _build_social_url(field, handle)
            if url and _validate_social(url, lounge or {}, source):
                result[field] = url

    return result


# ── Debug: resultados orgánicos de Google ─────────────────────────────────────

def _debug_google_organic(soup: BeautifulSoup, html: str):
    """
    Imprime los primeros 10 resultados orgánicos de Google tal como los lee
    Playwright: título, URL mostrada y snippet. Sin filtrar nada.
    """
    print("\n  ┌─ RESULTADOS ORGÁNICOS (raw) ─────────────────────────────")

    results_found = []

    # Estrategia 1: contenedores div.g (clásico)
    containers = soup.select("div.g")

    # Estrategia 2: si div.g no aparece, buscar por h3 + contexto
    if not containers:
        containers = [h3.find_parent("div") for h3 in soup.find_all("h3") if h3.find_parent("div")]

    for container in containers[:10]:
        title_tag   = container.find("h3")
        title       = title_tag.get_text(strip=True) if title_tag else "(sin título)"

        # URL: buscar en <cite> primero, luego en primer <a href>
        cite_tag = container.find("cite")
        url_disp = cite_tag.get_text(strip=True) if cite_tag else ""
        if not url_disp:
            a_tag    = container.find("a", href=True)
            url_disp = _unwrap_google_url(a_tag["href"]) if a_tag else "(sin URL)"

        # Snippet: div con clase conocida o primer texto largo
        snippet = ""
        for sel in ("div.VwiC3b", "span.st", "div[data-sncf]", "div.s"):
            s = container.select_one(sel)
            if s:
                snippet = s.get_text(strip=True)[:160]
                break
        if not snippet:
            texts = [t.strip() for t in container.stripped_strings if len(t.strip()) > 40]
            snippet = texts[0][:160] if texts else "(sin snippet)"

        results_found.append((title, url_disp, snippet))

    if not results_found:
        # Último recurso: imprimir todos los <h3> de la página
        h3_list = soup.find_all("h3")
        print(f"  │  div.g no encontrado. H3 en la página ({len(h3_list)}):")
        for h3 in h3_list[:10]:
            print(f"  │    • {h3.get_text(strip=True)}")

        # Y todos los links externos
        ext_links = [
            _unwrap_google_url(a["href"])
            for a in soup.find_all("a", href=True)
            if a.get("href", "").startswith("http") and "google" not in a.get("href","")
        ]
        print(f"  │  Links externos encontrados ({len(ext_links)}):")
        for lnk in ext_links[:10]:
            print(f"  │    → {lnk}")
    else:
        for i, (title, url, snippet) in enumerate(results_found, 1):
            print(f"  │  [{i}] {title}")
            print(f"  │      URL: {url}")
            print(f"  │      Snippet: {snippet}")
            print(f"  │")

    # Estadísticas rápidas de la página
    all_a    = soup.find_all("a", href=True)
    ext_a    = [a for a in all_a if a.get("href","").startswith("http") and "google" not in a["href"]]
    social_a = [a for a in all_a if any(s in a.get("href","") for s in
                ("facebook.com", "instagram.com", "tiktok.com"))]
    print(f"  │  Total <a href>: {len(all_a)} | externos: {len(ext_a)} | sociales: {len(social_a)}")
    if social_a:
        print(f"  │  Links sociales detectados en la página:")
        for a in social_a:
            print(f"  │    → {_unwrap_google_url(a['href'])}")
    print("  └──────────────────────────────────────────────────────────\n")


# ── Extracción de página de Google ────────────────────────────────────────────

def _extract_from_google_page(html: str, missing: set[str], lounge: dict) -> dict:
    soup = BeautifulSoup(html, "lxml")
    result = {}

    all_links = [
        (_unwrap_google_url(a.get("href", "")), a.get_text(strip=True))
        for a in soup.find_all("a", href=True)
    ]

    # Detectar Knowledge Panel
    kp = (
        soup.find("div", {"id": "rhs"}) or
        soup.find("div", {"id": "kp-wp-tab-overview"}) or
        soup.find("div", {"class": lambda c: bool(c and any("kp-" in x for x in c))}) or
        soup.find("div", {"data-attrid": re.compile(r"knowledge")})
    )
    kp_links = []
    if kp:
        kp_links = [
            (_unwrap_google_url(a.get("href", "")), a.get_text(strip=True))
            for a in kp.find_all("a", href=True)
        ]
        _dbg(f"  [Google] Knowledge Panel detectado ({len(kp_links)} links)")
    else:
        _dbg(f"  [Google] Sin Knowledge Panel — solo resultados orgánicos")
        if DEBUG:
            _debug_google_organic(soup, html)

    def _process_links(links: list, source: str):
        for href, text in links:
            if not href or href.startswith("#"):
                continue

            # Website
            if "website" in missing and "website" not in result:
                try:
                    parsed = urlparse(href)
                    domain = parsed.netloc.lower().replace("www.", "")
                    if (domain
                            and parsed.scheme in ("http", "https")
                            and not any(s in domain for s in SKIP_WEBSITE_DOMAINS)):
                        result["website"] = href
                        _dbg(f"  [Google/{source}] website: {href}")
                except Exception:
                    pass

            # Social
            for field, pattern in SOCIAL_RE.items():
                if field not in missing or field in result:
                    continue
                match = pattern.search(href)
                if match:
                    handle = match.group(1).strip("/")
                    _dbg(f"  [Google/{source}] {field}: handle='{handle}' en {href[:80]}")
                    if handle.lower() in SKIP_HANDLES or len(handle) <= 1:
                        _dbg(f"    ✗ RECHAZADO — handle en lista negra")
                        continue
                    url = _build_social_url(field, handle)
                    if url:
                        if _validate_social(url, lounge, source):
                            result[field] = url

            # Email en mailto:
            if "email" in missing and "email" not in result:
                if href.startswith("mailto:"):
                    email = href.replace("mailto:", "").split("?")[0].strip().lower()
                    domain = email.split("@")[-1] if "@" in email else ""
                    if domain and domain not in JUNK_EMAIL_DOMAINS:
                        result["email"] = email
                        _dbg(f"  [Google/{source}] email: {email}")

    # KP primero, luego orgánicos
    _process_links(kp_links, "knowledge_panel")

    remaining = (missing | {"website"}) - set(result.keys())
    if remaining:
        _process_links(all_links, "organic")

    # Email en texto como último recurso
    if "email" in missing and "email" not in result:
        for e in EMAIL_RE.findall(html):
            domain = e.split("@")[-1].lower()
            if domain not in JUNK_EMAIL_DOMAINS and "." in domain and "google" not in domain:
                result["email"] = e.lower()
                _dbg(f"  [Google/text] email: {e}")
                break

    # Si el parser no encontró nada y hay KP, mostrar de todas formas los orgánicos
    if DEBUG and not result and kp:
        _dbg("  [Google] KP presente pero parser devolvió vacío — mostrando orgánicos:")
        _debug_google_organic(soup, html)

    _dbg(f"  [Google] Resultado final del parser: {result}")
    return result


# ── Playwright: scrape website ────────────────────────────────────────────────

async def _scrape_website(page, url: str, missing: set[str], lounge: dict) -> dict:
    """
    Usa el crawler multi-página de browser_enricher.
    Visita hasta MAX_PAGES páginas internas buscando los campos faltantes.
    """
    from enrichment.browser_enricher import scrape_website as _crawl

    _dbg(f"  [P1] Crawler iniciado en: {url}")
    try:
        result = await _crawl(url, page, missing=missing)
    except Exception as e:
        logger.debug(f"Website crawl failed {url}: {e}")
        result = {}

    if DEBUG:
        print(f"\n  ┌─ scrape_website() devolvió ──────────────────────────")
        if result:
            for k, v in result.items():
                print(f"  │  {k:<20} = {v}")
        else:
            print(f"  │  (dict vacío)")
        print(f"  └──────────────────────────────────────────────────────")

    return result


# ── Playwright: búsqueda en Google ───────────────────────────────────────────

async def _google_search(page, lounge: dict, missing: set[str]) -> dict:
    from playwright_stealth import Stealth

    name  = lounge.get("name", "")
    city  = lounge.get("city", "")
    state = lounge.get("state", "")
    query = f"{name} {city} {state}"

    _dbg(f"  [Google] Buscando: \"{query}\"")

    try:
        await Stealth().apply_stealth_async(page)
        search_url = f"https://www.google.com/search?q={url_quote(query)}&hl=en"
        await page.goto(search_url, timeout=20000, wait_until="domcontentloaded")
        await asyncio.sleep(random.uniform(PAUSE_MIN, PAUSE_MAX))
        html = await page.content()
        _stats["google_searches"] += 1
        search_missing = missing | {"website"}
        return _extract_from_google_page(html, search_missing, lounge)
    except Exception as e:
        logger.debug(f"Google search failed for {name}: {e}")
        return {}


# ── Enriquecimiento de un lounge ──────────────────────────────────────────────

async def enrich_one(lounge: dict, browser, db, cache: dict) -> dict:
    name    = lounge.get("name", "")
    missing = _missing_fields(lounge)

    if not missing:
        return {}

    if DEBUG:
        print(f"\n{'─'*55}")
        print(f"  {name} | {lounge.get('city')}, {lounge.get('state')}")
        print(f"  Faltan: {sorted(missing)}")
        print(f"  Website: {lounge.get('website') or '(ninguno)'}")

    found   = {}
    website = lounge.get("website")

    page = await browser.new_page()
    try:
        # ── PRIORIDAD 1: Scrape del website existente ────────────────────────
        if website:
            _stats["website_reused"] += 1
            _dbg(f"  [P1] Scrapeando website: {website}")
            scraped = await _scrape_website(page, website, missing, lounge)
            for k, v in scraped.items():
                if v and k in missing:
                    found[k] = v
                elif DEBUG:
                    if not v:
                        _dbg(f"  [P1] '{k}' ignorado — valor vacío o None")
                    elif k not in missing:
                        _dbg(f"  [P1] '{k}' ignorado — ya existía en el registro (no estaba en missing)")
            missing -= set(found.keys())

            if DEBUG:
                print(f"\n  ┌─ found después de P1 ────────────────────────────────")
                if found:
                    for k, v in found.items():
                        print(f"  │  {k:<20} = {v}")
                else:
                    print(f"  │  (vacío — scrape no aportó nada nuevo)")
                print(f"  │  Siguen faltando: {sorted(missing)}")
                print(f"  └──────────────────────────────────────────────────────")

        # ── PRIORIDAD 2: Una sola búsqueda en Google ─────────────────────────
        if missing:
            cache_key = (name.lower(), lounge.get("city", "").lower(), lounge.get("state", "").lower())

            if cache_key in cache:
                _dbg(f"  [P2] Usando caché para '{name}'")
                google_result = cache[cache_key]
            else:
                google_result = await _google_search(page, lounge, missing)
                cache[cache_key] = google_result

            new_website = google_result.get("website")
            _dbg(f"  [P2] Google devolvió: {[k for k,v in google_result.items() if v]}")

            for k, v in google_result.items():
                if k == "website":
                    continue
                if v and k in missing and k not in found:
                    found[k] = v
            missing -= set(found.keys())

            # ── PRIORIDAD 3: Visitar el website nuevo encontrado ──────────────
            if new_website and not website:
                found["website"] = new_website
                _stats["website_found"] += 1
                _dbg(f"  [P3] Nuevo website: {new_website}")
                if missing:
                    scraped2 = await _scrape_website(page, new_website, missing, lounge)
                    for k, v in scraped2.items():
                        if v and k in missing and k not in found:
                            found[k] = v
                    missing -= set(found.keys())
                    _dbg(f"  [P3] Encontrado: {list(scraped2.keys())} | Siguen faltando: {sorted(missing)}")

    finally:
        await page.close()

    # ── Guardar en DB ─────────────────────────────────────────────────────────
    if found:
        update = {k: v for k, v in found.items() if v}

        if DEBUG:
            print(f"\n  ┌─ update enviado a Supabase ───────────────────────────")
            for k, v in update.items():
                print(f"  │  {k:<20} = {v}")
            print(f"  │  Total columnas en el update: {len(update)}")
            print(f"  └──────────────────────────────────────────────────────")

        try:
            resp = (
                db.client.table("cigar_lounges")
                .update(update)
                .eq("id", lounge["id"])
                .execute()
            )
            if DEBUG:
                rows = resp.data or []
                print(f"  ✓ Supabase respondió: {len(rows)} fila(s) afectada(s)")
                if rows:
                    # Mostrar qué columnas quedaron realmente escritas
                    updated_row = rows[0]
                    written = {k: updated_row.get(k) for k in update if k in updated_row}
                    print(f"  ┌─ columnas confirmadas en DB ──────────────────────────")
                    for k, v in written.items():
                        match = "✓" if str(v) == str(update[k]) else "✗ DIFIERE"
                        print(f"  │  {k:<20} = {v}  {match}")
                    if not written:
                        print(f"  │  (Supabase no devolvió el registro — normal sin .select())")
                    print(f"  └──────────────────────────────────────────────────────")
        except Exception as e:
            logger.warning(f"DB update failed for {name}: {e}")
            if DEBUG:
                print(f"  ✗ ERROR en DB update: {e}")
    elif DEBUG:
        print(f"\n  ── Sin datos nuevos — no se envió nada a Supabase")

    # ── Actualizar estadísticas ───────────────────────────────────────────────
    _stats["processed"] += 1
    for field in ("email", "facebook_url", "instagram_url", "tiktok_url"):
        if found.get(field):
            key = field.split("_")[0] + "_found"
            _stats[key] += 1

    return found


# ── Batch principal ───────────────────────────────────────────────────────────

async def enrich_batch_async(lounges: list[dict], db, concurrency: int = CONCURRENCY):
    from playwright.async_api import async_playwright

    _reset_stats()
    total     = len(lounges)
    done      = 0
    semaphore = asyncio.Semaphore(concurrency)
    cache: dict = {}

    # En modo DEBUG bajar la concurrencia para que los prints no se mezclen
    if DEBUG:
        concurrency = 1

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )

        async def process(lounge):
            nonlocal done
            async with semaphore:
                await enrich_one(lounge, browser, db, cache)
                done += 1
                if not DEBUG and (done % 5 == 0 or done == total):
                    elapsed = time.time() - _stats["start_time"]
                    avg = elapsed / done if done else 0
                    print(
                        f"  [{done}/{total}] "
                        f"web:{_stats['website_found']} "
                        f"email:{_stats['email_found']} "
                        f"fb:{_stats['facebook_found']} "
                        f"ig:{_stats['instagram_found']} "
                        f"tt:{_stats['tiktok_found']} "
                        f"searches:{_stats['google_searches']} "
                        f"avg:{avg:.1f}s",
                        end="\r",
                        flush=True,
                    )

        tasks = [process(l) for l in lounges]
        await asyncio.gather(*tasks, return_exceptions=True)
        await browser.close()

    if not DEBUG:
        print()
    _print_stats(total)


def _print_stats(total: int):
    elapsed = time.time() - _stats["start_time"]
    avg = elapsed / _stats["processed"] if _stats["processed"] else 0

    print(f"\n{'='*55}")
    print(f"  RESULTADOS FINALES")
    print(f"{'='*55}")
    print(f"  Procesados               : {_stats['processed']}")
    print(f"  Website encontrados      : {_stats['website_found']}")
    print(f"  Website reutilizados     : {_stats['website_reused']}")
    print(f"  Emails encontrados       : {_stats['email_found']}")
    print(f"  Facebook encontrados     : {_stats['facebook_found']}")
    print(f"  Instagram encontrados    : {_stats['instagram_found']}")
    print(f"  TikTok encontrados       : {_stats['tiktok_found']}")
    print(f"  Búsquedas en Google      : {_stats['google_searches']}")
    print(f"  Coincidencias descartadas: {_stats['discarded']}")
    print(f"  Tiempo promedio/negocio  : {avg:.1f}s")
    print(f"{'='*55}\n")
