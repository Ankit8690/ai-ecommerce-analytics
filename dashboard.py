"""
E-Commerce Business Intelligence & Analytics Dashboard.
Consumes FastAPI Backend API (http://127.0.0.1:8000/api/*).

Sidebar layout groups AI features into separate pages so no single page
becomes crowded:
  🤖 AI Business Analyst   — natural-language chat over analytics
  🔍 Question Library      — searchable 100-question catalog
  💻 SQL Query             — direct read-only SQL editor
  📚 Knowledge Base        — RAG grounded knowledge answers
  💡 Recommendations       — evidence-grounded decision support
Plus the existing analytics tabs (Executive Overview, Sales, Products, …).
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
from sqlalchemy import text as sa_text

from ai.sql_validator import validate_sql, check_known_tables, KNOWN_TABLES
from api.database import readonly_engine

# --- Phase 7 RAG (lazy so a missing index doesn't break the app) ---
try:
    from rag.retriever import Retriever
    from rag.synthesizer import answer_from_context
    _RAG_OK = True
except Exception as _rag_err:  # pragma: no cover
    _RAG_OK = False
    _RAG_ERR = str(_rag_err)

# --- Phase 8 Decision Support ---
try:
    from ai.decision_support import (
        generate_recommendation, classify_question, RECOMMENDATION_CATEGORIES,
    )
    _DS_OK = True
except Exception as _ds_err:  # pragma: no cover
    _DS_OK = False
    _DS_ERR = str(_ds_err)


def _normalize_api_url(raw: str) -> str:
    """Accept either 'host', 'host:port', or a full URL, and produce a
    scheme-qualified base URL. Public deployments (Render/Railway/Fly) expose
    services on HTTPS 443, not the container's internal port, so we strip a
    stray ':8000' when the host looks like a managed platform hostname."""
    v = (raw or "").strip().rstrip("/")
    if not v:
        return "http://127.0.0.1:8000"
    if "://" not in v:
        # Managed hosts always speak HTTPS; localhost stays HTTP.
        is_local = v.startswith(("localhost", "127.", "api:"))
        v = f"{'http' if is_local else 'https'}://{v}"
    # Strip an internal container port that leaked into the value (common
    # Render footgun — services listen on $PORT internally but are reached
    # externally on 443).
    if v.startswith("https://") and v.endswith(":8000"):
        v = v[: -len(":8000")]
    return v


API_BASE_URL = _normalize_api_url(os.environ.get("DASHBOARD_API_URL", ""))
ROOT = Path(__file__).resolve().parent
_QUESTION_LIBRARY_PATH = ROOT / "ai" / "question_library.json"

st.set_page_config(
    page_title="E-Commerce AI Analytics Platform",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .main-title { font-size: 2.2rem; font-weight: 700; color: #1E293B; margin-bottom: 0.2rem; }
    .sub-title  { font-size: 1.0rem; color: #64748B; margin-bottom: 1.5rem; }
    .metric-card { background:#F8FAFC;border:1px solid #E2E8F0;border-radius:8px;padding:1rem;text-align:center; }
</style>
""", unsafe_allow_html=True)


# --------------------------- shared helpers ---------------------------------
_FRIENDLY_500 = (
    "This section isn't available in the current deployment. "
    "The data it needs (typically an ML-derived table) hasn't been loaded to "
    "this environment yet — everything else on the site continues to work."
)


@st.cache_data(ttl=120)
def fetch_api_data(endpoint: str, params: dict = None, silent: bool = False):
    """
    GET an API endpoint with a cold-start-tolerant timeout and friendly errors.

    Render/Railway/Fly free-tier services sleep after ~15 min idle and take
    30-45 s to wake. A short timeout would surface spurious "can't connect"
    errors even though the API is fine — it's just booting.

    `silent=True` suppresses UI error messages so callers can render their own
    contextual empty-state (better UX than a red error box on every page).
    """
    url = f"{API_BASE_URL}{endpoint}"
    is_local = "localhost" in API_BASE_URL or "127.0.0.1" in API_BASE_URL
    timeout_s = 10 if is_local else 60
    try:
        resp = requests.get(url, params=params, timeout=timeout_s)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 404:
            if not silent:
                st.info("No record found for that identifier.")
            return None
        if resp.status_code == 500:
            if not silent:
                st.warning(_FRIENDLY_500)
            return None
        if not silent:
            st.warning(f"Request couldn't be completed (status {resp.status_code}). "
                       f"Try again in a moment.")
        return None
    except requests.exceptions.Timeout:
        if not silent:
            st.info(
                "⏳ The API is warming up (free-tier services sleep after 15 min idle). "
                "Give it 30-45 seconds and try again."
            )
        return None
    except Exception:
        if not silent:
            st.info("The backend is temporarily unreachable. Retrying in a few seconds usually works.")
        return None


@st.cache_data(show_spinner=False)
def load_question_library() -> list[dict]:
    try:
        return json.loads(_QUESTION_LIBRARY_PATH.read_text(encoding="utf-8")).get("questions", [])
    except Exception:
        return []


_STOP = {"the","a","an","is","are","of","in","to","and","or","for","on","by","our","we",
         "have","has","do","does","which","what","show","me","give","how","many","much",
         "with","that","this"}


def _tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
            if t not in _STOP and len(t) > 1]


def search_questions(library, query, category, top_k=10):
    pool = [q for q in library if not category or category == "All" or q["category"] == category]
    toks = _tokenize(query)
    if not toks:
        return pool[:top_k]
    scored = []
    for q in pool:
        text_toks = _tokenize(f"{q['question']} {q['category']}")
        overlap = len(set(toks) & set(text_toks))
        if overlap:
            scored.append((overlap + 0.1 * sum(1 for t in toks if any(t in w for w in text_toks)), q))
    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    return [q for _, q in scored[:top_k]]


@st.cache_resource(show_spinner=False)
def _get_retriever():
    if not _RAG_OK:
        return None, f"RAG package failed to import: {_RAG_ERR}"
    try:
        return Retriever(), None
    except FileNotFoundError as e:
        return None, str(e)
    except Exception as e:
        return None, f"Retriever init failed: {e}"


# ========================== SIDEBAR NAV =====================================
def main():
    st.sidebar.title("📊 E-Commerce BI")
    st.sidebar.markdown("Decision Support & Analytics Platform")

    NAV_OPTIONS = [
        "🤖 AI Business Analyst",
        "🔍 Question Library",
        "💻 SQL Query",
        "📚 Knowledge Base",
        "💡 Recommendations",
        "──────── Analytics ────────",
        "Executive Overview",
        "Sales & Revenue",
        "Products & Categories",
        "Customers & Segments",
        "Customer Experience",
        "Delivery & Operations",
        "Sales Forecasting",
    ]

    def _fmt(opt: str) -> str:
        return opt if not opt.startswith("─") else opt  # separator rendered as-is

    menu = st.sidebar.radio(
        "Navigation",
        NAV_OPTIONS,
        index=0,
        format_func=_fmt,
        label_visibility="collapsed",
    )
    # Separator row is not a real page — fall back to the analyst.
    if menu.startswith("─"):
        menu = "🤖 AI Business Analyst"

    st.sidebar.markdown("---")
    st.sidebar.caption(f"Connected to API: `{API_BASE_URL}`")
    st.sidebar.caption("Data provenance: public relabelled e-commerce dataset (unspecified currency units).")

    # ============================ AI PAGES ==================================
    if menu == "🤖 AI Business Analyst":
        _page_analyst()
    elif menu == "🔍 Question Library":
        _page_question_library()
    elif menu == "💻 SQL Query":
        _page_sql_editor()
    elif menu == "📚 Knowledge Base":
        _page_knowledge_base()
    elif menu == "💡 Recommendations":
        _page_recommendations()
    # ======================== ANALYTICS PAGES ==============================
    elif menu == "Executive Overview":
        _page_executive_overview()
    elif menu == "Sales & Revenue":
        _page_sales_revenue()
    elif menu == "Products & Categories":
        _page_products_categories()
    elif menu == "Customers & Segments":
        _page_customers_segments()
    elif menu == "Customer Experience":
        _page_customer_experience()
    elif menu == "Delivery & Operations":
        _page_delivery_operations()
    elif menu == "Sales Forecasting":
        _page_sales_forecasting()


# ============================================================================
# AI PAGE 1 — Business Analyst chat
# ============================================================================
def _page_analyst():
    st.markdown('<div class="main-title">🤖 AI Business Analyst</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Ask natural-language business questions grounded in real database analytics</div>', unsafe_allow_html=True)

    st.markdown("##### Suggested Sample Questions:")
    samples = [
        "What are our overall executive KPIs and revenue numbers?",
        "What are our best performing product categories by GMV?",
        "How are sales and order volumes trending month over month?",
        "Are late deliveries associated with negative reviews?",
        "Which customer segments are most valuable?",
        "What is the 3-month sales forecast?",
    ]

    def _set_analyst_question(text: str) -> None:
        st.session_state["analyst_question"] = text

    cols = st.columns(3)
    for idx, sample_text in enumerate(samples):
        with cols[idx % 3]:
            st.button(sample_text, key=f"btn_{idx}",
                      on_click=_set_analyst_question, args=(sample_text,))

    user_question = st.text_input(
        "Enter your question for the AI Analyst:",
        key="analyst_question",
        placeholder="e.g. Which categories generate the highest GMV and review ratings?",
    )

    if st.button("Ask Analyst", type="primary") and user_question.strip():
        with st.spinner("Analyzing ground-truth warehouse data..."):
            try:
                resp = requests.post(f"{API_BASE_URL}/api/analyst",
                                     json={"question": user_question.strip()}, timeout=60)
                if resp.status_code == 200:
                    res = resp.json()
                    st.markdown("---")
                    st.markdown(res["answer"])
                    st.caption(f"📌 **Analytics Source Used**: `{res['source']}`")
                    if res.get("sql_used"):
                        with st.expander("View Grounded SQL Query"):
                            st.code(res["sql_used"], language="sql")
                    if res.get("data"):
                        with st.expander("View Supporting Ground-Truth Data"):
                            st.dataframe(pd.DataFrame(res["data"]), use_container_width=True)
                else:
                    st.error(f"Error ({resp.status_code}): {resp.text}")
            except Exception as ex:
                st.error(f"Unable to connect to AI Analyst service at `{API_BASE_URL}/api/analyst`: {ex}")


# ============================================================================
# AI PAGE 2 — 100-question library
# ============================================================================
def _page_question_library():
    st.markdown('<div class="main-title">🔍 Question Library</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Search a curated set of 100 natural-language questions. No LLM calls.</div>', unsafe_allow_html=True)

    library = load_question_library()
    if not library:
        st.info("Question library not found.")
        return

    categories = ["All"] + sorted({q["category"] for q in library})
    sc1, sc2 = st.columns([3, 1])
    with sc1:
        query = st.text_input("Search questions",
                              key="question_lib_search",
                              placeholder="e.g. products with bad ratings")
    with sc2:
        selected_cat = st.selectbox("Category", categories, key="question_lib_cat")

    results = search_questions(library, query, selected_cat, top_k=20)
    st.caption(f"{len(results)} of {len(library)} questions shown.")

    def _use_q(text): st.session_state["analyst_question"] = text

    for q in results:
        r1, r2 = st.columns([5, 1])
        with r1:
            st.markdown(
                f"**Q{q['id']}** — {q['question']}  \n"
                f"<span style='color:#64748B;font-size:0.8rem;'>{q['category']} · source: `{q['source']}`</span>",
                unsafe_allow_html=True,
            )
        with r2:
            st.button("Use in Analyst", key=f"use_q_{q['id']}",
                      on_click=_use_q, args=(q["question"],))
    st.caption("Click **Use in Analyst** then switch to the 🤖 AI Business Analyst page to run it.")


# ============================================================================
# AI PAGE 3 — Direct SQL editor
# ============================================================================
def _page_sql_editor():
    st.markdown('<div class="main-title">💻 SQL Query</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Run read-only SELECT / WITH queries against PostgreSQL. Writes are blocked.</div>', unsafe_allow_html=True)

    with st.expander("📚 Available tables & views (click to view)"):
        public_tables = sorted(t for t in KNOWN_TABLES if t.startswith("public."))
        analytics_views = sorted(t for t in KNOWN_TABLES if t.startswith("analytics."))
        tc1, tc2 = st.columns(2)
        with tc1:
            st.markdown("**public.** (raw dataset tables)")
            for t in public_tables:
                st.markdown(f"- `{t}`")
        with tc2:
            st.markdown("**analytics.** (curated views / ML output)")
            for t in analytics_views:
                st.markdown(f"- `{t}`")

    def _set_sql(text_val): st.session_state["sql_editor"] = text_val

    st.markdown("**Starter templates:**")
    tcols = st.columns(4)
    templates = [
        ("Executive KPIs", "SELECT * FROM analytics.v_executive_kpis"),
        ("Top 5 products (GMV)",
         "SELECT product_id, product_category_name, quantity_sold, product_gmv\n"
         "FROM analytics.v_product_performance\n"
         "ORDER BY product_gmv DESC NULLS LAST\nLIMIT 5"),
        ("Worst 3 categories (rating)",
         "SELECT product_category_name, review_count, avg_review_score\n"
         "FROM analytics.v_category_performance\n"
         "WHERE review_count >= 100 AND avg_review_score IS NOT NULL\n"
         "ORDER BY avg_review_score ASC\nLIMIT 3"),
        ("Monthly sales",
         "SELECT month, order_count, product_gmv, cash_collected\n"
         "FROM analytics.v_monthly_sales\nORDER BY month"),
    ]
    for i, (label, tpl) in enumerate(templates):
        with tcols[i]:
            st.button(label, key=f"tpl_{i}", on_click=_set_sql, args=(tpl,))

    sql_input = st.text_area(
        "SQL query (SELECT / WITH only):",
        key="sql_editor",
        height=160,
        placeholder="SELECT product_id, product_gmv\nFROM analytics.v_product_performance\nORDER BY product_gmv DESC\nLIMIT 10",
    )
    rc1, rc2 = st.columns([1, 3])
    with rc1:
        row_cap = st.selectbox("Row cap", [100, 500, 1000], index=0, key="sql_row_cap")
    with rc2:
        run_clicked = st.button("▶ Run SQL", type="primary", key="sql_run_btn")

    if not run_clicked:
        return
    raw = (sql_input or "").strip()
    if not raw:
        st.warning("⚠️ Please enter a SQL query.")
        return

    ok, cleaned_or_err = validate_sql(raw)
    if not ok:
        st.error(f"⚠️ **Please give a valid query — {cleaned_or_err}**")
        m = (re.search(r"got\s+([A-Z_]+)", cleaned_or_err)
             or re.search(r"`([^`]+)`", cleaned_or_err)
             or re.search(r"detected:\s+(.+)$", cleaned_or_err))
        if m:
            st.code(f"Offending token / fragment: {m.group(1)}", language=None)
        st.caption("Only read-only SELECT / WITH statements are allowed.")
        return

    all_ok, bad_ref, refs = check_known_tables(cleaned_or_err)
    if not refs:
        st.error("⚠️ **Please give a valid query — no table or view referenced.**")
        return
    if not all_ok:
        st.error(f"⚠️ **Please give a valid query — unknown table/view:** `{bad_ref}`")
        st.caption("Choose from the tables/views listed in the expander above.")
        return

    final_sql = cleaned_or_err
    m_limit = re.search(r"\bLIMIT\s+(\d+)\s*$", final_sql, re.IGNORECASE)
    if m_limit:
        if int(m_limit.group(1)) > row_cap:
            final_sql = re.sub(r"\bLIMIT\s+\d+\s*$", f"LIMIT {row_cap}",
                               final_sql, flags=re.IGNORECASE)
    else:
        final_sql = f"{final_sql} LIMIT {row_cap}"

    try:
        t0 = time.perf_counter()
        with readonly_engine.connect() as conn:
            result = conn.execute(sa_text(final_sql))
            cols = list(result.keys())
            rows = [dict(r._mapping) for r in result]
        elapsed_ms = (time.perf_counter() - t0) * 1000

        df = pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
        mc1, mc2, mc3 = st.columns(3)
        mc1.metric("Rows returned", f"{len(df):,}")
        mc2.metric("Query time", f"{elapsed_ms:,.0f} ms")
        mc3.metric("Tables used", ", ".join(refs))
        if df.empty:
            st.warning("Query executed successfully but returned **0 rows**. Check your filters/joins.")
        else:
            st.dataframe(df, use_container_width=True)
            st.download_button("⬇ Download CSV",
                               data=df.to_csv(index=False).encode("utf-8"),
                               file_name="query_results.csv", mime="text/csv",
                               key="sql_csv_dl")
        with st.expander("View executed SQL"):
            st.code(final_sql, language="sql")
    except Exception as db_err:
        short = str(db_err).splitlines()[0][:400]
        st.error("⚠️ **PostgreSQL rejected the query.**")
        st.code(short, language=None)
        st.caption("Common causes: column name doesn't exist, wrong data type, missing GROUP BY, or invalid syntax.")


# ============================================================================
# AI PAGE 4 — Knowledge Base (RAG)
# ============================================================================
def _page_knowledge_base():
    st.markdown('<div class="main-title">📚 Knowledge Base (RAG)</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Grounded answers from project documentation. Cites sources.</div>', unsafe_allow_html=True)

    retriever, rag_err = _get_retriever()
    if retriever is None:
        st.info("Knowledge-base index not available. Build it once with:\n\n"
                "`.venv\\Scripts\\python.exe scripts/build_rag_index.py`")
        if rag_err:
            st.caption(f"Details: {rag_err}")
        return

    stats = retriever.stats()
    st.caption(f"Index: {stats['chunk_count']} chunks · {len(stats['sources'])} sources · vocab {stats['vocab_size']}")

    def _set_rag(text): st.session_state["rag_question"] = text

    kb_samples = [
        "What is Product GMV and how is it defined?",
        "How is a negative review defined?",
        "Why is churn not modelled in this project?",
        "Where does the dataset come from?",
    ]
    kb_cols = st.columns(4)
    for idx, s in enumerate(kb_samples):
        with kb_cols[idx]:
            st.button(s, key=f"kb_sample_{idx}", on_click=_set_rag, args=(s,))

    rag_question = st.text_input(
        "Ask a knowledge-base question:",
        key="rag_question",
        placeholder="e.g. What is the difference between GMV and cash collected?",
    )
    kc1, kc2 = st.columns([1, 3])
    with kc1:
        top_k = st.selectbox("Top-K excerpts", [3, 5, 7], index=1, key="rag_topk")
    with kc2:
        ask_kb = st.button("🔎 Retrieve & Answer", type="primary", key="rag_run")

    if not (ask_kb and rag_question.strip()):
        return
    with st.spinner("Retrieving grounded excerpts..."):
        results = retriever.retrieve(rag_question.strip(), k=top_k)
    if not results:
        st.warning("No relevant excerpts found. Try rephrasing.")
        return
    with st.spinner("Synthesizing grounded answer..."):
        answer_md, mode = answer_from_context(rag_question.strip(), results)
    st.markdown(answer_md)
    st.caption(f"Synthesis mode: **{mode}** · {len(results)} excerpts retrieved")
    with st.expander("View raw retrieved excerpts"):
        for i, r in enumerate(results, 1):
            st.markdown(f"**[{i}] {r.citation()}** — score `{r.score:.3f}`")
            st.code(r.chunk.text, language="markdown")


# ============================================================================
# AI PAGE 5 — Recommendations (Decision Support)
# ============================================================================
def _page_recommendations():
    st.markdown('<div class="main-title">💡 AI Business Recommendations</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Evidence-first decision support. SQL analytics + RAG context → structured recommendation. Every number traces to its source view.</div>', unsafe_allow_html=True)

    if not _DS_OK:
        st.info(f"Decision-support module unavailable: {_DS_ERR}")
        return

    st.caption("**Supported categories:** " + " · ".join(RECOMMENDATION_CATEGORIES))

    def _set_rec(text): st.session_state["rec_question"] = text

    rec_samples = [
        "Which categories should we investigate for quality issues?",
        "How is our shipping SLA performing?",
        "What is the sales trend month over month?",
        "Which customer segment should we prioritise for retention?",
    ]
    rc_cols = st.columns(4)
    for idx, s in enumerate(rec_samples):
        with rc_cols[idx]:
            st.button(s, key=f"rec_sample_{idx}", on_click=_set_rec, args=(s,))

    rec_question = st.text_input(
        "Ask for a recommendation:",
        key="rec_question",
        placeholder="e.g. Where should we focus operational improvements next?",
    )
    gc1, gc2 = st.columns([1, 3])
    with gc1:
        use_gemini = st.checkbox("Use Gemini reasoning (if available)", value=True, key="rec_use_gemini")
    with gc2:
        run_rec = st.button("💡 Generate Recommendation", type="primary", key="rec_run")

    if not (run_rec and rec_question.strip()):
        return
    slug = classify_question(rec_question.strip())
    st.caption(f"Routed to category: **{slug or 'unsupported'}**")
    with st.spinner("Collecting evidence and building recommendation..."):
        pkg = generate_recommendation(readonly_engine, rec_question.strip(), use_gemini=use_gemini)
    st.markdown(pkg.to_markdown())
    if pkg.evidence:
        with st.expander("View structured evidence"):
            ev_rows = [{
                "metric": e.metric,
                "value": e.value,
                "unit": e.unit,
                "comparison": (f"{e.comparison_label} {e.comparison_value}"
                               if e.comparison_value is not None else ""),
                "source_view": e.source_view,
            } for e in pkg.evidence]
            st.dataframe(pd.DataFrame(ev_rows), use_container_width=True)


# ============================================================================
# ANALYTICS PAGES (unchanged from Phase 5)
# ============================================================================
def _page_executive_overview():
    st.markdown('<div class="main-title">Executive Overview</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">High-level key performance indicators and revenue summary</div>', unsafe_allow_html=True)
    kpis = fetch_api_data("/api/kpis")
    if not kpis:
        return
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Orders", f"{kpis['total_orders']:,}")
    c2.metric("Total Customers", f"{kpis['total_customers']:,}")
    c3.metric("Unique People", f"{kpis['total_unique_customers']:,}")
    c4.metric("Total Products", f"{kpis['total_products']:,}")
    c5.metric("Total Sellers", f"{kpis['total_sellers']:,}")
    st.markdown("<br>", unsafe_allow_html=True)
    r1, r2, r3, r4, r5 = st.columns(5)
    r1.metric("Product GMV", f"{kpis['product_gmv']:,.2f}")
    r2.metric("Cash Collected", f"{kpis['cash_collected']:,.2f}")
    r3.metric("AOV (GMV)", f"{kpis['avg_order_value_gmv']:,.2f}")
    r4.metric("Delivered Rate", f"{kpis['delivered_pct']}%")
    r5.metric("Cancelled Rate", f"{kpis['cancelled_pct']}%")
    st.info("💡 **Metric Distinction**: **Product GMV** = price + freight from order items. "
            "**Cash Collected** = payments received (may include installments). Both distinct per DQ-15.")


def _page_sales_revenue():
    st.markdown('<div class="main-title">Sales & Revenue Trends</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Monthly performance time-series and transaction metrics</div>', unsafe_allow_html=True)
    sales_data = fetch_api_data("/api/sales/monthly")
    if not sales_data:
        return
    df_sales = pd.DataFrame(sales_data)
    fig_rev = px.line(df_sales, x="month", y=["product_gmv", "cash_collected"],
                      title="Monthly Product GMV vs Cash Collected",
                      labels={"value": "Amount (Currency Units)", "month": "Month", "variable": "Metric"},
                      markers=True)
    st.plotly_chart(fig_rev, use_container_width=True)
    col_a, col_b = st.columns(2)
    with col_a:
        st.plotly_chart(px.bar(df_sales, x="month", y="order_count",
                               title="Monthly Order Volume",
                               labels={"order_count": "Orders", "month": "Month"}),
                        use_container_width=True)
    with col_b:
        st.plotly_chart(px.line(df_sales, x="month", y="aov_gmv",
                                title="Monthly Average Order Value (AOV)",
                                labels={"aov_gmv": "AOV", "month": "Month"}, markers=True),
                        use_container_width=True)
    with st.expander("View Monthly Sales Table"):
        st.dataframe(df_sales, use_container_width=True)


def _page_products_categories():
    st.markdown('<div class="main-title">Product & Category Performance</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Category GMV, item volume, and top-performing products</div>', unsafe_allow_html=True)
    top_n = st.selectbox("Top N Categories/Products to Display", [10, 20, 50], index=0)
    cat_data = fetch_api_data(f"/api/categories?limit={top_n}")
    prod_data = fetch_api_data(f"/api/products?limit={top_n}")
    if not (cat_data and prod_data):
        return
    df_cat = pd.DataFrame(cat_data)
    df_prod = pd.DataFrame(prod_data)
    st.subheader(f"Top {top_n} Categories by GMV")
    fig_cat = px.bar(df_cat, x="product_gmv", y="product_category_name", orientation="h",
                     color="avg_review_score", color_continuous_scale="Viridis",
                     title=f"Top {top_n} Categories (GMV & Avg Review Rating)",
                     labels={"product_gmv": "Product GMV", "product_category_name": "Category",
                             "avg_review_score": "Avg Rating"})
    fig_cat.update_layout(yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(fig_cat, use_container_width=True)
    st.subheader(f"Top {top_n} Products")
    st.dataframe(
        df_prod[["gmv_rank", "product_id", "product_category_name", "quantity_sold",
                 "product_gmv", "avg_item_price", "avg_review_score"]],
        use_container_width=True,
    )


def _page_customers_segments():
    st.markdown('<div class="main-title">Customer Analytics & RFM Segmentation</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Phase 3 ML Customer Clusters & Individual Profile Lookup</div>', unsafe_allow_html=True)
    seg_counts = {
        "High-Value Champions": 2997,
        "Repeat Loyalists": 32962,
        "Recent One-Time Buyers": 23492,
        "Lapsed / Low-Spend Buyers": 36645,
    }
    df_seg = pd.DataFrame(list(seg_counts.items()), columns=["Segment", "Customers"])
    col1, col2 = st.columns([1, 1])
    with col1:
        st.plotly_chart(px.pie(df_seg, names="Segment", values="Customers",
                               title="Customer Segment Distribution (K-Means Clustering)", hole=0.4),
                        use_container_width=True)
    with col2:
        st.subheader("Segment Descriptions")
        st.markdown("""
        * **High-Value Champions** (2,997): Multi-order repeat customers with top spending.
        * **Repeat Loyalists** (32,962): Active, high-spend single/repeat order customers.
        * **Recent One-Time Buyers** (23,492): Recent single-order buyers with moderate spend.
        * **Lapsed / Low-Spend Buyers** (36,645): Older single-order buyers with low transaction values.
        """)
        st.caption("Note: Per Decision D-007, churn prediction is out of scope due to 3.12% repeat rate.")
    st.markdown("---")
    st.subheader("Customer Lookup Utility")
    sample_cid = st.text_input("Enter customer_unique_id",
                               value="0000366f3b9a7992bf8c76cfdf3221e2")
    if not sample_cid:
        return
    # Silent fetches so we render a single contextual message instead of
    # two stacked error boxes if the ML segment table isn't populated.
    cust = fetch_api_data(f"/api/customers/{sample_cid.strip()}", silent=True)
    cust_seg = fetch_api_data(f"/api/customers/{sample_cid.strip()}/segment", silent=True)
    if not cust:
        st.info("No customer profile found for that ID. Try a different `customer_unique_id`.")
        return
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Orders Placed", cust["order_count"])
    m2.metric("Total Spend (GMV)", f"{cust['total_gmv']:,.2f}")
    m3.metric("Avg Order Value", f"{cust['avg_order_value_gmv']:,.2f}")
    m4.metric("Segment Label",
              cust_seg["segment_label"] if cust_seg else "—")
    st.write(f"**First Order Date**: {cust['first_order_date']} | "
             f"**Latest Order Date**: {cust['latest_order_date']}")
    if not cust_seg:
        st.caption("ℹ️ Segment label unavailable in this deployment — the ML segmentation "
                   "table hasn't been loaded to the current environment. Customer profile "
                   "data above is fully accurate.")


def _page_customer_experience():
    st.markdown('<div class="main-title">Customer Experience & Review Analytics</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Review rating distribution and operational impact analysis</div>', unsafe_allow_html=True)
    reviews = fetch_api_data("/api/reviews")
    if not reviews:
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Reviews", f"{reviews['total_reviews']:,}")
    c2.metric("Average Review Rating", f"{reviews['avg_review_score']} / 5.0")
    c3.metric("Negative Reviews (≤2)", f"{reviews['negative_review_count']:,}")
    c4.metric("Negative Review Rate", f"{reviews['negative_review_rate_pct']}%")
    rating_df = pd.DataFrame({
        "Rating": ["1 Star", "2 Stars", "3 Stars", "4 Stars", "5 Stars"],
        "Count": [reviews["rating_1_count"], reviews["rating_2_count"], reviews["rating_3_count"],
                  reviews["rating_4_count"], reviews["rating_5_count"]],
    })
    st.plotly_chart(px.bar(rating_df, x="Rating", y="Count",
                           title="Customer Review Rating Distribution",
                           color="Rating", color_discrete_sequence=px.colors.qualitative.Set2),
                    use_container_width=True)
    st.warning("📌 **Phase 3 Research Finding**: on-time orders have 9.49% negative-review rate vs "
               "54.64% for late deliveries (associated with, not proven causal).")


def _page_delivery_operations():
    st.markdown('<div class="main-title">Delivery & Operational SLA Performance</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Fulfillment duration, delivery timeliness, and monthly trends</div>', unsafe_allow_html=True)
    deliv = fetch_api_data("/api/delivery")
    deliv_m = fetch_api_data("/api/delivery/monthly")
    if not (deliv and deliv_m):
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Delivered Orders", f"{deliv['delivered_order_count']:,}")
    c2.metric("Avg Delivery Days", f"{deliv['avg_delivery_days']} days")
    c3.metric("Median Delivery Days", f"{deliv['median_delivery_days']} days")
    c4.metric("Late Delivery Rate", f"{deliv['late_delivery_rate_pct']}%")
    df_deliv = pd.DataFrame(deliv_m)
    st.plotly_chart(px.line(df_deliv, x="month",
                            y=["avg_delivery_days", "late_delivery_rate_pct"],
                            title="Monthly Delivery Days & Late Delivery Rate Trend",
                            markers=True,
                            labels={"value": "Metric Value", "month": "Month", "variable": "Metric"}),
                    use_container_width=True)


def _page_sales_forecasting():
    st.markdown('<div class="main-title">Sales Forecasting</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Phase 3 Machine Learning 3-Month Forward GMV Forecast</div>', unsafe_allow_html=True)
    forecast_data = fetch_api_data("/api/forecast")
    if not forecast_data:
        return
    st.info(f"🤖 **Model Selected**: `{forecast_data['selected_model']}` | "
            f"Historical Baseline MAE: `{forecast_data['baseline_metrics']['MAE']:,.2f}`")
    df_fc = pd.DataFrame(forecast_data["forward_forecast"])
    fig_fc = go.Figure()
    fig_fc.add_trace(go.Scatter(x=df_fc["month"], y=df_fc["forecast_gmv"],
                                mode="lines+markers", name="Forecast GMV",
                                line=dict(color="#2563EB", width=3)))
    fig_fc.add_trace(go.Scatter(
        x=df_fc["month"].tolist() + df_fc["month"].tolist()[::-1],
        y=df_fc["upper_ci_95"].tolist() + df_fc["lower_ci_95"].tolist()[::-1],
        fill="toself", fillcolor="rgba(37, 99, 235, 0.15)",
        line=dict(color="rgba(255,255,255,0)"),
        hoverinfo="skip", showlegend=True, name="95% Confidence Interval"))
    fig_fc.update_layout(title="3-Month Forward Sales Forecast (Product GMV)",
                         xaxis_title="Month", yaxis_title="Forecast GMV")
    st.plotly_chart(fig_fc, use_container_width=True)
    st.caption("⚠️ Short-term forecast on 25 months of continuous history. Horizon: 3 months forward.")


if __name__ == "__main__":
    main()
