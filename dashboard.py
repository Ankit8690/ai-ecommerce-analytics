"""
E-Commerce Business Intelligence & Analytics Dashboard.
Consumes FastAPI Backend API (http://127.0.0.1:8000/api/*).
"""
from __future__ import annotations

import os
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Environment-configurable API Base URL
API_BASE_URL = os.environ.get("DASHBOARD_API_URL", "http://127.0.0.1:8000").rstrip("/")

st.set_page_config(
    page_title="E-Commerce AI Analytics Platform",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Styling
st.markdown("""
<style>
    .main-title {
        font-size: 2.2rem;
        font-weight: 700;
        color: #1E293B;
        margin-bottom: 0.2rem;
    }
    .sub-title {
        font-size: 1.0rem;
        color: #64748B;
        margin-bottom: 1.5rem;
    }
    .metric-card {
        background-color: #F8FAFC;
        border: 1px solid #E2E8F0;
        border-radius: 8px;
        padding: 1rem;
        text-align: center;
    }
    .metric-value {
        font-size: 1.6rem;
        font-weight: 700;
        color: #0F172A;
    }
    .metric-label {
        font-size: 0.85rem;
        color: #475569;
        font-weight: 500;
    }
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=120)
def fetch_api_data(endpoint: str, params: dict = None) -> dict | list | None:
    """Helper function to fetch data from FastAPI backend with error handling."""
    url = f"{API_BASE_URL}{endpoint}"
    try:
        resp = requests.get(url, params=params, timeout=5)
        if resp.status_code == 200:
            return resp.json()
        st.error(f"API Error ({resp.status_code}) on {endpoint}: {resp.text}")
        return None
    except Exception as e:
        st.error(f"Unable to connect to API backend at {API_BASE_URL}. Ensure FastAPI service is running.")
        return None


def main():
    st.sidebar.title("📊 E-Commerce BI")
    st.sidebar.markdown("Decision Support & Analytics Platform")
    
    menu = st.sidebar.radio(
        "Navigation",
        [
            "🤖 AI Business Analyst",
            "Executive Overview",
            "Sales & Revenue",
            "Products & Categories",
            "Customers & Segments",
            "Customer Experience",
            "Delivery & Operations",
            "Sales Forecasting"
        ]
    )
    
    st.sidebar.markdown("---")
    st.sidebar.caption(f"Connected to API: `{API_BASE_URL}`")
    st.sidebar.caption("Data provenance: Public relabelled e-commerce dataset (unspecified currency units).")

    # -----------------------------------------------------------------
    # 0. AI BUSINESS ANALYST
    # -----------------------------------------------------------------
    if menu == "🤖 AI Business Analyst":
        st.markdown('<div class="main-title">🤖 AI Business Analyst</div>', unsafe_allow_html=True)
        st.markdown('<div class="sub-title">Ask natural-language business questions grounded in real database analytics</div>', unsafe_allow_html=True)
        
        st.markdown("##### Suggested Sample Questions:")
        samples = [
            "What are our overall executive KPIs and revenue numbers?",
            "What are our best performing product categories by GMV?",
            "How are sales and order volumes trending month over month?",
            "Are late deliveries associated with negative reviews?",
            "Which customer segments are most valuable?",
            "What is the 3-month sales forecast?"
        ]
        
        cols = st.columns(3)
        selected_sample = None
        for idx, sample_text in enumerate(samples):
            with cols[idx % 3]:
                if st.button(sample_text, key=f"btn_{idx}"):
                    selected_sample = sample_text
                    
        user_question = st.text_input(
            "Enter your question for the AI Analyst:",
            value=selected_sample if selected_sample else "",
            placeholder="e.g. Which categories generate the highest GMV and review ratings?"
        )
        
        if st.button("Ask Analyst", type="primary") and user_question.strip():
            with st.spinner("Analyzing ground-truth warehouse data..."):
                try:
                    resp = requests.post(
                        f"{API_BASE_URL}/api/analyst",
                        json={"question": user_question.strip()},
                        timeout=10
                    )
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

    # -----------------------------------------------------------------
    # 1. EXECUTIVE OVERVIEW
    # -----------------------------------------------------------------
    elif menu == "Executive Overview":
        st.markdown('<div class="main-title">Executive Overview</div>', unsafe_allow_html=True)
        st.markdown('<div class="sub-title">High-level key performance indicators and revenue summary</div>', unsafe_allow_html=True)
        
        kpis = fetch_api_data("/api/kpis")
        if kpis:
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
            
            st.info(
                "💡 **Metric Distinction**: **Product GMV** represents total product price + freight from order items "
                "(15,843,553.24 units). **Cash Collected** represents gross payments received from customer installments "
                "(16,008,872.12 units). Both metrics are distinct per data quality standard DQ-15."
            )

    # -----------------------------------------------------------------
    # 2. SALES & REVENUE ANALYTICS
    # -----------------------------------------------------------------
    elif menu == "Sales & Revenue":
        st.markdown('<div class="main-title">Sales & Revenue Trends</div>', unsafe_allow_html=True)
        st.markdown('<div class="sub-title">Monthly performance time-series and transaction metrics</div>', unsafe_allow_html=True)
        
        sales_data = fetch_api_data("/api/sales/monthly")
        if sales_data:
            df_sales = pd.DataFrame(sales_data)
            
            fig_rev = px.line(
                df_sales,
                x="month",
                y=["product_gmv", "cash_collected"],
                title="Monthly Product GMV vs Cash Collected",
                labels={"value": "Amount (Currency Units)", "month": "Month", "variable": "Metric"},
                markers=True
            )
            st.plotly_chart(fig_rev, use_container_width=True)
            
            col_a, col_b = st.columns(2)
            with col_a:
                fig_ord = px.bar(
                    df_sales,
                    x="month",
                    y="order_count",
                    title="Monthly Order Volume",
                    labels={"order_count": "Orders", "month": "Month"}
                )
                st.plotly_chart(fig_ord, use_container_width=True)
                
            with col_b:
                fig_aov = px.line(
                    df_sales,
                    x="month",
                    y="aov_gmv",
                    title="Monthly Average Order Value (AOV)",
                    labels={"aov_gmv": "AOV", "month": "Month"},
                    markers=True
                )
                st.plotly_chart(fig_aov, use_container_width=True)
                
            with st.expander("View Monthly Sales Table"):
                st.dataframe(df_sales, use_container_width=True)

    # -----------------------------------------------------------------
    # 3. PRODUCTS & CATEGORIES
    # -----------------------------------------------------------------
    elif menu == "Products & Categories":
        st.markdown('<div class="main-title">Product & Category Performance</div>', unsafe_allow_html=True)
        st.markdown('<div class="sub-title">Category GMV, item volume, and top-performing products</div>', unsafe_allow_html=True)
        
        top_n = st.selectbox("Top N Categories/Products to Display", [10, 20, 50], index=0)
        
        cat_data = fetch_api_data(f"/api/categories?limit={top_n}")
        prod_data = fetch_api_data(f"/api/products?limit={top_n}")
        
        if cat_data and prod_data:
            df_cat = pd.DataFrame(cat_data)
            df_prod = pd.DataFrame(prod_data)
            
            st.subheader(f"Top {top_n} Categories by GMV")
            fig_cat = px.bar(
                df_cat,
                x="product_gmv",
                y="product_category_name",
                orientation="h",
                color="avg_review_score",
                color_continuous_scale="Viridis",
                title=f"Top {top_n} Categories (GMV & Avg Review Rating)",
                labels={"product_gmv": "Product GMV", "product_category_name": "Category", "avg_review_score": "Avg Rating"}
            )
            fig_cat.update_layout(yaxis={"categoryorder": "total ascending"})
            st.plotly_chart(fig_cat, use_container_width=True)
            
            st.subheader(f"Top {top_n} Products")
            st.dataframe(
                df_prod[["gmv_rank", "product_id", "product_category_name", "quantity_sold", "product_gmv", "avg_item_price", "avg_review_score"]],
                use_container_width=True
            )

    # -----------------------------------------------------------------
    # 4. CUSTOMERS & SEGMENTS
    # -----------------------------------------------------------------
    elif menu == "Customers & Segments":
        st.markdown('<div class="main-title">Customer Analytics & RFM Segmentation</div>', unsafe_allow_html=True)
        st.markdown('<div class="sub-title">Phase 3 ML Customer Clusters & Individual Profile Lookup</div>', unsafe_allow_html=True)
        
        seg_counts = {
            "High-Value Champions": 2997,
            "Repeat Loyalists": 32962,
            "Recent One-Time Buyers": 23492,
            "Lapsed / Low-Spend Buyers": 36645
        }
        df_seg = pd.DataFrame(list(seg_counts.items()), columns=["Segment", "Customers"])
        
        col1, col2 = st.columns([1, 1])
        with col1:
            fig_pie = px.pie(
                df_seg,
                names="Segment",
                values="Customers",
                title="Customer Segment Distribution (K-Means Clustering)",
                hole=0.4
            )
            st.plotly_chart(fig_pie, use_container_width=True)
            
        with col2:
            st.subheader("Segment Descriptions")
            st.markdown("""
            * **High-Value Champions** (2,997): Multi-order repeat customers with top spending.
            * **Repeat Loyalists** (32,962): Active, high-spend single/repeat order customers.
            * **Recent One-Time Buyers** (23,492): Recent single-order buyers with moderate spend.
            * **Lapsed / Low-Spend Buyers** (36,645): Older single-order buyers with low transaction values.
            """)
            st.caption("Note: Per Decision D-007, segments represent RFM behavior. Churn prediction is out of scope due to 3.12% repeat order rate.")

        st.markdown("---")
        st.subheader("Customer Lookup Utility")
        sample_cid = st.text_input("Enter customer_unique_id", value="0000366f3b9a7992bf8c76cfdf3221e2")
        
        if sample_cid:
            cust = fetch_api_data(f"/api/customers/{sample_cid.strip()}")
            cust_seg = fetch_api_data(f"/api/customers/{sample_cid.strip()}/segment")
            if cust and cust_seg:
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Orders Placed", cust["order_count"])
                m2.metric("Total Spend (GMV)", f"{cust['total_gmv']:,.2f}")
                m3.metric("Avg Order Value", f"{cust['avg_order_value_gmv']:,.2f}")
                m4.metric("Segment Label", cust_seg["segment_label"])
                st.write(f"**First Order Date**: {cust['first_order_date']} | **Latest Order Date**: {cust['latest_order_date']}")

    # -----------------------------------------------------------------
    # 5. CUSTOMER EXPERIENCE
    # -----------------------------------------------------------------
    elif menu == "Customer Experience":
        st.markdown('<div class="main-title">Customer Experience & Review Analytics</div>', unsafe_allow_html=True)
        st.markdown('<div class="sub-title">Review rating distribution and operational impact analysis</div>', unsafe_allow_html=True)
        
        reviews = fetch_api_data("/api/reviews")
        if reviews:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total Reviews", f"{reviews['total_reviews']:,}")
            c2.metric("Average Review Rating", f"{reviews['avg_review_score']} / 5.0")
            c3.metric("Negative Reviews (≤2)", f"{reviews['negative_review_count']:,}")
            c4.metric("Negative Review Rate", f"{reviews['negative_review_rate_pct']}%")
            
            rating_df = pd.DataFrame({
                "Rating": ["1 Star", "2 Stars", "3 Stars", "4 Stars", "5 Stars"],
                "Count": [
                    reviews["rating_1_count"],
                    reviews["rating_2_count"],
                    reviews["rating_3_count"],
                    reviews["rating_4_count"],
                    reviews["rating_5_count"]
                ]
            })
            
            fig_stars = px.bar(
                rating_df,
                x="Rating",
                y="Count",
                title="Customer Review Rating Distribution",
                color="Rating",
                color_discrete_sequence=px.colors.qualitative.Set2
            )
            st.plotly_chart(fig_stars, use_container_width=True)
            
            st.warning(
                "📌 **Phase 3 Research Finding**: Late deliveries are strongly associated with negative reviews. "
                "Delivered on-time orders have a **9.49% negative review rate**, whereas late deliveries have a **54.64% negative review rate** "
                "(over 5.7x higher). *Late deliveries are associated with substantially higher negative-review rates (correlation, not proven causality).*"
            )

    # -----------------------------------------------------------------
    # 6. DELIVERY & OPERATIONS
    # -----------------------------------------------------------------
    elif menu == "Delivery & Operations":
        st.markdown('<div class="main-title">Delivery & Operational SLA Performance</div>', unsafe_allow_html=True)
        st.markdown('<div class="sub-title">Fulfillment duration, delivery timeliness, and monthly trends</div>', unsafe_allow_html=True)
        
        deliv = fetch_api_data("/api/delivery")
        deliv_m = fetch_api_data("/api/delivery/monthly")
        
        if deliv and deliv_m:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Delivered Orders", f"{deliv['delivered_order_count']:,}")
            c2.metric("Avg Delivery Days", f"{deliv['avg_delivery_days']} days")
            c3.metric("Median Delivery Days", f"{deliv['median_delivery_days']} days")
            c4.metric("Late Delivery Rate", f"{deliv['late_delivery_rate_pct']}%")
            
            df_deliv = pd.DataFrame(deliv_m)
            fig_del = px.line(
                df_deliv,
                x="month",
                y=["avg_delivery_days", "late_delivery_rate_pct"],
                title="Monthly Delivery Days & Late Delivery Rate Trend",
                markers=True,
                labels={"value": "Metric Value", "month": "Month", "variable": "Metric"}
            )
            st.plotly_chart(fig_del, use_container_width=True)

    # -----------------------------------------------------------------
    # 7. SALES FORECASTING
    # -----------------------------------------------------------------
    elif menu == "Sales Forecasting":
        st.markdown('<div class="main-title">Sales Forecasting</div>', unsafe_allow_html=True)
        st.markdown('<div class="sub-title">Phase 3 Machine Learning 3-Month Forward GMV Forecast</div>', unsafe_allow_html=True)
        
        forecast_data = fetch_api_data("/api/forecast")
        if forecast_data:
            st.info(
                f"🤖 **Model Selected**: `{forecast_data['selected_model']}` | "
                f"Historical Baseline MAE: `{forecast_data['baseline_metrics']['MAE']:,.2f}`"
            )
            
            df_fc = pd.DataFrame(forecast_data["forward_forecast"])
            
            fig_fc = go.Figure()
            fig_fc.add_trace(go.Scatter(
                x=df_fc["month"],
                y=df_fc["forecast_gmv"],
                mode="lines+markers",
                name="Forecast GMV",
                line=dict(color="#2563EB", width=3)
            ))
            fig_fc.add_trace(go.Scatter(
                x=df_fc["month"].tolist() + df_fc["month"].tolist()[::-1],
                y=df_fc["upper_ci_95"].tolist() + df_fc["lower_ci_95"].tolist()[::-1],
                fill="toself",
                fillcolor="rgba(37, 99, 235, 0.15)",
                line=dict(color="rgba(255,255,255,0)"),
                hoverinfo="skip",
                showlegend=True,
                name="95% Confidence Interval"
            ))
            fig_fc.update_layout(title="3-Month Forward Sales Forecast (Product GMV)", xaxis_title="Month", yaxis_title="Forecast GMV")
            st.plotly_chart(fig_fc, use_container_width=True)
            
            st.caption(
                "⚠️ **Forecast Note**: Short-term forecast evaluated on 25 months of continuous historical volume. "
                "Forecasting horizon is conservatively limited to 3 months forward due to available dataset history length."
            )


if __name__ == "__main__":
    main()
