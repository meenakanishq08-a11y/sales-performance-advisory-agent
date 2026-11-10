"""Retrieval relevance grading and bounded query rewriting engine.

Validates that retrieved chunks match the target product and problem type.
If retrieval is irrelevant, rewrites the query and retries up to a strict limit
of MAX 2 retries. If still irrelevant, returns 'no relevant policy found'.
Never fabricates or hallucinates policy rules.
"""

from typing import Any, Dict, List, Optional
import re
from src.retriever import PlaybookChunk, PlaybookRetriever, POLICY_TYPE_KEYWORDS


# ---------------------------------------------------------------------------
# Relevance Grading Logic
# ---------------------------------------------------------------------------

def grade_chunk_relevance(
    chunk_dict: Dict[str, Any],
    problem_type: Optional[str] = None,
    target_product: Optional[str] = None,
    min_score: float = 0.05,
    score: Optional[float] = None,
) -> Tuple_Grade:
    """Evaluate whether a retrieved chunk is genuinely relevant to the query context.

    Criteria:
    1. Score check: score must be >= min_score (if score provided).
    2. Product alignment:
       - If target_product is specified, chunk must either be general (no specific product),
         match target_product, or mention target_product in text.
       - A chunk specifically tagged for a different product is rejected.
    3. Problem type alignment:
       - If problem_type is specified, chunk policy_type must match, or chunk text/title
         must contain keywords pertinent to problem_type.

    Returns:
        tuple (is_relevant: bool, reason: str)
    """
    if score is not None and score < min_score:
        return False, f"Score {score} is below relevance threshold {min_score}"

    text = chunk_dict.get("text", "").lower()
    section_title = chunk_dict.get("section_title", "").lower()
    chunk_product = chunk_dict.get("product")
    chunk_policy_type = chunk_dict.get("policy_type", "general")

    # 1. Product check
    if target_product:
        target_prod_lower = target_product.lower()
        if chunk_product and chunk_product.lower() != target_prod_lower:
            return False, f"Product mismatch: chunk is for '{chunk_product}', target is '{target_product}'"

    # 2. Problem type check
    if problem_type and problem_type.lower() != "general":
        prob_lower = problem_type.lower()
        if chunk_policy_type == prob_lower:
            return True, f"Policy type directly matches '{problem_type}'"

        # If the chunk explicitly belongs to another specific policy type, reject it
        if chunk_policy_type != "general" and chunk_policy_type != prob_lower:
            return False, f"Policy mismatch: chunk is categorized as '{chunk_policy_type}', expected '{problem_type}'"

        # For general policy chunks, require relevant keywords in text or title
        keywords = POLICY_TYPE_KEYWORDS.get(prob_lower, [prob_lower])
        matched_kw = [kw for kw in keywords if re.search(rf"\b{re.escape(kw)}\b", f"{section_title} {text}")]
        if not matched_kw:
            return False, f"Policy mismatch: chunk does not contain keywords for '{problem_type}'"

    return True, "Chunk passed product and problem type relevance criteria"


# Helper type for grade result
Tuple_Grade = tuple[bool, str]


# ---------------------------------------------------------------------------
# Bounded Query Rewriting
# ---------------------------------------------------------------------------

PROBLEM_SYNONYMS = {
    "discount": "discount policy pricing concession authorization approval limit",
    "low_revenue": "low revenue quota target achievement sales growth pipeline",
    "low_margin": "gross margin unit cost landed expenses accessory attachments",
    "returns": "product return rate defective items quarantine customer onboarding",
    "campaign": "promotions marketing funds co-op bundling incentives",
}


def rewrite_query(
    original_query: str,
    problem_type: Optional[str] = None,
    target_product: Optional[str] = None,
    attempt: int = 1,
) -> str:
    """Rewrite query using domain knowledge and semantic expansion.

    Args:
        original_query: The initial user query that yielded low relevance.
        problem_type: Contextual issue (e.g. 'discount', 'returns').
        target_product: Contextual product (e.g. 'Tab-X').
        attempt: The retry number (1 or 2).

    Returns:
        Expanded / rewritten query string.
    """
    prod_str = f"{target_product} " if target_product else ""

    if attempt == 1:
        # Expansion with domain-specific vocabulary and synonyms
        if problem_type and problem_type.lower() in PROBLEM_SYNONYMS:
            synonyms = PROBLEM_SYNONYMS[problem_type.lower()]
            return f"{prod_str}{synonyms} guidelines"
        return f"{prod_str}{original_query} policy rules section"

    elif attempt == 2:
        # High-focus canonical query
        if problem_type:
            return f"{prod_str}{problem_type} policy procedure"
        return f"{prod_str}official company policy guidelines"

    return original_query


# ---------------------------------------------------------------------------
# Bounded Retrieval & Grading Loop
# ---------------------------------------------------------------------------

def retrieve_with_grading(
    retriever: PlaybookRetriever,
    query: str,
    problem_type: Optional[str] = None,
    target_product: Optional[str] = None,
    max_retries: int = 2,
    top_k: int = 3,
    min_score: float = 0.05,
) -> Dict[str, Any]:
    """Execute search with relevance grading and bounded query rewriting.

    Retries up to `max_retries` (strictly <= 2). If no relevant chunk is found
    after retries, returns 'no relevant policy found' without hallucination.

    Returns:
        Dict:
            - status: "FOUND" or "NOT_FOUND"
            - attempts_used: int (0 for initial, 1 or 2 for retries)
            - query_used: str
            - chunks: List of relevant chunks
            - message: str
    """
    attempt = 0
    current_query = query
    audit_trail: List[Dict[str, Any]] = []

    while attempt <= max_retries:
        results = retriever.search(
            current_query,
            top_k=top_k,
            filter_product=target_product,
        )

        relevant_chunks: List[Dict[str, Any]] = []
        grading_details: List[Dict[str, Any]] = []

        for r in results:
            is_rel, reason = grade_chunk_relevance(
                chunk_dict=r["chunk"],
                problem_type=problem_type,
                target_product=target_product,
                min_score=min_score,
                score=r.get("score"),
            )
            grading_details.append({
                "source": r["chunk"].get("source"),
                "section": r["chunk"].get("section_title"),
                "is_relevant": is_rel,
                "reason": reason,
            })
            if is_rel:
                relevant_chunks.append(r)

        audit_trail.append({
            "attempt": attempt,
            "query": current_query,
            "results_count": len(results),
            "relevant_count": len(relevant_chunks),
            "grading": grading_details,
        })

        if relevant_chunks:
            return {
                "status": "FOUND",
                "attempts_used": attempt,
                "query_used": current_query,
                "chunks": relevant_chunks,
                "audit_trail": audit_trail,
                "message": f"Found {len(relevant_chunks)} relevant policy chunk(s)",
            }

        if attempt < max_retries:
            attempt += 1
            current_query = rewrite_query(
                original_query=query,
                problem_type=problem_type,
                target_product=target_product,
                attempt=attempt,
            )
        else:
            break

    # Strictly bounded: Max retries exhausted
    return {
        "status": "NOT_FOUND",
        "attempts_used": attempt,
        "query_used": current_query,
        "chunks": [],
        "audit_trail": audit_trail,
        "message": "no relevant policy found",
    }
