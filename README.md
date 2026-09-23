# SEC EDGAR Footnote Analysis Agent

An LLM-powered Python agent that helps financial analysts detect and analyze changes in the footnotes of SEC 10-K and 10-Q filings. It accepts natural language queries, fetches filings from EDGAR, extracts the "Notes to Financial Statements" sections, and uses the Google Gemini API to produce a structured comparison with a table of significant changes and an executive summary.

## Features

- **Natural language queries** — Just type what you want to analyze (e.g., *"Analyze Apple's latest 10-Q footnotes vs the previous quarter"*)
- **Automated EDGAR data pipeline** — Resolves tickers/company names → CIK, fetches filing history, downloads footnotes
- **Smart footnote extraction** — Uses SEC's `FilingSummary.xml` for pre-isolated footnote HTML files (fast, reliable) with BeautifulSoup fallback for older filings
- **LLM-powered analysis** — Google Gemini compares each footnote pair, classifying changes by category and severity
- **Rich output** — Color-coded console tables, standalone HTML reports, and Markdown exports
- **Dual interface** — CLI (interactive & one-shot modes) and Streamlit web UI

## Quick Start

### 1. Install Dependencies

```bash
python -m pip install -r requirements.txt
```

### 2. Configure Environment

Copy the example environment file and fill in your values:

```bash
cp .env.example .env
```

Edit `.env`:
```
GEMINI_API_KEY=your_gemini_api_key_here
SEC_USER_AGENT=YourName your@email.com
```

- **Gemini API Key**: Get a free key at [https://aistudio.google.com/apikey](https://aistudio.google.com/apikey)
- **SEC User-Agent**: Required by the SEC — must include your name and a contact email

### 3. Run the Agent

**Interactive CLI mode:**
```bash
python main.py
```

**One-shot mode:**
```bash
python main.py "Analyze Apple's latest 10-Q filing and see how the footnotes changed from the previous quarter"
```

**Web UI (Streamlit):**
```bash
streamlit run app.py
```

## Example Queries

| Query | What it does |
|-------|-------------|
| `"Analyze Apple's latest 10-Q footnotes vs the previous quarter"` | Compares AAPL's two most recent 10-Q footnotes |
| `"Compare MSFT's two most recent 10-K footnote disclosures"` | Compares Microsoft's latest two annual reports |
| `"Show changes in TSLA's latest 10-Q vs previous quarter, focus on debt and leases"` | Tesla 10-Q with focus on specific topics |
| `"Analyze NVDA's Q2 2024 vs Q1 2024 10-Q footnotes"` | Specific quarter comparison for NVIDIA |

## Output

The agent produces:

1. **Changes Table** — Each change includes:
   - Note number and title
   - Change category (New Disclosure, Language Change, Quantitative Change, etc.)
   - Severity rating (🔴 HIGH, 🟡 MEDIUM, 🟢 LOW)
   - Description of what changed
   - Analyst implication
   - Recommended action

2. **Executive Summary** — A narrative overview of the most important findings

3. **Reports** — Automatically saved to the `reports/` directory:
   - `footnote_analysis_{TICKER}_{timestamp}.html` — Standalone HTML report
   - `footnote_analysis_{TICKER}_{timestamp}.md` — Markdown report

## Architecture

```
User Query → Query Parser (Gemini) → EDGAR API → Footnote Extractor → Analyzer (Gemini) → Output
```

| Module | Purpose |
|--------|---------|
| `config.py` | Environment configuration and validation |
| `edgar_client.py` | SEC EDGAR API client (CIK lookup, filing history, downloads) |
| `footnote_extractor.py` | Footnote extraction via FilingSummary.xml + HTML fallback |
| `query_parser.py` | Gemini-powered natural language query parsing |
| `analyzer.py` | Gemini-powered footnote comparison (map-reduce pattern) |
| `output_formatter.py` | Rich console, HTML, and Markdown output formatting |
| `main.py` | CLI entry point |
| `app.py` | Streamlit web UI |

## Change Categories

| Category | Description |
|----------|-------------|
| **New Disclosure** | Information present in current filing but absent in previous |
| **Removed Disclosure** | Information present in previous filing but absent in current |
| **Language Change** | Wording or phrasing modifications |
| **Quantitative Change** | Different dollar amounts, percentages, or dates |
| **Accounting Policy Change** | Updates to accounting standards or policies |
| **Risk Factor Change** | Changes to disclosed risks |
| **Reclassification** | Items re-categorized or restructured |
| **Estimate Revision** | Changes in assumptions, estimates, or judgments |

## SEC EDGAR Fair Access

This tool respects the SEC's fair access policy:
- Custom `User-Agent` header with your name and email
- Rate limiting to ≤10 requests/second
- Local caching of downloaded filings in the `cache/` directory

## Requirements

- Python 3.10+
- Google Gemini API key (free tier available)
- Internet connection (for EDGAR and Gemini API access)

## License

This project is for educational and research purposes. SEC EDGAR data is public domain.
