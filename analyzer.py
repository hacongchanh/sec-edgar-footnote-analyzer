"""
analyzer.py — LLM-powered footnote comparison and analysis engine with robust harness controls.

Uses the Google Gemini API to perform deep comparison of footnotes between
two SEC filings, identifying and classifying significant changes.

Harness Controls Implemented:
  1. Dynamic Ground Truth Ingestion: Loads authoritative categories, severity rubric,
     and noise exclusion policies from ground_truth/ into system instructions.
  2. Semantic Footnote Alignment: Replaces fragile note-index matching with title-based
     semantic alignment and GAAP taxonomy topic mapping.
  3. Task State Checkpointing: Persists per-footnote diff results to disk (cache/checkpoints/)
     allowing interrupted jobs to resume without redundant API calls.
  4. Application Schema & Category Guard: Validates LLM outputs against 8 approved
     categories and deterministic severity rules.
  5. Mandatory Escalation Policy Engine: Evaluates deterministic business rules (ASC 450,
     ASC 250, ASC 205-40, ASC 470, etc.) to trigger human review with explicit justification.
"""

import json
import time
import re
import sys
import difflib
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional

# Ensure standard output can print Unicode characters on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


from google import genai
from google.genai import types
from pydantic import BaseModel

from config import GEMINI_API_KEY, GEMINI_MODEL, GROUND_TRUTH_DIR, CHECKPOINTS_DIR
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
    category: str            # Validated against 8 approved ground truth categories
    severity: str            # HIGH, MEDIUM, LOW
    current_text_excerpt: str
    previous_text_excerpt: str
    analyst_implication: str
    recommended_action: str
    match_rationale: Optional[str] = None  # How footnotes were aligned (e.g. semantic match)


class FootnoteAlignment(BaseModel):
    """Information regarding how a current footnote was paired with a previous footnote."""
    current_number: Optional[int]
    current_title: Optional[str]
    previous_number: Optional[int]
    previous_title: Optional[str]
    match_type: str          # "EXACT_TITLE", "SEMANTIC_TITLE", "GAAP_TOPIC", "NOTE_NUMBER", "ADDED", "REMOVED"
    match_score: float       # 0.0 to 1.0


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
    human_review_required: bool = False
    escalation_reasons: list[str] = []
    checkpoint_hits: int = 0
    alignment_details: list[FootnoteAlignment] = []
    action_warning: Optional[str] = None


# ── Ground Truth Policy & Rule Ingestion ───────────────────────────────────


APPROVED_CATEGORIES = [
    "Accounting Policy Change",
    "New Disclosure",
    "Removed Disclosure",
    "Risk Factor Change",
    "Estimate Revision",
    "Quantitative Change",
    "Reclassification",
    "Language Change",
]

APPROVED_SEVERITIES = ["HIGH", "MEDIUM", "LOW"]


def load_ground_truth_knowledge() -> dict:
    """
    Dynamically loads authoritative classification taxonomy, severity rubric,
    matching rules, and analyst review policy from ground_truth/.
    """
    data = {
        "categories": {},
        "severity_rubric": {},
        "taxonomy": [],
        "mandatory_escalation_rules": [],
        "noise_exclusion_rules": [],
    }

    # 1. Classification & Severity Rules
    class_rules_path = GROUND_TRUTH_DIR / "change_classification_rules.json"
    if class_rules_path.exists():
        try:
            with open(class_rules_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
                for cat in raw.get("categories", []):
                    data["categories"][cat["name"]] = cat
                data["severity_rubric"] = raw.get("severity_rubric", {})
        except Exception as e:
            print(f"  ⚠ Failed to load {class_rules_path}: {e}")

    # 2. Footnote Matching Rules & Taxonomy
    matching_rules_path = GROUND_TRUTH_DIR / "footnote_matching_rules.json"
    if matching_rules_path.exists():
        try:
            with open(matching_rules_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
                data["taxonomy"] = raw.get("standard_note_taxonomy", [])
        except Exception as e:
            print(f"  ⚠ Failed to load {matching_rules_path}: {e}")

    # 3. Analyst Review & Escalation Policy
    review_policy_path = GROUND_TRUTH_DIR / "analyst_review_policy.md"
    if review_policy_path.exists():
        try:
            content = review_policy_path.read_text(encoding="utf-8")
            # Parse mandatory escalation rules
            if "## 2. Mandatory Escalation Rules" in content:
                sec2 = content.split("## 2. Mandatory Escalation Rules")[1].split("## 3.")[0]
                data["mandatory_escalation_rules"] = [
                    line.strip() for line in sec2.strip().split("\n") if line.strip().startswith(("1.", "2.", "3.", "4.", "5.", "6."))
                ]
            # Parse noise exclusion rules
            if "## 3. Explicit Exclusion & Noise Filtering Rules" in content:
                sec3 = content.split("## 3. Explicit Exclusion & Noise Filtering Rules")[1].split("## 4.")[0]
                data["noise_exclusion_rules"] = [
                    line.strip() for line in sec3.strip().split("\n") if line.strip().startswith(("1.", "2.", "3.", "4."))
                ]
        except Exception as e:
            print(f"  ⚠ Failed to load {review_policy_path}: {e}")

    return data


# ── Title Normalization & Semantic Matching ────────────────────────────────


def normalize_title(title: str) -> str:
    """Normalize footnote titles for robust semantic comparison."""
    # Strip prefix like "Note 1 - ", "Note 1: ", "1. ", "NOTE 1 "
    t = re.sub(r"^(?:note\s+)?\d+[\.\:\-\—\–\s]+", "", title, flags=re.IGNORECASE)
    # Remove punctuation and non-alphanumeric characters
    t = re.sub(r"[^\w\s]", " ", t).lower()
    # Normalize whitespace
    t = re.sub(r"\s+", " ", t).strip()
    # Filter common stop words if string has multiple tokens
    stopwords = {"and", "the", "of", "to", "in", "for", "on", "with", "at", "by", "a", "an", "condensed", "consolidated", "interim"}
    tokens = [w for w in t.split() if w not in stopwords]
    return " ".join(tokens) if tokens else t


def get_gaap_topic(title: str, taxonomy: list[dict]) -> Optional[str]:
    """Map a footnote title to a standard GAAP topic (e.g. ASC 842 Leases)."""
    norm = normalize_title(title)
    norm_words = set(norm.split())

    for item in taxonomy:
        topic_title = normalize_title(item.get("title", ""))
        topic_words = set(topic_title.split())
        # If strong word overlap with standard topic
        overlap = norm_words & topic_words
        if len(overlap) >= 2 or (len(topic_words) <= 2 and len(overlap) >= 1):
            return item.get("topic")
    return None


def align_footnotes(
    current_footnotes: list[Footnote],
    previous_footnotes: list[Footnote],
    taxonomy: list[dict],
) -> tuple[list[tuple[Footnote, Footnote, str]], list[Footnote], list[Footnote], list[FootnoteAlignment]]:
    """
    Semantic Footnote Alignment Engine.

    Pairs footnotes across comparison periods according to ground truth priority rules:
      1. Exact Report Title match (normalized)
      2. High-confidence semantic title match (SequenceMatcher >= 0.70)
      3. GAAP standard accounting topic match (ASC 842, ASC 606, ASC 450, etc.)
      4. Explicit Note Number match when titles are semantically congruent (score >= 0.35)
      5. Unmatched current notes classified as 'New Disclosure' (added)
      6. Unmatched previous notes classified as 'Removed Disclosure' (removed)
    """
    matched_pairs: list[tuple[Footnote, Footnote, str]] = []
    alignments: list[FootnoteAlignment] = []
    unmatched_current = list(current_footnotes)
    unmatched_previous = list(previous_footnotes)

    # Pre-calculate normalized titles and GAAP topics
    current_meta = {
        fn.source_url: {
            "norm": normalize_title(fn.title),
            "gaap": get_gaap_topic(fn.title, taxonomy),
        }
        for fn in current_footnotes
    }
    previous_meta = {
        fn.source_url: {
            "norm": normalize_title(fn.title),
            "gaap": get_gaap_topic(fn.title, taxonomy),
        }
        for fn in previous_footnotes
    }

    # Pass 1: Exact Normalized Title Match
    for c in list(unmatched_current):
        norm_c = current_meta[c.source_url]["norm"]
        if not norm_c:
            continue
        for p in list(unmatched_previous):
            norm_p = previous_meta[p.source_url]["norm"]
            if norm_c == norm_p:
                matched_pairs.append((c, p, f"Exact Title Match ('{c.title}')"))
                alignments.append(FootnoteAlignment(
                    current_number=c.number,
                    current_title=c.title,
                    previous_number=p.number,
                    previous_title=p.title,
                    match_type="EXACT_TITLE",
                    match_score=1.0,
                ))
                unmatched_current.remove(c)
                unmatched_previous.remove(p)
                break

    # Pass 2: High-Confidence Semantic Title Match (SequenceMatcher >= 0.70)
    for c in list(unmatched_current):
        norm_c = current_meta[c.source_url]["norm"]
        if not norm_c:
            continue
        best_match = None
        best_score = 0.0

        for p in unmatched_previous:
            norm_p = previous_meta[p.source_url]["norm"]
            if not norm_p:
                continue
            score = difflib.SequenceMatcher(None, norm_c, norm_p).ratio()
            if score > best_score and score >= 0.70:
                best_score = score
                best_match = p

        if best_match:
            matched_pairs.append((c, best_match, f"Semantic Title Match (score: {best_score:.2f})"))
            alignments.append(FootnoteAlignment(
                current_number=c.number,
                current_title=c.title,
                previous_number=best_match.number,
                previous_title=best_match.title,
                match_type="SEMANTIC_TITLE",
                match_score=best_score,
            ))
            unmatched_current.remove(c)
            unmatched_previous.remove(best_match)

    # Pass 3: GAAP Accounting Topic Match (e.g. both map to ASC 842 Leases)
    for c in list(unmatched_current):
        gaap_c = current_meta[c.source_url]["gaap"]
        if not gaap_c:
            continue
        for p in list(unmatched_previous):
            gaap_p = previous_meta[p.source_url]["gaap"]
            if gaap_c and gaap_c == gaap_p:
                matched_pairs.append((c, p, f"GAAP Topic Match ({gaap_c})"))
                alignments.append(FootnoteAlignment(
                    current_number=c.number,
                    current_title=c.title,
                    previous_number=p.number,
                    previous_title=p.title,
                    match_type="GAAP_TOPIC",
                    match_score=0.85,
                ))
                unmatched_current.remove(c)
                unmatched_previous.remove(p)
                break

    # Pass 4: Note Number Match with Compatible Title (similarity >= 0.35)
    for c in list(unmatched_current):
        for p in list(unmatched_previous):
            if c.number == p.number and c.number > 0:
                norm_c = current_meta[c.source_url]["norm"]
                norm_p = previous_meta[p.source_url]["norm"]
                score = difflib.SequenceMatcher(None, norm_c, norm_p).ratio()
                if score >= 0.35:
                    matched_pairs.append((c, p, f"Note Number Match (Note {c.number}, score: {score:.2f})"))
                    alignments.append(FootnoteAlignment(
                        current_number=c.number,
                        current_title=c.title,
                        previous_number=p.number,
                        previous_title=p.title,
                        match_type="NOTE_NUMBER",
                        match_score=score,
                    ))
                    unmatched_current.remove(c)
                    unmatched_previous.remove(p)
                    break

    # Pass 5: Unmatched become Added / Removed
    for c in unmatched_current:
        alignments.append(FootnoteAlignment(
            current_number=c.number,
            current_title=c.title,
            previous_number=None,
            previous_title=None,
            match_type="ADDED",
            match_score=0.0,
        ))

    for p in unmatched_previous:
        alignments.append(FootnoteAlignment(
            current_number=None,
            current_title=None,
            previous_number=p.number,
            previous_title=p.title,
            match_type="REMOVED",
            match_score=0.0,
        ))

    # Sort matched pairs primarily by current note number, then previous note number
    matched_pairs.sort(key=lambda pair: (pair[0].number if pair[0].number > 0 else 999, pair[1].number))

    return matched_pairs, unmatched_current, unmatched_previous, alignments


# ── Task State & Checkpoint Cache Manager ──────────────────────────────────


class TaskCheckpointManager:
    """
    Persists per-footnote analysis progress to disk.
    Allows interrupted analyses to resume without losing work
    and avoids redundant LLM API calls on repeated queries.
    """

    def __init__(self, checkpoints_dir: Path = CHECKPOINTS_DIR):
        self.dir = checkpoints_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def _get_key(self, ticker: str, current_label: str, previous_label: str) -> str:
        raw_id = f"{ticker}_{current_label}_{previous_label}".encode("utf-8")
        clean_name = re.sub(r"[^\w\-]", "_", f"{ticker}_{current_label}_{previous_label}")[:40]
        hash_suffix = hashlib.md5(raw_id).hexdigest()[:8]
        return f"{clean_name}_{hash_suffix}"

    def load_checkpoint(self, ticker: str, current_label: str, previous_label: str) -> dict:
        key = self._get_key(ticker, current_label, previous_label)
        file_path = self.dir / f"{key}.json"
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"  ⚠ Failed to read checkpoint {file_path}: {e}")
        return {
            "ticker": ticker,
            "current_filing": current_label,
            "previous_filing": previous_label,
            "completed_notes": {},
            "summary": None,
            "created_at": datetime.now().isoformat(),
        }

    def save_note_diff(
        self,
        ticker: str,
        current_label: str,
        previous_label: str,
        pair_key: str,
        changes: list[dict],
    ) -> None:
        state = self.load_checkpoint(ticker, current_label, previous_label)
        state["completed_notes"][pair_key] = changes
        state["last_updated"] = datetime.now().isoformat()

        key = self._get_key(ticker, current_label, previous_label)
        file_path = self.dir / f"{key}.json"
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"  ⚠ Failed to write checkpoint {file_path}: {e}")

    def get_note_diff(
        self,
        ticker: str,
        current_label: str,
        previous_label: str,
        pair_key: str,
    ) -> Optional[list[dict]]:
        state = self.load_checkpoint(ticker, current_label, previous_label)
        return state.get("completed_notes", {}).get(pair_key)


# ── Application Schema & Category Guard ────────────────────────────────────


def normalize_category(cat: str) -> str:
    """Ensure category strictly conforms to the 8 approved ground truth categories."""
    if not cat:
        return "Language Change"

    cat_clean = cat.strip()
    for approved in APPROVED_CATEGORIES:
        if cat_clean.lower() == approved.lower():
            return approved

    # Fuzzy match to closest approved category
    matches = difflib.get_close_matches(cat_clean, APPROVED_CATEGORIES, n=1, cutoff=0.6)
    if matches:
        return matches[0]

    # Keyword heuristics
    lower = cat_clean.lower()
    if "policy" in lower or "accounting" in lower:
        return "Accounting Policy Change"
    if "new" in lower or "added" in lower:
        return "New Disclosure"
    if "remove" in lower or "delet" in lower or "omit" in lower:
        return "Removed Disclosure"
    if "risk" in lower or "conting" in lower or "litig" in lower:
        return "Risk Factor Change"
    if "estimat" in lower or "fair value" in lower or "judgment" in lower:
        return "Estimate Revision"
    if "quant" in lower or "number" in lower or "amount" in lower or "dollar" in lower:
        return "Quantitative Change"
    if "reclass" in lower or "shift" in lower:
        return "Reclassification"

    return "Language Change"


def normalize_severity(sev: str) -> str:
    """Ensure severity strictly conforms to HIGH, MEDIUM, LOW."""
    if not sev:
        return "MEDIUM"
    clean = sev.strip().upper()
    if clean in APPROVED_SEVERITIES:
        return clean
    if "HIGH" in clean or "CRIT" in clean or "SEVER" in clean:
        return "HIGH"
    if "LOW" in clean or "MINOR" in clean:
        return "LOW"
    return "MEDIUM"


# ── Mandatory Escalation Rule Engine ──────────────────────────────────────


def evaluate_escalation_rules(
    changes: list[Change],
    notes_added: list[str],
    notes_removed: list[str],
) -> tuple[bool, list[str]]:
    """
    Deterministic Application-Level Evaluation of Mandatory Escalation Rules.

    Evaluates whether human review is required according to ground_truth/analyst_review_policy.md:
      1. Litigation & Contingencies (ASC 450)
      2. Going Concern Disclosures (ASC 205-40)
      3. Restatements & Prior Period Adjustments (ASC 250)
      4. Debt Covenants & Default Triggers (ASC 470)
      5. Related Party Transactions (ASC 850)
      6. Discontinued Operations / Asset Impairment (ASC 360 / ASC 350)
      7. Any change evaluated as HIGH severity
      8. Any Accounting Policy Change or New/Removed Disclosure
    """
    reasons: list[str] = []

    # 1. Count high severity changes
    high_changes = [c for c in changes if c.severity == "HIGH"]
    if high_changes:
        reasons.append(f"{len(high_changes)} High-Severity disclosure change(s) detected requiring immediate review.")

    # 2. Check for Accounting Policy Changes
    policy_changes = [c for c in changes if c.category == "Accounting Policy Change"]
    if policy_changes:
        reasons.append(f"Accounting Policy Changes detected in Note(s): {', '.join(str(c.note_number) for c in policy_changes)}.")

    # 3. Check for specific ASC policy escalation triggers
    escalation_keywords = [
        ("ASC 450 (Litigation & Contingencies)", ["litigat", "contingenc", "loss reserve", "subpoena", "investigation", "lawsuit"]),
        ("ASC 205-40 (Going Concern)", ["going concern", "substantial doubt", "ability to continue"]),
        ("ASC 250 (Restatements & Error Corrections)", ["restat", "prior period adjustment", "accounting error", "retrospective revision"]),
        ("ASC 470 (Debt Covenants & Default Triggers)", ["covenant", "default trigger", "cross-default", "creditor waiver"]),
        ("ASC 850 (Related Party Transactions)", ["related party", "affiliated entity", "principal owner"]),
        ("ASC 360/350 (Asset Impairment & Held for Sale)", ["goodwill impairment", "asset impairment", "held for sale", "write-down"]),
    ]

    for label, keywords in escalation_keywords:
        matching_notes = []
        for c in changes:
            text_to_check = f"{c.note_title} {c.change_description} {c.current_text_excerpt} {c.analyst_implication}".lower()
            if any(kw in text_to_check for kw in keywords):
                matching_notes.append(f"Note {c.note_number}")
        if matching_notes:
            unique_notes = sorted(set(matching_notes))
            reasons.append(f"Mandatory Escalation under {label}: Detected in {', '.join(unique_notes)}.")

    # 4. Check for New or Removed Disclosures
    if notes_added:
        reasons.append(f"{len(notes_added)} newly added footnote disclosure(s) detected.")
    if notes_removed:
        reasons.append(f"{len(notes_removed)} removed footnote disclosure(s) detected.")

    human_review_required = len(reasons) > 0
    return human_review_required, reasons


# ── Analyzer Engine ────────────────────────────────────────────────────────


class FootnoteAnalyzer:
    """
    Compare footnotes between two filings using the Google Gemini API,
    governed by explicit application harness controls.
    """

    def __init__(self):
        self._client = genai.Client(api_key=GEMINI_API_KEY)
        self.ground_truth = load_ground_truth_knowledge()
        self.checkpoint_manager = TaskCheckpointManager()

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
        use_cache: bool = True,
        action_warning: Optional[str] = None,
    ) -> AnalysisResult:
        """
        Compare footnotes between two filings with semantic alignment,
        dynamic ground truth guidance, and task state checkpointing.
        """
        # Step 1: Semantic Footnote Alignment
        taxonomy = self.ground_truth.get("taxonomy", [])
        matched_pairs, unmatched_curr, unmatched_prev, alignments = align_footnotes(
            current_footnotes, previous_footnotes, taxonomy
        )

        notes_added = [f"Note {c.number}: {c.title}" for c in unmatched_curr]
        notes_removed = [f"Note {p.number}: {p.title}" for p in unmatched_prev]

        # Step 2: Compare matched pairs with LLM (with Task State Checkpointing)
        all_changes: list[Change] = []
        total_pairs = len(matched_pairs)
        checkpoint_hits = 0

        for i, (current_note, previous_note, match_rationale) in enumerate(matched_pairs, 1):
            pair_key = f"c{current_note.number}_p{previous_note.number}_{normalize_title(current_note.title)[:20]}"

            # Check Task State Checkpoint Cache
            cached_diff = None
            if use_cache:
                cached_diff = self.checkpoint_manager.get_note_diff(
                    ticker, current_label, previous_label, pair_key
                )

            if cached_diff is not None:
                checkpoint_hits += 1
                print(f"  ⚡ [State Cache Hit] Note {current_note.number} ({current_note.title}) loaded from checkpoint ({i}/{total_pairs})")
                for c_dict in cached_diff:
                    all_changes.append(Change(**c_dict))
            else:
                print(f"  📝 Analyzing Note {current_note.number} vs Prev Note {previous_note.number}: {current_note.title} ({i}/{total_pairs})…")
                changes = self._compare_single_note(
                    current_note, previous_note,
                    current_label, previous_label,
                    focus_areas, match_rationale,
                )
                all_changes.extend(changes)

                # Save to checkpoint store immediately
                if use_cache:
                    self.checkpoint_manager.save_note_diff(
                        ticker, current_label, previous_label, pair_key,
                        [c.model_dump() for c in changes],
                    )

        # Step 3: Flag added and removed notes as high severity changes
        for added_fn in unmatched_curr:
            all_changes.append(Change(
                note_number=added_fn.number,
                note_title=added_fn.title,
                change_description=f"NEW NOTE added in current filing: Note {added_fn.number} - {added_fn.title}",
                category="New Disclosure",
                severity="HIGH",
                current_text_excerpt=added_fn.content[:200] if added_fn.content else "(entire note is new)",
                previous_text_excerpt="(not present in previous filing)",
                analyst_implication="A new disclosure signals emerging business lines, new debt tranches, accounting policy adoption, or novel legal risks.",
                recommended_action="Conduct full review of this new footnote disclosure.",
                match_rationale="Semantic Alignment: Unmatched in prior period",
            ))

        for removed_fn in unmatched_prev:
            all_changes.append(Change(
                note_number=removed_fn.number,
                note_title=removed_fn.title,
                change_description=f"NOTE REMOVED from current filing: Note {removed_fn.number} - {removed_fn.title}",
                category="Removed Disclosure",
                severity="HIGH",
                current_text_excerpt="(not present in current filing)",
                previous_text_excerpt=removed_fn.content[:200] if removed_fn.content else "(was present in previous filing)",
                analyst_implication="A removed disclosure may indicate resolved legal disputes, debt payoff, or potential omission of adverse conditions.",
                recommended_action="Verify whether disclosure was consolidated elsewhere or underlying matter was fully resolved.",
                match_rationale="Semantic Alignment: Absent in current period",
            ))

        # Step 4: Sort by severity and note number
        severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        all_changes.sort(key=lambda c: (severity_order.get(c.severity, 3), c.note_number))

        # Step 5: Evaluate Application-Level Mandatory Escalation Rules
        human_review_required, escalation_reasons = evaluate_escalation_rules(
            all_changes, notes_added, notes_removed
        )

        # Step 6: Generate concise executive summary
        summary = self._generate_summary(
            all_changes, notes_added, notes_removed,
            company, current_label, previous_label, focus_areas,
            human_review_required, escalation_reasons,
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
            human_review_required=human_review_required,
            escalation_reasons=escalation_reasons,
            checkpoint_hits=checkpoint_hits,
            alignment_details=alignments,
            action_warning=action_warning,
        )

    def _build_system_instruction(self) -> str:
        """
        Dynamically constructs stable system instructions from ground truth knowledge.
        Separates stable instructions from task-specific variable context.
        """
        categories_desc = []
        for cat_name in APPROVED_CATEGORIES:
            cat_info = self.ground_truth["categories"].get(cat_name, {})
            definition = cat_info.get("definition", "")
            severity = cat_info.get("default_severity", "MEDIUM")
            categories_desc.append(f"- **{cat_name}** (Default Severity: {severity}): {definition}")

        categories_text = "\n".join(categories_desc)

        noise_text = "\n".join(self.ground_truth.get("noise_exclusion_rules", [
            "1. Whitespace & Typography: Trailing spaces, blank line changes, tab variations, bullet styles.",
            "2. Punctuation-Only Edits: Changing hyphen to em-dash, period adjustments in table labels.",
            "3. Trivial Date Advances: Standard rolling quarterly calendar table header dates without term changes.",
            "4. SEC EDGAR Boilerplate: Header envelopes, XML namespace tags, viewer script tags.",
        ]))

        escalation_text = "\n".join(self.ground_truth.get("mandatory_escalation_rules", [
            "1. Litigation & Contingencies (ASC 450)",
            "2. Going Concern Disclosures (ASC 205-40)",
            "3. Restatements & Prior Period Adjustments (ASC 250)",
            "4. Debt Covenants & Default Triggers (ASC 470)",
            "5. Related Party Transactions (ASC 850)",
            "6. Discontinued Operations / Asset Impairment (ASC 360 / ASC 350)",
        ]))

        return f"""You are a senior financial analyst and SEC EDGAR footnote examination agent.
Your mission is to compare two versions of an SEC financial footnote, identify material differences,
and classify each change strictly according to authoritative ground truth accounting rules.

=== APPROVED CHANGE CATEGORIES ===
You must classify EVERY identified change into exactly ONE of the following 8 approved categories:
{categories_text}

=== SEVERITY ASSIGNMENT RUBRIC ===
- HIGH: Material disclosures capable of altering valuation, leverage ratios, risk profiles, or compliance standing.
- MEDIUM: Notable operational, structural, or estimate adjustments that do not immediately threaten financial solvency.
- LOW: Minor language, technical, or standard recurring updates.

=== MANDATORY ESCALATION TOPICS ===
Changes touching the following topics MUST be assigned HIGH severity:
{escalation_text}

=== EXCLUSION & NOISE FILTERING RULES ===
Do NOT flag purely cosmetic differences. Return an empty list if the only changes are:
{noise_text}

=== OUTPUT FORMAT REQUIREMENTS ===
Return a structured JSON object containing a "changes" array.
Each change item must provide:
- change_description: Specific, clear explanation quoting relevant text.
- category: Exactly one of the 8 approved categories above.
- severity: HIGH, MEDIUM, or LOW.
- current_text_excerpt: Verbatim quote from CURRENT filing (max 200 chars).
- previous_text_excerpt: Verbatim quote from PREVIOUS filing (max 200 chars).
- analyst_implication: Concrete implications for financial modeling, valuation, or risk.
- recommended_action: Actionable recommendation for the financial analyst."""

    def _compare_single_note(
        self,
        current: Footnote,
        previous: Footnote,
        current_label: str,
        previous_label: str,
        focus_areas: list[str] | None,
        match_rationale: str,
    ) -> list[Change]:
        """
        Compare two aligned footnotes using Gemini with strict schema enforcement.
        """
        system_instruction = self._build_system_instruction()

        focus_instruction = ""
        if focus_areas:
            focus_instruction = (
                f"\n\n[PRIORITY FOCUS AREAS]: The user has highlighted priority areas: "
                f"{', '.join(focus_areas)}. Pay rigorous attention to any changes related to these topics."
            )

        task_content = f"""Compare the following two aligned footnote disclosures:

ALIGNMENT RATIONALE: {match_rationale}

=== CURRENT FILING: {current_label} ===
Note {current.number}: {current.title}

{current.content[:30000]}

=== PREVIOUS FILING: {previous_label} ===
Note {previous.number}: {previous.title}

{previous.content[:30000]}
{focus_instruction}"""

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
                        contents=task_content,
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            response_mime_type="application/json",
                            response_json_schema=change_schema,
                            temperature=0.2,
                        ),
                    )
                    break
                except Exception as api_err:
                    status = getattr(api_err, "status_code", None) or getattr(api_err, "code", 0)
                    if int(status) in (429, 503) and attempt < _MAX_RETRIES:
                        delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                        print(f"    ⚠ Gemini API temporarily unavailable ({status}), retrying in {delay}s…")
                        time.sleep(delay)
                        continue
                    raise

            data = json.loads(response.text)
            changes_data = data.get("changes", [])

            validated_changes: list[Change] = []
            for c in changes_data:
                # Enforce Application Category & Severity Guard
                validated_category = normalize_category(c.get("category", "Language Change"))
                validated_severity = normalize_severity(c.get("severity", "MEDIUM"))

                validated_changes.append(Change(
                    note_number=current.number,
                    note_title=current.title,
                    change_description=c.get("change_description", ""),
                    category=validated_category,
                    severity=validated_severity,
                    current_text_excerpt=c.get("current_text_excerpt", "")[:250],
                    previous_text_excerpt=c.get("previous_text_excerpt", "")[:250],
                    analyst_implication=c.get("analyst_implication", ""),
                    recommended_action=c.get("recommended_action", ""),
                    match_rationale=match_rationale,
                ))

            return validated_changes

        except Exception as e:
            print(f"  ⚠ Error analyzing Note {current.number}: {e}")
            return [Change(
                note_number=current.number,
                note_title=current.title,
                change_description=f"Analysis error: {str(e)[:200]}",
                category="Estimate Revision",
                severity="MEDIUM",
                current_text_excerpt="",
                previous_text_excerpt="",
                analyst_implication="Manual review required due to analysis error.",
                recommended_action="Review this footnote manually in the SEC filing.",
                match_rationale=match_rationale,
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
        human_review_required: bool,
        escalation_reasons: list[str],
    ) -> str:
        """
        Generate a concise executive summary synthesized from the verified changes.
        """
        if not changes and not notes_added and not notes_removed:
            return (
                f"No significant changes were identified in the footnotes between "
                f"{company}'s {current_label} and {previous_label}. "
                f"The disclosures appear substantially unchanged."
            )

        changes_summary = []
        for c in changes:
            changes_summary.append(
                f"- [{c.severity}] Note {c.note_number} ({c.note_title}): "
                f"{c.change_description} [Category: {c.category}]"
            )

        focus_text = ""
        if focus_areas:
            focus_text = f" The analyst prioritised these focus areas: {', '.join(focus_areas)}."

        escalation_status_text = (
            f"HUMAN REVIEW REQUIRED: YES\nTrigger Reasons:\n- " + "\n- ".join(escalation_reasons)
            if human_review_required else "HUMAN REVIEW REQUIRED: NO (all changes within standard parameters)"
        )

        prompt = f"""Write a professional executive summary (3-5 concise paragraphs) synthesizing the 
footnote changes for {company} between {current_label} and {previous_label}.{focus_text}

AUDIT STATUS:
{escalation_status_text}

CHANGES IDENTIFIED:
{chr(10).join(changes_summary)}

NOTES ADDED: {', '.join(notes_added) if notes_added else 'None'}
NOTES REMOVED: {', '.join(notes_removed) if notes_removed else 'None'}

HIGH severity count: {sum(1 for c in changes if c.severity == 'HIGH')}
MEDIUM severity count: {sum(1 for c in changes if c.severity == 'MEDIUM')}
LOW severity count: {sum(1 for c in changes if c.severity == 'LOW')}

Write for a professional financial analyst audience. Highlight material findings first, 
address the human review escalation status, and outline implications for valuation and risk."""

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
                        time.sleep(delay)
                        continue
            clean_summary = response.text.strip()
            # Escape unescaped dollar signs so Streamlit KaTeX doesn't break
            return re.sub(r'(?<!\\)\$', r'\$', clean_summary)

        except Exception as e:
            high = sum(1 for c in changes if c.severity == "HIGH")
            med = sum(1 for c in changes if c.severity == "MEDIUM")
            low = sum(1 for c in changes if c.severity == "LOW")
            return (
                f"Analysis of {company}'s footnotes between {current_label} and "
                f"{previous_label} identified {len(changes)} change(s): "
                f"{high} high-severity, {med} medium-severity, {low} low-severity. "
                f"{'Notes added: ' + ', '.join(notes_added) + '. ' if notes_added else ''}"
                f"{'Notes removed: ' + ', '.join(notes_removed) + '. ' if notes_removed else ''}"
                f"Human Review Required: {'YES' if human_review_required else 'NO'}. "
                f"Please review the detailed findings table for specifics."
            )
