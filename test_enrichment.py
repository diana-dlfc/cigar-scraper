import asyncio
import aiohttp
from dotenv import load_dotenv
load_dotenv()

from database.supabase_client import SupabaseClient

db = SupabaseClient()

# Toma un lounge real de la DB
resp = db.client.table("cigar_lounges").select("id,name,city,state,website").limit(1).execute()
lounge = resp.data[0]
print(f"Lounge: {lounge['name']} - {lounge['city']}, {lounge['state']}")

query = f'"{lounge["name"]}" cigar lounge {lounge["city"]} {lounge["state"]} official site'
print(f"Query: {query}\n")

SEARXNG_INSTANCES = [
    "https://searx.be",
    "https://search.sapti.me",
    "https://searxng.site",
]

async def test():
    async with aiohttp.ClientSession() as session:
        for instance in SEARXNG_INSTANCES:
            print(f"Probando {instance}...")
            try:
                async with session.get(
                    f"{instance}/search",
                    params={"q": query, "format": "json", "categories": "general"},
                    timeout=aiohttp.ClientTimeout(total=10),
                    headers={"User-Agent": "Mozilla/5.0"},
                ) as resp:
                    print(f"  Status: {resp.status}")
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        results = data.get("results", [])
                        print(f"  Resultados: {len(results)}")
                        for r in results[:3]:
                            print(f"    → {r.get('url')}")
                        break
                    else:
                        text = await resp.text()
                        print(f"  Response: {text[:200]}")
            except Exception as e:
                print(f"  ERROR: {e}")

asyncio.run(test())
