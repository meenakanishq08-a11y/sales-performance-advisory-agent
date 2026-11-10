"""Persistent storage engine for sales action plans.

Stores action plans in JSON format with unique IDs, timestamps, supporting metrics,
review dates, and idempotent (duplicate-safe) saving.
"""

from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import json
import uuid
from pydantic import BaseModel, Field


class ActionPlanRecord(BaseModel):
    """Schema for a persisted sales action plan."""
    plan_id: str = Field(default_factory=lambda: f"AP-{uuid.uuid4().hex[:8].upper()}")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    issue_type: str = Field(description="Issue being addressed (LOW_REVENUE, LOW_MARGIN, HIGH_RETURNS, etc.)")
    region: str = Field(description="Sales region")
    product: str = Field(description="Product SKU")
    month: str = Field(description="Target month (YYYY-MM)")
    proposed_action: str = Field(description="Detailed corrective action plan")
    supporting_metrics: Optional[Dict[str, Any]] = Field(default=None, description="Supporting metrics evidence")
    discount_pct: Optional[float] = Field(default=None, description="Discount % if applicable")
    approval_status: str = Field(default="STANDARD", description="STANDARD, APPROVED_BY_MANAGEMENT, or PENDING")
    owner: str = Field(default="Sales Operations", description="Plan assignee")
    review_date: Optional[str] = Field(
        default_factory=lambda: (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d"),
        description="Scheduled review date for action plan (YYYY-MM-DD)",
    )


class ActionStore:
    """JSON-based persistent repository for Action Plans with idempotent saves."""

    def __init__(self, storage_path: Union[str, Path] = "data/action_plans.json") -> None:
        self.path = Path(storage_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write([])

    def _read(self) -> List[Dict[str, Any]]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _write(self, data: List[Dict[str, Any]]) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def save_plan(
        self,
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
        """Save a new action plan with idempotent (duplicate-safe) checking."""
        plans = self._read()

        # Idempotent check: if an identical plan already exists for same region, product, month, and issue
        for existing in plans:
            if (
                existing.get("region", "").lower() == region.lower()
                and existing.get("product", "").lower() == product.lower()
                and existing.get("month") == month
                and existing.get("issue_type") == issue_type
                and existing.get("proposed_action") == proposed_action
            ):
                # Return existing record to prevent duplicates
                return existing

        record = ActionPlanRecord(
            issue_type=issue_type,
            region=region,
            product=product,
            month=month,
            proposed_action=proposed_action,
            supporting_metrics=supporting_metrics,
            discount_pct=discount_pct,
            approval_status=approval_status,
            owner=owner,
            review_date=review_date or (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d"),
        )

        plans.append(record.model_dump())
        self._write(plans)
        return record.model_dump()

    def list_plans(
        self,
        region: Optional[str] = None,
        product: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List stored plans, optionally filtered by region or product."""
        plans = self._read()
        filtered = plans
        if region:
            filtered = [p for p in filtered if p["region"].lower() == region.lower()]
        if product:
            filtered = [p for p in filtered if p["product"].lower() == product.lower()]
        return filtered

    def get_plan(self, plan_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a specific action plan by ID."""
        plans = self._read()
        for p in plans:
            if p["plan_id"].upper() == plan_id.upper():
                return p
        return None

    def clear(self) -> None:
        """Clear all stored plans (useful for test isolation)."""
        self._write([])
