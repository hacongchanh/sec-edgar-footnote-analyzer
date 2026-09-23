# Analyst Review, Escalation, and Noise Exclusion Policy

## 1. Overview
This policy governs what changes the agent must highlight for human review, which changes warrant immediate escalation, and which cosmetic variations must be excluded to prevent alert fatigue.

---

## 2. Mandatory Escalation Rules (Immediate Human Review Required)
The agent must tag and immediately prioritize for human analyst inspection any footnote change meeting any of the following criteria:

1. **Litigation & Contingencies (ASC 450):** Any change in the probability language regarding pending litigation (e.g., transition between "remote", "reasonably possible", and "probable") or adjustments to legal loss reserves.
2. **Going Concern Disclosures (ASC 205-40):** Any new mention or amendment of substantial doubt about the entity's ability to continue as a going concern.
3. **Restatements & Prior Period Adjustments (ASC 250):** Any mention of corrections of errors in previously issued financial statements or retrospective revisions.
4. **Debt Covenants & Default Triggers (ASC 470):** Any amendment of debt covenants, cross-default provisions, or waivers granted by creditors.
5. **Related Party Transactions (ASC 850):** Any newly disclosed transaction involving directors, principal owners, or affiliated entities.
6. **Discontinued Operations / Asset Impairment (ASC 360 / ASC 350):** Any write-down of goodwill, intangibles, or reclassification of major divisions as held for sale.

---

## 3. Explicit Exclusion & Noise Filtering Rules
The agent must **NOT** generate change records for purely cosmetic or non-substantive alterations:

1. **Whitespace & Typography:** Trailing spaces, blank line changes, tab variations, bullet character modifications, font family tags.
2. **Punctuation-Only Edits:** Changing hyphen to em-dash, adding/removing terminal periods in table labels, quotation mark stylistic differences.
3. **Trivial Date Advances:** Standard rolling date advances that do not alter the underlying commitment (e.g., standard rolling quarterly table header dates) unless the period duration or substantive terms changed.
4. **SEC EDGAR Boilerplate:** Header envelopes (`<DOCUMENT>`, `<TYPE>`, `<SEQUENCE>`, `IDEA: XBRL DOCUMENT`), viewer script tags, and XML namespace definitions.

---

## 4. Human-in-the-Loop Review Contract
- **No Automated Execution:** The agent is purely an advisory, diagnostic copilot. It must never execute trades, draft regulatory filings, or file compliance declarations without human sign-off.
- **Verification Links:** Every flagged change must provide verifiable links to the original SEC accession document so the analyst can validate the context in the official record.
