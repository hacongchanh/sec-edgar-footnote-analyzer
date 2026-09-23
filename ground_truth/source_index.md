# Source Index: Approved Ground Truth & Reference Information

This index documents the approved sources of truth, operational policies, and evaluation rules that the SEC EDGAR Footnote Analysis Agent is authorized to trust.

---

### Source 1: SEC Official Registries & Filing Archives (External Ground Truth)
- **Name:** SEC EDGAR Public Data API & Submission Archives
- **Purpose:** Authoritative primary source for company identifiers (CIK), filing submission histories (Forms 10-K and 10-Q), and official financial statement footnote HTML disclosures.
- **Date/Version:** Current Official SEC EDGAR System Specifications (as updated continuously by the U.S. Securities and Exchange Commission).
- **Authority / Origin:** U.S. Federal Government Regulatory Body (`sec.gov`, `data.sec.gov`).
- **Why the Agent May Trust It:** It is the legally mandated disclosure repository under federal securities laws (Securities Act of 1933 / Exchange Act of 1934). Every filing is electronically signed, timestamped, and accession-numbered by the registrant under SEC penalty of perjury.
- **Location / Schema Reference:** `ground_truth/edgar_authoritative_sources.json`

---

### Source 2: Footnote Change Classification Taxonomy
- **Name:** Standard Financial Statement Change Taxonomy & Severity Rubric
- **Purpose:** Defines the closed set of valid change categories (e.g., Accounting Policy Change, Quantitative Change, Removed Disclosure) and sets deterministic criteria for assigning severity levels (`HIGH`, `MEDIUM`, `LOW`).
- **Date/Version:** Version 1.0 (September 2026)
- **Authority / Origin:** Curated financial analysis reference derived from standard forensic accounting principles (GAAP/IFRS disclosure guidelines).
- **Why the Agent May Trust It:** Establishes objective, vetted boundary conditions for what constitutes a "material change" vs. "routine update," eliminating arbitrary LLM guessing about severity.
- **Location / File:** `ground_truth/change_classification_rules.json`

---

### Source 3: Analyst Review & Escalation Policy
- **Name:** Human-in-the-Loop Review, Escalation, and Noise Exclusion Policy
- **Purpose:** Dictates mandatory human review triggers (e.g., restatements, going concern disclosures, debt covenant revisions), defines non-discretionary review rules, and explicitly lists noise categories to ignore (e.g., cosmetic whitespace, punctuation-only edits).
- **Date/Version:** Version 1.0 (September 2026)
- **Authority / Origin:** Approved financial analyst workflow ruleset.
- **Why the Agent May Trust It:** Protects human analysts from alert fatigue while guaranteeing that high-risk disclosure omissions or additions are never silently dismissed.
- **Location / File:** `ground_truth/analyst_review_policy.md`

---

### Source 4: Standard Note Taxonomy & Matching Rules
- **Name:** Financial Statement Footnote Structural Taxonomy & Matching Rules
- **Purpose:** Defines the standard canonical note categories across public company balance sheets and sets rules for matching corresponding footnotes between consecutive quarters and fiscal years.
- **Date/Version:** Version 1.0 (September 2026)
- **Authority / Origin:** US-GAAP Standard Note Structure (FASB ASC guidelines) & SEC XBRL reporting guidelines.
- **Why the Agent May Trust It:** Formulates predictable note alignments, allowing the agent to distinguish between renamed notes, consolidated notes, and truly newly added or retired footnotes.
- **Location / File:** `ground_truth/footnote_matching_rules.json`
