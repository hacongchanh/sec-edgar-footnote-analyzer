"""
query_parser.py — LLM-powered natural language query parser with input validation.

Uses the Google Gemini API to extract structured parameters from free-form
user queries about SEC filing analysis and enforces input controls (Step 9)
to detect missing required information before execution.
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
    form_type: Optional[str] = None       # "10-K" or "10-Q" (None if missing)
    filing_period_current: Optional[str] = None   # e.g., "latest", "Q2 2025" (None if missing)
    filing_period_previous: Optional[str] = None  # e.g., "previous", "Q1 2025"
    focus_areas: list[str] = []           # e.g., ["revenue recognition", "debt"]
    comparison_type: str = "sequential"   # "sequential", "year-over-year", "custom"
    is_valid: bool = True                 # False if required information is missing
    missing_fields: list[str] = []        # List of missing required elements
    clarification_prompt: Optional[str] = None  # User-facing guidance when info is missing


# ── Query Parser ───────────────────────────────────────────────────────────


class QueryParser:
    """
    Parse natural language queries into structured analysis parameters
    using the Google Gemini API with input validation controls.
    """

    def __init__(self):
        self._client = genai.Client(api_key=GEMINI_API_KEY)

    def parse(self, user_input: str) -> ParsedQuery:
        """
        Parse a free-form user query into a validated ParsedQuery.

        Input Controls:
            Ensures company/ticker, filing type (10-K vs 10-Q), and
            filing timeframe are present. If any are missing, flags
            is_valid = False and returns guidance for the user.
        """
        system_prompt = """You are a financial query parser and input validation controller. 
Extract structured information from the user's natural language request about SEC filing analysis.

Strict Validation Rules:
1. company_name / ticker: Identify the company name or stock ticker. If absent, add "Company name or ticker symbol" to missing_fields.
2. form_type: Must be explicitly specified or clearly implied by the user (e.g., "10-K", "annual", "10-Q", "quarterly").
   - If the user specifies annual/10-K, set form_type to "10-K".
   - If the user specifies quarterly/10-Q, set form_type to "10-Q".
   - If the user DOES NOT specify whether they want 10-K or 10-Q, set form_type to null and add "Filing type (specify 10-K for annual or 10-Q for quarterly)" to missing_fields. DO NOT DEFAULT OR GUESS.
3. filing_period_current: Must indicate a timeframe or period (e.g., "latest", "most recent", "Q2 2024", "2023", "previous quarter").
   - If the user DOES NOT provide any timeframe or comparison period, set filing_period_current to null and add "Timeframe / filing period (e.g., 'latest' or specific quarter/year)" to missing_fields. DO NOT DEFAULT OR GUESS.
4. is_valid: Set to true ONLY if company/ticker, form_type, and timeframe are ALL present. If any are missing, set is_valid to false.
5. clarification_prompt: If is_valid is false, write a polite, concise instruction telling the user what is missing and provide an example of a complete query. Example: "Please specify whether you want a 10-K (annual) or 10-Q (quarterly) filing, and which period to compare. For example: 'Analyze Apple's latest 10-Q filing vs the previous quarter'." If is_valid is true, set clarification_prompt to null.
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
                    print(f"  ⚠ Gemini API temporarily unavailable ({status}), retrying in {delay}s…")
                    time.sleep(delay)
                    continue
                raise

        try:
            data = json.loads(response.text)
            parsed = ParsedQuery(**data)
            return self._enforce_validation_safeguards(parsed, user_input)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"  ⚠ Query parsing fallback (LLM returned invalid JSON): {e}")
            return self._fallback_parse(user_input)

    def _enforce_validation_safeguards(self, query: ParsedQuery, raw_input: str) -> ParsedQuery:
        """Deterministic programmatic validation safeguards on top of LLM output."""
        missing = []

        if not query.company_name and not query.ticker:
            missing.append("Company name or ticker symbol")

        if not query.form_type or query.form_type not in ("10-K", "10-Q"):
            missing.append("Filing type (specify 10-K for annual or 10-Q for quarterly)")
            query.form_type = None

        if not query.filing_period_current:
            missing.append("Filing period / timeframe (e.g., 'latest' or specific quarter/year)")

        if missing:
            query.is_valid = False
            query.missing_fields = missing
            if not query.clarification_prompt:
                query.clarification_prompt = (
                    f"Your request is missing required details: {', '.join(missing)}. "
                    "Please provide a complete query, for example: "
                    f"\"Analyze {query.company_name or 'Apple'}'s latest 10-Q filing vs previous quarter\"."
                )
        else:
            query.is_valid = True
            query.missing_fields = []
            query.clarification_prompt = None

        return query

    def _fallback_parse(self, user_input: str) -> ParsedQuery:
        """Simple regex-based fallback parser with input validation."""
        import re

        query = ParsedQuery()
        missing = []

        # Try to find ticker (1-5 uppercase letters)
        ticker_match = re.search(r"\b([A-Z]{1,5})\b", user_input)
        common_words = {"I", "A", "IT", "AT", "FOR", "ALL", "ARE", "THE", "AND", "OR", "NOT", "SEE", "ANALYZE"}

        if ticker_match and ticker_match.group(1) not in common_words:
            query.ticker = ticker_match.group(1)
        elif re.search(r"apple", user_input, re.IGNORECASE):
            query.company_name = "Apple"
            query.ticker = "AAPL"
        elif re.search(r"microsoft", user_input, re.IGNORECASE):
            query.company_name = "Microsoft"
            query.ticker = "MSFT"
        elif re.search(r"tesla", user_input, re.IGNORECASE):
            query.company_name = "Tesla"
            query.ticker = "TSLA"
        else:
            missing.append("Company name or ticker symbol")

        # Check for 10-K vs 10-Q
        if re.search(r"10-?K|annual", user_input, re.IGNORECASE):
            query.form_type = "10-K"
        elif re.search(r"10-?Q|quarter", user_input, re.IGNORECASE):
            query.form_type = "10-Q"
        else:
            query.form_type = None
            missing.append("Filing type (10-K or 10-Q)")

        # Check for timeframe
        if re.search(r"latest|most\s+recent|recent|202\d|q[1-4]", user_input, re.IGNORECASE):
            query.filing_period_current = "latest"
            query.filing_period_previous = "previous"
        else:
            query.filing_period_current = None
            missing.append("Timeframe / filing period")

        return self._enforce_validation_safeguards(query, user_input)
