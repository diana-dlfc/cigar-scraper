# debug_crawler.py
# Depuración detallada del crawler para un negocio específico.
# Muestra estadísticas por página y guarda el HTML cuando no encuentra redes.
#
# Run:
#   venv\Scripts\python debug_crawler.py https://padron.com
#   venv\Scripts\python debug_crawler.py padron.com        (agrega https:// solo)

import sys
import asyncio
import re
from pathlib import Path
from urllib.parse import urlparse, urljoin
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

import enrichment.browser_enricher as _be
_be.DEBUG = True  # activar logs del bypass en browser_enricher

from enrichment.browser_enricher import (
    _bypass_overlays,
    _find_priority_links,
    EMAIL_RE,
    JUNK_EMAIL_DOMAINS,
    MAX_PAGES,
    SOCIAL_RE,
)

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_URL = "https://padron.com"
OUTPUT_DIR  = Path("debug_html")

SOCIAL_DOMAINS = {
    "facebook.com": "Facebook",
    "instagram.com": "Instagram",
    "tiktok.com":    "TikTok",
}

# Patrones para detectar overlays ANTES de superarlos
_AGE_RE    = re.compile(r"\b(21|age|born|birth|legal|smoking|verify age|are you|how old)\b", re.I)
_COOKIE_RE = re.compile(r"\b(cookie|gdpr|consent|accept)\b", re.I)


# ── Helpers de presentación ───────────────────────────────────────────────────

def _sep(c="─", n=65):   print(c * n)
def _h(t):                _sep("═"); print(f"  {t}"); _sep("═")
def _field(k, v):         print(f"  {k:<20}: {v}")


# ── Análisis de una sola página ───────────────────────────────────────────────

async def _analyze_page(page, url: str, idx: int,
                        output_dir: Path, is_homepage: bool = False) -> dict:
    print(f"\n  ── Página {idx}: {url}")

    # ── Navegar ───────────────────────────────────────────────────────────────
    status = "?"
    try:
        resp   = await page.goto(url, timeout=25000, wait_until="domcontentloaded")
        status = resp.status if resp else "?"
    except Exception as e:
        print(f"  ✗ ERROR de carga: {e}")
        return {}

    _field("HTTP status", status)

    try:
        _field("Título", (await page.title())[:80] or "(sin título)")
    except Exception:
        _field("Título", "(error)")

    # ── Detectar overlays (antes de superarlos) ───────────────────────────────
    html_pre   = await page.content()
    text_pre   = BeautifulSoup(html_pre, "lxml").get_text(" ", strip=True)[:4000]
    age_detected    = bool(_AGE_RE.search(text_pre))
    cookie_detected = bool(_COOKIE_RE.search(text_pre))

    if age_detected:
        _field("Age Gate",      "⚠  DETECTADO")
    if cookie_detected:
        _field("Cookie banner", "⚠  DETECTADO")

    # ── Superar overlays (siempre, no solo en homepage) ───────────────────────
    await _bypass_overlays(page)
    await asyncio.sleep(0.4)

    if age_detected:
        _field("Age Gate",      "→ intento de bypass realizado")
    if cookie_detected:
        _field("Cookie banner", "→ intento de bypass realizado")

    # ── HTML final ────────────────────────────────────────────────────────────
    html = await page.content()
    soup = BeautifulSoup(html, "lxml")

    # ── iframes ───────────────────────────────────────────────────────────────
    iframes = soup.find_all("iframe")
    if iframes:
        srcs = [f.get("src", "(sin src)")[:70] for f in iframes[:3]]
        _field(f"iframes ({len(iframes)})", ", ".join(srcs))

    # ── Contar enlaces ────────────────────────────────────────────────────────
    base_dom = urlparse(url).netloc.lower()
    all_a    = soup.find_all("a", href=True)

    int_links   = []
    ext_links   = []
    social_hits = {label: [] for label in SOCIAL_DOMAINS.values()}

    for a in all_a:
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or href.startswith("javascript"):
            continue
        abs_href = urljoin(url, href)
        dom = urlparse(abs_href).netloc.lower()

        matched_social = False
        for s_dom, label in SOCIAL_DOMAINS.items():
            if s_dom in dom or s_dom in href:
                social_hits[label].append(abs_href)
                matched_social = True
                break

        if not matched_social:
            if dom == base_dom or not dom:
                int_links.append(abs_href)
            else:
                ext_links.append(abs_href)

    _field("<a> totales", len(all_a))
    _field("  internos",  len(int_links))
    _field("  externos",  len(ext_links))

    # ── Redes sociales ────────────────────────────────────────────────────────
    for label, hits in social_hits.items():
        unique = list(dict.fromkeys(hits))  # dedup preservando orden
        if unique:
            _field(f"  {label} ({len(unique)})", unique[0])
            for u in unique[1:3]:
                _field("", f"              {u}")
        else:
            _field(f"  {label}", "0")

    # ── Emails ────────────────────────────────────────────────────────────────
    emails = []
    for a in all_a:
        href = a.get("href", "")
        if href.startswith("mailto:"):
            e = href.replace("mailto:", "").split("?")[0].strip().lower()
            if "@" in e and e.split("@")[-1] not in JUNK_EMAIL_DOMAINS:
                emails.append(e)
    for e in EMAIL_RE.findall(html):
        dom = e.split("@")[-1].lower()
        if dom not in JUNK_EMAIL_DOMAINS and "." in dom and e.lower() not in emails:
            emails.append(e.lower())

    if emails:
        _field(f"  Emails ({len(emails)})", emails[0])
        for e in emails[1:3]:
            _field("", f"              {e}")
    else:
        _field("  Emails", "0")

    # ── Guardar HTML si no hay redes ──────────────────────────────────────────
    total_social = sum(len(v) for v in social_hits.values())
    if total_social == 0:
        safe_name = re.sub(r"[^\w]", "_", url.replace("https://", "").replace("http://", ""))[:60]
        fpath     = output_dir / f"page_{idx:02d}_{safe_name}.html"
        fpath.write_text(html, encoding="utf-8")
        print(f"\n  ⚠  Sin redes sociales → HTML guardado en: {fpath.name}")

    return {
        "url":       url,
        "status":    status,
        "facebook":  social_hits["Facebook"],
        "instagram": social_hits["Instagram"],
        "tiktok":    social_hits["TikTok"],
        "emails":    emails,
        "int_links": int_links,
        "html":      html,
    }


# ── Runner principal ──────────────────────────────────────────────────────────

async def debug_single(start_url: str):
    OUTPUT_DIR.mkdir(exist_ok=True)

    _h(f"DEBUG CRAWLER — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    _field("URL de inicio", start_url)
    _field("MAX_PAGES",     MAX_PAGES)
    _field("Output dir",    str(OUTPUT_DIR.resolve()))
    print()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,  # visible para debug
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        page = await browser.new_page()
        await Stealth().apply_stealth_async(page)

        # ── 1. Homepage ───────────────────────────────────────────────────────
        homepage = await _analyze_page(page, start_url, 1, OUTPUT_DIR, is_homepage=True)
        if not homepage:
            print("\n✗ No se pudo cargar la homepage. Abortando.")
            await browser.close()
            return

        # ── 2. Descubrir páginas internas ─────────────────────────────────────
        html_home    = homepage["html"]
        intern_links = _find_priority_links(html_home, start_url)

        print(f"\n  Páginas internas descubiertas ({len(intern_links)}):")
        for lnk in intern_links:
            print(f"    → {lnk}")

        if not intern_links:
            print("  (ninguna encontrada)")

        # ── 3. Visitar páginas internas ───────────────────────────────────────
        all_results = [homepage]
        for i, link in enumerate(intern_links[:MAX_PAGES], start=2):
            result = await _analyze_page(page, link, i, OUTPUT_DIR)
            if result:
                all_results.append(result)

            # Parar si ya tenemos todo
            got_fb = any(r.get("facebook") for r in all_results)
            got_ig = any(r.get("instagram") for r in all_results)
            got_em = any(r.get("emails")    for r in all_results)
            if got_fb and got_ig and got_em:
                print(f"\n  ✓ Campos clave encontrados — deteniendo crawler.")
                break

        await browser.close()

    # ── Resumen final ─────────────────────────────────────────────────────────
    print()
    _h(f"RESUMEN — {len(all_results)} página(s) visitadas")

    all_fb = list(dict.fromkeys(u for r in all_results for u in r.get("facebook", [])))
    all_ig = list(dict.fromkeys(u for r in all_results for u in r.get("instagram", [])))
    all_tt = list(dict.fromkeys(u for r in all_results for u in r.get("tiktok", [])))
    all_em = list(dict.fromkeys(u for r in all_results for u in r.get("emails", [])))

    _field("Facebook",  all_fb[0] if all_fb else "✗ no encontrado")
    _field("Instagram", all_ig[0] if all_ig else "✗ no encontrado")
    _field("TikTok",    all_tt[0] if all_tt else "✗ no encontrado")
    _field("Email",     all_em[0] if all_em else "✗ no encontrado")
    print()
    _field("HTML guardados", str(OUTPUT_DIR.resolve()))
    _sep("═")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    raw = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    url = raw if raw.startswith("http") else f"https://{raw}"
    asyncio.run(debug_single(url))
