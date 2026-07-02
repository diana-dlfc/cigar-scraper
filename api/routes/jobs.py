# api/routes/jobs.py
from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from api.models.responses import JobStatusResponse
from api.deps import get_db

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str, db=Depends(get_db)):
    """
    Poll the status of a background job (scrape or enrichment).
    status: pending | running | completed | failed
    """
    try:
        job = db.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
        return JobStatusResponse(**job)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get_job_status error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
