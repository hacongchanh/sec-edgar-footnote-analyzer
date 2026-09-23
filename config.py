"""
config.py — Centralized configuration for the SEC EDGAR Footnote Analysis Agent.

Loads environment variables from a .env file and provides validated
configuration constants used across all modules.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env file from the project root
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(_PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# Google Gemini API
# ---------------------------------------------------------------------------
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

# ---------------------------------------------------------------------------
# SEC EDGAR
# ---------------------------------------------------------------------------
SEC_USER_AGENT: str = os.getenv("SEC_USER_AGENT", "")

# ---------------------------------------------------------------------------
# Access Control (Step A8)
# ---------------------------------------------------------------------------
APP_ACCESS_CODE: str = os.getenv("APP_ACCESS_CODE", "")

# SEC API base URLs
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

# Rate-limiting: SEC allows max 10 req/s — we stay under at ~8 req/s
SEC_REQUEST_DELAY: float = 0.12  # seconds between requests

# ---------------------------------------------------------------------------
# Local caching
# ---------------------------------------------------------------------------
CACHE_DIR: Path = _PROJECT_ROOT / "cache"
CACHE_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_config() -> list[str]:
    """Return a list of configuration problems (empty if everything is OK)."""
    problems: list[str] = []
    if not GEMINI_API_KEY:
        problems.append(
            "GEMINI_API_KEY is not set. "
            "Get a free key at https://aistudio.google.com/apikey "
            "and add it to your .env file."
        )
    if not SEC_USER_AGENT:
        problems.append(
            "SEC_USER_AGENT is not set. "
            "The SEC requires a User-Agent header with your name and email. "
            'Example: SEC_USER_AGENT="Jane Doe jane@example.com"'
        )
    return problems


def require_valid_config() -> None:
    """Exit with a helpful error message if configuration is incomplete."""
    problems = validate_config()
    if problems:
        print("\n❌  Configuration errors:\n")
        for i, p in enumerate(problems, 1):
            print(f"   {i}. {p}")
        print(f"\n   Copy .env.example → .env and fill in the values.")
        print(f"   Location: {_PROJECT_ROOT / '.env'}\n")
        sys.exit(1)
