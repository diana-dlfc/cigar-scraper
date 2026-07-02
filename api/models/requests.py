# api/models/requests.py
from typing import Literal, Optional
from pydantic import BaseModel, Field


# ── Scraper ────────────────────────────────────────────────────────────────────

class ScrapeCityRequest(BaseModel):
    city: str = Field(..., examples=["Miami"])
    state: str = Field(..., min_length=2, max_length=2, examples=["FL"])
    sources: list[Literal["google", "yelp"]] = Field(
        default=["google", "yelp"],
        description="Which sources to scrape",
    )
    fetch_details: bool = Field(
        default=True,
        description="Fetch Place Details for each Google result (more data, more API calls)",
    )
    save_to_db: bool = Field(
        default=True,
        description="Persist results to Supabase",
    )


class ScrapeStateRequest(BaseModel):
    state: str = Field(..., min_length=2, max_length=2, examples=["FL"])
    sources: list[Literal["google", "yelp"]] = Field(default=["google", "yelp"])
    use_grid: bool = Field(
        default=True,
        description="Run geographic grid search in addition to city-name queries",
    )
    cell_size_km: float = Field(
        default=25.0,
        ge=5.0,
        le=100.0,
        description="Grid cell size in km (smaller = more coverage, more API calls)",
    )
    fetch_details: bool = Field(default=True)
    save_to_db: bool = Field(default=True)


# ── Enrichment ─────────────────────────────────────────────────────────────────

class EnrichBatchRequest(BaseModel):
    state: Optional[str] = Field(None, min_length=2, max_length=2)
    city: Optional[str] = None
    limit: int = Field(default=50, ge=1, le=500)
    skip_already_enriched: bool = Field(default=True)
    delay_seconds: float = Field(default=2.0, ge=0.5, le=10.0)


# ── Sheets ─────────────────────────────────────────────────────────────────────

class SheetsSyncRequest(BaseModel):
    state: Optional[str] = Field(None, min_length=2, max_length=2)
    spreadsheet_id: Optional[str] = Field(
        None,
        description="Override the GOOGLE_SHEETS_SPREADSHEET_ID from .env",
    )
    worksheet_name: str = Field(
        default="Cigar Lounges",
        description="Name of the worksheet tab to write to",
    )
    overwrite: bool = Field(
        default=True,
        description="Clear existing data before writing",
    )
