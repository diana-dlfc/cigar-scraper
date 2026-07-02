# test_phase3.py
# Tests manuales para Fase 3 — sin necesidad de API keys
# Corre con: python test_phase3.py

import sys
import math

# ── Colores ────────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

passed = 0
failed = 0

def ok(name):
    global passed
    passed += 1
    print(f"  {GREEN}✅ PASS{RESET}  {name}")

def fail(name, reason=""):
    global failed
    failed += 1
    print(f"  {RED}❌ FAIL{RESET}  {name}")
    if reason:
        print(f"         {RED}{reason}{RESET}")

def section(title):
    print(f"\n{CYAN}{BOLD}{'─'*55}{RESET}")
    print(f"{CYAN}{BOLD}  {title}{RESET}")
    print(f"{CYAN}{BOLD}{'─'*55}{RESET}")

# ==============================================================================
# 1. CONFIG
# ==============================================================================
section("1. CONFIG — settings, states, search_config, cities")

try:
    from config.settings import (
        SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY,
        GOOGLE_PLACES_API_KEY, YELP_API_KEY,
        DEFAULT_SEARCH_RADIUS_METERS, REQUEST_DELAY_SECONDS,
    )
    ok("config.settings importa correctamente")
    assert DEFAULT_SEARCH_RADIUS_METERS == 50000, f"Expected 50000, got {DEFAULT_SEARCH_RADIUS_METERS}"
    ok(f"DEFAULT_SEARCH_RADIUS_METERS = {DEFAULT_SEARCH_RADIUS_METERS}")
except Exception as e:
    fail("config.settings", str(e))

try:
    from config.states import US_STATES, get_state_name, get_state_bbox, get_all_state_abbrs
    assert len(US_STATES) == 51, f"Expected 51 states+DC, got {len(US_STATES)}"
    ok(f"US_STATES tiene {len(US_STATES)} entradas (50 estados + DC)")

    bbox = get_state_bbox("FL")
    assert bbox is not None and len(bbox) == 4
    ok(f"FL bounding box: {bbox}")

    name = get_state_name("TX")
    assert name == "Texas"
    ok(f"get_state_name('TX') = '{name}'")

    abbrs = get_all_state_abbrs()
    assert "NY" in abbrs and "CA" in abbrs and "DC" in abbrs
    ok("get_all_state_abbrs() incluye NY, CA, DC")
except Exception as e:
    fail("config.states", str(e))

try:
    from config.search_config import (
        SEARCH_QUERIES, CIGAR_KEYWORDS, EXCLUDE_KEYWORDS,
        GRID_CELL_SIZE_KM, NEARBY_SEARCH_RADIUS_M,
    )
    assert len(SEARCH_QUERIES) >= 3
    ok(f"SEARCH_QUERIES tiene {len(SEARCH_QUERIES)} queries: {SEARCH_QUERIES}")
    assert "cigar" in CIGAR_KEYWORDS
    ok(f"CIGAR_KEYWORDS incluye 'cigar': {CIGAR_KEYWORDS}")
    assert "hookah" in EXCLUDE_KEYWORDS
    ok(f"EXCLUDE_KEYWORDS incluye 'hookah': {EXCLUDE_KEYWORDS}")
    ok(f"GRID_CELL_SIZE_KM={GRID_CELL_SIZE_KM}, NEARBY_RADIUS={NEARBY_SEARCH_RADIUS_M}m")
except Exception as e:
    fail("config.search_config", str(e))

try:
    from config.cities.florida import FLORIDA_CITIES
    assert len(FLORIDA_CITIES) >= 50
    ok(f"FLORIDA_CITIES tiene {len(FLORIDA_CITIES)} ciudades")
    assert "Miami" in FLORIDA_CITIES and "Tampa" in FLORIDA_CITIES
    ok("Contiene Miami y Tampa")
except Exception as e:
    fail("config.cities.florida", str(e))


# ==============================================================================
# 2. UTILS — helpers
# ==============================================================================
section("2. UTILS — helpers.py")

try:
    from utils.helpers import (
        slugify, make_slug, normalize_phone, normalize_url,
        now_utc, safe_float, safe_int, chunks, extract_state_from_address
    )

    # slugify
    assert slugify("Casa Fuente!") == "casa-fuente"
    assert slugify("El Mejor Cigár") == "el-mejor-cigar"
    assert slugify("  Spaces  ") == "spaces"
    ok("slugify: caracteres especiales, acentos, espacios")

    # make_slug
    s = make_slug("Premium Cigars", "Miami", "FL")
    assert s == "premium-cigars-miami-fl", f"Got: {s}"
    ok(f"make_slug → '{s}'")

    # normalize_phone
    assert normalize_phone("(305) 555-1234") == "+13055551234"
    assert normalize_phone("3055551234") == "+13055551234"
    assert normalize_phone("13055551234") == "+13055551234"
    assert normalize_phone("") is None
    assert normalize_phone(None) is None
    ok("normalize_phone: formatos US → E.164")

    # normalize_url
    assert normalize_url("example.com") == "https://example.com"
    assert normalize_url("https://example.com") == "https://example.com"
    assert normalize_url("") is None
    assert normalize_url(None) is None
    ok("normalize_url: agrega https:// cuando falta")

    # safe_float / safe_int
    assert safe_float("4.5") == 4.5
    assert safe_float(None) is None
    assert safe_float("abc") is None
    assert safe_int("42") == 42
    assert safe_int(None) is None
    ok("safe_float / safe_int: conversiones seguras")

    # chunks
    result = list(chunks([1,2,3,4,5], 2))
    assert result == [[1,2],[3,4],[5]]
    ok(f"chunks([1..5], 2) → {result}")

    # extract_state_from_address
    state = extract_state_from_address("123 Main St, Miami, FL 33101, USA")
    assert state == "FL", f"Got: {state}"
    ok(f"extract_state_from_address → '{state}'")

    # now_utc
    ts = now_utc()
    assert "T" in ts and "+" in ts
    ok(f"now_utc() → '{ts}'")

except Exception as e:
    fail("utils.helpers", str(e))


# ==============================================================================
# 3. UTILS — validators
# ==============================================================================
section("3. UTILS — validators.py")

try:
    from utils.validators import is_cigar_venue, validate_lounge_data, sanitize_lounge_data

    # is_cigar_venue — positivos
    assert is_cigar_venue("Premium Cigar Lounge") == True
    assert is_cigar_venue("Casa del Cigár") == True
    assert is_cigar_venue("The Humidor") == True
    assert is_cigar_venue("Smoke & Lounge") == True
    ok("is_cigar_venue: detecta cigar lounges válidos")

    # is_cigar_venue — negativos
    assert is_cigar_venue("Pizza Palace") == False
    assert is_cigar_venue("Coffee Shop") == False
    ok("is_cigar_venue: rechaza lugares que no son cigar lounges")

    # is_cigar_venue — exclusiones
    assert is_cigar_venue("Hookah Cigar Lounge") == False
    assert is_cigar_venue("Vape & Smoke Shop") == False
    assert is_cigar_venue("Cannabis Dispensary") == False
    ok("is_cigar_venue: excluye hookah, vape, cannabis")

    # validate_lounge_data — válido
    valid_data = {
        "name": "Casa Fuente",
        "slug": "casa-fuente-las-vegas-nv",
        "latitude": 36.17,
        "longitude": -115.14,
        "rating": 4.8,
        "website": "https://example.com",
        "phone": "+17025551234",
    }
    is_valid, errors = validate_lounge_data(valid_data)
    assert is_valid, f"Should be valid, got errors: {errors}"
    ok("validate_lounge_data: datos válidos pasan")

    # validate_lounge_data — inválido
    bad_data = {"latitude": 999, "rating": 6.0, "website": "not-a-url"}
    is_valid, errors = validate_lounge_data(bad_data)
    assert not is_valid
    ok(f"validate_lounge_data: datos inválidos fallan → {errors}")

    # sanitize_lounge_data
    raw = {
        "name": "  El Cigarro  ",
        "rating": "4.5",
        "review_count": "123",
        "price_level": "2",
        "enriched": None,
    }
    cleaned = sanitize_lounge_data(raw)
    assert cleaned["name"] == "El Cigarro"
    assert cleaned["rating"] == 4.5
    assert cleaned["review_count"] == 123
    assert cleaned["enriched"] == False
    assert cleaned["country"] == "US"
    ok(f"sanitize_lounge_data: trimming, casting, defaults aplicados")

except Exception as e:
    fail("utils.validators", str(e))


# ==============================================================================
# 4. UTILS — deduplicator
# ==============================================================================
section("4. UTILS — deduplicator.py")

try:
    from utils.deduplicator import haversine_distance_km, are_duplicates, deduplicate_batch

    # haversine
    d = haversine_distance_km(25.77, -80.19, 25.77, -80.19)
    assert d == 0.0
    ok("haversine: misma coordenada → 0 km")

    d_miami_ft = haversine_distance_km(25.7617, -80.1918, 26.1224, -80.1373)
    assert 40 < d_miami_ft < 50, f"Miami→Ft.Lauderdale esperado ~45km, got {d_miami_ft:.1f}"
    ok(f"haversine: Miami → Ft. Lauderdale ≈ {d_miami_ft:.1f} km")

    # are_duplicates — mismo slug
    a = {"slug": "casa-fuente-miami-fl", "latitude": 25.77, "longitude": -80.19}
    b = {"slug": "casa-fuente-miami-fl", "latitude": 25.77, "longitude": -80.20}
    assert are_duplicates(a, b) == True
    ok("are_duplicates: mismo slug → duplicado")

    # are_duplicates — mismo source_id
    c1 = {"source_id": "ChIJabc123", "slug": "lounge-a"}
    c2 = {"source_id": "ChIJabc123", "slug": "lounge-b"}
    assert are_duplicates(c1, c2) == True
    ok("are_duplicates: mismo source_id → duplicado")

    # are_duplicates — muy cercanos + mismo nombre
    close_a = {"name": "Premium Cigar", "slug": "a", "latitude": 25.7617, "longitude": -80.1918}
    close_b = {"name": "Premium Cigar", "slug": "b", "latitude": 25.7618, "longitude": -80.1919}
    assert are_duplicates(close_a, close_b) == True
    ok("are_duplicates: mismo nombre + <100m → duplicado")

    # are_duplicates — diferentes
    far_a = {"name": "Cigar A", "slug": "cigar-a", "latitude": 25.77, "longitude": -80.19}
    far_b = {"name": "Cigar B", "slug": "cigar-b", "latitude": 26.12, "longitude": -80.13}
    assert are_duplicates(far_a, far_b) == False
    ok("are_duplicates: diferentes nombre y ubicación → no duplicado")

    # deduplicate_batch
    lounges = [
        {"name": "A", "slug": "lounge-a", "latitude": 25.77, "longitude": -80.19},
        {"name": "A", "slug": "lounge-a", "latitude": 25.77, "longitude": -80.19},  # dup
        {"name": "B", "slug": "lounge-b", "latitude": 26.12, "longitude": -80.13},
        {"name": "A", "slug": "lounge-a", "latitude": 25.77, "longitude": -80.20},  # dup por slug
    ]
    unique = deduplicate_batch(lounges)
    assert len(unique) == 2, f"Expected 2 unique, got {len(unique)}"
    ok(f"deduplicate_batch: 4 registros → {len(unique)} únicos")

except Exception as e:
    fail("utils.deduplicator", str(e))


# ==============================================================================
# 5. SCRAPERS — lógica de parseo con datos mock (sin API)
# ==============================================================================
section("5. SCRAPERS — parseo con datos mock (sin llamadas API)")

# Mock de un resultado de Google Places Text Search
MOCK_GOOGLE_RESULT = {
    "place_id": "ChIJMockCigar001",
    "name": "Premium Cigar Lounge Miami",
    "types": ["bar", "establishment"],
    "geometry": {"location": {"lat": 25.7617, "lng": -80.1918}},
    "formatted_address": "123 Brickell Ave, Miami, FL 33131, USA",
    "rating": 4.7,
    "user_ratings_total": 312,
    "vicinity": "123 Brickell Ave, Miami",
}

MOCK_GOOGLE_DETAIL = {
    "formatted_phone_number": "(305) 555-7890",
    "website": "https://premiumcigarmiami.com",
    "rating": 4.7,
    "user_ratings_total": 312,
    "price_level": 3,
    "url": "https://maps.google.com/?cid=001",
    "editorial_summary": {"overview": "Upscale cigar lounge in Brickell."},
    "address_components": [
        {"types": ["locality"], "long_name": "Miami", "short_name": "Miami"},
        {"types": ["administrative_area_level_1"], "long_name": "Florida", "short_name": "FL"},
        {"types": ["country"], "long_name": "United States", "short_name": "US"},
    ],
}

MOCK_YELP_BUSINESS = {
    "id": "premium-cigar-lounge-miami",
    "name": "Premium Cigar Lounge",
    "categories": [
        {"alias": "cigarlounge", "title": "Cigar Lounges"},
    ],
    "location": {
        "display_address": ["123 Brickell Ave", "Miami, FL 33131"],
        "city": "Miami",
        "state": "FL",
        "address1": "123 Brickell Ave",
    },
    "coordinates": {"latitude": 25.7617, "longitude": -80.1918},
    "display_phone": "(305) 555-7890",
    "phone": "+13055557890",
    "rating": 4.5,
    "review_count": 289,
    "price": "$$$",
    "url": "https://www.yelp.com/biz/premium-cigar-lounge-miami",
}

try:
    # Testear _parse_place_result de google_places directamente
    from scrapers.google_places import _parse_place_result, _extract_address_component

    # Sin detalle
    result = _parse_place_result(MOCK_GOOGLE_RESULT)
    assert result is not None, "Debería parsear el resultado"
    assert result["name"] == "Premium Cigar Lounge Miami"
    assert result["latitude"] == 25.7617
    assert result["rating"] == 4.7
    assert result["country"] == "US"
    assert result["enriched"] == False
    ok(f"google_places._parse_place_result (sin detalle): name='{result['name']}', rating={result['rating']}")

    # Con detalle
    result_full = _parse_place_result(MOCK_GOOGLE_RESULT, MOCK_GOOGLE_DETAIL)
    assert result_full is not None
    assert result_full["phone"] == "+13055557890"
    assert result_full["website"] == "https://premiumcigarmiami.com"
    assert result_full["city"] == "Miami"
    assert result_full["state"] == "FL"
    assert result_full["price_level"] == 3
    assert result_full["description"] == "Upscale cigar lounge in Brickell."
    ok(f"google_places._parse_place_result (con detalle): city={result_full['city']}, state={result_full['state']}, phone={result_full['phone']}")

    # Verificar que rechaza lugar no cigar
    bad_result = {**MOCK_GOOGLE_RESULT, "name": "Pizza Palace"}
    assert _parse_place_result(bad_result) is None
    ok("google_places._parse_place_result: rechaza 'Pizza Palace'")

    # _extract_address_component
    city = _extract_address_component(MOCK_GOOGLE_DETAIL["address_components"], "locality")
    assert city == "Miami"
    ok(f"_extract_address_component('locality') → '{city}'")

except Exception as e:
    fail("scrapers.google_places (parseo mock)", str(e))

try:
    from scrapers.grid_search import generate_grid, _parse_nearby_result, _extract_address_component

    # generate_grid con Florida completa
    bbox = (24.52, -87.63, 31.0, -80.03)
    grid_25 = generate_grid(*bbox, cell_size_km=25)
    grid_50 = generate_grid(*bbox, cell_size_km=50)
    assert len(grid_25) > len(grid_50), "Grid más fino debe tener más puntos"
    assert len(grid_25) > 100, f"FL con 25km debería tener >100 puntos, got {len(grid_25)}"
    ok(f"generate_grid FL: 25km→{len(grid_25)} puntos, 50km→{len(grid_50)} puntos")

    # Verificar que los puntos están dentro del bbox
    sample = grid_25[:5]
    for lat, lon in sample:
        assert bbox[0] <= lat <= bbox[2], f"lat {lat} fuera del bbox"
        assert bbox[1] <= lon <= bbox[3], f"lon {lon} fuera del bbox"
    ok(f"Puntos dentro del bounding box (muestra: {sample[0]})")

    # _parse_nearby_result
    nearby_mock = {
        "place_id": "ChIJMock002",
        "name": "Cigar Bar Downtown",
        "types": ["bar", "establishment"],
        "geometry": {"location": {"lat": 25.77, "lng": -80.20}},
        "vicinity": "456 SW 8th St, Miami",
        "rating": 4.2,
        "user_ratings_total": 88,
    }
    parsed = _parse_nearby_result(nearby_mock)
    assert parsed is not None
    assert parsed["name"] == "Cigar Bar Downtown"
    assert parsed["latitude"] == 25.77
    ok(f"grid_search._parse_nearby_result: '{parsed['name']}' lat={parsed['latitude']}")

except Exception as e:
    fail("scrapers.grid_search (parseo mock)", str(e))

try:
    from scrapers.yelp import _parse_yelp_business

    parsed = _parse_yelp_business(MOCK_YELP_BUSINESS)
    assert parsed is not None
    assert parsed["name"] == "Premium Cigar Lounge"
    assert parsed["city"] == "Miami"
    assert parsed["state"] == "FL"
    assert parsed["rating"] == 4.5
    assert parsed["review_count"] == 289
    assert parsed["price_level"] == 3  # "$$$" → 3
    assert parsed["latitude"] == 25.7617
    ok(f"yelp._parse_yelp_business: '{parsed['name']}', price_level={parsed['price_level']}, rating={parsed['rating']}")

    # Rechaza hookah
    hookah_biz = {**MOCK_YELP_BUSINESS, "name": "Hookah Palace", "categories": [{"alias": "hookah", "title": "Hookah Bars"}]}
    assert _parse_yelp_business(hookah_biz) is None
    ok("yelp._parse_yelp_business: rechaza hookah bar")

except Exception as e:
    fail("scrapers.yelp (parseo mock)", str(e))


# ==============================================================================
# 6. DATABASE — supabase_client (import y estructura)
# ==============================================================================
section("6. DATABASE — supabase_client estructura")

try:
    from database.supabase_client import SupabaseClient
    import inspect
    methods = [m for m, _ in inspect.getmembers(SupabaseClient, predicate=inspect.isfunction)]
    required = ["upsert_lounge", "get_lounge_by_slug", "get_lounges",
                "insert_source", "source_exists", "create_job", "update_job", "get_job"]
    for m in required:
        assert m in methods, f"Método '{m}' no encontrado"
    ok(f"SupabaseClient tiene todos los métodos requeridos: {required}")
except ImportError:
    print(f"  {YELLOW}⏭  SKIP{RESET}  database.supabase_client — supabase SDK no instalado en este entorno (usa el venv de Windows)")
except Exception as e:
    fail("database.supabase_client", str(e))

# ==============================================================================
# RESUMEN
# ==============================================================================
section("RESUMEN DE RESULTADOS")

total = passed + failed
pct = int(passed / total * 100) if total else 0
color = GREEN if failed == 0 else (YELLOW if pct >= 70 else RED)

print(f"\n  {color}{BOLD}Tests: {passed}/{total} pasaron ({pct}%){RESET}")

if failed == 0:
    print(f"\n  {GREEN}{BOLD}🎉 Todo en orden — Fase 3 lista para continuar{RESET}\n")
else:
    print(f"\n  {RED}{BOLD}⚠️  Hay {failed} test(s) fallando{RESET}\n")
    sys.exit(1)
