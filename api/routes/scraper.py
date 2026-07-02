# api/routes/scraper.py
import time
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from loguru import logger

from api.models.requests import ScrapeCityRequest, ScrapeStateRequest
from api.models.responses import JobStartedResponse, ScrapeResultResponse
from api.deps import get_db
from utils.deduplicator import DatabaseDeduplicator
from config.states import get_all_state_abbrs

router = APIRouter(prefix="/scrape", tags=["scraper"])


# ---------------------------------------------------------------------------
# Background task implementations
# ---------------------------------------------------------------------------

def _run_city_scrape(job_id: str, req: ScrapeCityRequest, db):
    """Background task: scrape a single city from one or more sources."""
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    db.update_job(job_id, {"status": "running", "started_at": started_at})

    all_lounges: list[dict] = []
    dedup = DatabaseDeduplicator(db) if req.save_to_db else None

    try:
        if "google" in req.sources:
            from scrapers.google_places import search_city as google_city
            results = google_city(req.city, req.state, fetch_details=req.fetch_details)
            all_lounges.extend(results)
            logger.info(f"[{job_id}] Google: {len(results)} results for {req.city}, {req.state}")

        if "yelp" in req.sources:
            from scrapers.yelp import search_city as yelp_city
            results = yelp_city(req.city, req.state)
            all_lounges.extend(results)
            logger.info(f"[{job_id}] Yelp: {len(results)} results for {req.city}, {req.state}")

        saved = skipped = 0
        if req.save_to_db and db:
            for lounge in all_lounges:
                source_id = lounge.pop("_source_id", None)
                source = lounge.pop("_source", "google_places")
                try:
                    if dedup and dedup.is_duplicate(lounge):
                        skipped += 1
                        continue
                    row = db.upsert_lounge(lounge)
                    if row and source_id:
                        db.insert_source({
                            "lounge_id": row["id"],
                            "source": source,
                            "source_id": source_id,
                            "source_url": lounge.get("source_url"),
                            "raw_data": lounge,
                        })
                    saved += 1
                except Exception as e:
                    logger.warning(f"[{job_id}] upsert error: {e}")
                    skipped += 1
        else:
            saved = len(all_lounges)

        db.update_job(job_id, {
            "status": "completed",
            "records_found": len(all_lounges),
            "records_saved": saved,
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        logger.info(f"[{job_id}] Done — found={len(all_lounges)} saved={saved} skipped={skipped}")

    except Exception as e:
        logger.error(f"[{job_id}] scrape_city failed: {e}")
        db.update_job(job_id, {
            "status": "failed",
            "error_message": str(e),
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })


def _run_state_scrape(job_id: str, req: ScrapeStateRequest, db):
    """Background task: full state scrape (city list + optional grid)."""
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    db.update_job(job_id, {"status": "running", "started_at": started_at})

    all_lounges: list[dict] = []
    dedup = DatabaseDeduplicator(db) if req.save_to_db else None

    try:
        # --- city-name queries ---
        if "google" in req.sources:
            from scrapers.google_places import search_state as google_state
            from config.cities.florida import FLORIDA_CITIES
            # TODO: generalize to all states with per-state city lists
            cities = FLORIDA_CITIES if req.state == "FL" else []
            if cities:
                results = google_state(req.state, cities, fetch_details=req.fetch_details)
                all_lounges.extend(results)
                logger.info(f"[{job_id}] Google city-search: {len(results)} for {req.state}")

        if "yelp" in req.sources:
            from scrapers.yelp import search_state as yelp_state
            from config.cities.florida import FLORIDA_CITIES
            cities = FLORIDA_CITIES if req.state == "FL" else []
            if cities:
                results = yelp_state(req.state, cities)
                all_lounges.extend(results)
                logger.info(f"[{job_id}] Yelp city-search: {len(results)} for {req.state}")

        # --- grid search ---
        if req.use_grid and "google" in req.sources:
            from scrapers.grid_search import search_state_grid
            grid_results = search_state_grid(
                req.state,
                cell_size_km=req.cell_size_km,
                fetch_details=req.fetch_details,
            )
            all_lounges.extend(grid_results)
            logger.info(f"[{job_id}] Grid search: {len(grid_results)} for {req.state}")

        # --- persist ---
        saved = skipped = 0
        if req.save_to_db and db:
            for lounge in all_lounges:
                source_id = lounge.pop("_source_id", None)
                source = lounge.pop("_source", "google_places")
                try:
                    if dedup and dedup.is_duplicate(lounge):
                        skipped += 1
                        continue
                    row = db.upsert_lounge(lounge)
                    if row and source_id:
                        db.insert_source({
                            "lounge_id": row["id"],
                            "source": source,
                            "source_id": source_id,
                            "source_url": lounge.get("source_url"),
                            "raw_data": lounge,
                        })
                    saved += 1
                except Exception as e:
                    logger.warning(f"[{job_id}] upsert error: {e}")
                    skipped += 1
        else:
            saved = len(all_lounges)

        db.update_job(job_id, {
            "status": "completed",
            "records_found": len(all_lounges),
            "records_saved": saved,
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        logger.info(f"[{job_id}] State {req.state} done — found={len(all_lounges)} saved={saved}")

    except Exception as e:
        logger.error(f"[{job_id}] scrape_state failed: {e}")
        db.update_job(job_id, {
            "status": "failed",
            "error_message": str(e),
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/states")
def list_states():
    """Return all supported state abbreviations."""
    from config.states import US_STATES
    return {"states": [{"abbr": k, "name": v["name"]} for k, v in US_STATES.items()]}


@router.post("/city", response_model=JobStartedResponse, status_code=202)
def scrape_city(
    req: ScrapeCityRequest,
    background_tasks: BackgroundTasks,
    db=Depends(get_db),
):
    """
    Start a background scrape for a single city.
    Returns a job_id you can poll with GET /jobs/{job_id}.
    """
    try:
        job = db.create_job("scrape_city", state=req.state)
        job_id = job["id"]
        background_tasks.add_task(_run_city_scrape, job_id, req, db)
        return JobStartedResponse(
            job_id=job_id,
            message=f"Scraping {req.city}, {req.state} from {req.sources}",
        )
    except Exception as e:
        logger.error(f"scrape_city start error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/state", response_model=JobStartedResponse, status_code=202)
def scrape_state(
    req: ScrapeStateRequest,
    background_tasks: BackgroundTasks,
    db=Depends(get_db),
):
    """
    Start a full state scrape (city queries + optional grid search).
    Long-running — returns a job_id immediately.
    """
    state = req.state.upper()
    if state not in get_all_state_abbrs():
        raise HTTPException(status_code=400, detail=f"Unknown state: {state}")
    try:
        job = db.create_job("scrape_state", state=state)
        job_id = job["id"]
        req.state = state
        background_tasks.add_task(_run_state_scrape, job_id, req, db)
        return JobStartedResponse(
            job_id=job_id,
            message=f"Scraping state {state} (grid={req.use_grid}, cell={req.cell_size_km}km)",
        )
    except Exception as e:
        logger.error(f"scrape_state start error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
