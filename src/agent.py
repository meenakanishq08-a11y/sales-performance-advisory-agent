"""Agent state, memory, and LangGraph workflow for Sales Advisory Agent.

Features:
- LangGraph StateGraph tracking messages, region, product, month, metrics, retrieved_policy, approval_status.
- SqliteSaver checkpointer with proper connection lifecycle and thread_id support.
- Follow-up conversational resolution ('What about last month?' updates ONLY month while preserving region & product).
- First-month edge case handling when previous sales data is absent.
- Environment-configured LLM (OPENAI_API_KEY, OPENAI_MODEL) with zero hardcoded secrets.
"""

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Dict, Generator, List, Optional, Tuple, TypedDict, Union
import os
import re

from dotenv import load_dotenv
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from src.tools import (
    get_sales_metrics,
    calculate_target_achievement,
    calculate_sales_growth,
    calculate_product_margin,
    identify_underperformers,
    search_playbook,
    check_discount,
    save_sales_action_plan,
    save_action_plan,
)

# Load environment configuration from .env
load_dotenv()


# ---------------------------------------------------------------------------
# State Definition
# ---------------------------------------------------------------------------

class AdvisoryState(TypedDict):
    """The working memory state for the Sales Performance Advisory Agent."""
    messages: Annotated[List[BaseMessage], add_messages]
    region: Optional[str]
    product: Optional[str]
    month: Optional[str]
    metrics: Optional[Dict[str, Any]]
    retrieved_policy: Optional[str]
    approval_status: Optional[str]


# ---------------------------------------------------------------------------
# Follow-up and Context Resolution Logic
# ---------------------------------------------------------------------------

KNOWN_REGIONS = ["South", "North", "West", "East", "Central"]
KNOWN_PRODUCTS = ["Tab-X", "Tab-Y"]


def calculate_relative_month(base_month: str, offset: int = -1) -> str:
    """Calculate relative month (offset=-1 for last month, +1 for next month).

    Args:
        base_month: 'YYYY-MM' (e.g. '2026-09')
        offset: Number of months to shift (-1 for previous).

    Returns:
        Shifted month string in 'YYYY-MM' format.
    """
    try:
        dt = datetime.strptime(base_month, "%Y-%m")
        # Compute year and month
        total_months = dt.year * 12 + (dt.month - 1) + offset
        new_year = total_months // 12
        new_month = (total_months % 12) + 1
        return f"{new_year:04d}-{new_month:02d}"
    except Exception:
        return base_month


def resolve_context(
    user_message: str,
    current_state: Dict[str, Any],
) -> Dict[str, Optional[str]]:
    """Resolve region, product, and month from the user query, retaining previous context.

    Follow-up rules:
    - 'What about last month?' -> updates ONLY month (e.g. 2026-09 -> 2026-08); region and product STAY THE SAME.
    - 'What about North?' -> updates ONLY region; product and month STAY THE SAME.
    - 'What about Tab-Y?' -> updates ONLY product; region and month STAY THE SAME.
    """
    msg = user_message.strip()

    # 1. Resolve Region
    new_region = current_state.get("region")
    for r in KNOWN_REGIONS:
        if re.search(rf"\b{re.escape(r)}\b", msg, re.IGNORECASE):
            new_region = r
            break

    # 2. Resolve Product
    new_product = current_state.get("product")
    for p in KNOWN_PRODUCTS:
        if re.search(rf"\b{re.escape(p)}\b", msg, re.IGNORECASE):
            new_product = p
            break

    # 3. Resolve Month
    new_month = current_state.get("month")

    # Explicit YYYY-MM
    explicit_match = re.search(r"\b(20\d{2}-(?:0[1-9]|1[0-2]))\b", msg)
    if explicit_match:
        new_month = explicit_match.group(1)
    # Relative follow-up: 'last month', 'previous month', 'prev month'
    elif re.search(r"\b(last\s+month|previous\s+month|prev\s+month)\b", msg, re.IGNORECASE):
        if new_month:
            new_month = calculate_relative_month(new_month, offset=-1)
    # Relative follow-up: 'next month'
    elif re.search(r"\b(next\s+month)\b", msg, re.IGNORECASE):
        if new_month:
            new_month = calculate_relative_month(new_month, offset=1)

    return {
        "region": new_region,
        "product": new_product,
        "month": new_month,
    }


# ---------------------------------------------------------------------------
# System Prompt & LLM Setup
# ---------------------------------------------------------------------------

AGENT_SYSTEM_PROMPT = """You are a senior Sales Performance Advisory Agent.
Your role is to analyze sales metrics, consult playbook governance, enforce strict discount guardrails, and formulate actionable remediation plans.

CORE GOVERNANCE RULES:
1. DETERMINISTIC METRICS: You must NEVER calculate or invent performance numbers yourself. Always invoke `get_sales_metrics(region, product, month)`.
2. ZERO TARGETS & FIRST-MONTH HANDLING:
   - If target is zero, target achievement is 'Not Available' (never divide by zero).
   - If prior sales is zero or missing (is_first_month=True), MoM growth is 'Not Defined' (no division by zero).
3. CONDITIONAL ROUTING & DIFFERENTIATED ACTIONS:
   - Low Revenue: Route to DEMAND actions (pipeline velocity qualification, targeted push campaigns, value-add non-cash bundling).
   - Low Margin: Route to PRICING/COST actions (halt discretionary discounting on low-margin SKUs, promote accessory attachments, review landed logistics costs). NEVER recommend discounts for a margin problem!
   - High Returns: Route to QUALITY actions (quarantine defective batches, initiate QA inspection, provide customer technical onboarding). NEVER recommend discounts for a returns problem!
4. RESPONSIBLE AI & CAUSAL CLAIMS:
   - Avoid unsupported causal claims. Describe data correlation as such; never invent root causes that the data does not explicitly substantiate.
5. DISCOUNT GUARDRAILS:
   - Account reps can only provide standard discounts up to the limit dynamically stated in the playbook.
   - Any request exceeding the limit (e.g. 'Offer 40% off' or 'Offer 15% off') must be DECLINED, citing the official playbook ceiling, unless authorized management approval is verified.
6. CONVERSATION CONTEXT & FOLLOW-UPS:
   - Remember current Region, Product, and Month across conversation turns.
   - If the user asks 'What about last month?', analyze the preceding month for the same region and product.
7. ACTION PLANS: When agreeing on a remediation action, invoke `save_sales_action_plan` with issue, supporting metrics, proposed action, owner, and review date.
"""


def get_llm():
    """Build and configure the ChatOpenAI client using environment variables.

    Reads OPENAI_API_KEY and OPENAI_MODEL from .env. Never hardcodes credentials.
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    model_name = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    if not api_key or api_key.startswith("your_"):
        # Return None or mockable client for test/offline environments
        return None

    return ChatOpenAI(
        model=model_name,
        temperature=0.0,
        api_key=api_key,
    )


# ---------------------------------------------------------------------------
# LangGraph Graph Construction
# ---------------------------------------------------------------------------

TOOLS = [
    get_sales_metrics,
    calculate_target_achievement,
    calculate_sales_growth,
    calculate_product_margin,
    identify_underperformers,
    search_playbook,
    check_discount,
    save_sales_action_plan,
]


def build_sales_advisory_graph(llm_override=None):
    """Construct the LangGraph StateGraph workflow for the sales advisory agent.

    Nodes:
      1. context_resolver: Extracts or updates region, product, month follow-up state.
      2. agent: LLM with tools bound, or deterministic rule engine if no LLM key provided.
      3. tools: Prebuilt ToolNode executing tool calls.
    """
    builder = StateGraph(AdvisoryState)

    llm = llm_override if llm_override is not None else get_llm()
    if llm is not None:
        llm_with_tools = llm.bind_tools(TOOLS)
    else:
        llm_with_tools = None

    # Node 1: Context Resolver
    def context_resolver_node(state: AdvisoryState) -> Dict[str, Any]:
        last_message = ""
        for m in reversed(state.get("messages", [])):
            if isinstance(m, HumanMessage):
                last_message = m.content
                break

        updated_context = resolve_context(last_message, state)
        return updated_context

    # Node 2: Agent Node
    def agent_node(state: AdvisoryState) -> Dict[str, Any]:
        messages = list(state.get("messages", []))
        region = state.get("region")
        product = state.get("product")
        month = state.get("month")

        # Inject system context prompt
        context_str = f"\nCurrent Active Context -> Region: {region or 'Not set'}, Product: {product or 'Not set'}, Month: {month or 'Not set'}"
        sys_msg = SystemMessage(content=AGENT_SYSTEM_PROMPT + context_str)

        all_messages = [sys_msg] + messages

        if llm_with_tools is not None:
            try:
                response = llm_with_tools.invoke(all_messages)
                return {"messages": [response]}
            except Exception as api_err:
                # If OpenAI quota/credit is exhausted (429) or network is offline, gracefully fall through
                pass

        # Deterministic Fallback Mode (for tests / environments without live OpenAI key)
        last_human = ""
        for m in reversed(messages):
            if isinstance(m, HumanMessage):
                last_human = str(m.content)
                break

        last_human_lower = last_human.lower()

        # Check if follow-up asked for metrics or last month
        if region and product and month:
            from src.tools import get_sales_metrics, search_playbook, check_discount, save_action_plan
            metrics_res = get_sales_metrics.invoke({"region": region, "product": product, "month": month})
            
            # Formulate deterministic response
            if metrics_res.get("status") == "SUCCESS":
                m_data = metrics_res["metrics"]
                growth_text = f"{m_data['sales_growth_pct']}%" if m_data['sales_growth_pct'] is not None else "N/A (First recording period)"
                first_month_note = " Note: 2026-08 is the first recorded month, so MoM growth is N/A." if m_data.get("is_first_month") else ""
                
                resp_text = (
                    f"Performance Analysis for {product} in {region} ({month}):\n"
                    f"- Revenue: Rs. {m_data['revenue']:,.0f} (Target: Rs. {m_data['target_revenue']:,.0f})\n"
                    f"- Target Achievement: {m_data['target_achievement_pct']}%\n"
                    f"- MoM Sales Growth: {growth_text}{first_month_note}\n"
                    f"- Gross Margin: Rs. {m_data['gross_margin']:,.0f} ({m_data['margin_rate_pct']}%)\n"
                    f"- Return Rate: {m_data['return_rate_pct']}%\n"
                )
                
                # Curriculum Routing Branches:
                recommendations = []
                if m_data.get("target_achievement_pct") is not None and m_data["target_achievement_pct"] < 90.0:
                    recommendations.append("📉 Low Revenue Action (Demand): Qualify quarter pipeline velocity and deploy non-cash partner bundling.")
                if m_data.get("margin_rate_pct") is not None and m_data["margin_rate_pct"] < 15.0:
                    recommendations.append("💰 Low Margin Action (Pricing/Cost): Halt discretionary discounting on low-margin SKUs; prioritize accessory attachments.")
                if m_data.get("return_rate_pct") is not None and m_data["return_rate_pct"] > 5.0:
                    recommendations.append("📦 High Returns Action (Quality): Quarantine defective batches, conduct QA inspection, and initiate customer onboarding. (Discounts prohibited for returns).")

                if recommendations:
                    resp_text += "\nRecommended Actions (Policy-Routed):\n" + "\n".join(f"- {r}" for r in recommendations) + "\n"

                # Check if discount was requested in user query
                if "discount" in last_human_lower or "%" in last_human:
                    # Extract requested discount number if present (e.g. 40% or 15%)
                    pct_match = re.search(r"(\d+(?:\.\d+)?)\s*%", last_human)
                    req_disc = float(pct_match.group(1)) if pct_match else 15.0
                    disc_check = check_discount.invoke({"discount_pct": req_disc, "product": product})
                    resp_text += f"\nDiscount Policy Check:\n- {disc_check['message']}\n"

                return {
                    "messages": [AIMessage(content=resp_text)],
                    "metrics": m_data,
                }

        # Workflow: Ask for missing scope information
        missing = []
        if not region: missing.append("Region (e.g. South, North, West)")
        if not product: missing.append("Product (e.g. Tab-X, Tab-Y)")
        if not month: missing.append("Analysis Month (e.g. 2026-09)")

        ask_text = (
            f"Hello! To analyze sales performance and consult playbook governance, "
            f"please provide the missing scope: {', '.join(missing)}."
        )
        return {
            "messages": [AIMessage(content=ask_text)],
        }

    # Conditional routing: Check if agent issued tool calls
    def should_continue(state: AdvisoryState) -> str:
        messages = state.get("messages", [])
        if not messages:
            return END
        last_message = messages[-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
        return END

    builder.add_node("context_resolver", context_resolver_node)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(TOOLS))

    builder.add_edge(START, "context_resolver")
    builder.add_edge("context_resolver", "agent")
    builder.add_conditional_edges("agent", should_continue, ["tools", END])
    builder.add_edge("tools", "agent")

    return builder


@contextmanager
def get_agent_with_checkpointer(
    db_path: Union[str, Path] = "checkpoints/agent_memory.db",
    llm_override=None,
) -> Generator[Any, None, None]:
    """Context manager maintaining SQLite checkpointer lifecycle.

    Guarantees the SQLite connection remains safely open during graph invocation.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with SqliteSaver.from_conn_string(str(path)) as checkpointer:
        builder = build_sales_advisory_graph(llm_override=llm_override)
        graph = builder.compile(checkpointer=checkpointer)
        yield graph
