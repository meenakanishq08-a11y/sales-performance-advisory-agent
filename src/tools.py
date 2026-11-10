"""Structured tool calling definitions for Sales Advisory Agent.

Exposes deterministic metrics calculation, playbook retrieval with grading,
policy guardrail checking, and persistent action plan recording as schema-validated
tools for LLM tool calling.
"""

from typing import Any, Dict, List, Optional
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.metrics import (
    load_margins_data,
    load_sales_data,
    load_targets_data,
    load_campaigns_data,
    get_record_metrics,
    calculate_target_achievement as calc_achv,
    calculate_sales_growth as calc_growth,
    calculate_product_margin as calc_margin,
    identify_underperformers as find_underperformers,
)
from src.retriever import PlaybookRetriever
from src.grader import retrieve_with_grading
from src.guardrails import validate_discount_policy
from src.action_store import ActionStore


# Default shared singletons for tool execution
_retriever: Optional[PlaybookRetriever] = None
_action_store: Optional[ActionStore] = None


def get_default_retriever() -> PlaybookRetriever:
    """Lazy initialize and return singleton PlaybookRetriever."""
    global _retriever
    if _retriever is None:
        _retriever = PlaybookRetriever(min_score_threshold=0.04)
        _retriever.load_directory("docs")
    return _retriever


def get_default_action_store() -> ActionStore:
    """Lazy initialize and return singleton ActionStore."""
    global _action_store
    if _action_store is None:
        _action_store = ActionStore()
    return _action_store


# ---------------------------------------------------------------------------
# Pydantic Tool Input Schemas
# ---------------------------------------------------------------------------

class GetSalesMetricsInput(BaseModel):
    region: str = Field(description="Geographic sales region (e.g. South, North, West, East)")
    product: str = Field(description="Product SKU name (e.g. Tab-X, Tab-Y)")
    month: str = Field(description="Performance month in YYYY-MM format (e.g. 2026-09)")


class TargetAchievementInput(BaseModel):
    revenue: float = Field(description="Actual revenue achieved")
    target_revenue: float = Field(description="Target quota revenue")


class SalesGrowthInput(BaseModel):
    current_revenue: float = Field(description="Current period revenue")
    previous_revenue: Optional[float] = Field(default=None, description="Prior period revenue (or None)")


class ProductMarginInput(BaseModel):
    revenue: float = Field(description="Total revenue")
    units: int = Field(description="Units sold")
    unit_cost: Optional[float] = Field(default=None, description="Cost per unit")


class IdentifyUnderperformersInput(BaseModel):
    target_achievement_threshold: float = Field(default=90.0, description="Quota threshold %")
    return_rate_threshold: float = Field(default=5.0, description="Return rate threshold %")
    margin_pct_threshold: float = Field(default=15.0, description="Gross margin threshold %")


class SearchPlaybookInput(BaseModel):
    query: str = Field(description="The question or policy topic to retrieve from the sales playbook")
    problem_type: Optional[str] = Field(
        default=None,
        description="Category of problem: 'discount', 'low_revenue', 'low_margin', 'returns', 'campaign', or 'general'",
    )
    target_product: Optional[str] = Field(
        default=None,
        description="Product SKU filter if the policy query is product-specific (e.g. Tab-X)",
    )


class CheckDiscountInput(BaseModel):
    discount_pct: float = Field(description="Requested commercial discount percentage (e.g. 10.0 or 15.0)")
    product: Optional[str] = Field(default=None, description="Product SKU (e.g. Tab-X)")
    has_management_approval: bool = Field(
        default=False,
        description="Whether written VP/Director authorization is explicitly granted",
    )


class SaveActionPlanInput(BaseModel):
    issue_type: str = Field(description="Core issue identified (LOW_REVENUE, LOW_MARGIN, HIGH_RETURNS)")
    region: str = Field(description="Sales region")
    product: str = Field(description="Product SKU name")
    month: str = Field(description="Month in YYYY-MM format")
    proposed_action: str = Field(description="Actionable remediation steps adhering to playbook policies")
    supporting_metrics: Optional[Dict[str, Any]] = Field(default=None, description="Supporting metrics evidence")
    discount_pct: Optional[float] = Field(default=None, description="Discount percentage if authorized")
    approval_status: str = Field(default="STANDARD", description="STANDARD, APPROVED_BY_MANAGEMENT, or PENDING")
    owner: str = Field(default="Sales Operations", description="Team member or role responsible for execution")
    review_date: Optional[str] = Field(default=None, description="Review date in YYYY-MM-DD format")


# ---------------------------------------------------------------------------
# Tools Exposed to LLM
# ---------------------------------------------------------------------------

@tool(args_schema=GetSalesMetricsInput)
def get_sales_metrics(region: str, product: str, month: str) -> Dict[str, Any]:
    """Retrieve verified, deterministic sales metrics for a specific region, product, and month.

    Computes units, revenue, target achievement %, MoM growth %, gross margin,
    margin status (flags MISSING_COST if cost absent), and return rate %.
    The LLM must NEVER compute these numbers itself.
    """
    try:
        sales_df = load_sales_data("data/sales.csv")
        targets_df = load_targets_data("data/targets.csv")
        margins_df = load_margins_data("data/margins.xlsx")
        metrics = get_record_metrics(region, product, month, sales_df, targets_df, margins_df)
        return {"status": "SUCCESS", "metrics": metrics}
    except KeyError as err:
        return {"status": "ERROR", "message": str(err)}
    except Exception as err:
        return {"status": "ERROR", "message": f"Data load error: {err}"}


@tool(args_schema=TargetAchievementInput)
def calculate_target_achievement(revenue: float, target_revenue: float) -> Optional[float]:
    """Calculate target achievement percentage safely. Returns None on zero target."""
    return calc_achv(revenue=revenue, target_revenue=target_revenue)


@tool(args_schema=SalesGrowthInput)
def calculate_sales_growth(current_revenue: float, previous_revenue: Optional[float] = None) -> Optional[float]:
    """Calculate MoM sales growth safely. Returns None if prior sales is 0 or missing."""
    return calc_growth(current_revenue=current_revenue, previous_revenue=previous_revenue)


@tool(args_schema=ProductMarginInput)
def calculate_product_margin(revenue: float, units: int, unit_cost: Optional[float] = None) -> Dict[str, Any]:
    """Calculate gross product margin. Returns MISSING_COST if cost is None."""
    return calc_margin(revenue=revenue, units=units, unit_cost=unit_cost)


@tool(args_schema=IdentifyUnderperformersInput)
def identify_underperformers(
    target_achievement_threshold: float = 90.0,
    return_rate_threshold: float = 5.0,
    margin_pct_threshold: float = 15.0,
) -> List[Dict[str, Any]]:
    """Scan dataset and return list of underperforming slices with flagged issues."""
    sales_df = load_sales_data("data/sales.csv")
    targets_df = load_targets_data("data/targets.csv")
    margins_df = load_margins_data("data/margins.xlsx")
    return find_underperformers(
        sales_df,
        targets_df,
        margins_df,
        target_achievement_threshold=target_achievement_threshold,
        return_rate_threshold=return_rate_threshold,
        margin_pct_threshold=margin_pct_threshold,
    )


@tool(args_schema=SearchPlaybookInput)
def search_playbook(
    query: str,
    problem_type: Optional[str] = None,
    target_product: Optional[str] = None,
) -> Dict[str, Any]:
    """Search company sales playbook documents (.txt, .pdf, .docx) with relevance grading.

    Performs TF-IDF similarity search and relevance checks against product and problem type.
    Automatically rewrites the query up to 2 times if initial retrieval is irrelevant.
    Returns 'no relevant policy found' if no policy applies. Never fabricates policies.
    """
    retriever = get_default_retriever()
    res = retrieve_with_grading(
        retriever=retriever,
        query=query,
        problem_type=problem_type,
        target_product=target_product,
        max_retries=2,
    )
    return res


@tool(args_schema=CheckDiscountInput)
def check_discount(
    discount_pct: float,
    product: Optional[str] = None,
    has_management_approval: bool = False,
) -> Dict[str, Any]:
    """Enforce company discount policy guardrails.

    Checks requested discount against standard discount limits read dynamically from the playbook.
    Blocks discounts above the authorized limit unless prior written management approval is verified.
    """
    return validate_discount_policy(
        discount_pct=discount_pct,
        has_management_approval=has_management_approval,
    )


@tool(args_schema=SaveActionPlanInput)
def save_sales_action_plan(
    issue_type: str,
    region: str,
    product: str,
    month: str,
    proposed_action: str,
    supporting_metrics: Optional[Dict[str, Any]] = None,
    discount_pct: Optional[float] = None,
    approval_status: str = "STANDARD",
    owner: str = "Sales Operations",
    review_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist an authorized sales performance action plan to the central ledger with duplicate-safe save.

    Records the issue, supporting metrics, corrective strategy, discount terms, owner, and review date.
    """
    store = get_default_action_store()
    saved = store.save_plan(
        issue_type=issue_type,
        region=region,
        product=product,
        month=month,
        proposed_action=proposed_action,
        supporting_metrics=supporting_metrics,
        discount_pct=discount_pct,
        approval_status=approval_status,
        owner=owner,
        review_date=review_date,
    )
    return {"status": "SAVED", "plan": saved}


# Backward-compatible alias
save_action_plan = save_sales_action_plan
