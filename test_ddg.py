from dotenv import load_dotenv
load_dotenv()

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

query = "Grand Havana Room Beverly Hills instagram"
print(f"Buscando: {query}")

resp = requests.get(
    "https://lite.duckduckgo.com/lite/",
    params={"q": query, "kl": "us-en"},
    headers=HEADERS,
    timeout=15,
)

print(f"Status: {resp.status_code}")
print(f"Longitud: {len(resp.text)}")
print(f"\nPrimeros 1000 chars:\n{resp.text[:1000]}")

# Parsear resultados
soup = BeautifulSoup(resp.text, "html.parser")
links = soup.select("a[href]")
print(f"\nLinks encontrados: {len(links)}")
for a in links[:10]:
    print(f"  {a.get('href', '')}")
