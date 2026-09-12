"""
E-commerce Sales Analytics — Streamlit Application

Conversational analytics interface for querying ClickHouse
data using natural language.
"""

import streamlit as st

from src.config import clickhouse_config, llm_config
from src.clickhouse_client import ClickHouseClient
from src.schema_inspector import SchemaInspector
from src.sql_generator import SQLGenerator
from src.result_analyzer import ResultAnalyzer
from src.visualization import Visualizer
from src.conversation import ConversationManager
from src.analytics_agent import AnalyticsAgent
from src.logger import setup_logging

# ─── Page Config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="E-commerce Sales Analytics",
    page_icon="🛍️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Custom CSS ────────────────────────────────────────────────────────────────

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

    /* Global */
    .stApp {
        font-family: 'Inter', sans-serif;
    }

    /* Main header */
    .main-header {
        background: linear-gradient(135deg, #1e1b4b 0%, #312e81 50%, #4338ca 100%);
        padding: 2rem 2.5rem;
        border-radius: 16px;
        margin-bottom: 1.5rem;
        border: 1px solid rgba(99, 102, 241, 0.2);
        box-shadow: 0 4px 24px rgba(99, 102, 241, 0.15);
    }
    .main-header h1 {
        color: #e0e7ff;
        font-size: 2rem;
        font-weight: 700;
        margin: 0 0 0.3rem 0;
        letter-spacing: -0.5px;
    }
    .main-header p {
        color: #a5b4fc;
        font-size: 1rem;
        margin: 0;
        font-weight: 400;
    }

    /* Status indicator */
    .status-badge {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 6px 14px;
        border-radius: 20px;
        font-size: 0.8rem;
        font-weight: 500;
    }
    .status-connected {
        background: rgba(34, 197, 94, 0.15);
        color: #4ade80;
        border: 1px solid rgba(34, 197, 94, 0.3);
    }
    .status-disconnected {
        background: rgba(239, 68, 68, 0.15);
        color: #f87171;
        border: 1px solid rgba(239, 68, 68, 0.3);
    }

    /* "What's in this data" overview panel */
    .data-overview-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        background: rgba(30, 27, 75, 0.4);
        padding: 8px 14px;
        border-radius: 10px;
        margin-bottom: 6px;
        border: 1px solid rgba(99, 102, 241, 0.15);
    }
    .data-overview-label {
        color: #c7d2fe;
        font-size: 0.85rem;
    }
    .data-overview-count {
        color: #a5b4fc;
        font-weight: 600;
        font-size: 0.85rem;
    }

    /* Dashboard section */
    .dashboard-heading {
        font-size: 1.1rem;
        font-weight: 600;
        color: #e0e7ff;
        margin: 0.5rem 0 0.75rem 0;
    }

    /* SQL expander */
    .sql-block {
        background: rgba(15, 23, 42, 0.6);
        border: 1px solid rgba(99, 102, 241, 0.2);
        border-radius: 10px;
        padding: 1rem;
        margin-top: 0.5rem;
    }

    /* Query metadata */
    .query-meta {
        display: flex;
        gap: 16px;
        margin-top: 8px;
        flex-wrap: wrap;
    }
    .meta-chip {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 4px 10px;
        border-radius: 6px;
        font-size: 0.75rem;
        background: rgba(99, 102, 241, 0.1);
        color: #818cf8;
        border: 1px solid rgba(99, 102, 241, 0.15);
    }

    /* Suggested question buttons */
    .suggestion-btn {
        background: rgba(99, 102, 241, 0.08);
        border: 1px solid rgba(99, 102, 241, 0.2);
        border-radius: 10px;
        padding: 10px 16px;
        color: #c7d2fe;
        font-size: 0.85rem;
        cursor: pointer;
        transition: all 0.2s;
        text-align: left;
        width: 100%;
    }
    .suggestion-btn:hover {
        background: rgba(99, 102, 241, 0.15);
        border-color: rgba(99, 102, 241, 0.4);
        transform: translateY(-1px);
    }

    /* Chat message styling */
    .stChatMessage {
        border-radius: 12px !important;
    }

    /* Sidebar refinements */
    section[data-testid="stSidebar"] {
        background: rgba(15, 23, 42, 0.95);
        border-right: 1px solid rgba(99, 102, 241, 0.1);
    }

    /* Hide Streamlit branding */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
</style>
""", unsafe_allow_html=True)


# ─── Session State Initialization ──────────────────────────────────────────────

def init_session_state():
    """Initialize all session state variables."""
    if "initialized" not in st.session_state:
        setup_logging()

        st.session_state.ch_client = ClickHouseClient()
        st.session_state.schema_inspector = SchemaInspector(st.session_state.ch_client)
        st.session_state.sql_generator = SQLGenerator()
        st.session_state.result_analyzer = ResultAnalyzer()
        st.session_state.visualizer = Visualizer()
        st.session_state.conversation = ConversationManager()
        st.session_state.agent = AnalyticsAgent(
            ch_client=st.session_state.ch_client,
            schema_inspector=st.session_state.schema_inspector,
            sql_generator=st.session_state.sql_generator,
            result_analyzer=st.session_state.result_analyzer,
            visualizer=st.session_state.visualizer,
            conversation=st.session_state.conversation,
        )
        st.session_state.messages = []
        st.session_state.initialized = True


init_session_state()


# ─── Helper Functions ──────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def get_connection_status() -> tuple:
    """Check ClickHouse connection (cached for 5 minutes)."""
    client = ClickHouseClient()
    return client.health_check()


def check_llm_configured() -> tuple[bool, str]:
    """Check if LLM API key is configured."""
    if not llm_config.api_key or llm_config.api_key == "your-api-key-here":
        return False, "LLM API key not configured"
    return True, f"Using {llm_config.provider} ({llm_config.model})"


# Plain-language labels for the sidebar's data overview panel. This is a
# presentation-layer nicety for this specific dataset, not part of the
# schema-aware AI pipeline — the SQL generation/validation path never uses
# these and stays fully dynamic (src/schema_inspector.py). An unmapped
# table just falls back to its raw name.
FRIENDLY_TABLE_LABELS: dict[str, tuple[str, str]] = {
    "orders": ("🛒", "Orders"),
    "customers": ("👥", "Customers"),
    "order_items": ("📦", "Items Sold"),
    "order_payments": ("💳", "Payments"),
    "order_reviews": ("⭐", "Reviews"),
    "products": ("🏷️", "Products"),
    "sellers": ("🏪", "Sellers"),
    "geolocation": ("📍", "Locations"),
    "product_category_name_translation": ("🌐", "Category Names"),
}


@st.cache_data(ttl=600)
def get_order_summary(_client: ClickHouseClient) -> dict:
    """
    Fetch fixed order-summary KPIs and charts straight from ClickHouse.

    Deliberately bypasses the LLM/LangGraph pipeline — this is a fixed
    "front page" overview of this known dataset, not a user question, so
    there's nothing to generate or validate.
    """
    total_orders = int(
        _client.execute_query("SELECT count() AS c FROM olist.orders").dataframe.iloc[0]["c"]
    )
    total_revenue = float(
        _client.execute_query(
            "SELECT sum(payment_value) AS s FROM olist.order_payments"
        ).dataframe.iloc[0]["s"]
    )
    avg_order_value = float(
        _client.execute_query("""
            SELECT avg(order_total) AS a FROM (
                SELECT order_id, sum(payment_value) AS order_total
                FROM olist.order_payments
                GROUP BY order_id
            )
        """).dataframe.iloc[0]["a"]
    )
    monthly_orders = _client.execute_query("""
        SELECT toStartOfMonth(order_purchase_timestamp) AS month, count() AS orders
        FROM olist.orders
        GROUP BY month
        ORDER BY month
    """).dataframe
    top_seller_cities = _client.execute_query("""
        SELECT s.seller_city AS city, count() AS orders
        FROM olist.order_items oi
        JOIN olist.sellers s ON oi.seller_id = s.seller_id
        GROUP BY city
        ORDER BY orders DESC
        LIMIT 10
    """).dataframe
    top_categories = _client.execute_query("""
        SELECT
            coalesce(t.product_category_name_english, p.product_category_name, 'unknown') AS category,
            sum(oi.price + oi.freight_value) AS revenue
        FROM olist.order_items oi
        JOIN olist.products p ON oi.product_id = p.product_id
        LEFT JOIN olist.product_category_name_translation t ON p.product_category_name = t.product_category_name
        GROUP BY category
        ORDER BY revenue DESC
        LIMIT 10
    """).dataframe

    return {
        "total_orders": total_orders,
        "total_revenue": total_revenue,
        "avg_order_value": avg_order_value,
        "monthly_orders": monthly_orders,
        "top_seller_cities": top_seller_cities,
        "top_categories": top_categories,
    }


def render_order_summary_dashboard() -> None:
    """Render the at-a-glance order summary: KPIs + 3 charts, no SQL in sight."""
    try:
        summary = get_order_summary(st.session_state.ch_client)
    except Exception as e:
        st.info(f"Order summary isn't available yet: {e}", icon="⏳")
        return

    st.markdown('<p class="dashboard-heading">📊 Orders at a glance</p>', unsafe_allow_html=True)

    kpi_cols = st.columns(3)
    kpi_cols[0].metric("Total Orders", f"{summary['total_orders']:,}")
    kpi_cols[1].metric("Total Revenue", f"${summary['total_revenue']:,.0f}")
    kpi_cols[2].metric("Average Order Value", f"${summary['avg_order_value']:,.2f}")

    trend_chart = st.session_state.visualizer.create_chart(
        df=summary["monthly_orders"], chart_type="line", question="Monthly orders"
    )
    if trend_chart is not None:
        trend_chart.update_layout(title="Orders per month", height=320)
        st.plotly_chart(trend_chart, use_container_width=True)

    chart_cols = st.columns(2)
    with chart_cols[0]:
        city_chart = st.session_state.visualizer.create_chart(
            df=summary["top_seller_cities"], chart_type="horizontal_bar", question="Top seller cities by orders"
        )
        if city_chart is not None:
            city_chart.update_layout(title="Top seller cities by orders", height=320)
            st.plotly_chart(city_chart, use_container_width=True)

    with chart_cols[1]:
        category_chart = st.session_state.visualizer.create_chart(
            df=summary["top_categories"], chart_type="horizontal_bar", question="Top categories by revenue"
        )
        if category_chart is not None:
            category_chart.update_layout(title="Top product categories by revenue", height=320)
            st.plotly_chart(category_chart, use_container_width=True)


def render_response(response) -> None:
    """Render an AnalyticsResponse: answer, chart, metadata, SQL, and data table."""
    st.markdown(response.answer)

    if response.chart is not None:
        st.plotly_chart(response.chart, use_container_width=True)

    meta_parts = []
    if response.execution_time > 0:
        meta_parts.append(f"⏱️ {response.execution_time}s")
    if response.row_count > 0:
        meta_parts.append(f"📊 {response.row_count:,} rows")
    if response.attempts > 1:
        meta_parts.append(f"🔄 {response.attempts} attempts")

    if meta_parts:
        chips_html = "".join(f'<span class="meta-chip">{p}</span>' for p in meta_parts)
        st.markdown(f'<div class="query-meta">{chips_html}</div>', unsafe_allow_html=True)

    if response.sql:
        with st.expander("🔍 View SQL", expanded=False):
            st.code(response.sql, language="sql")

    if response.dataframe is not None and not response.dataframe.empty:
        with st.expander(f"📋 View Data ({response.row_count} rows)", expanded=False):
            st.dataframe(
                response.dataframe,
                use_container_width=True,
                height=min(400, 35 * len(response.dataframe) + 38),
            )


# ─── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### 🔌 Connection")

    # ClickHouse status
    ch_healthy, ch_message = get_connection_status()
    if ch_healthy:
        st.markdown(
            '<div class="status-badge status-connected">● ClickHouse Connected</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="status-badge status-disconnected">● {ch_message}</div>',
            unsafe_allow_html=True,
        )

    # LLM status
    llm_ok, llm_message = check_llm_configured()
    if llm_ok:
        st.markdown(
            f'<div class="status-badge status-connected">● {llm_message}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="status-badge status-disconnected">● {llm_message}</div>',
            unsafe_allow_html=True,
        )
        st.info(
            "Set `LLM_API_KEY` in your `.env` file to enable AI features.",
            icon="🔑",
        )

    st.markdown("---")

    # What's in this data — a plain-language stand-in for a schema browser
    st.markdown("### 📦 What's in this data")

    if ch_healthy:
        try:
            schema = st.session_state.schema_inspector.get_schema()
            total_rows = sum(t.row_count for t in schema.tables.values())
            st.caption(f"{total_rows:,} records across {len(schema.tables)} datasets")

            for table_name, table_info in sorted(
                schema.tables.items(), key=lambda kv: -kv[1].row_count
            ):
                emoji, label = FRIENDLY_TABLE_LABELS.get(table_name, ("📄", table_name.replace("_", " ").title()))
                st.markdown(
                    f'<div class="data-overview-row">'
                    f'<span class="data-overview-label">{emoji} {label}</span>'
                    f'<span class="data-overview-count">{table_info.row_count:,}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        except Exception as e:
            st.error(f"Couldn't load data overview: {e}")
    else:
        st.caption("Connect to the database to see what's available")

    st.markdown("---")

    # Clear conversation
    if st.button("🗑️ Clear Conversation", use_container_width=True):
        st.session_state.messages = []
        st.session_state.conversation.clear()
        st.rerun()


# ─── Main Content ──────────────────────────────────────────────────────────────

# Header
st.markdown("""
<div class="main-header">
    <h1>🛍️ E-commerce Sales Analytics</h1>
    <p>Ask questions about your sales data in plain English — no SQL required</p>
</div>
""", unsafe_allow_html=True)

# Order summary dashboard + suggested questions, shown before any question is asked
if not st.session_state.messages:
    if ch_healthy:
        render_order_summary_dashboard()
        st.markdown("---")

    try:
        suggestions = st.session_state.agent.get_suggested_questions()
        if suggestions:
            st.markdown("##### 💡 Try asking:")
            cols = st.columns(2)
            for i, suggestion in enumerate(suggestions[:8]):
                col = cols[i % 2]
                with col:
                    if st.button(
                        f"→ {suggestion}",
                        key=f"suggestion_{i}",
                        use_container_width=True,
                    ):
                        st.session_state.pending_question = suggestion
                        st.rerun()
    except Exception:
        pass  # Schema not loaded yet

# Process pending suggestion
if "pending_question" in st.session_state:
    pending = st.session_state.pending_question
    del st.session_state.pending_question
    st.session_state.messages.append({"role": "user", "content": pending})

    with st.chat_message("user"):
        st.write(pending)

    with st.chat_message("assistant"):
        with st.spinner("Analyzing..."):
            response = st.session_state.agent.process_question(pending)
        render_response(response)

    # Store the response
    st.session_state.messages.append({
        "role": "assistant",
        "content": response.answer,
        "response": response,
    })
    st.rerun()

# Display chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg["role"] == "user":
            st.write(msg["content"])
        else:
            response = msg.get("response")
            if response:
                render_response(response)
            else:
                st.write(msg["content"])

# Chat input
if prompt := st.chat_input("Ask a question about your data..."):
    # Check prerequisites
    if not ch_healthy:
        st.error("Cannot process questions — ClickHouse is not connected.")
    elif not llm_ok:
        st.error("Cannot process questions — LLM API key is not configured. Set `LLM_API_KEY` in your `.env` file.")
    else:
        # Add user message
        st.session_state.messages.append({"role": "user", "content": prompt})

        with st.chat_message("user"):
            st.write(prompt)

        # Process with the agent
        with st.chat_message("assistant"):
            with st.spinner("🔍 Analyzing your question..."):
                response = st.session_state.agent.process_question(prompt)

            render_response(response)

        # Store the response
        st.session_state.messages.append({
            "role": "assistant",
            "content": response.answer,
            "response": response,
        })
        st.rerun()
