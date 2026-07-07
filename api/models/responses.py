# api/models/responses.py
from typing import Any, Optional
from pydantic import BaseModel


# ── Generic ────────────────────────────────────────────────────────────────────

class JobStartedResponse(BaseModel):
    job_id: str
    status: str = "pending"
    message: str


class JobStatusResponse(BaseModel):
    job_id: str
    type: str
    status: str                   # pending | running | completed | failed
    state: Optional[str] = None
    records_found: Optional[int] = None
    records_saved: Optional[int] = None
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    created_at: Optional[str] = None


# ── Lounges ────────────────────────────────────────────────────────────────────

class LoungeResponse(BaseModel):
    id: Optional[str] = None
    name: str
    slug: str
    city: Optional[str] = None
    state: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    rating: Optional[float] = None
    review_count: Optional[int] = None
    price_level: Optional[int] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    google_maps_url: Optional[str] = None
    description: Optional[str] = None
    enriched: bool = False
    email: Optional[str] = None
    instagram_url: Optional[str] = None
    facebook_url: Optional[str] = None
    owner_name: Optional[str] = None
    last_scraped_at: Optional[str] = None
    created_at: Optional[str] = None


class LoungesListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[LoungeResponse]


class StatsResponse(BaseModel):
    total_lounges: int
    enriched: int
    not_enriched: int
    by_state: dict[str, int]


# ── Enrich ─────────────────────────────────────────────────────────────────────

class EnrichResultResponse(BaseModel):
    processed: int
    enriched_count: int
    skipped: int
    errors: int
    results: list[dict[str, Any]]


# ── Sheets ─────────────────────────────────────────────────────────────────────

class SheetsSyncResponse(BaseModel):
    rows_written: int
    spreadsheet_id: str
    worksheet: str
    url: Optional[str] = None
