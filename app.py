"""
app.py — Streamlit web UI for the SEC EDGAR Footnote Analysis Agent.

Run with:
    streamlit run app.py
"""

import streamlit as st
import pandas as pd
from pathlib import Path

from config import validate_config, GEMINI_API_KEY, SEC_USER_AGENT, APP_ACCESS_CODE
from edgar_client import EdgarClient
from footnote_extractor import FootnoteExtractor
from query_parser import QueryParser
from analyzer import FootnoteAnalyzer, AnalysisResult
from output_formatter import OutputFormatter

# ── Page configuration ─────────────────────────────────────────────────────

st.set_page_config(
    page_title="SEC EDGAR Footnote Analyzer",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ─────────────────────────────────────────────────────────────

st.markdown("""
<style>
    .main-header {
        background: linear-gradient(135deg, #1e3a5f 0%, #0f172a 100%);
        color: white;
        padding: 1.5rem 2rem;
        border-radius: 12px;
        margin-bottom: 1.5rem;
    }
    .main-header h1 { color: white; margin: 0; font-size: 1.8rem; }
    .main-header p { color: #93c5fd; margin: 0.5rem 0 0 0; }
    .severity-high { background-color: #fee2e2; padding: 0.25rem 0.75rem; 
                     border-radius: 12px; color: #991b1b; font-weight: 600; }
    .severity-medium { background-color: #fef9c3; padding: 0.25rem 0.75rem;
                       border-radius: 12px; color: #854d0e; font-weight: 600; }
    .severity-low { background-color: #dcfce7; padding: 0.25rem 0.75rem;
                    border-radius: 12px; color: #166534; font-weight: 600; }
    .stat-box { text-align: center; padding: 1rem; background: white;
                border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
    .stat-number { font-size: 2rem; font-weight: bold; }
    .stat-label { color: #64748b; font-size: 0.85rem; }
</style>
""", unsafe_allow_html=True)

# ── Header ─────────────────────────────────────────────────────────────────

st.markdown("""
<div class="main-header">
    <h1>📊 SEC EDGAR Footnote Analyzer</h1>
    <p>AI-powered analysis of 10-K/10-Q footnote changes to help financial analysts spot material disclosures</p>
</div>
""", unsafe_allow_html=True)

# ── Sidebar ────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("⚙️ Configuration")

    # Access control (Step A8: Limit Access)
    if APP_ACCESS_CODE:
        entered_code = st.text_input(
            "🔒 Access Code",
            type="password",
            help="Enter the required access code to use this application",
        )
        if entered_code != APP_ACCESS_CODE:
            st.warning("Please enter the valid access code to unlock the agent.")
            st.stop()
        else:
            st.success("🔓 Access granted")
    
    # Check if API key is already in environment
    api_key = GEMINI_API_KEY
    user_agent = SEC_USER_AGENT
    
    if not api_key:
        api_key = st.text_input(
            "Gemini API Key",
            type="password",
            help="Get a free key at https://aistudio.google.com/apikey",
        )
    else:
        st.success("✅ Gemini API Key configured")

    if not user_agent:
        user_agent = st.text_input(
            "SEC User-Agent",
            placeholder="YourName your@email.com",
            help="Required by SEC: your name and email",
        )
    else:
        st.success("✅ SEC User-Agent configured")

    st.divider()
    st.header("📖 Example Queries")
    examples = [
        "Analyze Apple's latest 10-Q footnotes vs the previous quarter",
        "Compare Microsoft's two most recent 10-K footnote disclosures",
        "Show me changes in TSLA's latest 10-Q vs previous quarter, focus on debt and leases",
        "Analyze NVDA's latest 10-Q footnotes and highlight revenue recognition changes",
    ]
    for ex in examples:
        if st.button(f"📝 {ex[:50]}…", key=f"ex_{hash(ex)}", use_container_width=True):
            st.session_state["query_input"] = ex

# ── Main content ───────────────────────────────────────────────────────────

# Query input
query = st.text_area(
    "Enter your analysis query",
    value=st.session_state.get("query_input", ""),
    height=80,
    placeholder="e.g., Analyze Apple's latest 10-Q filing and compare footnotes to the previous quarter",
    key="query_box",
)

col1, col2 = st.columns([1, 5])
with col1:
    analyze_button = st.button("🔍 Analyze", type="primary", use_container_width=True)

# ── Analysis Pipeline ──────────────────────────────────────────────────────

if analyze_button and query.strip():
    # Validate configuration
    if not api_key:
        st.error("❌ Please enter your Gemini API Key in the sidebar.")
        st.stop()
    if not user_agent:
        st.error("❌ Please enter your SEC User-Agent in the sidebar.")
        st.stop()

    # Override config if provided via sidebar
    import config
    if not GEMINI_API_KEY:
        config.GEMINI_API_KEY = api_key
    if not SEC_USER_AGENT:
        config.SEC_USER_AGENT = user_agent

    progress_container = st.container()

    with progress_container:
        progress_bar = st.progress(0, text="Starting analysis…")

        try:
            # Step 1: Parse query
            progress_bar.progress(5, text="Step 1/6: Parsing your query…")
            parser = QueryParser()
            parsed = parser.parse(query)

            # Input validation control (Step 9: Check for missing information)
            if not parsed.is_valid:
                progress_bar.empty()
                st.warning(f"⚠️ **Incomplete Request:** {parsed.clarification_prompt}")
                if parsed.missing_fields:
                    st.info("**Missing required information:**\n- " + "\n- ".join(parsed.missing_fields))
                st.stop()

            with st.expander("📋 Parsed Query", expanded=False):
                st.json({
                    "company_name": parsed.company_name,
                    "ticker": parsed.ticker,
                    "form_type": parsed.form_type,
                    "current_period": parsed.filing_period_current,
                    "previous_period": parsed.filing_period_previous,
                    "focus_areas": parsed.focus_areas,
                    "comparison_type": parsed.comparison_type,
                })

            # Step 2: Resolve CIK
            progress_bar.progress(15, text="Step 2/6: Looking up company on SEC EDGAR…")
            client = EdgarClient(user_agent=user_agent)
            cik, ticker = client.resolve_identifier(parsed.company_name, parsed.ticker)
            st.info(f"📍 Resolved: **{parsed.company_name or ticker}** → CIK {cik}")

            # Step 3: Fetch filings
            progress_bar.progress(25, text=f"Step 3/6: Fetching {parsed.form_type} filing history…")
            filings = client.get_filings_list(cik, parsed.form_type)
            if len(filings) < 2:
                st.error(f"Only found {len(filings)} {parsed.form_type} filing(s). Need at least 2.")
                st.stop()

            current_filing, previous_filing = client.select_filing_pair(
                filings,
                current_hint=parsed.filing_period_current,
                previous_hint=parsed.filing_period_previous,
            )

            col_c, col_p = st.columns(2)
            with col_c:
                st.success(f"▶ Current: {current_filing.label}")
            with col_p:
                st.success(f"◀ Previous: {previous_filing.label}")

            # Step 4: Extract footnotes
            progress_bar.progress(40, text="Step 4/6: Extracting footnotes from both filings…")
            extractor = FootnoteExtractor()

            current_footnotes = extractor.extract_footnotes(client, current_filing)
            previous_footnotes = extractor.extract_footnotes(client, previous_filing)

            if not current_footnotes or not previous_footnotes:
                st.error("Could not extract footnotes from one or both filings.")
                st.stop()

            st.info(
                f"📝 Extracted **{len(current_footnotes)}** footnotes from current filing, "
                f"**{len(previous_footnotes)}** from previous filing"
            )

            # Step 5: Analyze
            progress_bar.progress(55, text="Step 5/6: Analyzing footnote changes with Gemini AI…")
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

            # Step 6: Display results
            progress_bar.progress(90, text="Step 6/6: Formatting results…")

            # Store result in session state
            st.session_state["result"] = result
            progress_bar.progress(100, text="✅ Analysis complete!")

        except Exception as e:
            st.error(f"❌ Error: {e}")
            st.stop()

# ── Display Results ────────────────────────────────────────────────────────

if "result" in st.session_state:
    result: AnalysisResult = st.session_state["result"]

    st.divider()
    st.header(f"📊 Results: {result.company} ({result.ticker})")

    # Stats cards
    high = sum(1 for c in result.changes if c.severity == "HIGH")
    med = sum(1 for c in result.changes if c.severity == "MEDIUM")
    low = sum(1 for c in result.changes if c.severity == "LOW")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🔴 High Severity", high)
    c2.metric("🟡 Medium Severity", med)
    c3.metric("🟢 Low Severity", low)
    c4.metric("Total Changes", len(result.changes))

    # Added/removed notes
    if result.notes_added:
        st.warning("**📌 Notes Added:** " + ", ".join(result.notes_added))
    if result.notes_removed:
        st.warning("**📌 Notes Removed:** " + ", ".join(result.notes_removed))

    # Changes table
    if result.changes:
        st.subheader("📋 Changes Table")

        # Build DataFrame
        df_data = []
        for c in result.changes:
            df_data.append({
                "Note": f"Note {c.note_number}: {c.note_title}",
                "Category": c.category,
                "Severity": c.severity,
                "Change Description": c.change_description,
                "Analyst Implication": c.analyst_implication,
                "Recommended Action": c.recommended_action,
            })

        df = pd.DataFrame(df_data)
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Severity": st.column_config.TextColumn(width="small"),
                "Category": st.column_config.TextColumn(width="medium"),
                "Change Description": st.column_config.TextColumn(width="large"),
            },
        )

        # Expandable details per change
        st.subheader("🔍 Detailed Change View")
        for i, c in enumerate(result.changes):
            emoji = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(c.severity, "⚪")
            with st.expander(
                f"{emoji} Note {c.note_number}: {c.note_title} — {c.category} ({c.severity})"
            ):
                st.markdown(f"**Change:** {c.change_description}")
                col_prev, col_curr = st.columns(2)
                with col_prev:
                    st.markdown("**◀ Previous Filing:**")
                    st.text(c.previous_text_excerpt or "(no excerpt)")
                with col_curr:
                    st.markdown("**▶ Current Filing:**")
                    st.text(c.current_text_excerpt or "(no excerpt)")
                st.markdown(f"**Implication:** {c.analyst_implication}")
                st.markdown(f"**Action:** {c.recommended_action}")

    # Executive Summary
    st.subheader("📋 Executive Summary")
    st.markdown(result.summary)

    # Source links
    st.subheader("🔗 Source Filings")
    st.markdown(f"- [Current Filing: {result.current_filing}]({result.current_filing_url})")
    st.markdown(f"- [Previous Filing: {result.previous_filing}]({result.previous_filing_url})")

    # Download buttons
    st.divider()
    formatter = OutputFormatter()

    col_dl1, col_dl2 = st.columns(2)
    with col_dl1:
        html_report = formatter.to_html(result)
        st.download_button(
            "📥 Download HTML Report",
            data=html_report,
            file_name=f"footnote_analysis_{result.ticker}.html",
            mime="text/html",
            use_container_width=True,
        )
    with col_dl2:
        md_report = formatter.to_markdown(result)
        st.download_button(
            "📥 Download Markdown Report",
            data=md_report,
            file_name=f"footnote_analysis_{result.ticker}.md",
            mime="text/markdown",
            use_container_width=True,
        )
