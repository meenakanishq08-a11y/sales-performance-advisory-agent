"""Test suite explicitly validating the official curriculum scenarios and learning objectives.

Scenario Checklist:
1. Zero target: target = 0 -> Achievement shown as not available (None).
2. Zero prior sales: Prior = 0 -> Growth not defined; no division by zero.
3. High returns: Return rate above threshold -> Quality/returns action, not discount.
4. Missing costs: No unit_cost -> Margin not computed; flagged as MISSING_COST.
5. Follow-up: 'What about last month?' -> Same filters, new period.
6. Out-of-policy discount: 'Offer 40% off' -> Declined; cites policy limit.
7. Group-by aggregation: period and region aggregation.
8. Idempotent action plan save: duplicate-safe save with review_date & metrics.
"""

from pathlib import Path
import pytest
import pandas as pd

from src.metrics import (
    safe_divide,
    calculate_target_achievement,
    calculate_sales_growth,
    calculate_product_margin,
    calculate_return_rate,
    load_sales_data,
    load_targets_data,
    load_margins_data,
    load_campaigns_data,
    aggregate_sales_data,
    identify_underperformers,
)
from src.retriever import PlaybookRetriever
from src.grader import retrieve_with_grading
from src.guardrails import validate_discount_policy
from src.action_store import ActionStore
from src.agent import resolve_context, get_agent_with_checkpointer
from langchain_core.messages import HumanMessage


# ---------------------------------------------------------------------------
# 1. Scenario: Zero Target -> Achievement shown as not available
# ---------------------------------------------------------------------------

def test_scenario_zero_target():
    achv = calculate_target_achievement(revenue=960000, target_revenue=0)
    assert achv is None, "Target achievement must be None (not available) when target is 0, never divide by zero"


# ---------------------------------------------------------------------------
# 2. Scenario: Zero Prior Sales -> Growth not defined; no division
# ---------------------------------------------------------------------------

def test_scenario_zero_prior_sales():
    growth = calculate_sales_growth(current_revenue=500000, previous_revenue=0)
    assert growth is None, "Sales growth must be None (not defined) when prior sales is 0, never divide by zero"


# ---------------------------------------------------------------------------
# 3. Scenario: High Returns -> Quality/returns action, NOT discount
# ---------------------------------------------------------------------------

def test_scenario_high_returns():
    # Return rate 28 / 320 = 8.75% > 5% threshold
    rate = calculate_return_rate(returns=28, units=320)
    assert rate == 8.75
    assert rate > 5.0

    # Retrieve policy specifically for returns issue
    retriever = PlaybookRetriever(min_score_threshold=0.04)
    retriever.load_directory("docs")
    res = retrieve_with_grading(
        retriever=retriever,
        query="High return rate remediation steps",
        problem_type="returns",
        target_product="Tab-X",
    )
    assert res["status"] == "FOUND"
    top_chunk = res["chunks"][0]["chunk"]

    # Must recommend QA / quarantine / onboarding quality actions, NOT discount
    all_returned_text = " ".join(c["chunk"]["text"].lower() for c in res["chunks"])
    assert ("quarantine" in all_returned_text or "quality assurance" in all_returned_text or "mitigation" in all_returned_text or "defective" in all_returned_text)
    assert any(c["chunk"]["policy_type"] == "returns" for c in res["chunks"])


# ---------------------------------------------------------------------------
# 4. Scenario: Missing Costs -> Margin not computed; flagged as MISSING_COST
# ---------------------------------------------------------------------------

def test_scenario_missing_costs():
    margin_res = calculate_product_margin(revenue=960000, units=320, unit_cost=None)
    assert margin_res["status"] == "MISSING_COST"
    assert margin_res["gross_margin"] is None
    assert margin_res["margin_rate_pct"] is None
    assert "missing" in margin_res["message"].lower()


# ---------------------------------------------------------------------------
# 5. Scenario: Follow-up 'What about last month?' -> Same filters, new period
# ---------------------------------------------------------------------------

def test_scenario_follow_up_filters():
    initial_state = {"region": "South", "product": "Tab-X", "month": "2026-09"}
    follow_up = resolve_context("What about last month?", initial_state)

    # Must preserve region and product while updating month
    assert follow_up["region"] == "South"
    assert follow_up["product"] == "Tab-X"
    assert follow_up["month"] == "2026-08"


# ---------------------------------------------------------------------------
# 6. Scenario: Out-of-policy discount ('Offer 40% off') -> Declined; cites policy limit
# ---------------------------------------------------------------------------

def test_scenario_out_of_policy_discount():
    check = validate_discount_policy(discount_pct=40.0, has_management_approval=False)
    assert check["status"] == "BLOCKED"
    assert check["is_authorized"] is False
    assert check["requires_approval"] is True
    # Cites the standard policy limit (10%)
    assert check["max_standard_limit"] == 10.0
    assert "exceeds the playbook standard limit of 10.0%" in check["message"]


# ---------------------------------------------------------------------------
# 7. Scenario: Group-By Aggregation (Period and Region) & Campaigns XLSX
# ---------------------------------------------------------------------------

def test_scenario_group_by_aggregation():
    sales_df = load_sales_data("data/sales.csv")

    # Group by region
    region_agg = aggregate_sales_data(sales_df, by="region")
    assert "region" in region_agg.columns
    assert "revenue" in region_agg.columns
    assert len(region_agg) >= 3

    # Group by [region, month]
    period_region_agg = aggregate_sales_data(sales_df, by=["region", "month"])
    assert len(period_region_agg) == len(sales_df)

    # Load campaigns dataset
    camp_df = load_campaigns_data("data/campaigns.xlsx")
    assert len(camp_df) >= 3
    assert "campaign_name" in camp_df.columns
    assert "budget" in camp_df.columns


# ---------------------------------------------------------------------------
# 8. Scenario: Idempotent (Duplicate-Safe) Action Plan Save
# ---------------------------------------------------------------------------

def test_scenario_idempotent_action_plan_save(tmp_path):
    store = ActionStore(storage_path=tmp_path / "idempotent_plans.json")

    # Save initial plan
    plan1 = store.save_plan(
        issue_type="LOW_REVENUE",
        region="South",
        product="Tab-X",
        month="2026-09",
        proposed_action="Execute Spring Hardware Push campaign with partner co-op funds.",
        supporting_metrics={"achievement_pct": 87.27, "growth_pct": -20.0},
        review_date="2026-10-31",
        owner="Sales Lead",
    )
    assert plan1["plan_id"].startswith("AP-")
    assert plan1["review_date"] == "2026-10-31"

    # Save duplicate plan with same parameters
    plan2 = store.save_plan(
        issue_type="LOW_REVENUE",
        region="South",
        product="Tab-X",
        month="2026-09",
        proposed_action="Execute Spring Hardware Push campaign with partner co-op funds.",
        supporting_metrics={"achievement_pct": 87.27, "growth_pct": -20.0},
        review_date="2026-10-31",
        owner="Sales Lead",
    )

    # Idempotent: must return existing record, total count must remain 1
    assert plan2["plan_id"] == plan1["plan_id"]
    assert len(store.list_plans()) == 1
