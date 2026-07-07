# enrichment/pipeline.py
"""
Enrichment pipeline — runs email, social, and owner finders
for a batch of lounges and saves results to Supabase.

Usage:
    from enrichment.pipeline import enrich_lounge, enrich_batch

    result = enrich_lounge(lounge_dict, db_client)
    enrich_batch(lounge_list, db_client, delay=2.0)
"""

import time
from loguru import logger
from utils.helpers import now_utc

from enrichment.email_finder  import find_emails
from enrichment.social_finder import find_socials
from enrichment.owner_finder  import find_owner


def enrich_lounge(lounge: dict, db=None) -> dict:
    """
    Run all enrichers for a single lounge.

    Args:
        lounge: Dict with at least {id, name, website, city, state}
        db:     SupabaseClient instance (optional — skip DB save if None)

    Returns:
        Merged enrichment dict with keys:
        email, emails_all, email_source,
        instagram_url, facebook_url, tiktok_url,
        owner_name, owner_source, enriched, last_enriched_at
    """
    name = lounge.get("name", "unknown")
    logger.info(f"Enriching: {name}")

    # Run all three finders
    email_data  = find_emails(lounge)
    social_data = find_socials(lounge)
    owner_data  = find_owner(lounge)

    enrichment = {
        # Email
        "email":        email_data.get("email"),
        "emails_all":   email_data.get("emails_all", []),
        "email_source": email_data.get("email_source"),

        # Social
        "instagram_url": social_data.get("instagram"),
        "facebook_url":  social_data.get("facebook"),
        "tiktok_url":    social_data.get("tiktok"),

        # Owner
        "owner_name":   owner_data.get("owner_name"),
        "owner_source": owner_data.get("owner_source"),

        # Status
        "enriched":         True,
        "last_enriched_at": now_utc(),
    }

    # Save to Supabase if db is provided
    if db and lounge.get("id"):
        _save_enrichment(db, lounge["id"], enrichment)

    return enrichment


def _save_enrichment(db, lounge_id: str, data: dict):
    """Persist enrichment fields to cigar_lounges table."""
    db_fields = {
        k: v for k, v in data.items()
        if k not in ("emails_all", "email_source", "owner_source")
        and v is not None
    }
    db_fields["enriched"] = True
    db_fields["last_enriched_at"] = data.get("last_enriched_at", now_utc())

    try:
        db.client.table("cigar_lounges").update(db_fields).eq("id", lounge_id).execute()
        logger.debug(f"Saved enrichment for lounge {lounge_id}")
    except Exception as e:
        logger.error(f"Failed to save enrichment for {lounge_id}: {e}")


def enrich_batch(
    lounges: list[dict],
    db=None,
    delay: float = 2.0,
    skip_already_enriched: bool = True,
) -> list[dict]:
    """
    Enrich a list of lounges.

    Args:
        lounges:                 List of lounge dicts from Supabase or scraper
        db:                      SupabaseClient instance
        delay:                   Seconds to wait between each lounge
        skip_already_enriched:   Skip lounges where enriched=True

    Returns:
        List of enrichment result dicts (same order as input)
    """
    results = []
    total = len(lounges)

    skipped = 0
    for i, lounge in enumerate(lounges):
        if skip_already_enriched and lounge.get("enriched"):
            skipped += 1
            results.append({"skipped": True})
            continue

        logger.info(f"[{i+1}/{total}] {lounge.get('name', '?')} — {lounge.get('city')}, {lounge.get('state')}")

        result = enrich_lounge(lounge, db=db)
        results.append(result)

        found = [k for k in ("email", "instagram_url", "facebook_url", "tiktok_url", "owner_name") if result.get(k)]
        logger.info(f"  → Found: {found if found else 'nothing'}")

        if i < total - 1:
            time.sleep(delay)

    enriched_count = sum(1 for r in results if not r.get("skipped"))
    logger.info(f"Enrichment complete: {enriched_count} processed, {skipped} skipped")
    return results
