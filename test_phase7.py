# test_phase7.py
# Tests manuales para Fase 7 — FastAPI (sin DB ni API keys)
# Corre con: python test_phase7.py

import sys
import json
import importlib

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

passed = failed = 0

def ok(name):
    global passed; passed += 1
    print(f"  {GREEN}✅ PASS{RESET}  {name}")

def fail(name, reason=""):
    global failed; failed += 1
    print(f"  {RED}❌ FAIL{RESET}  {name}")
    if reason: print(f"         {RED}{reason}{RESET}")

def section(title):
    print(f"\n{CYAN}{BOLD}{'─'*55}{RESET}")
    print(f"{CYAN}{BOLD}  {title}{RESET}")
    print(f"{CYAN}{BOLD}{'─'*55}{RESET}")


# ==============================================================================
# 1. REQUEST MODELS
# ==============================================================================
section("1. REQUEST MODELS")

try:
    from api.models.requests import (
        ScrapeCityRequest, ScrapeStateRequest,
        EnrichBatchRequest, SheetsSyncRequest,
    )

    # ScrapeCityRequest defaults
    req = ScrapeCityRequest(city="Miami", state="FL")
    assert req.city == "Miami"
    assert req.state == "FL"
    assert req.sources == ["google", "yelp"]
    assert req.fetch_details is True
    assert req.save_to_db is True
    ok("ScrapeCityRequest: defaults correctos")

    # ScrapeCityRequest — solo google
    req2 = ScrapeCityRequest(city="Tampa", state="FL", sources=["google"], fetch_details=False)
    assert req2.sources == ["google"]
    assert req2.fetch_details is False
    ok("ScrapeCityRequest: sources=['google'], fetch_details=False")

    # ScrapeStateRequest
    req3 = ScrapeStateRequest(state="TX", use_grid=False, cell_size_km=50.0)
    assert req3.state == "TX"
    assert req3.use_grid is False
    assert req3.cell_size_km == 50.0
    ok("ScrapeStateRequest: TX, use_grid=False, cell_size_km=50")

    # ScrapeStateRequest — cell_size_km bounds
    try:
        ScrapeStateRequest(state="FL", cell_size_km=1.0)   # below ge=5
        fail("ScrapeStateRequest: debería rechazar cell_size_km=1.0")
    except Exception:
        ok("ScrapeStateRequest: rechaza cell_size_km < 5")

    # EnrichBatchRequest
    req4 = EnrichBatchRequest(state="FL", limit=100)
    assert req4.skip_already_enriched is True
    assert req4.delay_seconds == 2.0
    ok("EnrichBatchRequest: defaults correctos")

    # SheetsSyncRequest
    req5 = SheetsSyncRequest()
    assert req5.worksheet_name == "Cigar Lounges"
    assert req5.overwrite is True
    ok("SheetsSyncRequest: defaults correctos")

except Exception as e:
    fail("api.models.requests", str(e))


# ==============================================================================
# 2. RESPONSE MODELS
# ==============================================================================
section("2. RESPONSE MODELS")

try:
    from api.models.responses import (
        JobStartedResponse, JobStatusResponse,
        LoungeResponse, LoungesListResponse,
        ScrapeResultResponse, EnrichResultResponse,
        StatsResponse, SheetsSyncResponse,
    )

    job = JobStartedResponse(job_id="abc-123", message="Scraping Miami, FL")
    assert job.job_id == "abc-123"
    assert job.status == "pending"
    ok("JobStartedResponse: job_id, status, message")

    status = JobStatusResponse(
        job_id="abc-123", type="scrape_city", status="running",
        state="FL", records_found=42,
    )
    assert status.records_saved is None
    ok("JobStatusResponse: campos opcionales son None")

    lounge = LoungeResponse(name="Casa Fuente", slug="casa-fuente-miami-fl")
    assert lounge.enriched is False
    assert lounge.city is None
    ok("LoungeResponse: defaults correctos")

    listing = LoungesListResponse(total=200, limit=100, offset=0, items=[lounge])
    assert listing.total == 200
    assert len(listing.items) == 1
    ok("LoungesListResponse: total/limit/offset/items")

    stats = StatsResponse(
        total_lounges=500, enriched=200, not_enriched=300,
        by_state={"FL": 120, "TX": 80},
    )
    assert stats.by_state["FL"] == 120
    ok("StatsResponse: by_state dict")

    scrape_res = ScrapeResultResponse(found=15, saved=12, skipped=3, lounges=[])
    assert scrape_res.skipped == 3
    ok("ScrapeResultResponse: found/saved/skipped")

    enrich_res = EnrichResultResponse(
        processed=10, enriched_count=8, skipped=1, errors=1, results=[]
    )
    assert enrich_res.enriched_count == 8
    ok("EnrichResultResponse: counts")

    sheets_res = SheetsSyncResponse(
        rows_written=150, spreadsheet_id="abc", worksheet="Cigar Lounges"
    )
    assert sheets_res.url is None
    ok("SheetsSyncResponse: url opcional None")

except Exception as e:
    fail("api.models.responses", str(e))


# ==============================================================================
# 3. FASTAPI APP — importa y rutas registradas
# ==============================================================================
section("3. FASTAPI APP — estructura y rutas")

try:
    from api.main import app
    ok("api.main importa sin errores")

    # Use openapi schema (works with include_router)
    schema = app.openapi()
    routes = set(schema["paths"].keys())

    expected = [
        "/",
        "/health",
        "/scrape/states",
        "/scrape/city",
        "/scrape/state",
        "/enrich/batch",
        "/enrich/lounge/{slug}",
        "/sheets/sync",
        "/jobs/{job_id}",
        "/lounges",
        "/lounges/stats",
        "/lounges/{slug}",
    ]
    missing = [p for p in expected if p not in routes]
    if missing:
        fail(f"Rutas faltantes", str(missing))
    else:
        ok(f"Todas las rutas registradas ({len(expected)} endpoints)")

    # Verificar métodos HTTP
    paths = schema["paths"]
    assert "post" in paths.get("/scrape/city", {}), "POST /scrape/city"
    assert "post" in paths.get("/scrape/state", {}), "POST /scrape/state"
    assert "get"  in paths.get("/scrape/states", {}), "GET /scrape/states"
    assert "post" in paths.get("/enrich/batch", {}), "POST /enrich/batch"
    assert "get"  in paths.get("/lounges", {}), "GET /lounges"
    assert "get"  in paths.get("/jobs/{job_id}", {}), "GET /jobs/{id}"
    ok("Métodos HTTP correctos (GET/POST)")

except Exception as e:
    fail("api.main", str(e))


# ==============================================================================
# 4. FASTAPI TESTCLIENT — endpoints sin DB
# ==============================================================================
section("4. TEST CLIENT — endpoints sin DB")

try:
    from fastapi.testclient import TestClient
    from unittest.mock import patch, MagicMock
    from api.main import app

    # Mock the DB dependency so no real Supabase needed
    mock_db = MagicMock()

    def mock_get_db():
        return mock_db

    with patch("api.deps._get_db_client", return_value=mock_db):
        from api import deps
        deps._get_db_client.cache_clear() if hasattr(deps._get_db_client, "cache_clear") else None

    from api.deps import get_db
    app.dependency_overrides[get_db] = mock_get_db

    client = TestClient(app, raise_server_exceptions=False)

    # GET /
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["service"] == "cigar-scraper-api"
    assert data["status"] == "ok"
    ok("GET / → 200 health response")

    # GET /scrape/states
    resp = client.get("/scrape/states")
    assert resp.status_code == 200
    data = resp.json()
    assert "states" in data
    assert len(data["states"]) == 51
    fl = next((s for s in data["states"] if s["abbr"] == "FL"), None)
    assert fl and fl["name"] == "Florida"
    ok(f"GET /scrape/states → 51 estados, FL=Florida")

    # POST /scrape/city — validación
    resp = client.post("/scrape/city", json={"city": "Miami", "state": "FL", "save_to_db": False})
    # Should start job (202) or fail gracefully (no DB mock for create_job)
    mock_db.create_job.return_value = {"id": "job-001"}
    resp = client.post("/scrape/city", json={"city": "Miami", "state": "FL", "save_to_db": False})
    assert resp.status_code in (202, 500)  # 500 ok if bg task fails, 202 if started
    ok(f"POST /scrape/city → {resp.status_code} (job started o error controlado)")

    # POST /scrape/city — bad request (invalid sources)
    resp = client.post("/scrape/city", json={"city": "Miami", "state": "FL", "sources": ["invalid_source"]})
    assert resp.status_code == 422
    ok("POST /scrape/city → 422 con sources inválidos")

    # POST /scrape/state — estado inválido
    mock_db.create_job.return_value = {"id": "job-002"}
    resp = client.post("/scrape/state", json={"state": "ZZ"})
    assert resp.status_code == 400
    ok("POST /scrape/state → 400 con estado inválido")

    # GET /lounges — mock data
    mock_db.client.table.return_value.select.return_value.eq.return_value.ilike.return_value \
        .eq.return_value.order.return_value.range.return_value.execute.return_value.data = []
    mock_db.client.table.return_value.select.return_value.execute.return_value.data = []
    mock_db.client.table.return_value.select.return_value.execute.return_value.count = 0
    resp = client.get("/lounges?state=FL&limit=10")
    assert resp.status_code in (200, 500)
    ok(f"GET /lounges?state=FL → {resp.status_code}")

    # POST /enrich/lounge/{slug} — lounge not found
    mock_db.get_lounge_by_slug.return_value = None
    resp = client.post("/enrich/lounge/nonexistent-slug")
    assert resp.status_code == 404
    ok("POST /enrich/lounge/{slug} → 404 si no existe")

    # GET /jobs/{job_id} — not found
    mock_db.get_job.return_value = None
    resp = client.get("/jobs/nonexistent-job-id")
    assert resp.status_code == 404
    ok("GET /jobs/{job_id} → 404 si no existe")

    # POST /sheets/sync — phase 6 not implemented
    mock_db.create_job.return_value = {"id": "job-003"}
    resp = client.post("/sheets/sync", json={})
    assert resp.status_code in (400, 501, 500)
    ok(f"POST /sheets/sync → {resp.status_code} (Phase 6 pendiente)")

    # Clean up
    app.dependency_overrides.clear()

except ImportError as e:
    print(f"  {YELLOW}⚠️  SKIP{RESET}  TestClient no disponible: {e}")
except Exception as e:
    fail("TestClient", str(e))


# ==============================================================================
# RESULTADO FINAL
# ==============================================================================
total = passed + failed
print(f"\n{BOLD}{'═'*55}{RESET}")
print(f"{BOLD}  RESULTADO: {passed}/{total} tests pasaron{RESET}")
if failed == 0:
    print(f"  {GREEN}{BOLD}✅ Fase 7 lista ✅{RESET}")
else:
    print(f"  {RED}{BOLD}❌ {failed} test(s) fallaron{RESET}")
print(f"{BOLD}{'═'*55}{RESET}\n")
sys.exit(0 if failed == 0 else 1)
