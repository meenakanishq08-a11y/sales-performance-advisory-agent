"""Policy guardrails and dynamic discount limit enforcement.

Extracts discount policy rules dynamically from the retrieved playbook
(never hardcodes 10%). Enforces compliance so representatives cannot quote
or promise unapproved discounts.
"""

from pathlib import Path
from typing import Any, Dict, Optional, Union
import re


def extract_playbook_discount_limit(
    playbook_text: Optional[str] = None,
    default_fallback: float = 10.0,
) -> float:
    """Dynamically extract maximum standard discount percentage from playbook text.

    Scans for patterns like:
      - 'up to 10%'
      - 'standard discount of up to 10%'
      - 'max standard discount 10%'

    If no text is supplied, reads from docs/sales_playbook.txt.
    Falls back to default_fallback only if extraction fails.
    """
    if not playbook_text:
        doc_path = Path("docs/sales_playbook.txt")
        if doc_path.is_file():
            playbook_text = doc_path.read_text(encoding="utf-8")
        else:
            return default_fallback

    # Regex patterns looking for standard discount limits
    patterns = [
        r"(?:standard\s+commercial\s+discount|standard\s+discount)[^\d]*up\s+to\s+(\d+(?:\.\d+)?)\s*%",
        r"max(?:imum)?\s+standard\s+discount[^\d]*(\d+(?:\.\d+)?)\s*%",
        r"authorized\s+to\s+offer[^\d]*up\s+to\s+(\d+(?:\.\d+)?)\s*%",
        r"discount\s+allowed\s+is\s+up\s+to\s+(\d+(?:\.\d+)?)\s*%",
        r"(\d+(?:\.\d+)?)\s*%\s*(?:discount\s+limit|standard\s+discount)",
    ]

    for pat in patterns:
        match = re.search(pat, playbook_text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except (ValueError, IndexError):
                continue

    return default_fallback


def validate_discount_policy(
    discount_pct: float,
    max_standard_limit: Optional[float] = None,
    has_management_approval: bool = False,
    playbook_context: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate whether a requested discount adheres to playbook governance.

    Args:
        discount_pct: Requested discount percentage (e.g. 10.0 or 15.0).
        max_standard_limit: Standard discount ceiling. If None, dynamically extracted.
        has_management_approval: Whether written authorization has already been obtained.
        playbook_context: Optional playbook text to extract limit from.

    Returns:
        Dict:
            - status: "APPROVED", "APPROVED_WITH_EXCEPTION", or "BLOCKED"
            - is_authorized: bool
            - requires_approval: bool
            - discount_pct: float
            - max_standard_limit: float
            - message: str
    """
    if discount_pct < 0.0 or discount_pct > 100.0:
        raise ValueError(f"Discount percentage must be between 0 and 100, got {discount_pct}")

    if max_standard_limit is None:
        max_standard_limit = extract_playbook_discount_limit(playbook_context)

    if discount_pct <= max_standard_limit:
        return {
            "status": "APPROVED",
            "is_authorized": True,
            "requires_approval": False,
            "discount_pct": discount_pct,
            "max_standard_limit": max_standard_limit,
            "message": (
                f"Discount of {discount_pct}% is within the standard authorized limit of "
                f"{max_standard_limit}%."
            ),
        }

    # Discount exceeds standard limit
    if has_management_approval:
        return {
            "status": "APPROVED_WITH_EXCEPTION",
            "is_authorized": True,
            "requires_approval": True,
            "discount_pct": discount_pct,
            "max_standard_limit": max_standard_limit,
            "message": (
                f"Discount of {discount_pct}% exceeds standard limit ({max_standard_limit}%), "
                f"but has official management approval."
            ),
        }

    # Violation / Guardrail block
    return {
        "status": "BLOCKED",
        "is_authorized": False,
        "requires_approval": True,
        "discount_pct": discount_pct,
        "max_standard_limit": max_standard_limit,
        "message": (
            f"POLICY GUARDRAIL: A {discount_pct}% discount exceeds the playbook standard limit "
            f"of {max_standard_limit}%. Representatives must NEVER promise unapproved discounts. "
            f"Prior written authorization from the Regional Sales Director is required."
        ),
    }
