"""Test suite for structured tool calling, guardrails, and action plan persistence."""

import pytest
from src.tools import (
    get_sales_metrics,
    search_playbook,
    check_discount,
    save_action_plan,
    GetSalesMetricsInput,
    SearchPlaybookInput,
    CheckDiscountInput,
    SaveActionPlanInput,
)
from src.guardrails import (
    extract_playbook_discount_limit,
    validate_discount_policy,
)
from src.action_store import ActionStore


# ---------------------------------------------------------------------------
# Test Guardrails & Dynamic Discount Extraction
# ---------------------------------------------------------------------------

def test_extract_playbook_discount_limit_from_text():
    # Verify extraction from snippet
    text = "Sales reps can provide standard commercial discount of up to 12% on all deals."
    limit = extract_playbook_discount_limit(text)
    assert limit == 12.0


def test_extract_playbook_discount_limit_from_default_playbook():
    # Reading from docs/sales_playbook.txt
    limit = extract_playbook_discount_limit()
    assert limit == 10.0


def test_validate_discount_within_standard_limit():
    result = validate_discount_policy(discount_pct=8.0, max_standard_limit=10.0)
    assert result["status"] == "APPROVED"
    assert result["is_authorized"] is True
    assert result["requires_approval"] is False


def test_validate_discount_exceeding_without_approval_blocked():
    result = validate_discount_policy(
        discount_pct=15.0,
        max_standard_limit=10.0,
        has_management_approval=False,
    )
    assert result["status"] == "BLOCKED"
    assert result["is_authorized"] is False
    assert result["requires_approval"] is True
    assert "POLICY GUARDRAIL" in result["message"]


def test_validate_discount_exceeding_with_approval_authorized():
    result = validate_discount_policy(
        discount_pct=15.0,
        max_standard_limit=10.0,
        has_management_approval=True,
    )
    assert result["status"] == "APPROVED_WITH_EXCEPTION"
    assert result["is_authorized"] is True
    assert result["requires_approval"] is True


def test_validate_discount_invalid_range():
    with pytest.raises(ValueError, match="between 0 and 100"):
        validate_discount_policy(discount_pct=-5.0)

    with pytest.raises(ValueError, match="between 0 and 100"):
        validate_discount_policy(discount_pct=120.0)


# ---------------------------------------------------------------------------
# Test Action Store
# ---------------------------------------------------------------------------

def test_action_store_persistence(tmp_path):
    store_file = tmp_path / "test_plans.json"
    store = ActionStore(storage_path=store_file)

    plan = store.save_plan(
        issue_type="LOW_ACHIEVEMENT",
        region="South",
        product="Tab-X",
        month="2026-09",
        proposed_action="Offer product bundling with extended warranty.",
        discount_pct=10.0,
        approval_status="STANDARD",
        owner="Regional Lead",
    )
    assert plan["plan_id"].startswith("AP-")
    assert plan["region"] == "South"

    # Verify listing and retrieval
    listed = store.list_plans(region="South")
    assert len(listed) == 1
    assert listed[0]["plan_id"] == plan["plan_id"]

    retrieved = store.get_plan(plan["plan_id"])
    assert retrieved is not None
    assert retrieved["proposed_action"] == "Offer product bundling with extended warranty."


# ---------------------------------------------------------------------------
# Test Structured Tool Invocation
# ---------------------------------------------------------------------------

def test_get_sales_metrics_tool_success():
    # Valid input model schema
    inp = GetSalesMetricsInput(region="South", product="Tab-X", month="2026-09")
    res = get_sales_metrics.invoke(inp.model_dump())

    assert res["status"] == "SUCCESS"
    metrics = res["metrics"]
    assert metrics["region"] == "South"
    assert metrics["product"] == "Tab-X"
    assert metrics["revenue"] == 960000.0
    assert metrics["target_achievement_pct"] == 87.27
    assert metrics["sales_growth_pct"] == -20.0
    assert metrics["gross_margin"] == 192000.0
    assert metrics["return_rate_pct"] == 8.75


def test_get_sales_metrics_tool_missing_slice():
    inp = GetSalesMetricsInput(region="East", product="Tab-Z", month="2026-09")
    res = get_sales_metrics.invoke(inp.model_dump())
    assert res["status"] == "ERROR"
    assert "No sales data found" in res["message"]


def test_search_playbook_tool():
    inp = SearchPlaybookInput(
        query="What is the allowed discount on Tab-X?",
        problem_type="discount",
        target_product="Tab-X",
    )
    res = search_playbook.invoke(inp.model_dump())
    assert res["status"] == "FOUND"
    assert len(res["chunks"]) > 0


def test_check_discount_tool_guardrail():
    # Requesting 15% discount without approval
    inp = CheckDiscountInput(discount_pct=15.0, product="Tab-X", has_management_approval=False)
    res = check_discount.invoke(inp.model_dump())
    assert res["status"] == "BLOCKED"
    assert res["is_authorized"] is False


def test_save_action_plan_tool(tmp_path):
    inp = SaveActionPlanInput(
        issue_type="HIGH_RETURNS",
        region="South",
        product="Tab-X",
        month="2026-09",
        proposed_action="Quarantine hardware batch and provide customer integration training.",
        discount_pct=None,
        approval_status="STANDARD",
        owner="QA Team",
    )
    res = save_action_plan.invoke(inp.model_dump())
    assert res["status"] == "SAVED"
    assert res["plan"]["issue_type"] == "HIGH_RETURNS"
    assert res["plan"]["owner"] == "QA Team"
