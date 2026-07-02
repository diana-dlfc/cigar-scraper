# config/search_config.py
# Search terms and configuration for scraping cigar lounges

# Primary search queries for Google Places & Yelp
SEARCH_QUERIES = [
    "cigar lounge",
    "cigar bar",
    "cigar shop lounge",
    "cigar club",
    "humidor lounge",
]

# Yelp categories for filtering
YELP_CATEGORIES = [
    "cigarlounge",
    "cigarbar",
    "tobaccoshops",
]

# Google Places types to include
GOOGLE_PLACE_TYPES = [
    "bar",
    "store",
    "establishment",
]

# Keywords that must appear in the name or types to confirm it's a cigar venue
CIGAR_KEYWORDS = [
    "cigar",
    "humidor",
    "tobacco",
    "smoke",
    "lounge",
    "stogie",
]

# Keywords that disqualify a result (false positives)
EXCLUDE_KEYWORDS = [
    "hookah",
    "vape",
    "e-cigarette",
    "electronic cigarette",
    "cannabis",
    "dispensary",
    "marijuana",
    "cbd",
]

# Grid search settings
GRID_CELL_SIZE_KM = 25      # cell size in km for grid search
NEARBY_SEARCH_RADIUS_M = 15000  # 15 km radius per grid point (Nearby Search max = 50 km)

# Text search radius (used when searching by city name)
TEXT_SEARCH_RADIUS_M = 50000  # 50 km

# Max results per query page (Google returns up to 20; Yelp up to 50)
MAX_RESULTS_PER_PAGE = 20

# Delay between API calls in seconds
API_CALL_DELAY = 1.5
