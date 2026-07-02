# api/main.py
"""
Cigar Scraper — FastAPI REST API
=================================
Run with:
    uvicorn api.main:app --reload --port 8000

Interactive docs:
    http://localhost:8000/docs        (Swagger UI)
    http://localhost:8000/redoc       (ReDoc)
"""
import sys
import os

# Make sure project root is on the path when running via `uvicorn api.main:app`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from api.routes import scraper, enrichment, sheets, jobs, lounges

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Cigar Scraper API",
    description=(
        "Market intelligence platform for US cigar lounges.\n\n"
        "**Workflow:**\n"
        "1. `POST /scrape/city` or `POST /scrape/state` → scrape data (returns `job_id`)\n"
        "2. `GET /jobs/{job_id}` → poll until `status=completed`\n"
        "3. `POST /enrich/batch` → enrich with email, social, owner data\n"
        "4. `POST /sheets/sync` → export to Google Sheets\n"
        "5. `GET /lounges` → query the collected data\n"
    ),
    version="1.0.0",
    contact={"name": "Metal Building Now", "email": "info@metalbuildingnow.com"},
)

# Allow local front-ends / testing tools
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(scraper.router)
app.include_router(enrichment.router)
app.include_router(sheets.router)
app.include_router(jobs.router)
app.include_router(lounges.router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/", tags=["health"])
def root():
    return {
        "service": "cigar-scraper-api",
        "version": "1.0.0",
        "status": "ok",
        "docs": "/docs",
    }


@app.get("/health", tags=["health"])
def health():
    """Check API and database connectivity."""
    db_ok = False
    db_error = None
    try:
        from database.supabase_client import SupabaseClient
        db = SupabaseClient()
        db.client.table("cigar_lounges").select("id").limit(1).execute()
        db_ok = True
    except Exception as e:
        db_error = str(e)
        logger.warning(f"DB health check failed: {e}")

    return {
        "api": "ok",
        "database": "ok" if db_ok else "unavailable",
        "db_error": db_error,
    }


# ---------------------------------------------------------------------------
# Startup log
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def on_startup():
    logger.info("🚀 Cigar Scraper API started")
    logger.info("   Docs → http://localhost:8000/docs")
