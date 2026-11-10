# Implementation Plan - Sales Performance Advisory Agent

A production-grade, modular, LLM-based advisory agent built with Python, LangGraph, Pydantic, and scikit-learn. The agent deterministically computes sales metrics, retrieves policy guidelines with TF-IDF, validates retrieval relevance with a bounded query-rewriting grader, enforces discount/policy guardrails, preserves conversation memory via SQLite checkpointing, and logs approved action plans.

---

## Technical Stack & Architecture

- **Python**: 3.11+
- **Data & Calculations**: `pandas`, `openpyxl`
- **Document Ingestion**: `pypdf`, `python-docx`
- **Retrieval Engine**: `scikit-learn` (TF-IDF vectorizer + Cosine Similarity)
- **Agent Framework**: `langgraph`, `langgraph-checkpoint-sqlite`, `langchain-core`, `langchain-openai`
- **Data Schemas & Validation**: `pydantic` v2
- **Testing**: `pytest`
- **Environment & Config**: `python-dotenv` (reading `OPENAI_API_KEY`, `OPENAI_MODEL`)

---

## Directory Structure

```text
sales-advisory-agent/
│
├── data/
│   ├── sales.csv
│   ├── targets.csv
│   └── margins.xlsx
│
├── docs/
│   ├── sales_playbook.txt
│   ├── sales_playbook.docx
│   └── sales_playbook.pdf
│
├── src/
│   ├── __init__.py
│   ├── metrics.py        # Phase 1: Pure functions, safe math, data loaders
│   ├── retriever.py      # Phase 2: Ingestion (.txt/.docx/.pdf), chunking, TF-IDF index & search
│   ├── grader.py         # Phase 3: Relevance scoring, bounded query rewriting (max 2 retries)
│   ├── guardrails.py     # Guardrail enforcement: dynamic discount extraction & approval checking
│   ├── action_store.py   # Persistent action plans (JSON/SQLite storage)
│   ├── tools.py          # Phase 4: Pydantic tool schemas & LangChain tool bindings
│   ├── agent.py          # Phase 5: LangGraph StateGraph, SqliteSaver memory, follow-up state handling
│   └── cli.py            # Phase 6: Interactive chat CLI loop with formatted outputs
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_metrics.py
│   ├── test_retriever.py
│   ├── test_grader.py
│   ├── test_tools.py
│   └── test_agent.py
│
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
└── implementation_plan.md
```

---

## Implementation Phases

### Phase 1: Deterministic Metrics Engine (`src/metrics.py`)
- **Deliverables**:
  - `safe_divide(numerator, denominator)`: Returns `None` on zero, negative denominator, or NaN; never 0.0 unless numerator is 0.
  - Data loaders for `sales.csv`, `targets.csv`, `margins.xlsx` with strict schema validation (missing files, missing columns, negative values check).
  - Metric calculations:
    - Target achievement rate % (`revenue / target_revenue * 100`).
    - MoM sales growth % (`(current_rev - prev_rev) / prev_rev * 100`) with previous month lookup.
    - Gross margin (`revenue - (units * unit_cost)`), returning `MISSING_COST` if cost data is absent.
    - Return rate % (`returns / units * 100`).
    - `identify_underperformers`: Identifies problem areas (low achievement, negative growth, high return rate, low margin).
- **Tests (`tests/test_metrics.py`)**:
  - Division by zero returns `None`.
  - Zero previous sales handling.
  - Missing product in margin data -> `MISSING_COST`.
  - High returns rate calculation.
  - Negative sales/returns validation error.
  - Missing required columns & missing rows.

### Phase 2: Multi-format Playbook Retriever (`src/retriever.py`)
- **Deliverables**:
  - Document loaders for TXT, PDF (page-aware using `pypdf`), and DOCX (`python-docx`).
  - Section-aware chunking with slight overlap and metadata tagging: `source`, `page`, `section_title`, `product` (if specific), `policy_type` (`discount`, `low_revenue`, `low_margin`, `returns`, `campaign`).
  - TF-IDF indexing with `scikit-learn` `TfidfVectorizer` and cosine similarity search.
  - Top-k retrieval with similarity score filtering (`min_score_threshold`).
  - Playbook sample creation across `.txt`, `.docx`, and `.pdf` formats.
- **Tests (`tests/test_retriever.py`)**:
  - Metadata verification for chunks from TXT, PDF, DOCX.
  - TF-IDF search returns expected policy sections.
  - Threshold filtering weeds out irrelevant queries.

### Phase 3: Retrieval Relevance Grader & Bounded Rewriter (`src/grader.py`)
- **Deliverables**:
  - Relevance assessment logic: Validates if retrieved chunk matches target product (or is general policy) AND problem type (e.g., discount vs. return rate vs. underperformance).
  - Bounded query rewriting: If chunk relevance score is below threshold or fails keyword/intent matching, rewrites query using domain heuristics / LLM grader up to a strict limit of **MAX 2 retries**.
  - Fallback: If still irrelevant after 2 retries, returns `"no relevant policy found"` (zero hallucination guarantee).
- **Tests (`tests/test_grader.py`)**:
  - Relevant chunk accepts immediately on attempt 0.
  - Irrelevant chunk triggers query rewriting and successfully retrieves on retry.
  - Exhausted retries (2 retries fail) returns `"no relevant policy found"`.

### Phase 4: Structured Tool Calling & Guardrails (`src/tools.py`, `src/guardrails.py`, `src/action_store.py`)
- **Deliverables**:
  - Pydantic models for inputs and outputs.
  - `get_sales_metrics`: Returns deterministic metrics summary for given region, product, and month.
  - `search_playbook`: Calls retriever with grading & bounded retry.
  - `check_discount`: Validates requested discount against the dynamic policy limit retrieved from the playbook (e.g. 10% standard limit, escalation required above).
  - `save_action_plan`: Persists structured action plan (issue, proposed action, discount approval status, owner, timestamp).
  - Policy guardrails in `guardrails.py` preventing unauthorized discount recommendations.
- **Tests (`tests/test_tools.py`)**:
  - Schema validation for inputs/outputs.
  - Tool execution outputs match expected structures.
  - Guardrail blocks discounts above policy threshold without manager approval.
  - Action plan saves and verifies persistence.

### Phase 5: Agent State, Memory & LangGraph Workflow (`src/agent.py`)
- **Deliverables**:
  - LangGraph `StateGraph` with typed state:
    - `messages`: List of chat messages (user, assistant, tool).
    - `region`: Current region context (e.g., "South").
    - `product`: Current product context (e.g., "Tab-X").
    - `month`: Current month context (e.g., "2026-09").
    - `metrics`: Computed metrics dictionary.
    - `retrieved_policy`: Playbook excerpt / policy text.
    - `approval_status`: Approval state for exceptions.
  - SQLite checkpointer (`SqliteSaver`) with proper connection context manager to ensure safe persistence.
  - State preservation & follow-up resolution: Handles follow-ups like "What about last month?" by updating only `month` while keeping `region` and `product`.
  - Edge cases: First month of data (no previous month available for growth calculation).
- **Tests (`tests/test_agent.py`)**:
  - Conversation memory across turns using `thread_id`.
  - Follow-up context retention (region/product preserved when month changes).
  - First-month edge case handling.
  - Tool calling execution loop verification (using mocked/live LLM).

### Phase 6: CLI Interface, Sample Datasets & Documentation (`src/cli.py`, `README.md`)
- **Deliverables**:
  - Interactive CLI loop with clean streaming/printing and colorized metrics table.
  - Complete demo dataset in `data/` and docs in `docs/`.
  - Comprehensive `README.md` with installation, setup, architecture diagram, and example sessions.
  - Environment template `.env.example`.

---

## Verification & Execution Protocol

1. **Phase Gate**: Each phase will be implemented sequentially.
2. **Pytest Run**: After every phase, all test suites will run via `pytest -v`.
3. **User Confirmation**: After tests pass and output is displayed, execution will **STOP** and wait for user confirmation before proceeding to the next phase.
