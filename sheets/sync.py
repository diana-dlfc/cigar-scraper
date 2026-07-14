# sheets/sync.py
"""
Google Sheets sync for cigar lounge data.

Authentication:
    Uses a Google Service Account credentials JSON file.
    Set GOOGLE_SHEETS_CREDENTIALS_FILE in .env, then share your spreadsheet
    with the service account email (Editor role).

Quick setup:
    1. Go to Google Cloud Console → APIs & Services → Credentials
    2. Create a Service Account → download JSON key → save as credentials.json
    3. Enable Google Sheets API on the project
    4. Share your Google Sheet with the service account email
    5. Set GOOGLE_SHEETS_CREDENTIALS_FILE=credentials.json in .env

Usage:
    from sheets.sync import export_to_sheets
    result = export_to_sheets(
        lounges,                          # list of lounge dicts
        spreadsheet_id="your-sheet-id",
        worksheet_name="Cigar Lounges",
        overwrite=True,
    )
    print(result["rows_written"], result["url"])
"""

import os
import time
from loguru import logger

from config.settings import GOOGLE_SHEETS_CREDENTIALS_FILE

# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------

# (header label, dict key)
COLUMNS: list[tuple[str, str]] = [
    ("Name",            "name"),
    ("City",            "city"),
    ("State",           "state"),
    ("Address",         "address"),
    ("Phone",           "phone"),
    ("Website",         "website"),
    ("Email",           "email"),
    ("Rating",          "rating"),
    ("Reviews",         "review_count"),
    ("Google Maps",     "google_maps_url"),
    ("Instagram",       "instagram_url"),
    ("Facebook",        "facebook_url"),
    ("TikTok",          "tiktok_url"),
    ("YouTube",         "youtube_url"),
    ("Owner",           "owner_name"),
]

HEADERS = [col[0] for col in COLUMNS]
KEYS    = [col[1] for col in COLUMNS]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _get_gspread_client(credentials_file: str = None):
    """
    Authenticate with Google Sheets using a Service Account credentials file.
    Returns a gspread.Client.
    """
    import gspread
    from google.oauth2.service_account import Credentials

    creds_path = credentials_file or GOOGLE_SHEETS_CREDENTIALS_FILE
    if not creds_path or not os.path.exists(creds_path):
        raise FileNotFoundError(
            f"Credentials file not found: '{creds_path}'. "
            "Set GOOGLE_SHEETS_CREDENTIALS_FILE in .env and share your sheet "
            "with the service account email."
        )

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
    ]
    creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
    return gspread.authorize(creds)


# ---------------------------------------------------------------------------
# Data formatting
# ---------------------------------------------------------------------------

# Keys that should render as clickable hyperlinks
LINK_KEYS = {
    "google_maps_url": "Ver en Maps",
    "instagram_url":   "Instagram",
    "facebook_url":    "Facebook",
    "tiktok_url":      "TikTok",
    "youtube_url":     "YouTube",
    "website":         "Sitio Web",
}


def _lounge_to_row(lounge: dict) -> list:
    """Convert a lounge dict to a flat list matching COLUMNS order."""
    row = []
    for key in KEYS:
        val = lounge.get(key)
        if val is None:
            row.append("")
        elif isinstance(val, bool):
            row.append("Yes" if val else "No")
        elif isinstance(val, float):
            row.append(round(val, 6))
        elif key in LINK_KEYS and isinstance(val, str) and val.startswith("http"):
            # URL directa — Sheets la hace clickeable automáticamente, sin riesgo de #ERROR!
            row.append(val)
        else:
            row.append(str(val))
    return row


def format_for_sheets(lounges: list[dict]) -> list[list]:
    """
    Convert list of lounge dicts to a 2D array ready for Sheets.
    First row is headers.
    """
    rows = [HEADERS]
    for lounge in lounges:
        rows.append(_lounge_to_row(lounge))
    return rows


# ---------------------------------------------------------------------------
# Core export function
# ---------------------------------------------------------------------------

def export_to_sheets(
    lounges: list[dict],
    spreadsheet_id: str,
    worksheet_name: str = "Cigar Lounges",
    overwrite: bool = True,
    credentials_file: str = None,
) -> dict:
    """
    Export a list of lounge dicts to a Google Sheets worksheet.

    Args:
        lounges: List of cigar lounge dicts (from Supabase or scraper).
        spreadsheet_id: The spreadsheet ID from the Google Sheets URL.
        worksheet_name: Name of the tab to write to.
        overwrite: If True, clears the worksheet before writing.
        credentials_file: Path to service account JSON. Falls back to .env setting.

    Returns:
        dict with keys: rows_written, spreadsheet_id, worksheet, url
    """
    gc = _get_gspread_client(credentials_file)
    spreadsheet = gc.open_by_key(spreadsheet_id)

    # Get or create worksheet
    try:
        ws = spreadsheet.worksheet(worksheet_name)
        logger.info(f"Using existing worksheet: '{worksheet_name}'")
    except Exception:
        ws = spreadsheet.add_worksheet(title=worksheet_name, rows=5000, cols=len(COLUMNS))
        logger.info(f"Created new worksheet: '{worksheet_name}'")

    if overwrite:
        ws.clear()
        logger.info("Worksheet cleared")

    rows = format_for_sheets(lounges)

    if len(rows) <= 1:  # only header row, no data
        logger.warning("No data to write to Sheets")
        return {
            "rows_written": 0,
            "spreadsheet_id": spreadsheet_id,
            "worksheet": worksheet_name,
            "url": spreadsheet.url,
        }

    # Write in batches to avoid hitting Sheets API limits (max 10MB per request)
    BATCH_SIZE = 1000
    written = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        start_row = i + 1  # 1-indexed
        ws.update(
            range_name=f"A{start_row}",
            values=batch,
            value_input_option="USER_ENTERED",
        )
        written += len(batch)
        logger.info(f"  Wrote rows {start_row}–{start_row + len(batch) - 1}")

    # Bold header + freeze
    try:
        ws.format("A1:Z1", {"textFormat": {"bold": True}})
        ws.freeze(rows=1)
    except Exception as e:
        logger.warning(f"Could not apply header formatting: {e}")

    # Format: wrap text + auto-resize + cap link columns
    _apply_formatting(spreadsheet, ws, len(rows))
    return _finish_export(spreadsheet, worksheet_name, written, spreadsheet_id)


def _apply_formatting(spreadsheet, ws, num_rows: int):
    """Aplica formateo completo a un worksheet: anchos, wrap, clip en links."""
    try:
        link_col_indices = [i for i, k in enumerate(KEYS) if k in LINK_KEYS]
        last_row = num_rows + 1
        requests = [
            # Wrap all text so nothing is cut off
            {"repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 0, "endRowIndex": last_row,
                    "startColumnIndex": 0, "endColumnIndex": len(COLUMNS),
                },
                "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
                "fields": "userEnteredFormat.wrapStrategy",
            }},
            # Auto-resize all columns to fit content
            {"autoResizeDimensions": {"dimensions": {
                "sheetId": ws.id, "dimension": "COLUMNS",
                "startIndex": 0, "endIndex": len(COLUMNS),
            }}},
        ]
        # Fixed widths for specific columns
        fixed_widths = {
            "address": 280,
            "phone":   130,
            "name":    200,
            "city":    120,
            "state":    60,
        }
        for key, px in fixed_widths.items():
            if key in KEYS:
                col_idx = KEYS.index(key)
                requests.append({"updateDimensionProperties": {
                    "range": {
                        "sheetId": ws.id, "dimension": "COLUMNS",
                        "startIndex": col_idx, "endIndex": col_idx + 1,
                    },
                    "properties": {"pixelSize": px},
                    "fields": "pixelSize",
                }})
        # Columnas de links: ancho fijo 150px + CLIP para que no desborden
        for col_idx in link_col_indices:
            requests.append({"updateDimensionProperties": {
                "range": {
                    "sheetId": ws.id, "dimension": "COLUMNS",
                    "startIndex": col_idx, "endIndex": col_idx + 1,
                },
                "properties": {"pixelSize": 150},
                "fields": "pixelSize",
            }})
            requests.append({"repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 1, "endRowIndex": last_row,
                    "startColumnIndex": col_idx, "endColumnIndex": col_idx + 1,
                },
                "cell": {"userEnteredFormat": {"wrapStrategy": "CLIP"}},
                "fields": "userEnteredFormat.wrapStrategy",
            }})
        # Delete extra empty columns beyond our data
        total_cols = ws.col_count
        if total_cols > len(COLUMNS):
            requests.append({"deleteDimension": {
                "range": {
                    "sheetId": ws.id, "dimension": "COLUMNS",
                    "startIndex": len(COLUMNS), "endIndex": total_cols,
                }
            }})
        # Force all columns to plain text to prevent Sheets autocomplete/dropdowns
        requests.append({"repeatCell": {
            "range": {
                "sheetId": ws.id,
                "startRowIndex": 1, "endRowIndex": last_row,
                "startColumnIndex": 0, "endColumnIndex": len(COLUMNS),
            },
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "TEXT"}}},
            "fields": "userEnteredFormat.numberFormat",
        }})
        # Clear any data validation on all cells
        requests.append({"setDataValidation": {
            "range": {
                "sheetId": ws.id,
                "startRowIndex": 1, "endRowIndex": last_row,
                "startColumnIndex": 0, "endColumnIndex": len(COLUMNS),
            },
        }})
        # Eliminar filtros básicos (dropdown arrows en headers)
        requests.append({"clearBasicFilter": {"sheetId": ws.id}})
        spreadsheet.batch_update({"requests": requests})
    except Exception as e:
        logger.warning(f"Could not format columns: {e}")


def _finish_export(spreadsheet, worksheet_name, written, spreadsheet_id):
    data_rows = written - 1  # subtract header
    logger.info(f"Exported {data_rows} lounges to '{worksheet_name}' in {spreadsheet.title}")

    return {
        "rows_written": data_rows,
        "spreadsheet_id": spreadsheet_id,
        "worksheet": worksheet_name,
        "url": spreadsheet.url,
    }


# ---------------------------------------------------------------------------
# Convenience: export by state from Supabase
# ---------------------------------------------------------------------------

def export_state_to_sheets(
    state: str,
    spreadsheet_id: str,
    worksheet_name: str = None,
    db=None,
    credentials_file: str = None,
) -> dict:
    """
    Fetch all lounges for a state from Supabase and export to Sheets.

    Args:
        state: 2-letter state abbreviation.
        spreadsheet_id: Google Sheets ID.
        worksheet_name: Defaults to the state abbreviation (e.g. "FL").
        db: SupabaseClient instance. Creates one if not provided.
        credentials_file: Path to credentials JSON.
    """
    if db is None:
        from database.supabase_client import SupabaseClient
        db = SupabaseClient()

    sheet_name = worksheet_name or state.upper()
    lounges = db.get_lounges(state=state.upper(), limit=10000)
    logger.info(f"Fetched {len(lounges)} lounges for {state}")

    return export_to_sheets(
        lounges,
        spreadsheet_id=spreadsheet_id,
        worksheet_name=sheet_name,
        credentials_file=credentials_file,
    )


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    load_dotenv()

    from config.settings import GOOGLE_SHEETS_SPREADSHEET_ID

    if len(sys.argv) < 2:
        print("Usage: python -m sheets.sync <STATE>  [spreadsheet_id]")
        print("  Example: python -m sheets.sync FL")
        sys.exit(1)

    state = sys.argv[1].upper()
    sheet_id = sys.argv[2] if len(sys.argv) > 2 else GOOGLE_SHEETS_SPREADSHEET_ID

    if not sheet_id:
        print("❌  No spreadsheet ID. Pass it as arg or set GOOGLE_SHEETS_SPREADSHEET_ID in .env")
        sys.exit(1)

    result = export_state_to_sheets(state, sheet_id)
    print(f'Exported {result["rows_written"]} rows -> {result["url"]}')

# ---------------------------------------------------------------------------
# Export all states — one worksheet per state, alphabetically ordered
# ---------------------------------------------------------------------------

def export_all_states_to_sheets(
    spreadsheet_id: str,
    db=None,
    credentials_file: str = None,
    states: list[str] = None,
) -> dict:
    """
    Export all lounges from Supabase to Google Sheets.
    Creates one worksheet per state, lounges sorted A-Z by name.
    Worksheets are ordered alphabetically by state name.

    Args:
        spreadsheet_id: Google Sheets ID.
        db: SupabaseClient instance. Creates one if not provided.
        credentials_file: Path to credentials JSON.
        states: Optional list of state abbreviations to export (default: all).

    Returns:
        dict with total_rows, sheets_created, url
    """
    from config.states import US_STATES

    if db is None:
        from database.supabase_client import SupabaseClient
        db = SupabaseClient()

    gc = _get_gspread_client(credentials_file)
    spreadsheet = gc.open_by_key(spreadsheet_id)

    # Fetch all lounges con paginación (Supabase limita a 1000 por query)
    all_lounges = []
    PAGE_SIZE = 1000
    offset = 0
    while True:
        if states:
            res = db.client.table("cigar_lounges").select("*") \
                .in_("state", [s.upper() for s in states]) \
                .range(offset, offset + PAGE_SIZE - 1).execute()
        else:
            res = db.client.table("cigar_lounges").select("*") \
                .range(offset, offset + PAGE_SIZE - 1).execute()
        batch = res.data or []
        all_lounges.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    logger.info(f"Total lounges to export: {len(all_lounges)}")

    # Group by state
    by_state: dict[str, list] = {}
    for lounge in all_lounges:
        state = lounge.get("state") or "Unknown"
        by_state.setdefault(state, []).append(lounge)

    # Sort state keys by full state name alphabetically
    def state_sort_key(abbr):
        return US_STATES.get(abbr, {}).get("name", abbr)

    sorted_states = sorted(by_state.keys(), key=state_sort_key)

    # Delete existing state worksheets so we can reorder them cleanly
    existing_sheets = {ws.title: ws for ws in spreadsheet.worksheets()}

    total_rows = 0
    sheets_created = 0

    for state_abbr in sorted_states:
        lounges = sorted(by_state[state_abbr], key=lambda x: (x.get("name") or "").lower())
        state_name = US_STATES.get(state_abbr, {}).get("name", state_abbr)
        sheet_title = f"{state_abbr} — {state_name}"

        # Get or create worksheet
        if sheet_title in existing_sheets:
            ws = existing_sheets[sheet_title]
            for attempt in range(3):
                try:
                    ws.clear()
                    break
                except Exception as e:
                    if "429" in str(e) or "Quota" in str(e):
                        logger.warning(f"Rate limit on clear, waiting 60s...")
                        time.sleep(60)
                    else:
                        raise
            logger.info(f"Cleared existing worksheet: '{sheet_title}'")
        else:
            for attempt in range(3):
                try:
                    ws = spreadsheet.add_worksheet(title=sheet_title, rows=5000, cols=len(COLUMNS))
                    logger.info(f"Created worksheet: '{sheet_title}'")
                    sheets_created += 1
                    break
                except Exception as e:
                    if "429" in str(e) or "Quota" in str(e):
                        logger.warning(f"Rate limit, waiting 60s... ({attempt+1}/3)")
                        time.sleep(60)
                    else:
                        raise

        rows = format_for_sheets(lounges)
        if len(rows) > 1:
            for attempt in range(3):
                try:
                    ws.update(range_name="A1", values=rows, value_input_option="USER_ENTERED")
                    break
                except Exception as e:
                    if "429" in str(e) or "Quota" in str(e):
                        logger.warning(f"Rate limit on update, waiting 30s...")
                        time.sleep(30)
                    else:
                        raise
            try:
                ws.format("A1:Z1", {"textFormat": {"bold": True}})
                ws.freeze(rows=1)
            except Exception:
                pass
            _apply_formatting(spreadsheet, ws, len(rows))
            total_rows += len(lounges)
            logger.info(f"  {sheet_title}: {len(lounges)} lounges")
        else:
            logger.info(f"  {sheet_title}: no data")

        time.sleep(3)  # pausa entre estados para evitar rate limit

    # Reorder all worksheets alphabetically by state name
    # Must include EVERY worksheet exactly once or Sheets API errors
    current_ws = spreadsheet.worksheets()
    ws_by_title = {ws.title: ws for ws in current_ws}
    state_titles = set()
    ordered = []
    for state_abbr in sorted_states:
        state_name = US_STATES.get(state_abbr, {}).get("name", state_abbr)
        title = f"{state_abbr} — {state_name}"
        state_titles.add(title)
        if title in ws_by_title:
            ordered.append(ws_by_title[title])
    # Append non-state sheets (Sheet1, Test, etc.) at the end
    for ws in current_ws:
        if ws.title not in state_titles:
            ordered.append(ws)
    try:
        time.sleep(10)  # pausa antes de reorder para evitar rate limit
        spreadsheet.reorder_worksheets(ordered)
        logger.info("Worksheets reordered alphabetically")
    except Exception as e:
        logger.warning(f"Could not reorder worksheets: {e}")

    # Delete default empty sheets (Sheet1, etc.) if state sheets exist
    if ordered:
        default_names = {"Sheet1", "Sheet 1", "Hoja1", "Hoja 1"}
        for ws in list(spreadsheet.worksheets()):
            if ws.title in default_names and ws not in ordered:
                try:
                    spreadsheet.del_worksheet(ws)
                    logger.info(f"Deleted default sheet: '{ws.title}'")
                except Exception:
                    pass

    logger.info(f"Export complete — {total_rows} rows across {len(sorted_states)} states")
    return {
        "total_rows": total_rows,
        "sheets_created": sheets_created,
        "states_exported": len(sorted_states),
        "url": spreadsheet.url,
    }
