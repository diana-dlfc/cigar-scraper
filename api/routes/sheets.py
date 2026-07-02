# api/routes/sheets.py
"""
Phase 6 placeholder — Google Sheets sync.
Endpoints are defined and documented here; the sync module (sheets/sync.py)
will be implemented in Phase 6.
"""
from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from api.models.requests import SheetsSyncRequest
from api.models.responses import SheetsSyncResponse
from api.deps import get_db
from config.settings import GOOGLE_SHEETS_SPREADSHEET_ID

router = APIRouter(prefix="/sheets", tags=["sheets"])


@router.post("/sync", response_model=SheetsSyncResponse)
def sync_to_sheets(req: SheetsSyncRequest, db=Depends(get_db)):
    """
    Export cigar lounge data from Supabase to Google Sheets.
    Requires GOOGLE_SHEETS_CREDENTIALS_FILE and GOOGLE_SHEETS_SPREADSHEET_ID
    to be set in .env (or passed in the request body).

    Phase 6 implementation pending.
    """
    try:
        from sheets.sync import export_to_sheets  # Phase 6
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="Google Sheets sync not yet implemented (Phase 6). "
                   "sheets/sync.py is missing.",
        )

    spreadsheet_id = req.spreadsheet_id or GOOGLE_SHEETS_SPREADSHEET_ID
    if not spreadsheet_id:
        raise HTTPException(
            status_code=400,
            detail="No spreadsheet_id provided. Set GOOGLE_SHEETS_SPREADSHEET_ID in .env "
                   "or pass it in the request body.",
        )

    try:
        # Fetch lounges
        query = db.client.table("cigar_lounges").select("*")
        if req.state:
            query = query.eq("state", req.state.upper())
        lounges = query.execute().data or []

        result = export_to_sheets(
            lounges,
            spreadsheet_id=spreadsheet_id,
            worksheet_name=req.worksheet_name,
            overwrite=req.overwrite,
        )

        return SheetsSyncResponse(
            rows_written=result.get("rows_written", len(lounges)),
            spreadsheet_id=spreadsheet_id,
            worksheet=req.worksheet_name,
            url=result.get("url"),
        )
    except Exception as e:
        logger.error(f"sheets sync error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
