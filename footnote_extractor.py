"""
footnote_extractor.py — Extract individual footnotes from SEC 10-K/10-Q filings.

Uses a two-tier strategy:
  1. Primary: Parse FilingSummary.xml to find pre-isolated footnote HTML files
  2. Fallback: Parse the monolithic filing HTML with BeautifulSoup + regex
"""

import re
import xml.etree.ElementTree as ET
from typing import Optional

from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel

from edgar_client import EdgarClient, FilingMeta


# ── Data Models ────────────────────────────────────────────────────────────


class Footnote(BaseModel):
    """A single footnote / note from a financial filing."""
    number: int            # Note number (1, 2, 3…)
    title: str             # e.g., "Summary of Significant Accounting Policies"
    content: str           # Full cleaned text content
    raw_html: str          # Original HTML for reference
    source_url: str        # Direct URL to the note on SEC.gov


# ── Footnote Extractor ─────────────────────────────────────────────────────


class FootnoteExtractor:
    """
    Extracts individual footnotes from SEC filings.

    Primary approach uses FilingSummary.xml (available for iXBRL filings
    since ~2019), which indexes each note as a standalone HTML file.
    Falls back to full-document HTML parsing for older filings.
    """

    def extract_footnotes(
        self,
        edgar_client: EdgarClient,
        filing: FilingMeta,
    ) -> list[Footnote]:
        """
        Main entry point.  Tries FilingSummary.xml first, then HTML parsing.
        """
        footnotes = self._extract_via_filing_summary(edgar_client, filing)
        if footnotes:
            return footnotes

        # Fallback: download the full filing and parse it
        print("  ℹ FilingSummary.xml not available — falling back to HTML parsing…")
        html = edgar_client.download_filing_html(filing)
        return self._extract_via_html_parsing(html, filing)

    # ── Primary method: FilingSummary.xml ──────────────────────────────

    def _extract_via_filing_summary(
        self,
        edgar_client: EdgarClient,
        filing: FilingMeta,
    ) -> list[Footnote]:
        """
        Download FilingSummary.xml, find all reports categorised as "Notes",
        then download each individual note's HTML file.
        """
        xml_content = edgar_client.get_filing_summary_xml(filing)
        if xml_content is None:
            return []

        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError:
            return []

        # Find all <Report> elements; handle both namespaced and plain XML
        reports = root.findall(".//Report")
        if not reports:
            # Try with common namespace
            for ns in ["{http://www.sec.gov/viewer/filingtype}", ""]:
                reports = root.findall(f".//{ns}Report")
                if reports:
                    break

        footnotes: list[Footnote] = []
        note_counter = 0  # Sequential counter for notes without explicit numbers
        for report in reports:
            if not self._is_footnote_report(report):
                continue

            short_name_el = report.find("ShortName")
            html_file_el = report.find("HtmlFileName")

            if html_file_el is None or not html_file_el.text:
                continue

            short_name = short_name_el.text if short_name_el is not None and short_name_el.text else "Unnamed Note"
            html_filename = html_file_el.text.strip()

            # Download the individual note HTML
            try:
                note_html = edgar_client.download_note_html(filing, html_filename)
            except Exception:
                continue

            number, title = self._extract_note_number_and_title(short_name)
            note_counter += 1
            # If no number was found in the title, assign sequential number
            if number == 0:
                number = note_counter

            content = self._clean_note_html(note_html)
            source_url = f"{filing.base_url}{html_filename}"

            footnotes.append(Footnote(
                number=number,
                title=title,
                content=content,
                raw_html=note_html,
                source_url=source_url,
            ))

        # Sort by note number
        footnotes.sort(key=lambda fn: fn.number)
        return footnotes

    def _is_footnote_report(self, report: ET.Element) -> bool:
        """Check whether a <Report> element represents a footnote."""
        cat_el = report.find("MenuCategory")
        short_el = report.find("ShortName")

        if cat_el is not None and cat_el.text:
            if "Notes" in cat_el.text:
                return True

        if short_el is not None and short_el.text:
            text_lower = short_el.text.lower()
            if "note" in text_lower and any(
                kw in text_lower
                for kw in ["note ", "note:", "notes "]
            ):
                return True

        return False

    # ── Fallback method: HTML parsing ─────────────────────────────────

    def _extract_via_html_parsing(
        self,
        html: str,
        filing: FilingMeta,
    ) -> list[Footnote]:
        """
        Parse the full filing HTML to find and extract footnotes.
        Uses regex to locate the "Notes to Financial Statements" section
        and split it into individual notes.
        """
        soup = BeautifulSoup(html, "lxml")
        notes_section = self._find_notes_section(soup)
        if notes_section is None:
            return []

        return self._split_into_individual_notes(str(notes_section), filing)

    def _find_notes_section(self, soup: BeautifulSoup) -> Optional[Tag]:
        """
        Locate the 'Notes to (Condensed/Consolidated) Financial Statements'
        section in the filing HTML.
        """
        # Pattern matches various header formulations
        pattern = re.compile(
            r"notes?\s+to\s+(condensed\s+)?(consolidated\s+)?"
            r"(unaudited\s+)?financial\s+statements",
            re.IGNORECASE,
        )

        # Search for the header text
        header = soup.find(string=pattern)
        if header is None:
            return None

        # Walk up to find the containing structural element
        parent = header.find_parent(["div", "p", "h1", "h2", "h3", "h4", "span", "b", "strong"])
        if parent is None:
            parent = header.parent

        # Collect everything from this header until the next major section
        section_parts: list[str] = []
        next_section_pattern = re.compile(
            r"(item\s+\d|part\s+(i{1,3}|iv|v|[12345])[\.\s])",
            re.IGNORECASE,
        )

        for sibling in parent.find_next_siblings():
            text = sibling.get_text(strip=True)
            if next_section_pattern.match(text):
                break
            section_parts.append(str(sibling))

        if not section_parts:
            return parent

        combined = f"<div>{''.join(section_parts)}</div>"
        return BeautifulSoup(combined, "lxml").find("div")

    def _split_into_individual_notes(
        self,
        section_html: str,
        filing: FilingMeta,
    ) -> list[Footnote]:
        """Split a notes section into individual numbered notes."""
        soup = BeautifulSoup(section_html, "lxml")
        text_content = soup.get_text("\n", strip=True)

        # Pattern to match note headers: "Note 1 —", "1.", "NOTE 1:", etc.
        split_pattern = re.compile(
            r"(?:^|\n)\s*(?:note\s+)?(\d{1,2})\s*[\.\:\—\-–]\s*(.+?)(?=\n)",
            re.IGNORECASE,
        )

        matches = list(split_pattern.finditer(text_content))
        if not matches:
            # Return entire section as a single note
            return [Footnote(
                number=0,
                title="Notes to Financial Statements",
                content=self._clean_note_html(section_html),
                raw_html=section_html,
                source_url=filing.sec_url,
            )]

        footnotes: list[Footnote] = []
        for i, match in enumerate(matches):
            number = int(match.group(1))
            title = match.group(2).strip()
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text_content)
            content = text_content[start:end].strip()

            footnotes.append(Footnote(
                number=number,
                title=title,
                content=content,
                raw_html="",  # Not available for individual notes in fallback mode
                source_url=filing.sec_url,
            ))

        return footnotes

    # ── Cleaning utilities ─────────────────────────────────────────────

    @staticmethod
    def _clean_note_html(html: str) -> str:
        """
        Convert note HTML to clean readable text.
        Preserves table structure in a simple text format.
        Strips SEC document envelope and XBRL boilerplate.
        """
        # Strip SEC document envelope tags (outside the HTML)
        # The file may start with <DOCUMENT><TYPE>XML<SEQUENCE>...<TEXT>
        text_match = re.search(r"<TEXT>\s*(.*)", html, re.DOTALL | re.IGNORECASE)
        if text_match:
            html = text_match.group(1)
        # Also strip closing </TEXT></DOCUMENT>
        html = re.sub(r"</TEXT>\s*</DOCUMENT>\s*$", "", html, flags=re.IGNORECASE)

        soup = BeautifulSoup(html, "lxml")

        # Remove script, style, meta, and link elements
        for tag in soup.find_all(["script", "style", "meta", "link"]):
            tag.decompose()

        # Remove hidden elements (often XBRL metadata)
        for tag in soup.find_all(attrs={"style": re.compile(r"display\s*:\s*none", re.IGNORECASE)}):
            tag.decompose()

        # For XBRL viewer format: the actual note content is in <td class="text">
        # If we find these, extract just the meaningful prose
        text_cells = soup.find_all("td", class_="text")
        if text_cells:
            # Collect content from text cells that have substantial content
            prose_parts = []
            for cell in text_cells:
                cell_text = cell.get_text(" ", strip=True)
                # Skip near-empty cells (just whitespace entities like &#160;)
                if len(cell_text) > 20:
                    # Process inner tables within the cell
                    inner_html = str(cell)
                    inner_soup = BeautifulSoup(inner_html, "lxml")
                    for table in inner_soup.find_all("table"):
                        rows_list: list[str] = []
                        for tr in table.find_all("tr"):
                            cells_list = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
                            if any(cells_list):
                                rows_list.append(" | ".join(cells_list))
                        if rows_list:
                            table_text = "\n".join(rows_list)
                            table.replace_with(f"\n[TABLE]\n{table_text}\n[/TABLE]\n")
                        else:
                            table.decompose()
                    prose_parts.append(inner_soup.get_text("\n", strip=True))

            if prose_parts:
                text = "\n\n".join(prose_parts)
                # Normalize whitespace
                text = re.sub(r"\n{3,}", "\n\n", text)
                text = re.sub(r"[ \t]+", " ", text)
                return text.strip()

        # Fallback: process the full document (non-XBRL viewer format)
        for table in soup.find_all("table"):
            rows_list2: list[str] = []
            for tr in table.find_all("tr"):
                cells_list2 = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
                if any(cells_list2):
                    rows_list2.append(" | ".join(cells_list2))
            if rows_list2:
                table_text = "\n".join(rows_list2)
                table.replace_with(f"\n[TABLE]\n{table_text}\n[/TABLE]\n")
            else:
                table.decompose()

        text = soup.get_text("\n", strip=True)

        # Normalize whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)

        return text.strip()

    @staticmethod
    def _extract_note_number_and_title(short_name: str) -> tuple[int, str]:
        """
        Parse a FilingSummary.xml ShortName into (number, title).

        Examples:
            "Note 1 - Summary of Significant Accounting Policies" → (1, "Summary of…")
            "Revenue from Contracts with Customers" → (0, "Revenue from…")
        """
        # Try "Note N - Title" pattern
        match = re.match(
            r"(?:note\s+)?(\d{1,2})\s*[\-\—\–:\.]\s*(.+)",
            short_name.strip(),
            re.IGNORECASE,
        )
        if match:
            return int(match.group(1)), match.group(2).strip()

        # Try leading number: "1. Title"
        match = re.match(r"(\d{1,2})\.\s*(.+)", short_name.strip())
        if match:
            return int(match.group(1)), match.group(2).strip()

        # No number found — use 0 and full name as title
        return 0, short_name.strip()
