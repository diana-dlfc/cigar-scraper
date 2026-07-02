# test_phase5.py
# Tests manuales para Fase 5 — sin llamadas HTTP reales
# Corre con: python test_phase5.py

import sys
from unittest.mock import patch, MagicMock

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
    print(f"  {GREEN}OK{RESET}  {name}")

def fail(name, reason=""):
    global failed
    failed += 1
    print(f"  {RED}FAIL{RESET}  {name}")
    if reason:
        print(f"       {RED}{reason}{RESET}")

def section(title):
    print(f"\n{CYAN}{BOLD}{'─'*55}{RESET}")
    print(f"{CYAN}{BOLD}  {title}{RESET}")
    print(f"{CYAN}{BOLD}{'─'*55}{RESET}")

# ==============================================================================
# 1. EMAIL FINDER
# ==============================================================================
section("1. EMAIL FINDER")

try:
    from enrichment.email_finder import _extract_emails_from_text, find_emails

    # _extract_emails_from_text
    html = """
    <html><body>
    Contact us at info@premiumcigars.com or manager@cigarsmiami.com
    <a href="mailto:hello@example-lounge.com">Email us</a>
    Bad: not-an-email, fake@, @nodomain, spam@sentry.io
    </body></html>
    """
    emails = _extract_emails_from_text(html)
    assert "info@premiumcigars.com" in emails
    assert "manager@cigarsmiami.com" in emails
    assert "hello@example-lounge.com" in emails
    assert not any("sentry.io" in e for e in emails)
    ok(f"_extract_emails_from_text: found {emails}")

    # find_emails — mock website scraping
    mock_html = "<html><body>Email: contact@testlounge.com</body></html>"
    lounge = {"name": "Test Lounge", "website": "https://testlounge.com"}

    with patch("enrichment.email_finder._fetch", return_value=mock_html):
        result = find_emails(lounge)

    assert result["email"] == "contact@testlounge.com"
    assert result["email_source"] == "website"
    assert "contact@testlounge.com" in result["emails_all"]
    ok(f"find_emails (mock): email='{result['email']}', source='{result['email_source']}'")

    # find_emails — no website
    result_empty = find_emails({"name": "No Website Lounge"})
    assert result_empty["email"] is None
    assert result_empty["emails_all"] == []
    ok("find_emails: returns None when no website")

    # find_emails — website returns nothing
    with patch("enrichment.email_finder._fetch", return_value="<html>no contact here</html>"):
        result_none = find_emails({"name": "Empty Site", "website": "https://empty.com"})
    assert result_none["email"] is None
    ok("find_emails: returns None when no email in site")

except Exception as e:
    fail("email_finder", str(e))
    import traceback; traceback.print_exc()


# ==============================================================================
# 2. SOCIAL FINDER
# ==============================================================================
section("2. SOCIAL FINDER")

try:
    from enrichment.social_finder import _extract_socials_from_html, find_socials

    # _extract_socials_from_html
    html = """
    <html><body>
    <a href="https://instagram.com/premiumcigarsmiami">Instagram</a>
    <a href="https://facebook.com/premiumcigarsmiami">Facebook</a>
    <a href="https://x.com/cigarsmiami">Twitter</a>
    <a href="https://yelp.com/biz/premium-cigars-miami-fl">Yelp</a>
    <a href="https://facebook.com/sharer/sharer.php">Share</a>
    </body></html>
    """
    socials = _extract_socials_from_html(html)
    assert socials.get("instagram") == "https://instagram.com/premiumcigarsmiami"
    ok(f"instagram detected: {socials['instagram']}")
    assert socials.get("facebook") == "https://facebook.com/premiumcigarsmiami"
    ok(f"facebook detected: {socials['facebook']}")
    assert socials.get("twitter") is not None
    ok(f"twitter/X detected: {socials['twitter']}")
    assert socials.get("yelp") is not None
    ok(f"yelp detected: {socials['yelp']}")
    # Sharer link should NOT be extracted
    assert "sharer" not in (socials.get("facebook") or "")
    ok("facebook sharer link correctly ignored")

    # find_socials — full mock
    lounge = {"name": "Premium Cigars", "website": "https://premiumcigars.com"}
    with patch("enrichment.social_finder._fetch", return_value=html):
        result = find_socials(lounge)
    assert result["instagram"] is not None
    assert result["facebook"] is not None
    ok(f"find_socials (mock): {[k for k,v in result.items() if v]}")

    # find_socials — no website
    result_empty = find_socials({"name": "No Website"})
    assert all(v is None for v in result_empty.values())
    ok("find_socials: all None when no website")

    # TikTok detection
    tiktok_html = '<a href="https://tiktok.com/@cigarlounge">TikTok</a>'
    socials_tt = _extract_socials_from_html(tiktok_html)
    assert socials_tt.get("tiktok") is not None
    ok(f"tiktok detected: {socials_tt['tiktok']}")

except Exception as e:
    fail("social_finder", str(e))
    import traceback; traceback.print_exc()


# ==============================================================================
# 3. OWNER FINDER
# ==============================================================================
section("3. OWNER FINDER")

try:
    from enrichment.owner_finder import _extract_owner_from_text, _clean_name, find_owner

    # _clean_name
    assert _clean_name("John Smith") == "John Smith"
    assert _clean_name("John") is None            # single word
    assert _clean_name("john smith") == "john smith"  # no casing req now
    assert _clean_name("Suite 200") is None       # digit
    ok("_clean_name: filters single words and digits")

    # _extract_owner_from_text
    texts = [
        ("Owner John Smith opened the lounge in 2015.", "John Smith"),
        ("Founded by Maria Lopez, a cigar aficionado.", "Maria Lopez"),
        ("Robert Johnson, owner of Premium Cigars.", "Robert Johnson"),
        ("No owner mentioned anywhere here.", None),
    ]
    for text, expected in texts:
        result = _extract_owner_from_text(text)
        if expected:
            assert result == expected, f"Expected '{expected}', got '{result}' for: {text}"
            ok(f"_extract_owner_from_text: '{result}' ← '{text[:40]}...'")
        else:
            assert result is None, f"Expected None, got '{result}'"
            ok("_extract_owner_from_text: correctly returns None when no owner")

    # find_owner — mock website with owner
    owner_html = "<html><body>Our owner, Carlos Medina, has 20 years of experience.</body></html>"
    lounge = {"name": "Test Lounge", "website": "https://test.com", "city": "Miami", "state": "FL"}
    with patch("enrichment.owner_finder._fetch", return_value=owner_html):
        result = find_owner(lounge)
    assert result["owner_name"] == "Carlos Medina"
    assert result["owner_source"] == "website"
    ok(f"find_owner (mock website): '{result['owner_name']}' from {result['owner_source']}")

    # find_owner — no info found
    with patch("enrichment.owner_finder._fetch", return_value="<html>nothing here</html>"):
        result_none = find_owner(lounge)
    assert result_none["owner_name"] is None
    ok("find_owner: returns None when no owner found")

    # find_owner — no website (mock fetch so no real HTTP to Google)
    with patch("enrichment.owner_finder._fetch", return_value=None):
        result_no_site = find_owner({"name": "No Website", "city": "Tampa", "state": "FL"})
    assert result_no_site["owner_name"] is None
    ok("find_owner: returns None with no website and no city match")

except Exception as e:
    fail("owner_finder", str(e))
    import traceback; traceback.print_exc()


# ==============================================================================
# 4. PIPELINE
# ==============================================================================
section("4. ENRICHMENT PIPELINE")

try:
    from enrichment.pipeline import enrich_lounge, enrich_batch

    lounge = {
        "id": "test-uuid-001",
        "name": "Casa del Cigarro",
        "website": "https://casadelcigarro.com",
        "city": "Miami",
        "state": "FL",
    }

    email_html  = "<html><body>Email: info@casadelcigarro.com</body></html>"
    social_html = '<a href="https://instagram.com/casadelcigarro">IG</a>'
    owner_html  = "<html><body>Founded by Pedro Alvarez in 2010.</body></html>"

    # Patch all three HTTP fetchers
    with patch("enrichment.email_finder._fetch", return_value=email_html), \
         patch("enrichment.social_finder._fetch", return_value=social_html), \
         patch("enrichment.owner_finder._fetch", return_value=owner_html):

        result = enrich_lounge(lounge, db=None)

    assert result["email"] == "info@casadelcigarro.com"
    assert result["instagram"] is not None
    assert result["owner_name"] == "Pedro Alvarez"
    assert result["enriched"] == True
    assert "last_enriched_at" in result
    ok(f"enrich_lounge: email={result['email']}, ig={result['instagram']}, owner={result['owner_name']}")

    # enrich_batch — skip already enriched
    lounges = [
        {**lounge, "id": "uuid-1", "enriched": False},
        {**lounge, "id": "uuid-2", "enriched": True},   # should skip
        {**lounge, "id": "uuid-3", "enriched": False},
    ]

    with patch("enrichment.email_finder._fetch", return_value=email_html), \
         patch("enrichment.social_finder._fetch", return_value=social_html), \
         patch("enrichment.owner_finder._fetch", return_value=owner_html):

        results = enrich_batch(lounges, db=None, delay=0, skip_already_enriched=True)

    assert len(results) == 3
    assert results[1].get("skipped") == True    # uuid-2 was already enriched
    assert results[0].get("email") is not None
    assert results[2].get("email") is not None
    ok(f"enrich_batch: 3 lounges, 1 skipped, 2 enriched")

    # enrich_batch — force re-enrich
    with patch("enrichment.email_finder._fetch", return_value=email_html), \
         patch("enrichment.social_finder._fetch", return_value=social_html), \
         patch("enrichment.owner_finder._fetch", return_value=owner_html):

        results_all = enrich_batch(lounges, db=None, delay=0, skip_already_enriched=False)

    assert all(not r.get("skipped") for r in results_all)
    ok("enrich_batch (skip_already_enriched=False): all 3 processed")

except Exception as e:
    fail("pipeline", str(e))
    import traceback; traceback.print_exc()


# ==============================================================================
# RESUMEN
# ==============================================================================
section("RESUMEN")
total = passed + failed
pct = int(passed / total * 100) if total else 0
color = GREEN if failed == 0 else (YELLOW if pct >= 70 else RED)
print(f"\n  {color}{BOLD}Tests: {passed}/{total} pasaron ({pct}%){RESET}")
if failed == 0:
    print(f"\n  {GREEN}{BOLD}Fase 5 lista{RESET}\n")
else:
    print(f"\n  {RED}{BOLD}{failed} test(s) fallando{RESET}\n")
    sys.exit(1)
