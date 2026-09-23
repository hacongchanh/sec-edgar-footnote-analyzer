"""
main.py — CLI entry point for the SEC EDGAR Footnote Analysis Agent.

Usage:
    python main.py                              # Interactive mode
    python main.py "Analyze Apple's latest 10-Q"  # One-shot mode
    python main.py --help                        # Show help
"""

import sys
import argparse
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from config import require_valid_config
from edgar_client import EdgarClient
from footnote_extractor import FootnoteExtractor
from query_parser import QueryParser
from analyzer import FootnoteAnalyzer
from output_formatter import OutputFormatter

console = Console()


def run_analysis(query: str) -> None:
    """
    Execute the full analysis pipeline for a user query.

    Pipeline:
        1. Parse the natural language query → structured parameters
        2. Resolve company → CIK
        3. Fetch filing list, select the two filings to compare
        4. Extract footnotes from both filings
        5. Compare footnotes with LLM
        6. Format and display results
        7. Optionally save reports
    """
    console.print()
    console.print(Panel(
        f"[bold]Query:[/bold] {query}",
        title="🔍 Starting Analysis",
        border_style="cyan",
    ))
    console.print()

    # ── Step 1: Parse the query ───────────────────────────────────────
    console.print("[bold cyan]Step 1/6:[/bold cyan] Parsing your query…")
    parser = QueryParser()
    parsed = parser.parse(query)
    console.print(f"  Company: [bold]{parsed.company_name or '—'}[/bold]")
    console.print(f"  Ticker:  [bold]{parsed.ticker or '—'}[/bold]")
    console.print(f"  Form:    [bold]{parsed.form_type}[/bold]")
    console.print(f"  Current: [bold]{parsed.filing_period_current or 'latest'}[/bold]")
    console.print(f"  Previous:[bold]{parsed.filing_period_previous or 'previous'}[/bold]")
    if parsed.focus_areas:
        console.print(f"  Focus:   [bold]{', '.join(parsed.focus_areas)}[/bold]")
    console.print()

    # ── Step 2: Resolve company to CIK ────────────────────────────────
    console.print("[bold cyan]Step 2/6:[/bold cyan] Looking up company on SEC EDGAR…")
    client = EdgarClient()
    try:
        cik, ticker = client.resolve_identifier(parsed.company_name, parsed.ticker)
    except ValueError as e:
        console.print(f"  [bold red]Error:[/bold red] {e}")
        return
    console.print(f"  CIK: [bold]{cik}[/bold], Ticker: [bold]{ticker}[/bold]")
    console.print()

    # ── Step 3: Fetch filings and select pair ─────────────────────────
    console.print(f"[bold cyan]Step 3/6:[/bold cyan] Fetching {parsed.form_type} filing history…")
    filings = client.get_filings_list(cik, parsed.form_type)
    if len(filings) < 2:
        console.print(
            f"  [bold red]Error:[/bold red] Found only {len(filings)} {parsed.form_type} filing(s). "
            f"Need at least 2 to compare."
        )
        return
    console.print(f"  Found [bold]{len(filings)}[/bold] {parsed.form_type} filings")

    try:
        current_filing, previous_filing = client.select_filing_pair(
            filings,
            current_hint=parsed.filing_period_current,
            previous_hint=parsed.filing_period_previous,
        )
    except ValueError as e:
        console.print(f"  [bold red]Error:[/bold red] {e}")
        return

    console.print(f"  ▶ Current:  [bold]{current_filing.label}[/bold]")
    console.print(f"  ▶ Previous: [bold]{previous_filing.label}[/bold]")
    console.print()

    # ── Step 4: Extract footnotes ─────────────────────────────────────
    console.print("[bold cyan]Step 4/6:[/bold cyan] Extracting footnotes from both filings…")
    extractor = FootnoteExtractor()

    console.print(f"  📄 Extracting from current filing ({current_filing.filing_date})…")
    current_footnotes = extractor.extract_footnotes(client, current_filing)
    console.print(f"     Found [bold]{len(current_footnotes)}[/bold] footnotes")

    console.print(f"  📄 Extracting from previous filing ({previous_filing.filing_date})…")
    previous_footnotes = extractor.extract_footnotes(client, previous_filing)
    console.print(f"     Found [bold]{len(previous_footnotes)}[/bold] footnotes")

    if not current_footnotes:
        console.print("  [bold red]Error:[/bold red] Could not extract footnotes from the current filing.")
        return
    if not previous_footnotes:
        console.print("  [bold red]Error:[/bold red] Could not extract footnotes from the previous filing.")
        return

    console.print()

    # ── Step 5: Compare footnotes with LLM ────────────────────────────
    console.print("[bold cyan]Step 5/6:[/bold cyan] Analyzing footnote changes with Gemini…")
    console.print("  (This may take 1-3 minutes depending on the number of footnotes)")
    console.print()

    analyzer = FootnoteAnalyzer()
    company_name = parsed.company_name or ticker
    result = analyzer.compare_footnotes(
        current_footnotes=current_footnotes,
        previous_footnotes=previous_footnotes,
        current_label=current_filing.label,
        previous_label=previous_filing.label,
        company=company_name,
        ticker=ticker,
        current_url=current_filing.sec_url,
        previous_url=previous_filing.sec_url,
        focus_areas=parsed.focus_areas if parsed.focus_areas else None,
    )

    # ── Step 6: Display results ───────────────────────────────────────
    console.print()
    console.print("[bold cyan]Step 6/6:[/bold cyan] Formatting results…")
    console.print()

    formatter = OutputFormatter()
    formatter.print_console(result)

    # Save reports
    reports_dir = Path(__file__).parent / "reports"
    html_path = formatter.save_html(result, reports_dir)
    md_path = formatter.save_markdown(result, reports_dir)
    console.print(f"  📁 HTML report saved: [link=file://{html_path}]{html_path}[/link]")
    console.print(f"  📁 Markdown report saved: [link=file://{md_path}]{md_path}[/link]")
    console.print()


def interactive_mode() -> None:
    """Run the agent in interactive mode, accepting queries in a loop."""
    console.print(Panel(
        "[bold]SEC EDGAR Footnote Analysis Agent[/bold]\n\n"
        "Enter a query to analyze footnote changes in SEC filings.\n"
        "Examples:\n"
        '  • "Analyze Apple\'s latest 10-Q footnotes vs the previous quarter"\n'
        '  • "Compare MSFT\'s two most recent 10-K footnote disclosures"\n'
        '  • "Show me changes in TSLA\'s Q2 2024 vs Q1 2024 10-Q footnotes"\n\n'
        "Type [bold]quit[/bold] or [bold]exit[/bold] to stop.",
        title="📊 Welcome",
        border_style="cyan",
        padding=(1, 2),
    ))

    while True:
        console.print()
        try:
            query = console.input("[bold cyan]Enter your query:[/bold cyan] ")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye![/dim]")
            break

        query = query.strip()
        if not query:
            continue
        if query.lower() in ("quit", "exit", "q"):
            console.print("[dim]Goodbye![/dim]")
            break

        try:
            run_analysis(query)
        except KeyboardInterrupt:
            console.print("\n[yellow]Analysis interrupted.[/yellow]")
        except Exception as e:
            console.print(f"\n[bold red]Error:[/bold red] {e}")
            console.print("[dim]Please try again with a different query.[/dim]")


def main():
    """Entry point with argument parsing."""
    parser = argparse.ArgumentParser(
        description="SEC EDGAR Footnote Analysis Agent — Compare footnotes across filings",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py
  python main.py "Analyze Apple's latest 10-Q filing"
  python main.py "Compare MSFT 10-K footnotes for 2024 vs 2023"
  python main.py "Show TSLA footnote changes in their latest 10-Q"
        """,
    )
    parser.add_argument(
        "query",
        nargs="?",
        help="Analysis query (omit for interactive mode)",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Don't save HTML/Markdown reports to disk",
    )

    args = parser.parse_args()

    # Validate configuration
    require_valid_config()

    if args.query:
        # One-shot mode
        run_analysis(args.query)
    else:
        # Interactive mode
        interactive_mode()


if __name__ == "__main__":
    main()
