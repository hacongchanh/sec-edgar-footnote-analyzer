"""
analyzer.py — LLM-powered footnote comparison and analysis engine.

Uses the Google Gemini API to perform deep comparison of footnotes between
two SEC filings, identifying and classifying significant changes.
"""

import json
import time
from typing import Optional

from google import genai
from google.genai import types
from pydantic import BaseModel

from config import GEMINI_API_KEY, GEMINI_MODEL
from footnote_extractor import Footnote

# Retry settings for transient Gemini API errors (503, 429)
_MAX_RETRIES = 3
_RETRY_DELAYS = [5, 15, 30]  # seconds


# ── Data Models ────────────────────────────────────────────────────────────


class Change(BaseModel):
    """A single identified change between two versions of a footnote."""
    note_number: int
    note_title: str
    change_description: str
    category: str            # New Disclosure, Removed Disclosure, Language Change, etc.
    severity: str            # HIGH, MEDIUM, LOW
    current_text_excerpt: str
    previous_text_excerpt: str
    analyst_implication: str
    recommended_action: str


class AnalysisResult(BaseModel):
    """Complete result of a footnote comparison analysis."""
    company: str
    ticker: str
    current_filing: str        # e.g., "10-Q filed 2025-08-01 (period: 2025-06-28)"
    previous_filing: str       # e.g., "10-Q filed 2025-05-02 (period: 2025-03-29)"
    current_filing_url: str
    previous_filing_url: str
    changes: list[Change]
    summary: str               # Overall narrative summary
    notes_added: list[str]     # Notes in current but not previous
    notes_removed: list[str]   # Notes in previous but not current
    total_notes_compared: int


# ── Analyzer ───────────────────────────────────────────────────────────────


class FootnoteAnalyzer:
    """
    Compare footnotes between two filings using the Google Gemini API.

    Uses a map-reduce strategy:
      Map:    Compare each note pair individually (focused, reliable)
      Reduce: Synthesize all changes into an overall summary
    """

    def __init__(self):
        self._client = genai.Client(api_key=GEMINI_API_KEY)

    def compare_footnotes(
        self,
        current_footnotes: list[Footnote],
        previous_footnotes: list[Footnote],
        current_label: str,
        previous_label: str,
        company: str,
        ticker: str,
        current_url: str,
        previous_url: str,
        focus_areas: list[str] | None = None,
    ) -> AnalysisResult:
        """
        Compare footnotes between two filings and produce a structured analysis.
        """
        # Step 1: Match footnotes by number and title
        current_map = {fn.number: fn for fn in current_footnotes}
        previous_map = {fn.number: fn for fn in previous_footnotes}

        all_numbers = sorted(set(current_map.keys()) | set(previous_map.keys()))

        # Step 2: Identify added and removed notes
        notes_added = []
        notes_removed = []
        matched_pairs: list[tuple[Footnote, Footnote]] = []

        for num in all_numbers:
            c = current_map.get(num)
            p = previous_map.get(num)

            if c and not p:
                notes_added.append(f"Note {c.number}: {c.title}")
            elif p and not c:
                notes_removed.append(f"Note {p.number}: {p.title}")
            elif c and p:
                matched_pairs.append((c, p))

        # Step 3: Map — Compare each matched pair with the LLM
        all_changes: list[Change] = []
        total_pairs = len(matched_pairs)

        for i, (current_note, previous_note) in enumerate(matched_pairs, 1):
            print(f"  📝 Analyzing Note {current_note.number}: {current_note.title} ({i}/{total_pairs})…")
            changes = self._compare_single_note(
                current_note, previous_note,
                current_label, previous_label,
                focus_areas,
            )
            all_changes.extend(changes)

        # Also flag added/removed notes as HIGH severity changes
        for added in notes_added:
            all_changes.append(Change(
                note_number=0,
                note_title=added,
                change_description=f"NEW NOTE added in the current filing: {added}",
                category="New Disclosure",
                severity="HIGH",
                current_text_excerpt="(entire note is new)",
                previous_text_excerpt="(not present in previous filing)",
                analyst_implication="A new disclosure may indicate a new risk, transaction, or accounting policy change.",
                recommended_action="Read the full new note to understand the disclosure.",
            ))

        for removed in notes_removed:
            all_changes.append(Change(
                note_number=0,
                note_title=removed,
                change_description=f"NOTE REMOVED from the current filing: {removed}",
                category="Removed Disclosure",
                severity="HIGH",
                current_text_excerpt="(not present in current filing)",
                previous_text_excerpt="(was present in previous filing)",
                analyst_implication="A removed disclosure may indicate a resolved matter, or the topic may have been consolidated elsewhere.",
                recommended_action="Verify whether the disclosure was moved, consolidated, or the underlying matter was resolved.",
            ))

        # Step 4: Reduce — Sort by severity and generate overall summary
        severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        all_changes.sort(key=lambda c: (severity_order.get(c.severity, 3), c.note_number))

        # Generate summary
        summary = self._generate_summary(
            all_changes, notes_added, notes_removed,
            company, current_label, previous_label, focus_areas,
        )

        return AnalysisResult(
            company=company,
            ticker=ticker,
            current_filing=current_label,
            previous_filing=previous_label,
            current_filing_url=current_url,
            previous_filing_url=previous_url,
            changes=all_changes,
            summary=summary,
            notes_added=notes_added,
            notes_removed=notes_removed,
            total_notes_compared=len(matched_pairs),
        )

    def _compare_single_note(
        self,
        current: Footnote,
        previous: Footnote,
        current_label: str,
        previous_label: str,
        focus_areas: list[str] | None,
    ) -> list[Change]:
        """
        Use Gemini to compare two versions of the same footnote and identify changes.
        """
        focus_instruction = ""
        if focus_areas:
            focus_instruction = (
                f"\n\nPay SPECIAL attention to changes related to: {', '.join(focus_areas)}. "
                f"These are the user's priority areas."
            )

        prompt = f"""You are a senior financial analyst reviewing SEC filing footnotes for 
material changes. Compare these two versions of the same footnote and identify ALL 
significant differences.

=== CURRENT FILING: {current_label} ===
Note {current.number}: {current.title}

{current.content[:30000]}

=== PREVIOUS FILING: {previous_label} ===
Note {previous.number}: {previous.title}

{previous.content[:30000]}

=== INSTRUCTIONS ===
Identify ALL significant changes between these two versions. A "change" includes:
- New language or disclosures added
- Language or disclosures that were removed
- Quantitative changes (different dollar amounts, percentages, dates)
- Accounting policy changes or updates
- Risk factor changes
- Reclassifications or re-categorizations
- Changes in estimates, assumptions, or judgments

For EACH change found, provide:
1. change_description: What specifically changed (be precise and quote relevant text)
2. category: One of [New Disclosure, Removed Disclosure, Language Change, Quantitative Change, Accounting Policy Change, Risk Factor Change, Reclassification, Estimate Revision]
3. severity: HIGH (material, could affect valuation or risk assessment), MEDIUM (notable but not immediately material), or LOW (minor wording or formatting)
4. current_text_excerpt: Brief quote from the CURRENT filing showing the change (max 200 chars)
5. previous_text_excerpt: Brief quote from the PREVIOUS filing showing what it was before (max 200 chars)
6. analyst_implication: Why this matters to a financial analyst
7. recommended_action: What the analyst should do about it

If the two versions are substantially identical with no meaningful changes, return an empty list.
Do NOT flag purely cosmetic differences (e.g., whitespace, formatting, punctuation-only changes).
{focus_instruction}"""

        # Define response schema for structured output
        change_schema = {
            "type": "OBJECT",
            "properties": {
                "changes": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "change_description": {"type": "STRING"},
                            "category": {"type": "STRING"},
                            "severity": {"type": "STRING"},
                            "current_text_excerpt": {"type": "STRING"},
                            "previous_text_excerpt": {"type": "STRING"},
                            "analyst_implication": {"type": "STRING"},
                            "recommended_action": {"type": "STRING"},
                        },
                        "required": [
                            "change_description", "category", "severity",
                            "current_text_excerpt", "previous_text_excerpt",
                            "analyst_implication", "recommended_action",
                        ],
                    },
                },
            },
            "required": ["changes"],
        }

        try:
            response = None
            for attempt in range(_MAX_RETRIES + 1):
                try:
                    response = self._client.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_json_schema=change_schema,
                            temperature=0.2,
                        ),
                    )
                    break  # success
                except Exception as api_err:
                    status = getattr(api_err, "status_code", None) or getattr(api_err, "code", 0)
                    if int(status) in (429, 503) and attempt < _MAX_RETRIES:
                        delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                        print(f"    \u26a0 Gemini API temporarily unavailable ({status}), retrying in {delay}s\u2026")
                        time.sleep(delay)
                        continue
                    raise

            data = json.loads(response.text)
            changes_data = data.get("changes", [])

            return [
                Change(
                    note_number=current.number,
                    note_title=current.title,
                    change_description=c.get("change_description", ""),
                    category=c.get("category", "Language Change"),
                    severity=c.get("severity", "MEDIUM").upper(),
                    current_text_excerpt=c.get("current_text_excerpt", "")[:250],
                    previous_text_excerpt=c.get("previous_text_excerpt", "")[:250],
                    analyst_implication=c.get("analyst_implication", ""),
                    recommended_action=c.get("recommended_action", ""),
                )
                for c in changes_data
            ]

        except Exception as e:
            print(f"  \u26a0 Error analyzing Note {current.number}: {e}")
            return [Change(
                note_number=current.number,
                note_title=current.title,
                change_description=f"Analysis error: {str(e)[:200]}",
                category="Error",
                severity="MEDIUM",
                current_text_excerpt="",
                previous_text_excerpt="",
                analyst_implication="Manual review required due to analysis error.",
                recommended_action="Review this footnote manually.",
            )]

    def _generate_summary(
        self,
        changes: list[Change],
        notes_added: list[str],
        notes_removed: list[str],
        company: str,
        current_label: str,
        previous_label: str,
        focus_areas: list[str] | None,
    ) -> str:
        """
        Generate a concise executive summary of all identified changes.
        """
        if not changes and not notes_added and not notes_removed:
            return (
                f"No significant changes were identified in the footnotes between "
                f"{company}'s {current_label} and {previous_label}. "
                f"The disclosures appear substantially unchanged."
            )

        # Build a structured summary of changes for the LLM
        changes_summary = []
        for c in changes:
            changes_summary.append(
                f"- [{c.severity}] Note {c.note_number} ({c.note_title}): "
                f"{c.change_description} [Category: {c.category}]"
            )

        focus_text = ""
        if focus_areas:
            focus_text = f" The analyst is particularly interested in: {', '.join(focus_areas)}."

        prompt = f"""Write a concise executive summary (3-5 paragraphs) of the following 
footnote changes for {company} between their {current_label} and {previous_label}.{focus_text}

CHANGES IDENTIFIED:
{chr(10).join(changes_summary)}

NOTES ADDED: {', '.join(notes_added) if notes_added else 'None'}
NOTES REMOVED: {', '.join(notes_removed) if notes_removed else 'None'}

HIGH severity count: {sum(1 for c in changes if c.severity == 'HIGH')}
MEDIUM severity count: {sum(1 for c in changes if c.severity == 'MEDIUM')}
LOW severity count: {sum(1 for c in changes if c.severity == 'LOW')}

Write for a professional financial analyst audience. Start with the most important 
findings. Focus on what requires immediate attention. Be specific about implications 
for valuation, risk, and financial reporting."""

        try:
            response = None
            for attempt in range(_MAX_RETRIES + 1):
                try:
                    response = self._client.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            temperature=0.3,
                        ),
                    )
                    break
                except Exception as api_err:
                    status = getattr(api_err, "status_code", None) or getattr(api_err, "code", 0)
                    if int(status) in (429, 503) and attempt < _MAX_RETRIES:
                        delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                        print(f"    \u26a0 Gemini API temporarily unavailable ({status}), retrying in {delay}s\u2026")
                        time.sleep(delay)
                        continue
                    raise
            return response.text.strip()
        except Exception as e:
            # Fallback: generate a basic summary
            high = sum(1 for c in changes if c.severity == "HIGH")
            med = sum(1 for c in changes if c.severity == "MEDIUM")
            low = sum(1 for c in changes if c.severity == "LOW")
            return (
                f"Analysis of {company}'s footnotes between {current_label} and "
                f"{previous_label} identified {len(changes)} change(s): "
                f"{high} high-severity, {med} medium-severity, {low} low-severity. "
                f"{'Notes added: ' + ', '.join(notes_added) + '. ' if notes_added else ''}"
                f"{'Notes removed: ' + ', '.join(notes_removed) + '. ' if notes_removed else ''}"
                f"Please review the detailed changes table for specifics."
            )
