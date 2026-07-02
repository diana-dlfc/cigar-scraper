# api/routes/lounges.py
from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger

from api.models.responses import LoungeResponse, LoungesListResponse, StatsResponse
from api.deps import get_db

router = APIRouter(prefix="/lounges", tags=["lounges"])


@router.get("", response_model=LoungesListResponse)
def list_lounges(
    state: str | None = Query(None, min_length=2, max_length=2, description="2-letter state code"),
    city: str | None = Query(None),
    enriched: bool | None = Query(None, description="Filter by enrichment status"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
):
    """List cigar lounges with optional filters."""
    try:
        query = db.client.table("cigar_lounges").select("*")
        if state:
            query = query.eq("state", state.upper())
        if city:
            query = query.ilike("city", f"%{city}%")
        if enriched is not None:
            query = query.eq("enriched", enriched)

        # Count total (without limit/offset)
        count_q = db.client.table("cigar_lounges").select("id", count="exact")
        if state:
            count_q = count_q.eq("state", state.upper())
        if city:
            count_q = count_q.ilike("city", f"%{city}%")
        if enriched is not None:
            count_q = count_q.eq("enriched", enriched)
        count_res = count_q.execute()
        total = count_res.count or 0

        res = query.order("name").range(offset, offset + limit - 1).execute()
        items = [LoungeResponse(**row) for row in (res.data or [])]
        return LoungesListResponse(total=total, limit=limit, offset=offset, items=items)
    except Exception as e:
        logger.error(f"list_lounges error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats", response_model=StatsResponse)
def get_stats(db=Depends(get_db)):
    """Return aggregate statistics."""
    try:
        all_res = db.client.table("cigar_lounges").select("state, enriched").execute()
        rows = all_res.data or []
        total = len(rows)
        enriched = sum(1 for r in rows if r.get("enriched"))
        by_state: dict[str, int] = {}
        for r in rows:
            s = r.get("state") or "Unknown"
            by_state[s] = by_state.get(s, 0) + 1
        return StatsResponse(
            total_lounges=total,
            enriched=enriched,
            not_enriched=total - enriched,
            by_state=dict(sorted(by_state.items(), key=lambda x: -x[1])),
        )
    except Exception as e:
        logger.error(f"get_stats error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{slug}", response_model=LoungeResponse)
def get_lounge(slug: str, db=Depends(get_db)):
    """Get a single lounge by slug."""
    try:
        row = db.get_lounge_by_slug(slug)
        if not row:
            raise HTTPException(status_code=404, detail=f"Lounge not found: {slug}")
        return LoungeResponse(**row)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get_lounge error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
