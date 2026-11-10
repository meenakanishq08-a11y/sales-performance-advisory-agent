"""Test suite for metrics calculation, safe math, and data loading."""

from pathlib import Path
import pytest
import pandas as pd
from src.metrics import (
    safe_divide,
    calculate_target_achievement,
    calculate_sales_growth,
    calculate_gross_margin,
    calculate_return_rate,
    load_sales_data,
    load_targets_data,
    load_margins_data,
    get_record_metrics,
    identify_underperformers,
)


# ---------------------------------------------------------------------------
# Test safe_divide
# ---------------------------------------------------------------------------

def test_safe_divide_normal():
    assert safe_divide(100, 200) == 0.5
    assert safe_divide(0, 100) == 0.0


def test_safe_divide_zero_denominator():
    # Must return None on zero denominator, never raise ZeroDivisionError and never 0.0
    assert safe_divide(100, 0) is None
    assert safe_divide(0, 0) is None


def test_safe_divide_none_and_nan():
    assert safe_divide(None, 100) is None
    assert safe_divide(100, None) is None
    assert safe_divide(float("nan"), 100) is None
    assert safe_divide(100, float("nan")) is None


def test_safe_divide_negative_denominator():
    # Negative denominator represents invalid quota or base
    assert safe_divide(100, -50) is None


# ---------------------------------------------------------------------------
# Test Target Achievement
# ---------------------------------------------------------------------------

def test_target_achievement_normal():
    # 960,000 / 1,100,000 * 100 = 87.27%
    achv = calculate_target_achievement(960000, 1100000)
    assert achv == 87.27


def test_target_achievement_zero_target():
    # When target is 0, must return None (not 0.0, not error)
    assert calculate_target_achievement(960000, 0) is None


def test_target_achievement_zero_revenue():
    # When actual revenue is 0 and target is valid, achievement is 0.0%
    assert calculate_target_achievement(0, 1000000) == 0.0


# ---------------------------------------------------------------------------
# Test Sales Growth
# ---------------------------------------------------------------------------

def test_sales_growth_negative_growth():
    # Current: 960k, Prev: 1200k -> (960 - 1200) / 1200 = -20.0%
    growth = calculate_sales_growth(960000, 1200000)
    assert growth == -20.0


def test_sales_growth_positive_growth():
    # Current: 1200k, Prev: 1000k -> +20.0%
    growth = calculate_sales_growth(1200000, 1000000)
    assert growth == 20.0


def test_sales_growth_zero_previous_sales():
    # If previous sales were 0, growth percentage is mathematically undefined -> None
    assert calculate_sales_growth(500000, 0) is None


def test_sales_growth_first_month():
    # First month has no previous sales record -> None
    assert calculate_sales_growth(500000, None) is None


# ---------------------------------------------------------------------------
# Test Simplified Gross Margin & MISSING_COST
# ---------------------------------------------------------------------------

def test_gross_margin_normal():
    # Tab-X: units=320, revenue=960,000, unit_cost=2400
    # total_cost = 320 * 2400 = 768,000
    # margin = 960,000 - 768,000 = 192,000 (20.0%)
    result = calculate_gross_margin(revenue=960000, units=320, unit_cost=2400)
    assert result["status"] == "OK"
    assert result["gross_margin"] == 192000.0
    assert result["margin_rate_pct"] == 20.0


def test_gross_margin_missing_cost():
    # When product cost is None -> status MISSING_COST
    result = calculate_gross_margin(revenue=960000, units=320, unit_cost=None)
    assert result["status"] == "MISSING_COST"
    assert result["gross_margin"] is None
    assert result["margin_rate_pct"] is None


def test_gross_margin_nan_cost():
    result = calculate_gross_margin(revenue=960000, units=320, unit_cost=float("nan"))
    assert result["status"] == "MISSING_COST"
    assert result["gross_margin"] is None


# ---------------------------------------------------------------------------
# Test Return Rate
# ---------------------------------------------------------------------------

def test_return_rate_normal():
    # returns=28, units=320 -> 28 / 320 * 100 = 8.75%
    rate = calculate_return_rate(returns=28, units=320)
    assert rate == 8.75


def test_return_rate_zero_returns():
    assert calculate_return_rate(returns=0, units=320) == 0.0


def test_return_rate_zero_units():
    assert calculate_return_rate(returns=0, units=0) is None


def test_return_rate_exceeding_units():
    with pytest.raises(ValueError, match="cannot exceed"):
        calculate_return_rate(returns=350, units=320)


# ---------------------------------------------------------------------------
# Test Negative Inputs Validation
# ---------------------------------------------------------------------------

def test_negative_inputs_validation():
    with pytest.raises(ValueError, match="must be non-negative"):
        calculate_target_achievement(revenue=-100, target_revenue=500)

    with pytest.raises(ValueError, match="must be non-negative"):
        calculate_sales_growth(current_revenue=-100, previous_revenue=500)

    with pytest.raises(ValueError, match="must be non-negative"):
        calculate_gross_margin(revenue=500, units=-10, unit_cost=50)

    with pytest.raises(ValueError, match="must be non-negative"):
        calculate_return_rate(returns=-5, units=100)


# ---------------------------------------------------------------------------
# Test Data Loaders & Schema Errors
# ---------------------------------------------------------------------------

def test_load_sales_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_sales_data("data/non_existent.csv")


def test_load_sales_missing_column(tmp_path):
    bad_csv = tmp_path / "bad_sales.csv"
    bad_csv.write_text("region,product,revenue\nSouth,Tab-X,1000\n")
    with pytest.raises(ValueError, match="missing required columns"):
        load_sales_data(bad_csv)


def test_load_sales_negative_value(tmp_path):
    bad_csv = tmp_path / "neg_sales.csv"
    bad_csv.write_text("region,product,month,units,revenue,returns\nSouth,Tab-X,2026-09,-10,1000,2\n")
    with pytest.raises(ValueError, match="negative values"):
        load_sales_data(bad_csv)


def test_load_sample_data_success():
    sales_df = load_sales_data("data/sales.csv")
    targets_df = load_targets_data("data/targets.csv")
    margins_df = load_margins_data("data/margins.xlsx")

    assert len(sales_df) == 4
    assert len(targets_df) == 4
    assert len(margins_df) == 2


# ---------------------------------------------------------------------------
# Test Slice Lookup & Underperformer Identification
# ---------------------------------------------------------------------------

def test_get_record_metrics_south_tab_x():
    sales_df = load_sales_data("data/sales.csv")
    targets_df = load_targets_data("data/targets.csv")
    margins_df = load_margins_data("data/margins.xlsx")

    metrics = get_record_metrics("South", "Tab-X", "2026-09", sales_df, targets_df, margins_df)
    assert metrics["region"] == "South"
    assert metrics["product"] == "Tab-X"
    assert metrics["month"] == "2026-09"
    assert metrics["units"] == 320
    assert metrics["revenue"] == 960000.0
    assert metrics["target_achievement_pct"] == 87.27
    assert metrics["sales_growth_pct"] == -20.0
    assert metrics["gross_margin"] == 192000.0
    assert metrics["margin_status"] == "OK"
    assert metrics["margin_rate_pct"] == 20.0
    assert metrics["return_rate_pct"] == 8.75


def test_get_record_metrics_missing_row():
    sales_df = load_sales_data("data/sales.csv")
    targets_df = load_targets_data("data/targets.csv")
    margins_df = load_margins_data("data/margins.xlsx")

    with pytest.raises(KeyError, match="No sales data found"):
        get_record_metrics("UnknownRegion", "Tab-X", "2026-09", sales_df, targets_df, margins_df)


def test_identify_underperformers_flags():
    sales_df = load_sales_data("data/sales.csv")
    targets_df = load_targets_data("data/targets.csv")
    margins_df = load_margins_data("data/margins.xlsx")

    underperformers = identify_underperformers(sales_df, targets_df, margins_df)
    assert len(underperformers) > 0

    # South Tab-X 2026-09 has:
    # 1. LOW_ACHIEVEMENT (87.27% < 90%)
    # 2. NEGATIVE_GROWTH (-20% < 0%)
    # 3. HIGH_RETURNS (8.75% > 5%)
    south_under = next(u for u in underperformers if u["region"] == "South" and u["month"] == "2026-09")
    issue_types = [f["issue"] for f in south_under["flags"]]
    assert "LOW_ACHIEVEMENT" in issue_types
    assert "NEGATIVE_GROWTH" in issue_types
    assert "HIGH_RETURNS" in issue_types
