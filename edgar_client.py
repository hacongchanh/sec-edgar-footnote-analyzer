"""
edgar_client.py — SEC EDGAR API client for fetching company filings.

Handles ticker-to-CIK resolution, filing history retrieval, and document
downloading with proper rate limiting and caching per SEC fair-access rules.
"""

import json
import time
import re
from pathlib import Path
from typing import Optional

import requests
from pydantic import BaseModel

from config import (
    SEC_USER_AGENT,
    SEC_TICKERS_URL,
    SEC_SUBMISSIONS_URL,
    SEC_ARCHIVES_BASE,
    SEC_REQUEST_DELAY,
    CACHE_DIR,
)


# ── Data Models ────────────────────────────────────────────────────────────


class FilingMeta(BaseModel):
    """Metadata for a single SEC filing."""
    accession_number: str        # e.g., "0000320193-24-000106"
    filing_date: str             # e.g., "2024-11-01"
    report_date: str             # e.g., "2024-09-28"
    form_type: str               # "10-K" or "10-Q"
    primary_document: str        # e.g., "aapl-20240928.htm"
    base_url: str                # Archive directory URL
    sec_url: str                 # Full URL to the primary document

    @property
    def accession_nodash(self) -> str:
        return self.accession_number.replace("-", "")

    @property
    def label(self) -> str:
        return f"{self.form_type} filed {self.filing_date} (period: {self.report_date})"


# ── EDGAR Client ───────────────────────────────────────────────────────────


class EdgarClient:
    """
    Client for the SEC EDGAR REST APIs.

    Respects the SEC fair-access policy:
      • Custom User-Agent header with name + email
      • ≤10 requests / second (enforced via sleep)
      • Local caching to minimize redundant requests
    """

    def __init__(self, user_agent: str | None = None):
        self._user_agent = user_agent or SEC_USER_AGENT
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": self._user_agent,
            "Accept-Encoding": "gzip, deflate",
        })
        self._last_request_time: float = 0.0
        self._tickers_cache: dict | None = None

    # ── HTTP helpers ───────────────────────────────────────────────────

    def _rate_limited_get(self, url: str, params: dict | None = None) -> requests.Response:
        """GET with mandatory inter-request delay and retry on 429/403."""
        elapsed = time.time() - self._last_request_time
        if elapsed < SEC_REQUEST_DELAY:
            time.sleep(SEC_REQUEST_DELAY - elapsed)

        for attempt in range(3):
            resp = self._session.get(url, params=params, timeout=30)
            self._last_request_time = time.time()

            if resp.status_code in (429, 403) and attempt < 2:
                wait = 2 ** (attempt + 1)
                print(f"  ⚠ SEC rate limit hit ({resp.status_code}), retrying in {wait}s…")
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return resp

        resp.raise_for_status()
        return resp  # unreachable, but satisfies type checker

    def _get_cached(self, cache_key: str, url: str) -> str:
        """Return cached content or download and cache it."""
        cache_path = CACHE_DIR / cache_key
        if cache_path.exists():
            return cache_path.read_text(encoding="utf-8")

        resp = self._rate_limited_get(url)
        content = resp.text
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(content, encoding="utf-8")
        return content

    # ── Ticker / CIK resolution ────────────────────────────────────────

    def _load_tickers(self) -> dict:
        """Download and cache the SEC master ticker→CIK mapping."""
        if self._tickers_cache is not None:
            return self._tickers_cache

        cache_path = CACHE_DIR / "company_tickers.json"
        if cache_path.exists():
            data = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            resp = self._rate_limited_get(SEC_TICKERS_URL)
            data = resp.json()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(data), encoding="utf-8")

        self._tickers_cache = data
        return data

    def ticker_to_cik(self, ticker: str) -> str:
        """
        Resolve a stock ticker to a 10-digit zero-padded CIK string.

        Raises ValueError if the ticker is not found.
        """
        data = self._load_tickers()
        ticker_upper = ticker.upper().strip()
        for entry in data.values():
            if entry["ticker"] == ticker_upper:
                return str(entry["cik_str"]).zfill(10)
        raise ValueError(
            f"Ticker '{ticker}' not found in SEC EDGAR registry. "
            f"Please check the ticker symbol."
        )

    def company_name_to_cik(self, name: str) -> tuple[str, str]:
        """
        Fuzzy-match a company name to a (CIK, ticker) pair.

        Returns the first match using case-insensitive substring matching.
        Raises ValueError if no match is found.
        """
        data = self._load_tickers()
        name_lower = name.lower().strip()
        for entry in data.values():
            if name_lower in entry["title"].lower():
                cik = str(entry["cik_str"]).zfill(10)
                return cik, entry["ticker"]
        raise ValueError(
            f"Company '{name}' not found in SEC EDGAR registry. "
            f"Try using the stock ticker instead."
        )

    def resolve_identifier(self, company_name: str | None, ticker: str | None) -> tuple[str, str]:
        """
        Resolve either a company name or ticker to (CIK, ticker).

        Tries ticker first (exact match), then company name (fuzzy).
        """
        if ticker:
            cik = self.ticker_to_cik(ticker)
            return cik, ticker.upper().strip()
        if company_name:
            return self.company_name_to_cik(company_name)
        raise ValueError("Either company_name or ticker must be provided.")

    # ── Filing metadata ────────────────────────────────────────────────

    def get_submissions(self, cik: str) -> dict:
        """Fetch the full submissions JSON for a company."""
        padded = cik.zfill(10)
        url = SEC_SUBMISSIONS_URL.format(cik=padded)
        resp = self._rate_limited_get(url)
        return resp.json()

    def get_filings_list(self, cik: str, form_type: str = "10-Q") -> list[FilingMeta]:
        """
        Return a list of FilingMeta for a company's filings of the given type,
        ordered most-recent-first.
        """
        data = self.get_submissions(cik)
        recent = data["filings"]["recent"]
        cik_unpadded = str(int(cik))

        filings: list[FilingMeta] = []
        for i, form in enumerate(recent["form"]):
            if form.upper() == form_type.upper():
                accession = recent["accessionNumber"][i]
                acc_nodash = accession.replace("-", "")
                primary_doc = recent["primaryDocument"][i]
                base_url = f"{SEC_ARCHIVES_BASE}/{cik_unpadded}/{acc_nodash}/"
                sec_url = f"{base_url}{primary_doc}"

                filings.append(FilingMeta(
                    accession_number=accession,
                    filing_date=recent["filingDate"][i],
                    report_date=recent.get("reportDate", recent["filingDate"])[i],
                    form_type=form,
                    primary_document=primary_doc,
                    base_url=base_url,
                    sec_url=sec_url,
                ))
        return filings

    def select_filing_pair(
        self,
        filings: list[FilingMeta],
        current_hint: str | None = None,
        previous_hint: str | None = None,
    ) -> tuple[FilingMeta, FilingMeta]:
        """
        Select two filings to compare based on user hints.

        Hints can be "latest", "previous", a date like "2024-06-30", 
        a quarter like "Q2 2024", or a year like "2024".

        Defaults: current = latest filing, previous = second-latest filing.
        """
        if len(filings) < 2:
            raise ValueError(
                f"Need at least 2 filings to compare, but only found {len(filings)}."
            )

        def _match(hint: str | None, filings_list: list[FilingMeta], default_idx: int) -> FilingMeta:
            if hint is None or hint.lower() in ("latest", "most recent"):
                return filings_list[default_idx]
            if hint.lower() in ("previous", "prior", "preceding"):
                return filings_list[min(default_idx + 1, len(filings_list) - 1)]

            hint_lower = hint.lower().strip()

            # Try exact date match (YYYY-MM-DD)
            for f in filings_list:
                if f.filing_date == hint_lower or f.report_date == hint_lower:
                    return f

            # Try quarter match (e.g., "Q2 2024")
            q_match = re.match(r"q(\d)\s*(\d{4})", hint_lower)
            if q_match:
                quarter, year = int(q_match.group(1)), q_match.group(2)
                quarter_months = {1: ("01", "03"), 2: ("04", "06"), 3: ("07", "09"), 4: ("10", "12")}
                if quarter in quarter_months:
                    start_m, end_m = quarter_months[quarter]
                    for f in filings_list:
                        if f.report_date.startswith(year):
                            month = f.report_date[5:7]
                            if start_m <= month <= end_m:
                                return f

            # Try year match
            for f in filings_list:
                if hint_lower in f.report_date or hint_lower in f.filing_date:
                    return f

            # Fallback to default
            return filings_list[default_idx]

        current = _match(current_hint, filings, 0)
        previous = _match(previous_hint, filings, 1)

        # Ensure we don't compare a filing with itself
        if current.accession_number == previous.accession_number:
            idx = filings.index(current)
            if idx + 1 < len(filings):
                previous = filings[idx + 1]
            elif idx - 1 >= 0:
                previous = filings[idx - 1]
            else:
                raise ValueError("Cannot find two distinct filings to compare.")

        return current, previous

    # ── Document downloading ───────────────────────────────────────────

    def get_filing_summary_xml(self, filing: FilingMeta) -> str | None:
        """
        Download FilingSummary.xml from the filing's archive directory.
        Returns None if the file doesn't exist (older filings).
        """
        url = f"{filing.base_url}FilingSummary.xml"
        cache_key = f"{filing.accession_nodash}/FilingSummary.xml"
        try:
            return self._get_cached(cache_key, url)
        except requests.HTTPError:
            return None

    def download_note_html(self, filing: FilingMeta, html_filename: str) -> str:
        """Download an individual footnote HTML file (e.g., R12.htm)."""
        url = f"{filing.base_url}{html_filename}"
        cache_key = f"{filing.accession_nodash}/{html_filename}"
        return self._get_cached(cache_key, url)

    def download_filing_html(self, filing: FilingMeta) -> str:
        """Download the full primary filing document HTML."""
        cache_key = f"{filing.accession_nodash}/{filing.primary_document}"
        return self._get_cached(cache_key, filing.sec_url)
