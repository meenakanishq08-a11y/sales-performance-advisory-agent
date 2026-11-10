"""Interactive CLI chat loop for Sales Performance Advisory Agent.

Supports interactive chat, follow-up memory across turns, policy queries,
guardrail enforcement, and action plan persistence.
"""

import argparse
import sys
import uuid
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from langchain_core.messages import HumanMessage
from src.agent import get_agent_with_checkpointer
from src.metrics import (
    load_margins_data,
    load_sales_data,
    load_targets_data,
    identify_underperformers,
)
from src.action_store import ActionStore


BANNER = r"""
======================================================================
  SALES PERFORMANCE ADVISORY AGENT 📊🤖
  AI Advisory & Governance Engine (LangGraph + Guardrails + Memory)
======================================================================
Available commands:
  /metrics  - View underperforming slices flagged in current dataset
  /plans    - List saved action plans
  /reset    - Start a new conversation session
  exit/quit - Exit the advisory loop
----------------------------------------------------------------------
"""


def print_underperformers_table():
    """Print formatted summary of underperforming slices."""
    try:
        sales_df = load_sales_data("data/sales.csv")
        targets_df = load_targets_data("data/targets.csv")
        margins_df = load_margins_data("data/margins.xlsx")
        underperformers = identify_underperformers(sales_df, targets_df, margins_df)

        print("\n--- Identified Underperforming Slices ---")
        if not underperformers:
            print("No underperforming slices identified.")
            return

        for u in underperformers:
            print(f"\n[{u['region']} - {u['product']} - {u['month']}]")
            for flag in u["flags"]:
                print(f"  * {flag['issue']}: {flag['detail']}")
        print("-----------------------------------------\n")
    except Exception as err:
        print(f"Error loading underperformers: {err}")


def print_saved_plans():
    """Print all saved action plans."""
    store = ActionStore()
    plans = store.list_plans()
    print("\n--- Central Action Plan Ledger ---")
    if not plans:
        print("No action plans recorded yet.")
        return

    for p in plans:
        disc_text = f" | Disc: {p['discount_pct']}%" if p.get("discount_pct") is not None else ""
        print(f"[{p['plan_id']}] {p['region']} {p['product']} ({p['month']}) - Status: {p['approval_status']}{disc_text}")
        print(f"  Issue: {p['issue_type']} | Owner: {p['owner']}")
        print(f"  Action: {p['proposed_action']}")
        print("-" * 40)
    print("----------------------------------\n")


def run_cli_loop(thread_id: str = None, single_query: str = None):
    """Run interactive or single-query agent session."""
    if not thread_id:
        thread_id = f"cli-session-{uuid.uuid4().hex[:6]}"

    config = {"configurable": {"thread_id": thread_id}}

    if single_query:
        with get_agent_with_checkpointer("checkpoints/agent_memory.db") as agent:
            result = agent.invoke(
                {"messages": [HumanMessage(content=single_query)]},
                config=config,
            )
            last_msg = result["messages"][-1]
            print(last_msg.content)
            return

    print(BANNER)
    print(f"Session started with Thread ID: {thread_id}\n")

    with get_agent_with_checkpointer("checkpoints/agent_memory.db") as agent:
        while True:
            try:
                user_input = input("You > ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nExiting session. Goodbye!")
                break

            if not user_input:
                continue

            if user_input.lower() in ["exit", "quit", "q"]:
                print("Session terminated. Goodbye!")
                break

            if user_input.lower() == "/metrics":
                print_underperformers_table()
                continue

            if user_input.lower() == "/plans":
                print_saved_plans()
                continue

            if user_input.lower() == "/reset":
                thread_id = f"cli-session-{uuid.uuid4().hex[:6]}"
                config = {"configurable": {"thread_id": thread_id}}
                print(f"\nConversation reset! New Thread ID: {thread_id}\n")
                continue

            try:
                result = agent.invoke(
                    {"messages": [HumanMessage(content=user_input)]},
                    config=config,
                )
                last_msg = result["messages"][-1]
                print(f"\nAdvisor >\n{last_msg.content}\n")
            except Exception as err:
                print(f"\nError processing query: {err}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sales Advisory Agent CLI")
    parser.add_argument("--thread-id", type=str, default=None, help="Conversation thread ID")
    parser.add_argument("--query", type=str, default=None, help="Execute a single query and exit")
    args = parser.parse_args()

    run_cli_loop(thread_id=args.thread_id, single_query=args.query)
