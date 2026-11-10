# 📊 Sales Performance Advisory Agent

An LLM-based Sales Advisory & Policy Governance Agent built with **Python 3.11+**, **LangGraph**, **Pydantic**, **Scikit-Learn (TF-IDF)**, and **Streamlit**.

The agent deterministically computes sales performance metrics from raw data, retrieves internal governance policies from multi-format playbooks (`.txt`, `.pdf`, `.docx`), enforces dynamic discount guardrails (read straight from policy documents, never hardcoded), maintains conversational memory across turns using an SQLite checkpointer, and persists corrective action plans to an auditable ledger.

---

## 🚀 Key Highlights & Architectural Guarantees

1. **Deterministic Metrics Engine (`src/metrics.py`)**:
   - Zero hallucination of numbers: The LLM **never** computes metrics itself.
   - `safe_divide`: Protects against ZeroDivisionError and NaN; guaranteed to return `None` on invalid denominators, never returning `0.0` unless actual numerator is `0`.
   - Metrics computed: Target Achievement %, MoM Sales Growth %, Gross Margin (with explicit `MISSING_COST` status if product cost is absent), Return Rate %.
   - Identifies underperforming slices (Achievement < 90%, Negative Growth, Return Rate > 5%, Low Margin < 15%).
   - Strict data validation: File presence, required columns, and non-negative value checks.

2. **Multi-format Playbook Retriever (`src/retriever.py`)**:
   - Ingests plain text (`.txt`), page-wise PDFs via `pypdf`, and DOCX files via `python-docx`.
   - Chunking with small overlap and metadata tagging: `source`, `page`, `section_title`, `product` (`Tab-X`, `Tab-Y`), and `policy_type` (`discount`, `low_revenue`, `low_margin`, `returns`, `campaign`, `general`).
   - Indexed using `scikit-learn` `TfidfVectorizer` (unigrams + bigrams) with cosine similarity search and minimum score thresholding.

3. **Relevance Grading & Bounded Rewriter (`src/grader.py`)**:
   - Verifies product alignment and problem type match for each retrieved chunk.
   - If initial retrieval is irrelevant or vague, automatically rewrites the query using domain-specific expansions up to a **strict limit of MAX 2 retries**.
   - If still irrelevant after 2 retries, returns `"no relevant policy found"` (zero hallucination guarantee).

4. **Policy Guardrails & Structured Tools (`src/guardrails.py`, `src/tools.py`)**:
   - **Dynamic Discount Limits**: Reads the standard discount ceiling dynamically from the playbook (e.g. 10%); the 10% limit is never hardcoded.
   - Enforces approval protocols:
     - Discount within limit: `APPROVED`.
     - Discount exceeding limit with written authorization: `APPROVED_WITH_EXCEPTION`.
     - Discount exceeding limit without authorization: `BLOCKED` with an explicit violation guardrail alert.
   - Exposes tools via Pydantic schemas: `get_sales_metrics`, `search_playbook`, `check_discount`, `save_action_plan`.

5. **Conversational Memory & State Machine (`src/agent.py`)**:
   - LangGraph `StateGraph` managing state: `messages`, `region`, `product`, `month`, `metrics`, `retrieved_policy`, `approval_status`.
   - Persistent `SqliteSaver` checkpointer with managed connection lifecycles and thread isolation (`thread_id`).
   - Follow-up resolution:
     - *"What about last month?"* updates **ONLY the month** (`2026-09` -> `2026-08`), while retaining `region` and `product`.
     - *"What about North?"* updates only region; product and month stay the same.
   - First-month edge case: For `2026-08` (earliest record), growth is recognized as `None`/`N/A` and clearly explained.

6. **Action Plan Ledger (`src/action_store.py`)**:
   - Central repository (`data/action_plans.json`) logging proposed corrective actions, discount conditions, and responsible owners.

---

## 📁 Project Directory Structure

```text
sales-advisory-agent/
│
├── data/
│   ├── sales.csv               # Sales transactions (units, revenue, returns)
│   ├── targets.csv             # Regional revenue quotas
│   ├── margins.xlsx            # Product unit cost (Tab-X: 2400, Tab-Y: 1800)
│   └── action_plans.json       # Persisted corrective action plans
│
├── docs/
│   ├── sales_playbook.txt      # Master markdown policy document
│   ├── sales_playbook.docx     # Word document policy edition
│   └── sales_playbook.pdf      # Multi-page PDF policy document
│
├── src/
│   ├── __init__.py
│   ├── metrics.py              # Phase 1: Math engine, data loaders, underperformer flags
│   ├── retriever.py            # Phase 2: Ingestion (.txt/.docx/.pdf), chunking, TF-IDF search
│   ├── grader.py               # Phase 3: Relevance grading, bounded query rewrites (max 2 retries)
│   ├── guardrails.py           # Phase 4: Dynamic discount extraction & approval guardrails
│   ├── action_store.py         # Phase 4: Persistent action plan repository
│   ├── tools.py                # Phase 4: Pydantic-validated LangChain tools
│   ├── agent.py                # Phase 5: LangGraph StateGraph, SqliteSaver memory, follow-up logic
│   ├── cli.py                  # Phase 6: Terminal chat interface & CLI commands
│   └── ui.py                   # Phase 6: Streamlit Web UI with KPI Dashboard & Chat
│
├── tests/
│   ├── __init__.py
│   ├── test_metrics.py         # 26 unit tests for metrics and data loading
│   ├── test_retriever.py       # 12 unit tests for multi-format ingestion & TF-IDF
│   ├── test_grader.py          # 7 unit tests for grading & bounded retries
│   ├── test_tools.py           # 12 unit tests for tools & guardrails
│   └── test_agent.py           # 7 unit tests for StateGraph memory & follow-ups
│
├── checkpoints/                # SQLite checkpoint memory database
├── .env.example                # Environment configuration template
├── .gitignore                  # Git ignore rules
├── requirements.txt            # Python dependencies
├── implementation_plan.md      # Approved phased implementation plan
└── README.md                   # System documentation
```

---

## 🛠️ Setup & Installation

### 1. Prerequisites
- Python 3.11, 3.12, 3.13, or 3.14

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Environment Configuration
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Edit `.env` to supply your OpenAI credentials (optional for testing, required for live LLM chat):
```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
```

---

## 🧪 Running the Test Suite

Run all 72 automated tests with pytest:
```bash
pytest tests/ -v
```

Run tests by module:
```bash
pytest tests/test_curriculum_scenarios.py -v # Official curriculum scenarios (72/72)
pytest tests/test_metrics.py -v              # Pure math, loaders, underperformers
pytest tests/test_retriever.py -v            # Multi-format parsing, TF-IDF ranking
pytest tests/test_grader.py -v               # Relevance grading & bounded retries
pytest tests/test_tools.py -v                # Tools, guardrails, action plans
pytest tests/test_agent.py -v                # LangGraph memory & follow-up resolution
```

### 📋 Official Curriculum Scenarios Verified
| Scenario | Input | Expected & Verified Outcome |
| :--- | :--- | :--- |
| **Zero target** | `target = 0` | Achievement shown as not available (`None`), no division error |
| **Zero prior sales** | `Prior = 0` | Growth not defined (`None`), no division error |
| **High returns** | `Return rate > 5%` | Quality/QA & quarantine action recommended, **NOT** discount |
| **Missing costs** | `No unit_cost` | Margin not computed; flagged as `MISSING_COST` |
| **Follow-up** | `'What about last month?'` | Same filters (`region`, `product`), updated period (`2026-08`) |
| **Out-of-policy discount** | `'Offer 40% off'` | **Declined**; cites 10% standard policy limit |
| **Group-by aggregation** | Period & region | Deterministic aggregation by region/month |
| **Idempotent action plans** | Duplicate save | Duplicate-safe save with unique ID, metrics & review date |

---

## 💻 Running the Applications

### Option A: Interactive CLI Terminal
Start the interactive advisory session:
```bash
python src/cli.py
```

Useful CLI commands inside the session:
- `/metrics` - Print flagged underperforming slices.
- `/plans` - Display all logged action plans.
- `/reset` - Start a new conversation thread.
- `exit` - Terminate the session.

Run a single query directly from command line:
```bash
python src/cli.py --query "Analyze South Tab-X 2026-09"
```

### Option B: Streamlit Web Interface
Launch the browser application:
```bash
streamlit run src/ui.py
```
**Web UI Features**:
- **Sidebar KPI Cards**: Real-time cards displaying Target Achievement %, MoM Growth %, Gross Margin, and Return Rate for selected slice.
- **Conversational Chat**: Interactive chat with conversational memory powered by LangGraph & SQLite.
- **Guardrail Badges**: Instant warning alerts when an unauthorized discount proposal is detected.
- **Action Plan Ledger**: Live table of all logged remediation plans with one-click **CSV Export**.

---

## 📖 Conversational Example

```text
User: "Analyze performance for Tab-X in South for 2026-09."
Advisor:
Performance Analysis for Tab-X in South (2026-09):
- Revenue: Rs. 960,000 (Target: Rs. 1,100,000)
- Target Achievement: 87.27% (Under quota)
- MoM Sales Growth: -20.0% (Previous month: Rs. 1,200,000)
- Gross Margin: Rs. 192,000 (20.0%)
- Return Rate: 8.75% (Exceeds 5% quality threshold)

User: "What about last month?"
Advisor:
Performance Analysis for Tab-X in South (2026-08):
- Revenue: Rs. 1,200,000 (Target: Rs. 1,200,000)
- Target Achievement: 100.0%
- MoM Sales Growth: N/A (First recording period) Note: 2026-08 is the first recorded month, so MoM growth is N/A.
- Gross Margin: Rs. 240,000 (20.0%)
- Return Rate: 5.0%

User: "Can I offer a 15% discount on Tab-X to boost current month sales?"
Advisor:
⚠️ POLICY GUARDRAIL: A 15% discount exceeds the playbook standard limit of 10%.
Representatives must NEVER promise unapproved discounts.
Prior written authorization from the Regional Sales Director is required.
Playbook Recommendation: Section 2 suggests offering targeted product bundling or value-add support packages instead of steep price concessions.
```
