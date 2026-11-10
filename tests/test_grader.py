"""Test suite for retrieval relevance grading and bounded query rewriting."""

import pytest
from src.retriever import PlaybookRetriever
from src.grader import (
    grade_chunk_relevance,
    rewrite_query,
    retrieve_with_grading,
)


@pytest.fixture
def indexed_retriever():
    retriever = PlaybookRetriever(min_score_threshold=0.04)
    retriever.load_directory("docs")
    return retriever


# ---------------------------------------------------------------------------
# Test Grade Chunk Relevance
# ---------------------------------------------------------------------------

def test_grade_chunk_relevance_matching():
    chunk = {
        "text": "Standard discount allowed is up to 10% on Tab-X.",
        "section_title": "Section 1: Discount Policy",
        "product": "Tab-X",
        "policy_type": "discount",
    }
    is_rel, reason = grade_chunk_relevance(
        chunk, problem_type="discount", target_product="Tab-X", score=0.25
    )
    assert is_rel is True
    assert "matches" in reason.lower() or "passed" in reason.lower()


def test_grade_chunk_relevance_product_mismatch():
    chunk = {
        "text": "Specific policy for Tab-Y only.",
        "section_title": "Product Policy",
        "product": "Tab-Y",
        "policy_type": "discount",
    }
    is_rel, reason = grade_chunk_relevance(
        chunk, problem_type="discount", target_product="Tab-X", score=0.25
    )
    assert is_rel is False
    assert "Product mismatch" in reason


def test_grade_chunk_relevance_policy_mismatch():
    chunk = {
        "text": "Investigate defective hardware batches and quarantine units.",
        "section_title": "Section 4: High Returns",
        "product": None,
        "policy_type": "returns",
    }
    # Asking for discount policy, but chunk is purely returns policy
    is_rel, reason = grade_chunk_relevance(
        chunk, problem_type="discount", target_product="Tab-X", score=0.20
    )
    assert is_rel is False
    assert "Policy mismatch" in reason


# ---------------------------------------------------------------------------
# Test Query Rewriting (Bounded to 2 retries)
# ---------------------------------------------------------------------------

def test_rewrite_query_progression():
    # Attempt 1: Semantic expansion
    q1 = rewrite_query("can I give discount?", problem_type="discount", target_product="Tab-X", attempt=1)
    assert "Tab-X" in q1
    assert "discount" in q1.lower()
    assert "guidelines" in q1.lower() or "policy" in q1.lower()

    # Attempt 2: Canonical targeted query
    q2 = rewrite_query("can I give discount?", problem_type="discount", target_product="Tab-X", attempt=2)
    assert "Tab-X" in q2
    assert "discount policy" in q2.lower()


# ---------------------------------------------------------------------------
# Test retrieve_with_grading: Immediate Success (Attempt 0)
# ---------------------------------------------------------------------------

def test_retrieve_immediate_success(indexed_retriever):
    result = retrieve_with_grading(
        retriever=indexed_retriever,
        query="Standard discount authority limit and escalation approval",
        problem_type="discount",
        target_product="Tab-X",
        max_retries=2,
    )
    assert result["status"] == "FOUND"
    assert result["attempts_used"] == 0
    assert len(result["chunks"]) > 0
    top_chunk = result["chunks"][0]["chunk"]
    assert "discount" in top_chunk["text"].lower() or top_chunk["policy_type"] == "discount"


# ---------------------------------------------------------------------------
# Test retrieve_with_grading: Success on Retry (Vague Initial Query)
# ---------------------------------------------------------------------------

def test_retrieve_retry_success(indexed_retriever):
    # Initial query retrieves shipping/logistics chunk, but context asks for discount policy
    # Initial attempt 0 retrieves non-discount chunks, which fail grading
    # Attempt 1 rewrites the query using discount synonyms and succeeds
    result = retrieve_with_grading(
        retriever=indexed_retriever,
        query="shipping freight expenses logistics",
        problem_type="discount",
        target_product="Tab-X",
        max_retries=2,
    )
    assert result["status"] == "FOUND"
    # Must have required at least 1 retry because initial query was about shipping/logistics
    assert result["attempts_used"] >= 1
    assert len(result["chunks"]) > 0
    # The retrieved chunk must match discount policy
    matched_type = result["chunks"][0]["chunk"]["policy_type"]
    assert matched_type == "discount"


# ---------------------------------------------------------------------------
# Test retrieve_with_grading: Retry Limit Reached (No Hallucination)
# ---------------------------------------------------------------------------

def test_retrieve_retry_limit_reached(indexed_retriever):
    # Completely non-existent policy topic (e.g., aerospace rocket fuel guidelines)
    result = retrieve_with_grading(
        retriever=indexed_retriever,
        query="aerospace rocket fuel orbital launch criteria",
        problem_type="aerospace_safety",
        target_product="Rocket-Z",
        max_retries=2,
    )
    assert result["status"] == "NOT_FOUND"
    assert result["attempts_used"] == 2
    assert result["chunks"] == []
    # Zero hallucination guarantee: returns exact string "no relevant policy found"
    assert result["message"] == "no relevant policy found"
    # Audit trail verifies exactly 3 queries attempted (attempt 0, 1, 2)
    assert len(result["audit_trail"]) == 3
