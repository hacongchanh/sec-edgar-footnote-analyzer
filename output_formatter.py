"""
output_formatter.py — Format analysis results for console, HTML, and Markdown output.

Uses the Rich library for beautiful console tables and generates standalone
HTML reports with color-coded severity and collapsible detail sections.
"""

import html as html_module
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.columns import Columns
from rich.markdown import Markdown
from rich import box

from analyzer import AnalysisResult, Change


# ── Console Output ─────────────────────────────────────────────────────────


class OutputFormatter:
    """Format AnalysisResult for display in console, HTML, or Markdown."""

    SEVERITY_COLORS = {
        "HIGH": "red",
        "MEDIUM": "yellow",
        "LOW": "green",
    }

    SEVERITY_EMOJI = {
        "HIGH": "🔴",
        "MEDIUM": "🟡",
        "LOW": "🟢",
    }

    def __init__(self):
        self.console = Console()

    # ── Console (Rich) ─────────────────────────────────────────────────

    def print_console(self, result: AnalysisResult) -> None:
        """Print the full analysis result to the console using Rich."""
        self.console.print()

        # Header
        header_text = Text()
        header_text.append(f"SEC Filing Footnote Analysis\n", style="bold white")
        header_text.append(f"{result.company} ({result.ticker})", style="bold cyan")

        self.console.print(Panel(
            header_text,
            title="📊 EDGAR Footnote Analyzer",
            border_style="cyan",
            padding=(1, 2),
        ))

        # Filing info
        self.console.print(f"  📄 Current:  [bold]{result.current_filing}[/bold]")
        self.console.print(f"     URL: [dim]{result.current_filing_url}[/dim]")
        self.console.print(f"  📄 Previous: [bold]{result.previous_filing}[/bold]")
        self.console.print(f"     URL: [dim]{result.previous_filing_url}[/dim]")
        self.console.print(f"  📝 Notes compared: [bold]{result.total_notes_compared}[/bold]")
        self.console.print()

        # Stats bar
        high = sum(1 for c in result.changes if c.severity == "HIGH")
        med = sum(1 for c in result.changes if c.severity == "MEDIUM")
        low = sum(1 for c in result.changes if c.severity == "LOW")
        self.console.print(
            f"  Changes found: "
            f"[bold red]🔴 {high} HIGH[/bold red]  "
            f"[bold yellow]🟡 {med} MEDIUM[/bold yellow]  "
            f"[bold green]🟢 {low} LOW[/bold green]  "
            f"[bold]Total: {len(result.changes)}[/bold]"
        )
        self.console.print()

        # Added/removed notes
        if result.notes_added:
            self.console.print("  [bold red]📌 Notes ADDED in current filing:[/bold red]")
            for note in result.notes_added:
                self.console.print(f"     + {note}")
            self.console.print()

        if result.notes_removed:
            self.console.print("  [bold red]📌 Notes REMOVED from current filing:[/bold red]")
            for note in result.notes_removed:
                self.console.print(f"     − {note}")
            self.console.print()

        # Changes table
        if result.changes:
            self._print_changes_table(result.changes)
            self.console.print()

            # Detailed excerpts for HIGH severity changes
            high_changes = [c for c in result.changes if c.severity == "HIGH"]
            if high_changes:
                self.console.print(Panel(
                    "[bold red]HIGH-SEVERITY CHANGE DETAILS[/bold red]",
                    border_style="red",
                ))
                for c in high_changes:
                    self._print_change_detail(c)

        # Summary
        self.console.print()
        self.console.print(Panel(
            Markdown(result.summary),
            title="📋 Executive Summary",
            border_style="blue",
            padding=(1, 2),
        ))
        self.console.print()

    def _print_changes_table(self, changes: list[Change]) -> None:
        """Print a formatted table of changes sorted by severity."""
        table = Table(
            title="Footnote Changes",
            box=box.ROUNDED,
            show_lines=True,
            title_style="bold",
            header_style="bold white on dark_blue",
            padding=(0, 1),
        )

        table.add_column("#", style="dim", width=4, justify="center")
        table.add_column("Note", width=28)
        table.add_column("Category", width=18)
        table.add_column("Severity", width=10, justify="center")
        table.add_column("Change Description", width=50)
        table.add_column("Analyst Implication", width=40)

        for i, change in enumerate(changes, 1):
            color = self.SEVERITY_COLORS.get(change.severity, "white")
            emoji = self.SEVERITY_EMOJI.get(change.severity, "⚪")
            table.add_row(
                str(i),
                f"[{color}]Note {change.note_number}[/{color}]\n{change.note_title[:25]}…" if len(change.note_title) > 25 else f"[{color}]Note {change.note_number}[/{color}]\n{change.note_title}",
                change.category,
                f"[bold {color}]{emoji} {change.severity}[/bold {color}]",
                change.change_description[:120] + ("…" if len(change.change_description) > 120 else ""),
                change.analyst_implication[:100] + ("…" if len(change.analyst_implication) > 100 else ""),
            )

        self.console.print(table)

    def _print_change_detail(self, change: Change) -> None:
        """Print detailed before/after excerpts for a single change."""
        self.console.print(
            f"  [bold]Note {change.note_number}: {change.note_title}[/bold] "
            f"[dim]({change.category})[/dim]"
        )
        self.console.print(f"  [bold]{change.change_description}[/bold]")
        self.console.print()

        if change.previous_text_excerpt:
            self.console.print(f"  [red]◀ Previous:[/red] {change.previous_text_excerpt}")
        if change.current_text_excerpt:
            self.console.print(f"  [green]▶ Current:[/green]  {change.current_text_excerpt}")
        self.console.print(f"  [blue]→ Action:[/blue]   {change.recommended_action}")
        self.console.print("  " + "─" * 70)
        self.console.print()

    # ── HTML Report ────────────────────────────────────────────────────

    def to_html(self, result: AnalysisResult) -> str:
        """Generate a standalone HTML report."""
        high = sum(1 for c in result.changes if c.severity == "HIGH")
        med = sum(1 for c in result.changes if c.severity == "MEDIUM")
        low = sum(1 for c in result.changes if c.severity == "LOW")

        severity_css = {
            "HIGH": "background-color: #fee2e2; color: #991b1b; border-left: 4px solid #dc2626;",
            "MEDIUM": "background-color: #fef9c3; color: #854d0e; border-left: 4px solid #eab308;",
            "LOW": "background-color: #dcfce7; color: #166534; border-left: 4px solid #22c55e;",
        }

        badge_css = {
            "HIGH": "background-color: #dc2626; color: white;",
            "MEDIUM": "background-color: #eab308; color: #1a1a1a;",
            "LOW": "background-color: #22c55e; color: white;",
        }

        esc = html_module.escape

        # Build changes table rows
        changes_rows = ""
        for i, c in enumerate(result.changes, 1):
            sev_style = severity_css.get(c.severity, "")
            badge_style = badge_css.get(c.severity, "")
            changes_rows += f"""
            <tr style="{sev_style}">
                <td>{i}</td>
                <td><strong>Note {c.note_number}</strong><br><em>{esc(c.note_title)}</em></td>
                <td>{esc(c.category)}</td>
                <td><span class="badge" style="{badge_style}">{c.severity}</span></td>
                <td>{esc(c.change_description)}</td>
                <td>{esc(c.analyst_implication)}</td>
                <td>
                    <details>
                        <summary>View Details</summary>
                        <div class="excerpt-box">
                            <div class="previous"><strong>◀ Previous:</strong> {esc(c.previous_text_excerpt)}</div>
                            <div class="current"><strong>▶ Current:</strong> {esc(c.current_text_excerpt)}</div>
                            <div class="action"><strong>→ Action:</strong> {esc(c.recommended_action)}</div>
                        </div>
                    </details>
                </td>
            </tr>"""

        # Build added/removed notes sections
        added_html = ""
        if result.notes_added:
            added_items = "".join(f"<li>{esc(n)}</li>" for n in result.notes_added)
            added_html = f"""
            <div class="alert alert-added">
                <h3>📌 Notes Added in Current Filing</h3>
                <ul>{added_items}</ul>
            </div>"""

        removed_html = ""
        if result.notes_removed:
            removed_items = "".join(f"<li>{esc(n)}</li>" for n in result.notes_removed)
            removed_html = f"""
            <div class="alert alert-removed">
                <h3>📌 Notes Removed from Current Filing</h3>
                <ul>{removed_items}</ul>
            </div>"""

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Footnote Analysis: {esc(result.company)} ({esc(result.ticker)})</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; 
                background: #f8fafc; color: #1e293b; line-height: 1.6; padding: 2rem; }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        .header {{ background: linear-gradient(135deg, #1e3a5f 0%, #0f172a 100%);
                   color: white; padding: 2rem; border-radius: 12px; margin-bottom: 1.5rem; }}
        .header h1 {{ font-size: 1.8rem; margin-bottom: 0.5rem; }}
        .header .subtitle {{ opacity: 0.85; font-size: 0.95rem; }}
        .header a {{ color: #93c5fd; text-decoration: none; }}
        .header a:hover {{ text-decoration: underline; }}
        .stats {{ display: flex; gap: 1rem; margin: 1.5rem 0; flex-wrap: wrap; }}
        .stat-card {{ background: white; border-radius: 8px; padding: 1rem 1.5rem;
                      box-shadow: 0 1px 3px rgba(0,0,0,0.1); flex: 1; min-width: 150px;
                      text-align: center; }}
        .stat-card .number {{ font-size: 2rem; font-weight: bold; }}
        .stat-card .label {{ font-size: 0.85rem; color: #64748b; }}
        .stat-high .number {{ color: #dc2626; }}
        .stat-med .number {{ color: #eab308; }}
        .stat-low .number {{ color: #22c55e; }}
        table {{ width: 100%; border-collapse: collapse; background: white;
                 border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1);
                 margin: 1.5rem 0; }}
        th {{ background: #1e3a5f; color: white; padding: 0.75rem 1rem; text-align: left;
              font-weight: 600; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.5px; }}
        td {{ padding: 0.75rem 1rem; border-bottom: 1px solid #e2e8f0; 
              vertical-align: top; font-size: 0.9rem; }}
        tr:hover {{ filter: brightness(0.97); }}
        .badge {{ padding: 0.25rem 0.75rem; border-radius: 12px; font-weight: 600;
                  font-size: 0.8rem; display: inline-block; }}
        details {{ cursor: pointer; }}
        summary {{ color: #2563eb; font-size: 0.85rem; }}
        .excerpt-box {{ margin-top: 0.5rem; font-size: 0.85rem; }}
        .excerpt-box .previous {{ background: #fef2f2; padding: 0.5rem; border-radius: 4px; margin: 0.25rem 0; }}
        .excerpt-box .current {{ background: #f0fdf4; padding: 0.5rem; border-radius: 4px; margin: 0.25rem 0; }}
        .excerpt-box .action {{ background: #eff6ff; padding: 0.5rem; border-radius: 4px; margin: 0.25rem 0; }}
        .summary-panel {{ background: white; border-radius: 8px; padding: 1.5rem 2rem;
                         box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin: 1.5rem 0;
                         border-left: 4px solid #2563eb; }}
        .summary-panel h2 {{ color: #1e3a5f; margin-bottom: 1rem; }}
        .alert {{ padding: 1rem 1.5rem; border-radius: 8px; margin: 1rem 0; }}
        .alert h3 {{ margin-bottom: 0.5rem; }}
        .alert ul {{ padding-left: 1.5rem; }}
        .alert-added {{ background: #fee2e2; border-left: 4px solid #dc2626; }}
        .alert-removed {{ background: #fef9c3; border-left: 4px solid #eab308; }}
        .footer {{ text-align: center; color: #94a3b8; font-size: 0.8rem; margin-top: 2rem;
                   padding-top: 1rem; border-top: 1px solid #e2e8f0; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📊 Footnote Analysis Report</h1>
            <div class="subtitle">
                <strong>{esc(result.company)} ({esc(result.ticker)})</strong><br>
                Current: {esc(result.current_filing)}
                (<a href="{esc(result.current_filing_url)}" target="_blank">SEC Filing</a>)<br>
                Previous: {esc(result.previous_filing)}
                (<a href="{esc(result.previous_filing_url)}" target="_blank">SEC Filing</a>)<br>
                Notes compared: {result.total_notes_compared}
            </div>
        </div>

        <div class="stats">
            <div class="stat-card stat-high">
                <div class="number">{high}</div>
                <div class="label">🔴 High Severity</div>
            </div>
            <div class="stat-card stat-med">
                <div class="number">{med}</div>
                <div class="label">🟡 Medium Severity</div>
            </div>
            <div class="stat-card stat-low">
                <div class="number">{low}</div>
                <div class="label">🟢 Low Severity</div>
            </div>
            <div class="stat-card">
                <div class="number">{len(result.changes)}</div>
                <div class="label">Total Changes</div>
            </div>
        </div>

        {added_html}
        {removed_html}

        <table>
            <thead>
                <tr>
                    <th>#</th>
                    <th>Note</th>
                    <th>Category</th>
                    <th>Severity</th>
                    <th>Change Description</th>
                    <th>Analyst Implication</th>
                    <th>Details</th>
                </tr>
            </thead>
            <tbody>
                {changes_rows}
            </tbody>
        </table>

        <div class="summary-panel">
            <h2>📋 Executive Summary</h2>
            <div>{esc(result.summary).replace(chr(10), '<br>')}</div>
        </div>

        <div class="footer">
            Generated on {timestamp} | Data sourced from SEC EDGAR (sec.gov) | 
            Analysis powered by Google Gemini
        </div>
    </div>
</body>
</html>"""

    # ── Markdown Report ────────────────────────────────────────────────

    def to_markdown(self, result: AnalysisResult) -> str:
        """Generate a Markdown report."""
        high = sum(1 for c in result.changes if c.severity == "HIGH")
        med = sum(1 for c in result.changes if c.severity == "MEDIUM")
        low = sum(1 for c in result.changes if c.severity == "LOW")

        lines = [
            f"# Footnote Analysis: {result.company} ({result.ticker})",
            "",
            f"**Current Filing:** {result.current_filing}",
            f"- SEC URL: {result.current_filing_url}",
            f"",
            f"**Previous Filing:** {result.previous_filing}",
            f"- SEC URL: {result.previous_filing_url}",
            f"",
            f"**Notes Compared:** {result.total_notes_compared}",
            f"",
            f"## Change Summary",
            f"",
            f"| Severity | Count |",
            f"|----------|-------|",
            f"| 🔴 HIGH | {high} |",
            f"| 🟡 MEDIUM | {med} |",
            f"| 🟢 LOW | {low} |",
            f"| **Total** | **{len(result.changes)}** |",
            f"",
        ]

        if result.notes_added:
            lines.append("## ⚠ Notes Added")
            for n in result.notes_added:
                lines.append(f"- {n}")
            lines.append("")

        if result.notes_removed:
            lines.append("## ⚠ Notes Removed")
            for n in result.notes_removed:
                lines.append(f"- {n}")
            lines.append("")

        if result.changes:
            lines.append("## Detailed Changes")
            lines.append("")
            lines.append("| # | Note | Category | Severity | Change | Implication |")
            lines.append("|---|------|----------|----------|--------|-------------|")
            for i, c in enumerate(result.changes, 1):
                emoji = self.SEVERITY_EMOJI.get(c.severity, "⚪")
                desc = c.change_description.replace("|", "\\|")[:80]
                impl = c.analyst_implication.replace("|", "\\|")[:60]
                lines.append(
                    f"| {i} | Note {c.note_number}: {c.note_title[:20]} | "
                    f"{c.category} | {emoji} {c.severity} | {desc} | {impl} |"
                )
            lines.append("")

            # Detailed sections for HIGH severity
            high_changes = [c for c in result.changes if c.severity == "HIGH"]
            if high_changes:
                lines.append("## 🔴 High-Severity Details")
                lines.append("")
                for c in high_changes:
                    lines.append(f"### Note {c.note_number}: {c.note_title}")
                    lines.append(f"**Category:** {c.category}")
                    lines.append(f"")
                    lines.append(f"**Change:** {c.change_description}")
                    lines.append(f"")
                    if c.previous_text_excerpt:
                        lines.append(f"> **Previous:** {c.previous_text_excerpt}")
                    if c.current_text_excerpt:
                        lines.append(f"> **Current:** {c.current_text_excerpt}")
                    lines.append(f"")
                    lines.append(f"**Action:** {c.recommended_action}")
                    lines.append(f"")
                    lines.append("---")
                    lines.append("")

        lines.append("## Executive Summary")
        lines.append("")
        lines.append(result.summary)
        lines.append("")

        return "\n".join(lines)

    # ── File saving ────────────────────────────────────────────────────

    def save_html(self, result: AnalysisResult, output_dir: str | Path = ".") -> Path:
        """Save the HTML report and return the file path."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"footnote_analysis_{result.ticker}_{timestamp}.html"
        filepath = output_dir / filename
        filepath.write_text(self.to_html(result), encoding="utf-8")
        return filepath

    def save_markdown(self, result: AnalysisResult, output_dir: str | Path = ".") -> Path:
        """Save the Markdown report and return the file path."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"footnote_analysis_{result.ticker}_{timestamp}.md"
        filepath = output_dir / filename
        filepath.write_text(self.to_markdown(result), encoding="utf-8")
        return filepath
