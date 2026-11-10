"""Streamlit Web UI for Sales Performance Advisory Agent.

Features:
- Live KPI Dashboard in sidebar (Achievement %, MoM Growth, Margin, Return Rate).
- Interactive conversational chat with LangGraph agent.
- SQLite-backed conversation memory across turns.
- Guardrail warning alerts for discount violations.
- Action plan management with CSV export.
"""

import sys
from pathlib import Path
import pandas as pd
import streamlit as st
from langchain_core.messages import HumanMessage, AIMessage

# Ensure workspace root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent import get_agent_with_checkpointer
from src.metrics import (
    load_margins_data,
    load_sales_data,
    load_targets_data,
    get_record_metrics,
)
from src.action_store import ActionStore
from src.guardrails import extract_playbook_discount_limit

# Page Configuration
st.set_page_config(
    page_title="Sales Performance Advisory Agent",
    page_icon="📊",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Session State Initialization
# ---------------------------------------------------------------------------

if "thread_id" not in st.session_state:
    import uuid
    st.session_state.thread_id = f"web-session-{uuid.uuid4().hex[:8]}"

if "chat_history" not in st.session_state:
    st.session_state.chat_history = [
        {
            "role": "assistant",
            "content": "👋 Welcome to the **Sales Performance Advisory Agent**! I can help you analyze quota performance, consult the sales playbook, verify discount guardrails, and log action plans.",
        }
    ]

# ---------------------------------------------------------------------------
# Sidebar: Context Filters & Real-Time KPI Cards
# ---------------------------------------------------------------------------

st.sidebar.title("📊 Sales Controls & KPIs")

try:
    sales_df = load_sales_data("data/sales.csv")
    targets_df = load_targets_data("data/targets.csv")
    margins_df = load_margins_data("data/margins.xlsx")

    regions = sorted(list(sales_df["region"].unique()))
    products = sorted(list(sales_df["product"].unique()))
    months = sorted(list(sales_df["month"].unique()), reverse=True)

    selected_region = st.sidebar.selectbox("Region", regions, index=0)
    selected_product = st.sidebar.selectbox("Product", products, index=0)
    selected_month = st.sidebar.selectbox("Month", months, index=0)

    # Compute deterministic metrics for selected slice
    try:
        metrics = get_record_metrics(
            selected_region, selected_product, selected_month, sales_df, targets_df, margins_df
        )

        st.sidebar.markdown("### 📈 Live KPI Performance")
        col1, col2 = st.sidebar.columns(2)
        with col1:
            achv = metrics["target_achievement_pct"]
            achv_val = f"{achv}%" if achv is not None else "N/A"
            col1.metric("Achievement", achv_val, delta=f"{round(achv - 100, 1)}%" if achv else None)

        with col2:
            growth = metrics["sales_growth_pct"]
            growth_val = f"{growth}%" if growth is not None else "N/A (First mo)"
            col2.metric("MoM Growth", growth_val, delta=f"{growth}%" if growth else None)

        col3, col4 = st.sidebar.columns(2)
        with col3:
            margin = metrics["gross_margin"]
            m_pct = metrics["margin_rate_pct"]
            col3.metric("Gross Margin", f"₹{margin:,.0f}" if margin else "N/A", f"{m_pct}%" if m_pct else None)

        with col4:
            ret_rate = metrics["return_rate_pct"]
            ret_val = f"{ret_rate}%" if ret_rate is not None else "N/A"
            # Color red if high returns > 5%
            col4.metric("Return Rate", ret_val, delta="-High" if ret_rate and ret_rate > 5 else "OK", delta_color="inverse")

    except KeyError:
        st.sidebar.warning("No sales record for this specific slice.")

except Exception as err:
    st.sidebar.error(f"Error loading sales data: {err}")

# Discount Policy Baseline indicator
standard_limit = extract_playbook_discount_limit()
st.sidebar.info(f"🛡️ **Playbook Discount Limit**: **{standard_limit}%** (Requests above require Director approval).")

# Action Routing Strategy Guide
st.sidebar.markdown("### 🧭 Governance Routing Branches")
st.sidebar.markdown(
    """
- 📉 **Low Revenue**: Demand Actions (Pipeline, bundling, push campaigns)
- 💰 **Low Margin**: Cost Actions (Halt discounts, attach accessories)
- 📦 **High Returns**: Quality Actions (Quarantine batches, QA review)
*Note: Correlation observed; avoid unsupported causal claims.*
"""
)

# Active Campaigns Viewer
try:
    from src.metrics import load_campaigns_data
    camp_df = load_campaigns_data("data/campaigns.xlsx")
    with st.sidebar.expander("📢 Active Push Campaigns"):
        st.dataframe(camp_df[["campaign_name", "region", "product", "budget", "status"]], use_container_width=True)
except Exception:
    pass

# Action Plans Ledger in Sidebar
st.sidebar.markdown("---")
st.sidebar.markdown("### 📋 Action Plan Ledger")
store = ActionStore()
plans = store.list_plans()

if plans:
    plans_df = pd.DataFrame(plans)[["plan_id", "region", "product", "month", "issue_type", "approval_status", "owner"]]
    st.sidebar.dataframe(plans_df, use_container_width=True)
    csv_data = pd.DataFrame(plans).to_csv(index=False).encode("utf-8")
    st.sidebar.download_button("📥 Export Plans (CSV)", csv_data, "action_plans.csv", "text/csv")
else:
    st.sidebar.caption("No action plans logged yet.")

if st.sidebar.button("🔄 Reset Conversation"):
    import uuid
    st.session_state.thread_id = f"web-session-{uuid.uuid4().hex[:8]}"
    st.session_state.chat_history = [
        {
            "role": "assistant",
            "content": "Conversation reset. How can I assist you with sales performance or playbook rules?",
        }
    ]
    st.rerun()

# ---------------------------------------------------------------------------
# Main Chat Area
# ---------------------------------------------------------------------------

st.title("💼 Sales Performance Advisory Agent")
st.caption(f"Session Thread: `{st.session_state.thread_id}` | Powered by LangGraph & Scikit-Learn TF-IDF")

# Render existing chat messages
for msg in st.session_state.chat_history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# User Chat Input
user_query = st.chat_input("Ask a question (e.g. 'Why did South Tab-X miss target?', 'What about last month?', 'Can I give a 15% discount?')")

if user_query:
    # Append & display user message
    st.session_state.chat_history.append({"role": "user", "content": user_query})
    with st.chat_message("user"):
        st.markdown(user_query)

    # Invoke Agent with SqliteSaver Checkpointer
    with st.chat_message("assistant"):
        with st.spinner("Analyzing metrics and consulting sales playbook..."):
            with get_agent_with_checkpointer("checkpoints/agent_memory.db") as agent:
                config = {"configurable": {"thread_id": st.session_state.thread_id}}
                result = agent.invoke(
                    {"messages": [HumanMessage(content=user_query)]},
                    config=config,
                )
                assistant_response = result["messages"][-1].content

                # Check if guardrail block occurred to display visual alert
                if "POLICY GUARDRAIL" in assistant_response:
                    st.warning("⚠️ **Policy Guardrail Alert Triggered**: Unauthorized discount proposal.")

                st.markdown(assistant_response)

    st.session_state.chat_history.append({"role": "assistant", "content": assistant_response})
    st.rerun()
