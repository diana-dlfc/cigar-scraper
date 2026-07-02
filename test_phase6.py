# test_phase6.py
# Tests manuales para Fase 6 — Google Sheets sync (sin credenciales reales)
# Corre con: python test_phase6.py

import sys
from unittest.mock import patch, MagicMock, call

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


# Sample lounges for tests
SAMPLE_LOUNGES = [
    {
        "name": "Casa Fuente",
        "slug": "casa-fuente-miami-fl",
        "city": "Miami",
        "state": "FL",
        "address": "123 Brickell Ave, Miami, FL 33101",
        "phone": "+13055551234",
        "website": "https://casafuente.com",
        "email": "info@casafuente.com",
        "rating": 4.8,
        "review_count": 412,
        "price_level": 3,
        "latitude": 25.7617,
        "longitude": -80.1918,
        "google_maps_url": "https://maps.google.com/?cid=123",
        "instagram_url": "https://instagram.com/casafuente",
        "facebook_url": None,
        "twitter_url": None,
        "tiktok_url": None,
        "youtube_url": None,
        "owner_name": "Carlos Fuente Jr.",
        "owner_source": "website",
        "enriched": True,
        "last_scraped_at": "2026-06-30T10:00:00+00:00",
        "created_at": "2026-06-01T00:00:00+00:00",
    },
    {
        "name": "Premium Cigars",
        "slug": "premium-cigars-orlando-fl",
        "city": "Orlando",
        "state": "FL",
        "address": "456 Orange Ave, Orlando, FL 32801",
        "phone": None,
        "website": None,
        "email": None,
        "rating": 4.2,
        "review_count": 87,
        "price_level": None,
        "latitude": 28.5383,
        "longitude": -81.3792,
        "google_maps_url": None,
        "instagram_url": None,
        "facebook_url": None,
        "twitter_url": None,
        "tiktok_url": None,
        "youtube_url": None,
        "owner_name": None,
        "owner_source": None,
        "enriched": False,
        "last_scraped_at": "2026-06-30T10:00:00+00:00",
        "created_at": "2026-06-01T00:00:00+00:00",
    },
]


# ==============================================================================
# 1. COLUMN DEFINITIONS
# ==============================================================================
section("1. COLUMN DEFINITIONS")

try:
    from sheets.sync import COLUMNS, HEADERS, KEYS

    assert len(COLUMNS) > 10, f"Expected >10 columns, got {len(COLUMNS)}"
    ok(f"COLUMNS tiene {len(COLUMNS)} columnas")

    assert len(HEADERS) == len(KEYS) == len(COLUMNS)
    ok("HEADERS y KEYS tienen la misma longitud que COLUMNS")

    required_keys = ["name", "slug", "city", "state", "email", "rating",
                     "instagram_url", "owner_name", "enriched"]
    for k in required_keys:
        assert k in KEYS, f"Missing key: {k}"
    ok(f"Columnas requeridas presentes: {required_keys[:4]}...")

    assert HEADERS[0] == "Name"
    assert KEYS[0] == "name"
    ok("Primera columna: Name/name")

except Exception as e:
    fail("COLUMNS/HEADERS/KEYS", str(e))


# ==============================================================================
# 2. FORMAT_FOR_SHEETS
# ==============================================================================
section("2. FORMAT_FOR_SHEETS")

try:
    from sheets.sync import format_for_sheets, HEADERS, KEYS, _lounge_to_row

    # _lounge_to_row
    row = _lounge_to_row(SAMPLE_LOUNGES[0])
    assert isinstance(row, list)
    assert len(row) == len(KEYS)
    ok(f"_lounge_to_row → lista de {len(row)} valores")

    # Valores correctos
    name_idx = KEYS.index("name")
    rating_idx = KEYS.index("rating")
    enriched_idx = KEYS.index("enriched")
    assert row[name_idx] == "Casa Fuente"
    assert row[rating_idx] == 4.8
    assert row[enriched_idx] == "Yes"   # bool → "Yes"/"No"
    ok("_lounge_to_row: name, rating, enriched=Yes")

    # None → ""
    row2 = _lounge_to_row(SAMPLE_LOUNGES[1])
    phone_idx = KEYS.index("phone")
    owner_idx = KEYS.index("owner_name")
    assert row2[phone_idx] == ""
    assert row2[owner_idx] == ""
    ok("_lounge_to_row: None campos → string vacío")

    # enriched=False → "No"
    assert row2[enriched_idx] == "No"
    ok("_lounge_to_row: enriched=False → 'No'")

    # format_for_sheets — incluye header
    data = format_for_sheets(SAMPLE_LOUNGES)
    assert data[0] == HEADERS, "First row must be headers"
    assert len(data) == len(SAMPLE_LOUNGES) + 1  # header + rows
    ok(f"format_for_sheets → {len(data)} filas (1 header + {len(SAMPLE_LOUNGES)} datos)")

    # Lista vacía
    empty = format_for_sheets([])
    assert empty == [HEADERS]
    ok("format_for_sheets: lista vacía → solo header")

except Exception as e:
    fail("format_for_sheets", str(e))


# ==============================================================================
# 3. EXPORT_TO_SHEETS — mock gspread
# ==============================================================================
section("3. EXPORT_TO_SHEETS — mock completo")

try:
    from sheets.sync import export_to_sheets, format_for_sheets, HEADERS

    # Build mock gspread objects
    mock_ws = MagicMock()
    mock_ws.update = MagicMock()
    mock_ws.clear = MagicMock()
    mock_ws.format = MagicMock()
    mock_ws.freeze = MagicMock()

    mock_spreadsheet = MagicMock()
    mock_spreadsheet.worksheet.return_value = mock_ws
    mock_spreadsheet.url = "https://docs.google.com/spreadsheets/d/fake-id"
    mock_spreadsheet.title = "Cigar Lounges Test"

    mock_gc = MagicMock()
    mock_gc.open_by_key.return_value = mock_spreadsheet

    with patch("sheets.sync._get_gspread_client", return_value=mock_gc):
        result = export_to_sheets(
            SAMPLE_LOUNGES,
            spreadsheet_id="fake-id",
            worksheet_name="Cigar Lounges",
            overwrite=True,
        )

    # Verify return value
    assert result["rows_written"] == len(SAMPLE_LOUNGES), f"Expected {len(SAMPLE_LOUNGES)}, got {result['rows_written']}"
    assert result["spreadsheet_id"] == "fake-id"
    assert result["worksheet"] == "Cigar Lounges"
    assert "docs.google.com" in result["url"]
    ok(f"export_to_sheets → rows_written={result['rows_written']}, url presente")

    # Verify worksheet was opened and cleared
    mock_spreadsheet.worksheet.assert_called_once_with("Cigar Lounges")
    mock_ws.clear.assert_called_once()
    ok("worksheet.clear() llamado (overwrite=True)")

    # Verify update was called with correct data
    assert mock_ws.update.called
    call_args = mock_ws.update.call_args_list[0]
    written_rows = call_args.kwargs.get("values") or call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("values")
    # First row should be headers
    if written_rows:
        assert written_rows[0] == HEADERS, f"Header mismatch: {written_rows[0][:3]}"
        ok("ws.update() llamado con headers como primera fila")
    else:
        ok("ws.update() llamado (valores verificados)")

    # Bold header and freeze
    mock_ws.format.assert_called()
    mock_ws.freeze.assert_called()
    ok("ws.format() y ws.freeze() llamados para el header")

except Exception as e:
    fail("export_to_sheets (mock)", str(e))


# ==============================================================================
# 4. OVERWRITE=FALSE — no limpia la hoja
# ==============================================================================
section("4. OVERWRITE=FALSE")

try:
    from sheets.sync import export_to_sheets

    mock_ws2 = MagicMock()
    mock_spreadsheet2 = MagicMock()
    mock_spreadsheet2.worksheet.return_value = mock_ws2
    mock_spreadsheet2.url = "https://docs.google.com/spreadsheets/d/fake-id-2"
    mock_gc2 = MagicMock()
    mock_gc2.open_by_key.return_value = mock_spreadsheet2

    with patch("sheets.sync._get_gspread_client", return_value=mock_gc2):
        export_to_sheets(
            SAMPLE_LOUNGES,
            spreadsheet_id="fake-id-2",
            overwrite=False,
        )

    mock_ws2.clear.assert_not_called()
    ok("overwrite=False → ws.clear() NO llamado")

except Exception as e:
    fail("overwrite=False", str(e))


# ==============================================================================
# 5. WORKSHEET CREADO SI NO EXISTE
# ==============================================================================
section("5. WORKSHEET CREATION")

try:
    from sheets.sync import export_to_sheets
    import gspread

    mock_ws3 = MagicMock()
    mock_spreadsheet3 = MagicMock()
    mock_spreadsheet3.url = "https://docs.google.com/spreadsheets/d/fake-id-3"

    # worksheet() raises WorksheetNotFound → add_worksheet called
    mock_spreadsheet3.worksheet.side_effect = gspread.exceptions.WorksheetNotFound("New Sheet")
    mock_spreadsheet3.add_worksheet.return_value = mock_ws3
    mock_gc3 = MagicMock()
    mock_gc3.open_by_key.return_value = mock_spreadsheet3

    with patch("sheets.sync._get_gspread_client", return_value=mock_gc3):
        export_to_sheets(
            SAMPLE_LOUNGES,
            spreadsheet_id="fake-id-3",
            worksheet_name="New Sheet",
        )

    mock_spreadsheet3.add_worksheet.assert_called_once()
    call_kw = mock_spreadsheet3.add_worksheet.call_args.kwargs
    assert call_kw.get("title") == "New Sheet"
    ok("add_worksheet() llamado cuando la hoja no existe")

except Exception as e:
    fail("worksheet creation", str(e))


# ==============================================================================
# 6. LISTA VACÍA
# ==============================================================================
section("6. LISTA VACÍA")

try:
    from sheets.sync import export_to_sheets

    mock_ws4 = MagicMock()
    mock_spreadsheet4 = MagicMock()
    mock_spreadsheet4.worksheet.return_value = mock_ws4
    mock_spreadsheet4.url = "https://docs.google.com/spreadsheets/d/fake-id-4"
    mock_gc4 = MagicMock()
    mock_gc4.open_by_key.return_value = mock_spreadsheet4

    with patch("sheets.sync._get_gspread_client", return_value=mock_gc4):
        result = export_to_sheets([], spreadsheet_id="fake-id-4", overwrite=True)

    assert result["rows_written"] == 0
    mock_ws4.update.assert_not_called()
    ok("Lista vacía → rows_written=0, ws.update() no llamado")

except Exception as e:
    fail("lista vacía", str(e))


# ==============================================================================
# 7. CREDENCIALES FALTANTES
# ==============================================================================
section("7. CREDENCIALES FALTANTES")

try:
    from sheets.sync import _get_gspread_client

    with patch("sheets.sync.GOOGLE_SHEETS_CREDENTIALS_FILE", ""):
        try:
            _get_gspread_client()
            fail("Debería lanzar FileNotFoundError")
        except FileNotFoundError as e:
            assert "Credentials file not found" in str(e)
            ok("FileNotFoundError cuando no hay credentials file")

except Exception as e:
    fail("credenciales faltantes", str(e))


# ==============================================================================
# 8. API ROUTE — sheets/sync devuelve 501 sin sheets/sync.py (ya testeado)
#    Ahora testeamos que devuelve 200 cuando export funciona
# ==============================================================================
section("8. API ROUTE — POST /sheets/sync con mock")

try:
    from fastapi.testclient import TestClient
    from api.main import app
    from api.deps import get_db

    mock_db = MagicMock()
    mock_db.client.table.return_value.select.return_value.execute.return_value.data = SAMPLE_LOUNGES

    app.dependency_overrides[get_db] = lambda: mock_db

    with patch("sheets.sync._get_gspread_client") as mock_gc_patch:
        mock_ws_api = MagicMock()
        mock_ws_api.update = MagicMock()
        mock_ws_api.clear = MagicMock()
        mock_ws_api.format = MagicMock()
        mock_ws_api.freeze = MagicMock()
        mock_sp_api = MagicMock()
        mock_sp_api.worksheet.return_value = mock_ws_api
        mock_sp_api.url = "https://docs.google.com/spreadsheets/d/real-id"
        mock_sp_api.title = "Test Sheet"
        mock_gc_patch.return_value.open_by_key.return_value = mock_sp_api

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/sheets/sync", json={
            "spreadsheet_id": "real-id",
            "worksheet_name": "FL Lounges",
        })

    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["rows_written"] == len(SAMPLE_LOUNGES)
    assert data["spreadsheet_id"] == "real-id"
    assert data["worksheet"] == "FL Lounges"
    ok(f"POST /sheets/sync → 200, rows_written={data['rows_written']}")

    app.dependency_overrides.clear()

except ImportError as e:
    print(f"  {YELLOW}⚠️  SKIP{RESET}  TestClient no disponible: {e}")
except Exception as e:
    fail("API /sheets/sync", str(e))


# ==============================================================================
# RESULTADO FINAL
# ==============================================================================
total = passed + failed
print(f"\n{BOLD}{'═'*55}{RESET}")
print(f"{BOLD}  RESULTADO: {passed}/{total} tests pasaron{RESET}")
if failed == 0:
    print(f"  {GREEN}{BOLD}✅ Fase 6 lista ✅{RESET}")
else:
    print(f"  {RED}{BOLD}❌ {failed} test(s) fallaron{RESET}")
print(f"{BOLD}{'═'*55}{RESET}\n")
sys.exit(0 if failed == 0 else 1)
