"""Metrics calculation and data validation engine for Sales Advisory Agent.

Deterministic calculations for target achievement, sales growth, gross margin,
return rates, and underperformer identification.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import math
import pandas as pd


def safe_divide(
    numerator: Optional[Union[int, float]],
    denominator: Optional[Union[int, float]],
) -> Optional[float]:
    """Safely divide two numbers, avoiding ZeroDivisionError, NaN, and invalid values.

    Args:
        numerator: The dividend value.
        denominator: The divisor value.

    Returns:
        float if division succeeds, or None if denominator is zero, None, NaN, or non-positive.
        Guarantees that 0.0 is returned only if numerator == 0 and denominator > 0.
    """
    if numerator is None or denominator is None:
        return None

    try:
        num = float(numerator)
        den = float(denominator)
    except (ValueError, TypeError):
        return None

    if math.isnan(num) or math.isnan(den):
        return None

    if den <= 0:
        return None

    if num == 0.0:
        return 0.0

    return num / den


def validate_non_negative(**kwargs: Any) -> None:
    """Validate that named numeric arguments are non-negative.

    Raises:
        ValueError: If any numeric argument is strictly negative.
    """
    for name, value in kwargs.items():
        if value is not None:
            try:
                num = float(value)
                if not math.isnan(num) and num < 0:
                    raise ValueError(f"'{name}' must be non-negative, got {value}")
            except (TypeError, ValueError) as err:
                if "must be non-negative" in str(err):
                    raise
                # Skip non-numeric values (handled elsewhere)


def calculate_target_achievement(
    revenue: Optional[Union[int, float]],
    target_revenue: Optional[Union[int, float]],
) -> Optional[float]:
    """Calculate target achievement percentage (revenue / target_revenue * 100).

    Args:
        revenue: Actual revenue generated.
        target_revenue: Target quota for the period.

    Returns:
        Achievement percentage (e.g. 87.27 for 87.27%) or None if target is 0/missing.
    """
    validate_non_negative(revenue=revenue, target_revenue=target_revenue)
    ratio = safe_divide(revenue, target_revenue)
    if ratio is None:
        return None
    return round(ratio * 100.0, 2)


def calculate_sales_growth(
    current_revenue: Optional[Union[int, float]],
    previous_revenue: Optional[Union[int, float]],
) -> Optional[float]:
    """Calculate month-over-month (MoM) sales growth percentage.

    Formula: ((current_revenue - previous_revenue) / previous_revenue) * 100

    Args:
        current_revenue: Revenue for current month.
        previous_revenue: Revenue for previous month (can be None for first month).

    Returns:
        MoM growth percentage (e.g. -20.0 for -20%), or None if previous revenue is zero/missing.
    """
    validate_non_negative(current_revenue=current_revenue, previous_revenue=previous_revenue)
    if current_revenue is None or previous_revenue is None:
        return None

    delta = float(current_revenue) - float(previous_revenue)
    ratio = safe_divide(delta, previous_revenue)
    if ratio is None:
        return None
    return round(ratio * 100.0, 2)


def calculate_gross_margin(
    revenue: Optional[Union[int, float]],
    units: Optional[int],
    unit_cost: Optional[Union[int, float]],
) -> Dict[str, Any]:
    """Calculate simplified gross margin: revenue - (units * unit_cost).

    Args:
        revenue: Total revenue.
        units: Total units sold.
        unit_cost: Cost per unit from margin dataset.

    Returns:
        Dict containing:
            - status: "OK" or "MISSING_COST"
            - gross_margin: Absolute dollar/rupee margin or None
            - margin_rate_pct: Margin as a percentage of revenue or None
    """
    validate_non_negative(revenue=revenue, units=units, unit_cost=unit_cost)

    if unit_cost is None or (isinstance(unit_cost, float) and math.isnan(unit_cost)):
        return {
            "status": "MISSING_COST",
            "gross_margin": None,
            "margin_rate_pct": None,
            "message": "Cost data missing for product",
        }

    if revenue is None or units is None:
        return {
            "status": "MISSING_COST",
            "gross_margin": None,
            "margin_rate_pct": None,
            "message": "Revenue or units missing",
        }

    total_cost = float(units) * float(unit_cost)
    margin = float(revenue) - total_cost
    margin_rate = safe_divide(margin, revenue)
    margin_pct = round(margin_rate * 100.0, 2) if margin_rate is not None else None

    return {
        "status": "OK",
        "gross_margin": round(margin, 2),
        "margin_rate_pct": margin_pct,
        "message": "Gross margin calculated successfully",
    }


def calculate_product_margin(
    revenue: Optional[Union[int, float]],
    units: Optional[int],
    unit_cost: Optional[Union[int, float]],
    *args: Any,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Alias for calculate_gross_margin with consistent curriculum naming and *args/**kwargs support."""
    return calculate_gross_margin(revenue=revenue, units=units, unit_cost=unit_cost)


def calculate_return_rate(
    returns: Optional[int],
    units: Optional[int],
) -> Optional[float]:
    """Calculate return rate percentage (returns / units * 100).

    Args:
        returns: Number of units returned.
        units: Total units sold/shipped.

    Returns:
        Return rate percentage (e.g. 8.75%) or None if units is zero/missing.

    Raises:
        ValueError: If returns or units are negative, or if returns > units.
    """
    validate_non_negative(returns=returns, units=units)
    if returns is not None and units is not None:
        if returns > units:
            raise ValueError(f"Returns ({returns}) cannot exceed units sold ({units})")

    ratio = safe_divide(returns, units)
    if ratio is None:
        return None
    return round(ratio * 100.0, 2)


# ---------------------------------------------------------------------------
# Data Loaders with schema validation
# ---------------------------------------------------------------------------

REQUIRED_SALES_COLUMNS = ["region", "product", "month", "units", "revenue", "returns"]
REQUIRED_TARGETS_COLUMNS = ["region", "product", "month", "target_revenue"]
REQUIRED_MARGINS_COLUMNS = ["product", "unit_cost"]
REQUIRED_CAMPAIGNS_COLUMNS = ["campaign_name", "region", "product", "month"]


def load_campaigns_data(filepath: Union[str, Path] = "data/campaigns.xlsx") -> pd.DataFrame:
    """Load and validate marketing campaigns XLSX dataset."""
    path = Path(filepath)
    if not path.is_file():
        raise FileNotFoundError(f"Campaigns file not found at: {path}")

    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
    else:
        df = pd.read_excel(path)

    missing_cols = [c for c in REQUIRED_CAMPAIGNS_COLUMNS if c not in df.columns]
    if missing_cols:
        raise ValueError(f"Campaigns dataset missing required columns: {missing_cols}")

    return df


def aggregate_sales_data(
    sales_df: pd.DataFrame,
    by: Union[str, List[str]] = "region",
    *args: Any,
    **kwargs: Any,
) -> pd.DataFrame:
    """Group-by aggregation across periods and regions (Module 02-05 curriculum objective).

    Args:
        sales_df: Raw sales DataFrame.
        by: Column name(s) to group by (e.g. 'region', 'month', or ['region', 'month']).

    Returns:
        Aggregated DataFrame with total units, revenue, and returns.
    """
    group_cols = [by] if isinstance(by, str) else list(by)
    for col in group_cols:
        if col not in sales_df.columns:
            raise KeyError(f"Group-by column '{col}' not in sales dataset columns: {sales_df.columns.tolist()}")

    agg_df = sales_df.groupby(group_cols, as_index=False).agg({
        "units": "sum",
        "revenue": "sum",
        "returns": "sum",
    })
    return agg_df


def load_sales_data(filepath: Union[str, Path]) -> pd.DataFrame:
    """Load and validate sales CSV dataset."""
    path = Path(filepath)
    if not path.is_file():
        raise FileNotFoundError(f"Sales file not found at: {path}")

    df = pd.read_csv(path)
    missing_cols = [c for c in REQUIRED_SALES_COLUMNS if c not in df.columns]
    if missing_cols:
        raise ValueError(f"Sales dataset missing required columns: {missing_cols}")

    # Validate non-negative numbers
    for col in ["units", "revenue", "returns"]:
        if (df[col] < 0).any():
            invalid_rows = df[df[col] < 0].to_dict(orient="records")
            raise ValueError(f"Sales column '{col}' contains negative values: {invalid_rows}")

    return df


def load_targets_data(filepath: Union[str, Path]) -> pd.DataFrame:
    """Load and validate targets CSV dataset."""
    path = Path(filepath)
    if not path.is_file():
        raise FileNotFoundError(f"Targets file not found at: {path}")

    df = pd.read_csv(path)
    missing_cols = [c for c in REQUIRED_TARGETS_COLUMNS if c not in df.columns]
    if missing_cols:
        raise ValueError(f"Targets dataset missing required columns: {missing_cols}")

    if (df["target_revenue"] < 0).any():
        invalid_rows = df[df["target_revenue"] < 0].to_dict(orient="records")
        raise ValueError(f"Target revenue contains negative values: {invalid_rows}")

    return df


def load_margins_data(filepath: Union[str, Path]) -> pd.DataFrame:
    """Load and validate margins Excel (.xlsx) or CSV dataset."""
    path = Path(filepath)
    if not path.is_file():
        raise FileNotFoundError(f"Margins file not found at: {path}")

    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
    else:
        df = pd.read_excel(path)

    missing_cols = [c for c in REQUIRED_MARGINS_COLUMNS if c not in df.columns]
    if missing_cols:
        raise ValueError(f"Margins dataset missing required columns: {missing_cols}")

    if (df["unit_cost"] < 0).any():
        invalid_rows = df[df["unit_cost"] < 0].to_dict(orient="records")
        raise ValueError(f"Unit cost contains negative values: {invalid_rows}")

    return df


# ---------------------------------------------------------------------------
# Combined record metrics and underperformer identification
# ---------------------------------------------------------------------------

def get_record_metrics(
    region: str,
    product: str,
    month: str,
    sales_df: pd.DataFrame,
    targets_df: pd.DataFrame,
    margins_df: pd.DataFrame,
) -> Dict[str, Any]:
    """Extract metrics for a specific (region, product, month) slice.

    Looks up previous month automatically for MoM growth calculation.

    Returns:
        Structured dictionary of computed metrics or error message if row is missing.
    """
    row = sales_df[
        (sales_df["region"].str.lower() == region.lower())
        & (sales_df["product"].str.lower() == product.lower())
        & (sales_df["month"] == month)
    ]

    if row.empty:
        raise KeyError(f"No sales data found for region='{region}', product='{product}', month='{month}'")

    current = row.iloc[0]
    units = int(current["units"])
    revenue = float(current["revenue"])
    returns = int(current["returns"])

    # Lookup target
    target_row = targets_df[
        (targets_df["region"].str.lower() == region.lower())
        & (targets_df["product"].str.lower() == product.lower())
        & (targets_df["month"] == month)
    ]
    target_revenue = float(target_row.iloc[0]["target_revenue"]) if not target_row.empty else None

    # Lookup unit cost
    margin_row = margins_df[margins_df["product"].str.lower() == product.lower()]
    unit_cost = float(margin_row.iloc[0]["unit_cost"]) if not margin_row.empty else None

    # Lookup previous month revenue (find by date sorting for this region/product)
    prev_revenue = None
    all_months = sales_df[
        (sales_df["region"].str.lower() == region.lower())
        & (sales_df["product"].str.lower() == product.lower())
    ].sort_values("month")

    current_idx_list = all_months.index[all_months["month"] == month].tolist()
    if current_idx_list:
        pos = list(all_months["month"]).index(month)
        if pos > 0:
            prev_row = all_months.iloc[pos - 1]
            prev_revenue = float(prev_row["revenue"])

    target_achv = calculate_target_achievement(revenue, target_revenue)
    growth = calculate_sales_growth(revenue, prev_revenue)
    margin_info = calculate_gross_margin(revenue, units, unit_cost)
    return_rate = calculate_return_rate(returns, units)

    return {
        "region": region,
        "product": product,
        "month": month,
        "units": units,
        "revenue": revenue,
        "returns": returns,
        "target_revenue": target_revenue,
        "target_achievement_pct": target_achv,
        "previous_revenue": prev_revenue,
        "sales_growth_pct": growth,
        "gross_margin": margin_info["gross_margin"],
        "margin_status": margin_info["status"],
        "margin_rate_pct": margin_info["margin_rate_pct"],
        "return_rate_pct": return_rate,
        "is_first_month": prev_revenue is None,
    }


def identify_underperformers(
    sales_df: pd.DataFrame,
    targets_df: pd.DataFrame,
    margins_df: pd.DataFrame,
    target_achievement_threshold: float = 90.0,
    return_rate_threshold: float = 5.0,
    margin_pct_threshold: float = 15.0,
) -> List[Dict[str, Any]]:
    """Scan all records in sales_df and identify underperforming slices.

    Flags issues:
        - LOW_ACHIEVEMENT: Target achievement < threshold
        - NEGATIVE_GROWTH: MoM growth < 0%
        - HIGH_RETURNS: Return rate > threshold
        - LOW_MARGIN: Gross margin % < threshold
        - MISSING_COST: Unit cost not found in margins table

    Returns:
        List of underperforming records with detailed flags and explanations.
    """
    underperformers = []

    for _, row in sales_df.iterrows():
        region = str(row["region"])
        product = str(row["product"])
        month = str(row["month"])

        try:
            metrics = get_record_metrics(region, product, month, sales_df, targets_df, margins_df)
        except KeyError:
            continue

        flags = []

        # Target achievement check
        achv = metrics["target_achievement_pct"]
        if achv is not None and achv < target_achievement_threshold:
            flags.append({
                "issue": "LOW_ACHIEVEMENT",
                "detail": f"Target achievement is {achv}% (target: {target_achievement_threshold}%)",
            })

        # Sales growth check
        growth = metrics["sales_growth_pct"]
        if growth is not None and growth < 0:
            flags.append({
                "issue": "NEGATIVE_GROWTH",
                "detail": f"MoM sales growth is {growth}%",
            })

        # Return rate check
        ret_rate = metrics["return_rate_pct"]
        if ret_rate is not None and ret_rate > return_rate_threshold:
            flags.append({
                "issue": "HIGH_RETURNS",
                "detail": f"Return rate is {ret_rate}% (threshold: {return_rate_threshold}%)",
            })

        # Gross margin check
        if metrics["margin_status"] == "MISSING_COST":
            flags.append({
                "issue": "MISSING_COST",
                "detail": f"Unit cost missing for product {product}",
            })
        elif metrics["margin_rate_pct"] is not None and metrics["margin_rate_pct"] < margin_pct_threshold:
            flags.append({
                "issue": "LOW_MARGIN",
                "detail": f"Gross margin rate is {metrics['margin_rate_pct']}% (threshold: {margin_pct_threshold}%)",
            })

        if flags:
            underperformers.append({
                "region": region,
                "product": product,
                "month": month,
                "metrics": metrics,
                "flags": flags,
            })

    return underperformers
