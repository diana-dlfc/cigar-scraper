import asyncio
from playwright.async_api import async_playwright
from urllib.parse import quote

async def test():
    from playwright_stealth import Stealth
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        page = await browser.new_page()
        await Stealth().apply_stealth_async(page)

        query = "Cuban Style Cigars Miami FL cigar lounge"
        url = f"https://www.bing.com/search?q={quote(query)}"
        print(f"Buscando: {url}\n")

        await page.goto(url, timeout=20000, wait_until="domcontentloaded")
        await asyncio.sleep(2)

        # Ver el título de la página
        title = await page.title()
        print(f"Título: {title}")

        # Intentar diferentes selectores
        for selector in ["li.b_algo h2 a", ".b_algo a", "h2 a", "a[href]"]:
            links = await page.eval_on_selector_all(
                selector,
                "els => els.map(e => e.href)"
            )
            http_links = [l for l in links if l.startswith("http") and "bing.com" not in l]
            print(f"Selector '{selector}': {len(links)} total, {len(http_links)} externos")
            for l in http_links[:3]:
                print(f"  → {l}")
            if http_links:
                break

        # Screenshot para ver qué está renderizando
        await page.screenshot(path="debug_bing.png")
        print("\nScreenshot guardado: debug_bing.png")

        await browser.close()

asyncio.run(test())
