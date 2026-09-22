# Real Estate Asset Manager — Multi-Agent Prototype (LangGraph)

A LangGraph-orchestrated assistant that answers natural-language questions
about a real-estate ledger dataset: P&L, property/period comparisons, top
tenants, and anomaly detection.

## Setup

```bash
pip install -r requirements.txt

# pick a provider and set the matching key
export LLM_PROVIDER=anthropic        # or "openai"
export ANTHROPIC_API_KEY=sk-...      # if using anthropic
# export OPENAI_API_KEY=sk-...       # if using openai

streamlit run app.py
```

Place your dataset at `data/ledger.parquet` (already included here).

## Dataset

`data/ledger.parquet` — 3,924 rows, one row per ledger entry:

| column | meaning |
|---|---|
| `entity_name` | owning legal entity (single value: PropCo) |
| `property_name` | one of 5 buildings; **null for corporate-level entries** (bank charges, director's fee — not tied to a building) |
| `tenant_name` | one of 18 tenants; null for non-tenant-specific entries |
| `ledger_type` | `revenue` or `expenses` |
| `ledger_group` / `ledger_category` | classification of the entry |
| `month` / `quarter` / `year` | period, as strings (`2025-M01`, `2025-Q1`, `2025`) |
| `profit` | signed amount |

This is a P&L ledger, not a price/valuation table — the task's original
"price comparison" example doesn't map onto this data, so "comparison"
here means comparing revenue/expenses/net between properties or periods.
Questions asking for market price/valuation are explicitly treated as
**unsupported** (see below) rather than answered with made-up numbers.

## Architecture

```
START
  |
  v
[router] -- classifies intent(s): pnl_summary, property_comparison,
  |         period_comparison, top_tenants, anomaly_detection,
  |         property_details, general_knowledge, unsupported
  |
  +--> general_knowledge / unsupported --> END
  |
  v
[extractor] -- pulls properties / tenants / period(s) out of the text
  |
  v
[validator] -- fuzzy-matches extracted names against the real dataset,
  |            checks periods exist, decides if a two-period compare
  |            is missing its second period
  |
  +--> invalid/ambiguous --> [clarification] --> END
  |
  v
[calculator] -- one deterministic data_tools.py call per intent
  |             (sums, comparisons, ranking, z-score outlier detection)
  v
[responder] -- turns the JSON results into a natural-language answer
  |
  v
END
```

Implemented in `graph.py` with `langgraph.graph.StateGraph`. Routing after
`router` and after `validator` uses `add_conditional_edges`, so a query
that fails validation never reaches the data layer, and a general-knowledge
question never touches the dataset at all.

**Why intents is a list, not a single value:** compound questions ("who are
my top tenants, *and* is anything unusual") are explicitly required by the
task. The calculator node loops over every recognized intent and the
responder stitches the results into one answer, so one extra intent doesn't
need a new graph branch — it's just another key in `results`.

**Why the LLM never touches the raw DataFrame:** all data access goes
through typed functions in `data_tools.py` (`pnl_summary`,
`compare_properties`, `top_tenants`, `detect_anomalies`, ...). This keeps
every number in the final answer traceable to a deterministic pandas
computation instead of something the model computed itself, and lets the
data layer be unit-tested independent of any LLM call (see "Testing"
below).

## Error handling

| Situation | Behavior |
|---|---|
| Property/tenant name not in the dataset | Fuzzy-matched (rapidfuzz) against real names; a digit-mismatch guard stops "Building 500" from silently matching "Building 120" just because the strings look similar. No confident match → routed to `clarification` with a suggestion. |
| Year not in the dataset | Flagged in validation, routed to `clarification`, lists the years that do exist. |
| `period_comparison` intent with only one period given | Routed to `clarification` asking for the second period. |
| Corporate-level rows (no `property_name`) | Excluded from `top_tenants` scoring; labeled explicitly as "corporate-level" rather than attributed to a building when they show up in anomalies. |
| Question outside the dataset (e.g. asking for a sale price) | Classified as `unsupported` by the router, short-circuits before extraction, explains what the dataset *can* answer instead of guessing. |
| Truly unrelated question ("what is P&L?") | Classified as `general_knowledge`, answered directly without touching the ledger. |

## Anomaly detection method

For each `ledger_category`, rows more than 3 standard deviations from that
category's mean `profit` are flagged (skipped for categories with fewer
than 4 rows — not enough signal for a mean/std to be meaningful). This is
a simple, explainable baseline; swapping in IQR or a per-property baseline
would be a natural next iteration.

## Testing

Every `data_tools.py` function is deterministic and was smoke-tested
independently of the LLM (see commit history / can be turned into
`pytest` cases): P&L sums, property comparison, fuzzy-match rejecting a
nonexistent property number, and anomaly detection returning
"corporate-level" instead of crashing on null `property_name`/`tenant_name`.

The graph's non-LLM nodes (`validator_node`, `calculator_node`,
`route_after_validator`) were also run directly against hand-built state to
confirm the compound-query path and the clarification path both produce
the expected routing without needing an API key.

## Known limitations / next steps

- Router and extractor are two separate LLM calls; could be merged into one
  structured-output call to cut latency, at the cost of a fuzzier
  separation of concerns.
- No conversation memory across turns yet — `MemorySaver` checkpointing
  would let a clarification answer feed back into the same query context
  instead of starting over.
- Anomaly threshold (3 std devs) is a fixed constant; a config option or a
  per-category calibration would be more robust on a larger dataset.
