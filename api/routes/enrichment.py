# api/routes/enrichment.py
import time
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from loguru import logger

from api.models.requests import EnrichBatchRequest
from api.models.responses import JobStartedResponse, EnrichResultResponse
from api.deps import get_db

router = APIRouter(prefix="/enrich", tags=["enrichment"])


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------

def _run_enrich_batch(job_id: str, req: EnrichBatchRequest, db):
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    db.update_job(job_id, {"status": "running", "started_at": started_at})

    try:
        from enrichment.pipeline import enrich_batch

        # Fetch lounges to enrich
        query = db.client.table("cigar_lounges").select("*")
        if req.state:
            query = query.eq("state", req.state.upper())
        if req.city:
            query = query.ilike("city", f"%{req.city}%")
        if req.skip_already_enriched:
            query = query.eq("enriched", False)
        query = query.limit(req.limit)
        lounges = query.execute().data or []

        logger.info(f"[{job_id}] Enriching {len(lounges)} lounges")

        results = enrich_batch(
            lounges,
            db=db,
            delay=req.delay_seconds,
            skip_already_enriched=req.skip_already_enriched,
        )

        enriched_count = sum(1 for r in results if r.get("status") == "enriched")
        errors = sum(1 for r in results if r.get("status") == "error")
        skipped = sum(1 for r in results if r.get("status") == "skipped")

        db.update_job(job_id, {
            "status": "completed",
            "records_found": len(lounges),
            "records_saved": enriched_count,
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        logger.info(f"[{job_id}] Enrichment done — enriched={enriched_count} skipped={skipped} errors={errors}")

    except Exception as e:
        logger.error(f"[{job_id}] enrich_batch failed: {e}")
        db.update_job(job_id, {
            "status": "failed",
            "error_message": str(e),
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/batch", response_model=JobStartedResponse, status_code=202)
def enrich_batch(
    req: EnrichBatchRequest,
    background_tasks: BackgroundTasks,
    db=Depends(get_db),
):
    """
    Enrich a batch of lounges (email, social, owner) in the background.
    Filters by state/city; respects skip_already_enriched.
    """
    try:
        job = db.create_job("enrich_batch", state=req.state)
        job_id = job["id"]
        background_tasks.add_task(_run_enrich_batch, job_id, req, db)
        filters = []
        if req.state:
            filters.append(f"state={req.state}")
        if req.city:
            filters.append(f"city={req.city}")
        filter_str = ", ".join(filters) if filters else "all states"
        return JobStartedResponse(
            job_id=job_id,
            message=f"Enriching up to {req.limit} lounges ({filter_str})",
        )
    except Exception as e:
        logger.error(f"enrich_batch start error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/lounge/{slug}", response_model=EnrichResultResponse)
def enrich_single(slug: str, db=Depends(get_db)):
    """
    Enrich a single lounge synchronously.
    Returns immediately with the enrichment result.
    """
    try:
        lounge = db.get_lounge_by_slug(slug)
        if not lounge:
            raise HTTPException(status_code=404, detail=f"Lounge not found: {slug}")

        from enrichment.pipeline import enrich_lounge
        data = enrich_lounge(lounge, db=db)

        return EnrichResultResponse(
            processed=1,
            enriched_count=1 if data else 0,
            skipped=0,
            errors=0 if data else 1,
            results=[{"slug": slug, "data": data}],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"enrich_single error for {slug}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
