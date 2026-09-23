"""
query_parser.py — LLM-powered natural language query parser.

Uses the Google Gemini API to extract structured parameters from free-form
user queries about SEC filing analysis.
"""

import json
import time
from typing import Optional

from google import genai
from google.genai import types
from pydantic import BaseModel

from config import GEMINI_API_KEY, GEMINI_MODEL

# Retry settings for transient Gemini API errors (503, 429)
_MAX_RETRIES = 3
_RETRY_DELAYS = [5, 15, 30]  # seconds


# ── Data Models ────────────────────────────────────────────────────────────


class ParsedQuery(BaseModel):
    """Structured representation of a user's filing analysis request."""
    company_name: Optional[str] = None    # e.g., "Apple"
    ticker: Optional[str] = None          # e.g., "AAPL"
    form_type: str = "10-Q"               # "10-K" or "10-Q"
    filing_period_current: Optional[str] = None   # e.g., "latest", "Q2 2025"
    filing_period_previous: Optional[str] = None  # e.g., "previous", "Q1 2025"
    focus_areas: list[str] = []           # e.g., ["revenue recognition", "debt"]
    comparison_type: str = "sequential"   # "sequential", "year-over-year", "custom"


# ── Query Parser ───────────────────────────────────────────────────────────


class QueryParser:
    """
    Parse natural language queries into structured analysis parameters
    using the Google Gemini API with structured JSON output.
    """

    def __init__(self):
        self._client = genai.Client(api_key=GEMINI_API_KEY)

    def parse(self, user_input: str) -> ParsedQuery:
        """
        Parse a free-form user query into a ParsedQuery.

        Examples:
            "Analyze Apple's latest 10-Q filing"
            "Compare MSFT's Q2 2024 and Q1 2024 10-Q footnotes"
            "Show me changes in Tesla's most recent 10-K vs the previous one"
        """
        system_prompt = """You are a financial query parser. Extract structured information from 
the user's natural language request about SEC filing analysis.

Rules:
- If the user mentions a company by name, set company_name. If they use a ticker symbol 
  (like AAPL, MSFT, TSLA), set ticker.
- Default form_type to "10-Q" unless the user specifically mentions "10-K" or "annual".
- filing_period_current: What the user considers the "current" or "primary" filing.
  Use "latest" if they say "latest", "most recent", etc. Use specific period if given 
  (e.g., "Q2 2025", "2024").
- filing_period_previous: The filing to compare against. Use "previous" if they say 
  "previous quarter", "prior", "preceding". Use specific period if given.
  If not mentioned, default to "previous".
- focus_areas: Specific topics the user wants to focus on (e.g., "revenue recognition", 
  "debt", "leases", "goodwill", "risk factors"). Empty list if no specific focus.
- comparison_type: "sequential" (latest vs immediately prior), "year-over-year" 
  (same quarter, different year), or "custom" (specific periods given).
"""

        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = self._client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=user_input,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        response_mime_type="application/json",
                        response_json_schema=ParsedQuery.model_json_schema(),
                        temperature=0.1,
                    ),
                )
                break  # success
            except Exception as api_err:
                status = getattr(api_err, "status_code", None) or getattr(api_err, "code", 0)
                if int(status) in (429, 503) and attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    print(f"  \u26a0 Gemini API temporarily unavailable ({status}), retrying in {delay}s\u2026")
                    time.sleep(delay)
                    continue
                raise

        try:
            data = json.loads(response.text)
            return ParsedQuery(**data)
        except (json.JSONDecodeError, ValueError) as e:
            # If structured parsing fails, try to extract basics manually
            print(f"  \u26a0 Query parsing fallback (LLM returned invalid JSON): {e}")
            return self._fallback_parse(user_input)

    def _fallback_parse(self, user_input: str) -> ParsedQuery:
        """Simple regex-based fallback parser for when the LLM fails."""
        import re

        query = ParsedQuery()

        # Try to find ticker (1-5 uppercase letters)
        ticker_match = re.search(r"\b([A-Z]{1,5})\b", user_input)
        # Common tickers that are also English words
        common_words = {"I", "A", "IT", "AT", "FOR", "ALL", "ARE", "THE", "AND", "OR", "NOT", "SEE"}

        if ticker_match and ticker_match.group(1) not in common_words:
            query.ticker = ticker_match.group(1)

        # Check for 10-K vs 10-Q
        if re.search(r"10-?K", user_input, re.IGNORECASE):
            query.form_type = "10-K"
        else:
            query.form_type = "10-Q"

        # Check for year-over-year comparison
        if re.search(r"year.over.year|yoy|same\s+quarter", user_input, re.IGNORECASE):
            query.comparison_type = "year-over-year"

        return query
