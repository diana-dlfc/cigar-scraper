import asyncio
from playwright.async_api import async_playwright
from dotenv import load_dotenv
load_dotenv()

from database.supabase_client import SupabaseClient

db = SupabaseClient()

# Toma un lounge con source_url de Yelp
resp = db.client.table("cigar_lounges") \
    .select("id,name,city,state,source_url,website") \
    .not_.is_("source_url", "null") \
    .limit(1).execute()

lounge = resp.data[0]
print(f"Lounge: {lounge['name']} — {lounge['city']}, {lounge['state']}")
print(f"Yelp URL: {lounge['source_url']}")
print(f"Website actual: {lounge.get('website')}\n")

async def test():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()

        await page.goto(lounge["source_url"], timeout=20000, wait_until="domcontentloaded")
        await asyncio.sleep(2)

        title = await page.title()
        print(f"Título: {title}")

        # Buscar el link del website en la página de Yelp
        # Yelp muestra el website en un link con clase que incluye "businessWebsite"
        for selector in [
            "a[href*='biz_redir']",
            "a[data-testid='biz-website']",
            "p.businessWebsite a",
            "a[target='_blank'][rel='noopener']",
            "a[href*='redirect']",
        ]:
            try:
                links = await page.eval_on_selector_all(
                    selector,
                    "els => els.map(e => ({href: e.href, text: e.innerText}))"
                )
                if links:
                    print(f"Selector '{selector}': {links[:3]}")
            except Exception as e:
                print(f"Selector '{selector}': error — {e}")

        # Screenshot
        await page.screenshot(path="debug_yelp.png")
        print("\nScreenshot: debug_yelp.png")
        await browser.close()

asyncio.run(test())
