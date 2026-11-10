"""Playbook retrieval engine with multi-format ingestion and TF-IDF search.

Supports TXT, PDF (page-wise with pypdf), and DOCX (python-docx).
Chunks documents with overlap and enriches each chunk with metadata:
- source (filename / path)
- page (1-based int)
- section_title
- product (e.g. Tab-X, Tab-Y, or None)
- policy_type (discount / low_revenue / low_margin / returns / campaign / general)
"""

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import re

import docx
import numpy as np
import pypdf
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


@dataclass
class PlaybookChunk:
    """Represents a chunk of policy text with rich metadata."""
    text: str
    source: str
    page: int
    section_title: str
    product: Optional[str] = None
    policy_type: str = "general"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Metadata inference helpers
# ---------------------------------------------------------------------------

KNOWN_PRODUCTS = ["Tab-X", "Tab-Y"]

POLICY_TYPE_KEYWORDS = {
    "discount": ["discount", "pricing", "concession", "authorization", "approval", "margin lock"],
    "low_revenue": ["revenue", "target", "achievement", "quota", "underperformance", "pipeline velocity"],
    "low_margin": ["margin", "unit cost", "cogs", "landed cost", "landed costs", "landed", "gross margin"],
    "returns": ["return", "returns", "defective", "refund", "qa", "quarantine", "replacement"],
    "campaign": ["campaign", "promotion", "marketing", "partner", "co-op", "bundling"],
}


def infer_product(text: str) -> Optional[str]:
    """Detect if chunk pertains specifically to a known product.

    Returns the product string (e.g. "Tab-X", "Tab-Y") or None if general / mentions both.
    """
    found = [p for p in KNOWN_PRODUCTS if re.search(rf"\b{re.escape(p)}\b", text, re.IGNORECASE)]
    if len(found) == 1:
        return found[0]
    return None


def infer_policy_type(text: str, section_title: str = "") -> str:
    """Classify the chunk into one of the playbook policy categories."""
    combined = f"{section_title} {text}".lower()

    # Score matches by category
    scores: Dict[str, int] = {}
    for ptype, keywords in POLICY_TYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if re.search(rf"\b{re.escape(kw)}\b", combined))
        if score > 0:
            scores[ptype] = score

    if not scores:
        return "general"

    # Return category with highest keyword frequency
    return max(scores, key=scores.get)  # type: ignore


# ---------------------------------------------------------------------------
# Chunking logic with small overlap
# ---------------------------------------------------------------------------

def split_text_with_overlap(
    text: str,
    chunk_size: int = 350,
    chunk_overlap: int = 50,
) -> List[str]:
    """Split long text into chunks of roughly `chunk_size` characters with `chunk_overlap`.

    Splits along sentence/paragraph boundaries when possible.
    """
    text = text.strip()
    if not text:
        return []

    if len(text) <= chunk_size:
        return [text]

    # Split into paragraphs or sentences
    paragraphs = [p.strip() for p in re.split(r"\n{2,}|\n(?=[A-Z0-9\-\*])", text) if p.strip()]

    chunks: List[str] = []
    current_chunk = ""

    for para in paragraphs:
        if not current_chunk:
            current_chunk = para
        elif len(current_chunk) + len(para) + 1 <= chunk_size:
            current_chunk += "\n" + para
        else:
            chunks.append(current_chunk)
            # Create overlap from end of current chunk
            overlap_prefix = current_chunk[-chunk_overlap:] if len(current_chunk) > chunk_overlap else current_chunk
            current_chunk = overlap_prefix + "\n" + para

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


# ---------------------------------------------------------------------------
# Multi-format Document Loaders
# ---------------------------------------------------------------------------

def load_txt_document(filepath: Union[str, Path]) -> List[PlaybookChunk]:
    """Load and parse plain text playbook file.

    Extracts sections designated by markdown headings (# or ## SECTION ...).
    Skips empty title headers without body text.
    """
    path = Path(filepath)
    if not path.is_file():
        raise FileNotFoundError(f"TXT file not found: {path}")

    raw_text = path.read_text(encoding="utf-8")
    chunks: List[PlaybookChunk] = []

    # Split by markdown headers (# or ##)
    section_splits = re.split(r"(?m)^(?=#{1,3}\s+)", raw_text)

    for section in section_splits:
        section = section.strip()
        if not section:
            continue

        # Extract title if present
        title_match = re.match(r"^#{1,3}\s+(.+)$", section, re.MULTILINE)
        section_title = title_match.group(1).strip() if title_match else "General Policy"

        # Check if there is body text under this header
        body_lines = [line for line in section.splitlines() if not re.match(r"^#{1,3}\s+", line)]
        body_text = "\n".join(body_lines).strip()
        if not body_text:
            continue

        sub_chunks = split_text_with_overlap(section, chunk_size=400, chunk_overlap=50)
        for sc in sub_chunks:
            p_type = infer_policy_type(sc, section_title)
            prod = infer_product(sc)
            chunks.append(PlaybookChunk(
                text=sc,
                source=path.name,
                page=1,
                section_title=section_title,
                product=prod,
                policy_type=p_type,
            ))

    return chunks


def load_pdf_document(filepath: Union[str, Path]) -> List[PlaybookChunk]:
    """Load PDF document page-by-page using pypdf.

    Preserves 1-based page metadata for each chunk.
    """
    path = Path(filepath)
    if not path.is_file():
        raise FileNotFoundError(f"PDF file not found: {path}")

    reader = pypdf.PdfReader(str(path))
    chunks: List[PlaybookChunk] = []

    for page_idx, page in enumerate(reader.pages):
        page_num = page_idx + 1
        page_text = page.extract_text() or ""
        page_text = page_text.strip()
        if not page_text:
            continue

        # Detect section title if any
        title_match = re.search(r"(?:Section\s+\d+|[A-Z\s]{4,}):?\s*([^\n\.]+)", page_text)
        section_title = title_match.group(0).strip() if title_match else f"Page {page_num} Section"

        sub_chunks = split_text_with_overlap(page_text, chunk_size=400, chunk_overlap=50)
        for sc in sub_chunks:
            chunks.append(PlaybookChunk(
                text=sc,
                source=path.name,
                page=page_num,
                section_title=section_title,
                product=infer_product(sc),
                policy_type=infer_policy_type(sc, section_title),
            ))

    return chunks


def load_docx_document(filepath: Union[str, Path]) -> List[PlaybookChunk]:
    """Load DOCX document using python-docx.

    Groups text under headings and segments into metadata-enriched chunks.
    """
    path = Path(filepath)
    if not path.is_file():
        raise FileNotFoundError(f"DOCX file not found: {path}")

    doc = docx.Document(str(path))
    chunks: List[PlaybookChunk] = []

    current_title = "General Policy"
    current_paragraphs: List[str] = []

    def flush_section():
        nonlocal current_paragraphs
        if not current_paragraphs:
            return
        section_text = "\n".join(current_paragraphs).strip()
        if section_text:
            sub_chunks = split_text_with_overlap(section_text, chunk_size=400, chunk_overlap=50)
            for sc in sub_chunks:
                chunks.append(PlaybookChunk(
                    text=sc,
                    source=path.name,
                    page=1,
                    section_title=current_title,
                    product=infer_product(sc),
                    policy_type=infer_policy_type(sc, current_title),
                ))
        current_paragraphs = []

    for p in doc.paragraphs:
        txt = p.text.strip()
        if not txt:
            continue
        if p.style.name.startswith("Heading"):
            flush_section()
            current_title = txt
        else:
            current_paragraphs.append(txt)

    flush_section()
    return chunks


def load_document(filepath: Union[str, Path]) -> List[PlaybookChunk]:
    """Universal document loader branching by file extension."""
    path = Path(filepath)
    ext = path.suffix.lower()
    if ext == ".txt":
        return load_txt_document(path)
    elif ext == ".pdf":
        return load_pdf_document(path)
    elif ext in [".docx", ".doc"]:
        return load_docx_document(path)
    else:
        raise ValueError(f"Unsupported document format: {ext} (supported: .txt, .pdf, .docx)")


# ---------------------------------------------------------------------------
# TF-IDF + Cosine Similarity Retriever
# ---------------------------------------------------------------------------

class PlaybookRetriever:
    """TF-IDF indexed vector search over playbook chunks with cosine similarity."""

    def __init__(
        self,
        min_score_threshold: float = 0.08,
        stop_words: str = "english",
    ) -> None:
        """Initialize the retriever.

        Args:
            min_score_threshold: Minimum cosine similarity score required for top-k results.
            stop_words: Stop words setting for TfidfVectorizer.
        """
        self.min_score_threshold = min_score_threshold
        self.vectorizer = TfidfVectorizer(
            stop_words=stop_words,
            ngram_range=(1, 2),
            lowercase=True,
        )
        self.chunks: List[PlaybookChunk] = []
        self.tfidf_matrix: Optional[np.ndarray] = None

    def add_documents(self, chunks: List[PlaybookChunk]) -> None:
        """Add chunks to the retriever and fit the TF-IDF matrix."""
        if not chunks:
            return

        self.chunks.extend(chunks)
        corpus = [c.text for c in self.chunks]
        self.tfidf_matrix = self.vectorizer.fit_transform(corpus)

    def load_directory(self, docs_dir: Union[str, Path]) -> int:
        """Load and index all supported documents (.txt, .pdf, .docx) from directory."""
        dir_path = Path(docs_dir)
        if not dir_path.is_dir():
            raise NotADirectoryError(f"Directory not found: {dir_path}")

        all_chunks: List[PlaybookChunk] = []
        for file in sorted(dir_path.iterdir()):
            if file.suffix.lower() in [".txt", ".pdf", ".docx"]:
                try:
                    loaded = load_document(file)
                    all_chunks.extend(loaded)
                except Exception as err:
                    # Skip problematic files gracefully
                    print(f"Warning: Failed to load {file.name}: {err}")

        self.chunks = []
        self.add_documents(all_chunks)
        return len(self.chunks)

    def search(
        self,
        query: str,
        top_k: int = 3,
        filter_policy_type: Optional[str] = None,
        filter_product: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search the indexed playbook for chunks matching the query.

        Args:
            query: User or agent question / search string.
            top_k: Number of highest-ranking chunks to retrieve.
            filter_policy_type: Optional policy type filter (e.g. 'discount').
            filter_product: Optional product filter (e.g. 'Tab-X').

        Returns:
            List of result dicts: {"chunk": chunk.to_dict(), "score": float}
            filtered by min_score_threshold.
        """
        if not query.strip() or self.tfidf_matrix is None or not self.chunks:
            return []

        query_vec = self.vectorizer.transform([query])
        similarities = cosine_similarity(query_vec, self.tfidf_matrix)[0]

        # Get indices sorted descending
        ranked_indices = np.argsort(similarities)[::-1]

        results: List[Dict[str, Any]] = []
        for idx in ranked_indices:
            score = float(similarities[idx])
            if score < self.min_score_threshold:
                break

            chunk = self.chunks[idx]

            # Apply metadata filters if specified
            if filter_policy_type and chunk.policy_type != filter_policy_type and chunk.policy_type != "general":
                continue
            if filter_product and chunk.product and chunk.product.lower() != filter_product.lower():
                continue

            results.append({
                "chunk": chunk.to_dict(),
                "score": round(score, 4),
            })

            if len(results) >= top_k:
                break

        return results
