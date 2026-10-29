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
    is_valid: bool = True                 # False if required info is missing or contradictory
    missing_fields: list[str] = []        # List of missing or contradictory elements
    clarification_prompt: Optional[str] = None  # User-facing guidance when info is missing or contradictory
    detected_actions: list[str] = []      # Any detected operational/trading commands
    action_warning: Optional[str] = None  # Explicit refusal banner when action commands are detected


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
3. filing_period_current & filing_period_previous: Must indicate a timeframe or period (e.g., "latest", "most recent", "Q2 2024", "2023", "previous quarter").
   - If the user requests "2 latest" or "two most recent" filings, set filing_period_current to "latest" and filing_period_previous to "previous".
   - If the user DOES NOT provide any timeframe or comparison period, set filing_period_current to null and add "Timeframe / filing period (e.g., 'latest' or specific quarter/year)" to missing_fields. DO NOT DEFAULT OR GUESS.
4. Contradictory Filing Requests:
   - Public registrants NEVER file Form 10-Q for the fourth quarter (Q4). The fourth quarter is the fiscal year end; fourth-quarter results and disclosures are audited and consolidated into the annual Form 10-K.
   - If the user requests a "Q4 10-Q", "4th quarter 10-Q", or "fourth quarter 10-Q", you MUST set is_valid to false, add "Contradictory filing request: Form 10-Q does not exist for Q4 (4th quarter results are reported in annual Form 10-K)" to missing_fields, and explain this in clarification_prompt.
   - Form 10-K is an annual filing, not a quarterly filing (there is no "Q1 10-K" or "Q2 10-K"). If requested, set is_valid to false.
5. Autonomous Action Detection:
   - This agent is strictly an analytical advisory tool and cannot execute trades, purchase shares, submit regulatory filings to the SEC, or transfer funds.
   - If the query includes commands to execute actions (e.g. "buy 50 shares", "sell stock", "trade", "wire funds", "submit filing to SEC", "file with SEC"), extract the action command into detected_actions (e.g. ["buy 50 of its shares"]).
6. is_valid: Set to true ONLY if company/ticker, form_type, and timeframe are ALL present, AND there are NO contradictory filing requests. If any are missing or contradictory, set is_valid to false.
7. clarification_prompt: If is_valid is false, write a polite, concise instruction explaining what is missing or contradictory and provide an example of a complete query. If is_valid is true, set clarification_prompt to null.
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
        import re

        # Normalize "2 latest" / "two most recent" sequential requests
        if re.search(r"\b(?:2|two)\s+(?:latest|most\s+recent)\b", raw_input, re.IGNORECASE):
            if not query.filing_period_current or query.filing_period_current.lower() in ("null", "none"):
                query.filing_period_current = "latest"
            if not query.filing_period_previous or query.filing_period_previous.lower() in ("null", "none"):
                query.filing_period_previous = "previous"

        # Check for autonomous action execution commands (trading, transfers, SEC submissions, operations)
        action_patterns = [
            # Stock trading / order execution
            r"\b(?:buy|sell|short|purchase|trade|order|transact|invest\s+in|liquidate|rebalance)\s+(?:\d+\s+(?:of\s+its\s+shares?|shares?|contracts?)|[\$\d]+|stock|shares?|equity|portfolio|position|calls?|puts?|some|all|of\s+its\s+shares?|\d+)\b",
            r"\b(?:place|execute|submit)\s+(?:an?\s+)?(?:market|limit|stop)?\s*(?:order|trade)\b",
            # Funds transfers & banking
            r"\b(?:wire|transfer|send|remit|deposit|withdraw)\s+(?:funds?|money|cash|\$[\d,]+)\b",
            # Regulatory / SEC submissions & filings
            r"\b(?:submit|file|upload|amend|post)\s+(?:this|the|an?)?\s*(?:filing|form|10-k|10-q|8-k|report|disclosure)\s+(?:to|with)\s+(?:sec|edgar)\b",
            r"\b(?:submit\s+to\s+sec|file\s+with\s+sec|upload\s+to\s+edgar)\b",
            # System & operational actions
            r"\b(?:execute\s+contract|sign\s+agreement|delete\s+records?|drop\s+database|wipe\s+cache)\b",
        ]

        detected = list(query.detected_actions or [])
        for pat in action_patterns:
            for match in re.finditer(pat, raw_input, re.IGNORECASE):
                m_str = match.group(0).strip()
                if m_str not in detected:
                    detected.append(m_str)

        if detected:
            query.detected_actions = detected
            actions_list_str = ", ".join(f"'{a}'" for a in detected)
            refusal_banner = (
                f"⚠️ Autonomous Action Refusal: This agent is strictly a read-only analytical advisory tool and does not "
                f"possess broker connectivity, SEC filing submission credentials, banking wire authorization, or transactional "
                f"capabilities. The requested autonomous action command ({actions_list_str}) has been REFUSED and was NOT executed."
            )
            query.action_warning = refusal_banner

            # If user query is PURELY an action command without a filing analysis request
            if not query.form_type and not query.filing_period_current and not re.search(r"10-?[kq]|footnote|annual|quarterly", raw_input, re.IGNORECASE):
                query.is_valid = False
                query.missing_fields = ["Analysis request (company, form type, period)"]
                query.clarification_prompt = (
                    f"{refusal_banner} Please provide an SEC filing analysis request instead, for example: "
                    f"\"Analyze {query.company_name or 'Apple'}'s latest 10-Q filing vs previous quarter\"."
                )
                return query

        # Check for Contradictory Filing Requests: Q4 10-Q
        is_10q = (query.form_type == "10-Q") or bool(re.search(r"\b10-?q\b", raw_input, re.IGNORECASE))
        has_q4 = bool(re.search(r"\b(?:q4|4th\s+quarter|fourth\s+quarter)\b", raw_input, re.IGNORECASE)) or (
            query.filing_period_current is not None and bool(re.search(r"\b(?:q4|4th\s+quarter|fourth\s+quarter)\b", str(query.filing_period_current), re.IGNORECASE))
        )

        if is_10q and has_q4:
            query.is_valid = False
            contra_msg = "Contradictory filing request: Form 10-Q does not exist for the fourth quarter (Q4 results are reported in annual Form 10-K)"
            query.missing_fields = [contra_msg]
            query.clarification_prompt = (
                "⚠️ Contradictory Filing Request: Public companies do not file Form 10-Q for the fourth quarter (Q4). "
                "Fourth-quarter financial results and footnote disclosures are audited and consolidated into the annual Form 10-K. "
                "Please specify whether you want to analyze the annual Form 10-K (e.g., 'Analyze Apple\\'s latest 10-K vs previous year') "
                "or the third-quarter Form 10-Q (e.g., 'Analyze Apple\\'s latest 10-Q vs previous quarter')."
            )
            return query

        # Check for Contradictory Filing Requests: Quarterly 10-K
        is_10k = (query.form_type == "10-K") or bool(re.search(r"\b10-?k\b", raw_input, re.IGNORECASE))
        has_quarter = bool(re.search(r"\b(?:q[1-4]|1st\s+quarter|2nd\s+quarter|3rd\s+quarter|4th\s+quarter|first\s+quarter|second\s+quarter|third\s+quarter|fourth\s+quarter)\b", raw_input, re.IGNORECASE))
        if is_10k and has_quarter:
            query.is_valid = False
            contra_msg = "Contradictory filing request: Form 10-K is an annual report covering full fiscal years, not quarterly periods"
            query.missing_fields = [contra_msg]
            query.clarification_prompt = (
                "⚠️ Contradictory Filing Request: Form 10-K is an annual report covering the entire fiscal year, not an individual quarter. "
                "If you are seeking quarterly footnote disclosures, please specify Form 10-Q (e.g., 'Analyze Apple\\'s latest 10-Q filing vs previous quarter')."
            )
            return query

        # Check required fields for completeness
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
        elif re.search(r"mcdonald", user_input, re.IGNORECASE):
            query.company_name = "McDonald's"
            query.ticker = "MCD"
        elif re.search(r"nike", user_input, re.IGNORECASE):
            query.company_name = "Nike"
            query.ticker = "NKE"
        elif re.search(r"boeing", user_input, re.IGNORECASE):
            query.company_name = "Boeing"
            query.ticker = "BA"
        elif re.search(r"ford", user_input, re.IGNORECASE):
            query.company_name = "Ford"
            query.ticker = "F"
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

        return self._enforce_validation_safeguards(query, user_input)
