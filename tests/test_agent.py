"""Test suite for LangGraph Agent state, memory, SqliteSaver lifecycle, and follow-ups."""

from pathlib import Path
import pytest
from langchain_core.messages import HumanMessage, AIMessage
from src.agent import (
    calculate_relative_month,
    resolve_context,
    get_agent_with_checkpointer,
    build_sales_advisory_graph,
)


# ---------------------------------------------------------------------------
# Test Relative Month Calculation & Follow-up Parsing
# ---------------------------------------------------------------------------

def test_calculate_relative_month():
    assert calculate_relative_month("2026-09", offset=-1) == "2026-08"
    assert calculate_relative_month("2026-01", offset=-1) == "2025-12"
    assert calculate_relative_month("2026-08", offset=1) == "2026-09"


def test_resolve_context_initial_query():
    state = {"region": None, "product": None, "month": None}
    resolved = resolve_context("How is Tab-X doing in the South for 2026-09?", state)
    assert resolved["region"] == "South"
    assert resolved["product"] == "Tab-X"
    assert resolved["month"] == "2026-09"


def test_resolve_context_followup_last_month():
    # Previous state had South, Tab-X, 2026-09
    state = {"region": "South", "product": "Tab-X", "month": "2026-09"}

    # User asks "What about last month?"
    resolved = resolve_context("What about last month?", state)

    # Month MUST be updated to 2026-08
    assert resolved["month"] == "2026-08"
    # Region and Product MUST STAY THE SAME
    assert resolved["region"] == "South"
    assert resolved["product"] == "Tab-X"


def test_resolve_context_followup_different_region():
    state = {"region": "South", "product": "Tab-X", "month": "2026-09"}

    # User asks "What about North region?"
    resolved = resolve_context("What about North?", state)

    # Region updates to North
    assert resolved["region"] == "North"
    # Product and month STAY THE SAME
    assert resolved["product"] == "Tab-X"
    assert resolved["month"] == "2026-09"


def test_resolve_context_followup_different_product():
    state = {"region": "West", "product": "Tab-Y", "month": "2026-09"}

    # User asks "What about Tab-X?"
    resolved = resolve_context("What about Tab-X?", state)

    assert resolved["product"] == "Tab-X"
    assert resolved["region"] == "West"
    assert resolved["month"] == "2026-09"


# ---------------------------------------------------------------------------
# Test SqliteSaver Connection Lifecycle & Multi-turn Memory
# ---------------------------------------------------------------------------

def test_sqlite_saver_context_lifecycle_and_turn_persistence(tmp_path):
    db_file = tmp_path / "test_memory.db"
    thread_id = "test-session-123"
    config = {"configurable": {"thread_id": thread_id}}

    with get_agent_with_checkpointer(db_path=db_file) as agent:
        # Turn 1: Analyze South, Tab-X, 2026-09
        turn1 = agent.invoke(
            {"messages": [HumanMessage(content="Analyze performance for Tab-X in South for 2026-09.")]},
            config=config,
        )
        assert turn1["region"] == "South"
        assert turn1["product"] == "Tab-X"
        assert turn1["month"] == "2026-09"
        assert turn1["metrics"] is not None
        assert turn1["metrics"]["revenue"] == 960000.0
        assert turn1["metrics"]["sales_growth_pct"] == -20.0

        # Turn 2: Follow-up "What about last month?"
        turn2 = agent.invoke(
            {"messages": [HumanMessage(content="What about last month?")]},
            config=config,
        )

        # Region & Product preserved from Turn 1, Month updated to 2026-08
        assert turn2["region"] == "South"
        assert turn2["product"] == "Tab-X"
        assert turn2["month"] == "2026-08"

        # Check First-Month Edge Case for 2026-08
        metrics = turn2["metrics"]
        assert metrics["revenue"] == 1200000.0
        assert metrics["is_first_month"] is True
        assert metrics["sales_growth_pct"] is None

        # Verify conversation history has multiple messages
        assert len(turn2["messages"]) >= 4


def test_sqlite_saver_independent_threads(tmp_path):
    db_file = tmp_path / "test_independent.db"

    with get_agent_with_checkpointer(db_path=db_file) as agent:
        # Thread A: North Tab-X 2026-09
        agent.invoke(
            {"messages": [HumanMessage(content="Analyze Tab-X in North for 2026-09.")]},
            config={"configurable": {"thread_id": "thread-A"}},
        )

        # Thread B: West Tab-Y 2026-09
        agent.invoke(
            {"messages": [HumanMessage(content="Analyze Tab-Y in West for 2026-09.")]},
            config={"configurable": {"thread_id": "thread-B"}},
        )

        # Retrieve Thread A state
        state_a = agent.get_state(config={"configurable": {"thread_id": "thread-A"}})
        assert state_a.values["region"] == "North"
        assert state_a.values["product"] == "Tab-X"

        # Retrieve Thread B state
        state_b = agent.get_state(config={"configurable": {"thread_id": "thread-B"}})
        assert state_b.values["region"] == "West"
        assert state_b.values["product"] == "Tab-Y"
