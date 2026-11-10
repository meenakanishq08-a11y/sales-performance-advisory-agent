"""Test suite for playbook ingestion, metadata enrichment, and TF-IDF retrieval."""

from pathlib import Path
import pytest
from src.retriever import (
    PlaybookChunk,
    PlaybookRetriever,
    infer_policy_type,
    infer_product,
    load_docx_document,
    load_pdf_document,
    load_txt_document,
    load_document,
    split_text_with_overlap,
)


# ---------------------------------------------------------------------------
# Test Metadata Inference
# ---------------------------------------------------------------------------

def test_infer_product():
    assert infer_product("This applies to Tab-X units only.") == "Tab-X"
    assert infer_product("Discount policy on Tab-Y models.") == "Tab-Y"
    # When both or none are mentioned, return None
    assert infer_product("Applies to both Tab-X and Tab-Y.") is None
    assert infer_product("General organizational policy.") is None


def test_infer_policy_type():
    assert infer_policy_type("Maximum standard discount allowed is 10%.", "Discount Policy") == "discount"
    assert infer_policy_type("Target achievement below 90% or negative revenue growth.", "Low Revenue") == "low_revenue"
    assert infer_policy_type("Gross margin percentage drops below 15% on unit cost.", "Margin Rules") == "low_margin"
    assert infer_policy_type("Product return rate exceeds 5% and requires quarantine.", "Returns") == "returns"
    assert infer_policy_type("Push campaigns and bundling incentives for partners.", "Campaign") == "campaign"
    assert infer_policy_type("General notes on office timings.", "Miscellaneous") == "general"


# ---------------------------------------------------------------------------
# Test Chunking with Overlap
# ---------------------------------------------------------------------------

def test_split_text_with_overlap():
    text = "Paragraph one with some text.\n\nParagraph two with another text.\n\nParagraph three continues here."
    chunks = split_text_with_overlap(text, chunk_size=50, chunk_overlap=15)
    assert len(chunks) >= 2
    # Check that chunks are non-empty strings
    assert all(len(c) > 0 for c in chunks)


def test_split_short_text():
    short = "Short single sentence."
    chunks = split_text_with_overlap(short, chunk_size=100, chunk_overlap=20)
    assert len(chunks) == 1
    assert chunks[0] == short


# ---------------------------------------------------------------------------
# Test Multi-format Loaders
# ---------------------------------------------------------------------------

def test_load_txt_document_metadata():
    chunks = load_txt_document("docs/sales_playbook.txt")
    assert len(chunks) >= 4

    # Verify chunk structure and metadata
    first = chunks[0]
    assert isinstance(first, PlaybookChunk)
    assert first.source == "sales_playbook.txt"
    assert first.page == 1
    assert "SECTION 1" in first.section_title or "DISCOUNT" in first.section_title.upper()
    assert first.policy_type == "discount"

    # Check for presence of returns policy
    returns_chunk = next(c for c in chunks if c.policy_type == "returns")
    assert "return" in returns_chunk.text.lower()


def test_load_docx_document_metadata():
    chunks = load_docx_document("docs/sales_playbook.docx")
    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk.source == "sales_playbook.docx"
        assert chunk.page == 1
        assert chunk.section_title != ""
        assert chunk.policy_type in ["discount", "returns", "general", "low_revenue", "low_margin", "campaign"]


def test_load_pdf_document_page_wise():
    chunks = load_pdf_document("docs/sales_playbook.pdf")
    assert len(chunks) >= 5

    # Check page 1 chunk (discount limits)
    p1 = [c for c in chunks if c.page == 1]
    assert len(p1) > 0
    assert "discount" in p1[0].text.lower() or "page 1" in p1[0].text.lower()

    # Check page 2 chunk (low revenue actions)
    p2 = [c for c in chunks if c.page == 2]
    assert len(p2) > 0
    assert "revenue" in p2[0].text.lower() or p2[0].policy_type == "low_revenue"

    # Check page 3 chunk (low margin actions)
    p3 = [c for c in chunks if c.page == 3]
    assert len(p3) > 0
    assert "margin" in p3[0].text.lower() or p3[0].policy_type == "low_margin"


def test_load_document_unsupported_format(tmp_path):
    invalid_file = tmp_path / "notes.xyz"
    invalid_file.write_text("dummy")
    with pytest.raises(ValueError, match="Unsupported document format"):
        load_document(invalid_file)


# ---------------------------------------------------------------------------
# Test TF-IDF Vector Index & Cosine Similarity Search
# ---------------------------------------------------------------------------

def test_retriever_search_discount_policy():
    retriever = PlaybookRetriever(min_score_threshold=0.05)
    total_loaded = retriever.load_directory("docs")
    assert total_loaded > 0

    results = retriever.search("Can I offer a 15% discount on Tab-X?", top_k=3)
    assert len(results) > 0

    top = results[0]
    assert "score" in top
    assert top["score"] >= 0.05
    assert "chunk" in top
    chunk = top["chunk"]
    assert "discount" in chunk["text"].lower() or chunk["policy_type"] == "discount"


def test_retriever_search_returns_policy():
    retriever = PlaybookRetriever(min_score_threshold=0.05)
    retriever.load_directory("docs")

    results = retriever.search("What is the protocol for high product return rate?", top_k=2)
    assert len(results) > 0

    top_chunk = results[0]["chunk"]
    assert "return" in top_chunk["text"].lower() or top_chunk["policy_type"] == "returns"


def test_retriever_min_score_threshold_filters_gibberish():
    retriever = PlaybookRetriever(min_score_threshold=0.15)
    retriever.load_directory("docs")

    # Complete gibberish query should produce no results above threshold
    results = retriever.search("xylophone quantum astrophysics submarine nebula", top_k=3)
    assert len(results) == 0


def test_retriever_metadata_filtering():
    retriever = PlaybookRetriever(min_score_threshold=0.01)
    retriever.load_directory("docs")

    # Filter specifically for returns policy
    results = retriever.search("What should we do?", top_k=5, filter_policy_type="returns")
    for r in results:
        assert r["chunk"]["policy_type"] in ["returns", "general"]
