"""
LangGraph workflow for the real-estate asset-management assistant.

Graph shape:

    START
      |
      v
    [router]  -- classifies intents, drafts entities
      |
      +--> unsupported intent --> [general_knowledge] --> END
      |
      v
    [extractor]  -- precise entity extraction (properties/tenants/period)
      |
      v
    [validator]  -- checks entities against the real dataset, fuzzy-matches typos
      |
      +--> invalid / ambiguous --> [clarification] --> END
      |
      v
    [retrieval]   -- pulls the data each intent needs via data_tools.py
      |
      v
    [calculator]  -- pnl sums, comparisons, top tenants, anomaly detection
      |
      v
    [responder]   -- turns results into a natural-language answer
      |
      v
    END

Design notes (see README for the full writeup):
- Router and Extractor are kept separate: the router only needs a cheap,
  coarse classification to decide which branch to take; the extractor
  does the more expensive, precise slot-filling only once we know we're
  on the data path.
- All dataset access goes through data_tools.py functions (never raw
  DataFrame access from the LLM) so arithmetic is deterministic and
  testable independently of the LLM.
- intents is a list, not a single value, so compound questions like
  "who are my top tenants and is anything unusual" run both branches
  and get stitched together by the responder.
"""
from __future__ import annotations

import json
import os
from typing import Annotated, Literal, Optional, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, END

import data_tools as dt

VALID_INTENTS = [
    "pnl_summary",
    "property_comparison",
    "period_comparison",
    "top_tenants",
    "anomaly_detection",
    "property_details",
    "general_knowledge",
    "unsupported",
]


# --------------------------------------------------------------------------
# LLM setup — swap providers by changing get_llm(). Structured output is
# used everywhere an LLM produces something downstream code will parse,
# so we never depend on regexing free text out of a chat completion.
# --------------------------------------------------------------------------
def get_llm():
    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"), temperature=0)
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5"), temperature=0)


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------
class Entities(TypedDict, total=False):
    properties: list[str]
    tenants: list[str]
    period_a: dict  # {"year": "2025", "quarter": "2025-Q1", "month": "2025-M01"}
    period_b: dict  # only for period_comparison


class AgentState(TypedDict, total=False):
    query: str
    intents: list[str]
    entities: Entities
    validation_errors: list[str]
    needs_clarification: bool
    clarification_question: str
    results: dict
    response: str


# --------------------------------------------------------------------------
# Node: router — coarse intent classification
# --------------------------------------------------------------------------
ROUTER_SYSTEM = """You classify a real-estate asset-management question into one or more intents.
Valid intents: {intents}
- pnl_summary: total revenue/expenses/net for a property or the whole portfolio
- property_comparison: comparing 2+ properties on some metric
- period_comparison: comparing two time periods (this quarter vs last, YoY, etc.)
- top_tenants: ranking tenants by revenue
- anomaly_detection: "anything unusual", outliers, irregular numbers
- property_details: general info about one property
- general_knowledge: not about this dataset at all (e.g. "what is P&L?")
- unsupported: cannot be answered with this dataset (e.g. asking for a property price/valuation, which this ledger does not contain)

A question can have multiple intents. Respond with ONLY a JSON object:
{{"intents": ["..."]}}"""


def router_node(state: AgentState) -> dict:
    llm = get_llm()
    msg = llm.invoke(
        [
            SystemMessage(content=ROUTER_SYSTEM.format(intents=VALID_INTENTS)),
            HumanMessage(content=state["query"]),
        ]
    )
    try:
        parsed = json.loads(_strip_fences(msg.content))
        intents = [i for i in parsed.get("intents", []) if i in VALID_INTENTS] or ["unsupported"]
    except Exception:
        intents = ["unsupported"]
    return {"intents": intents}


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def route_after_router(state: AgentState) -> Literal["extractor", "general_knowledge"]:
    intents = state.get("intents", [])
    if intents == ["general_knowledge"] or intents == ["unsupported"]:
        return "general_knowledge"
    return "extractor"


# --------------------------------------------------------------------------
# Node: general_knowledge / unsupported — short-circuit, skip the data path
# --------------------------------------------------------------------------
def general_knowledge_node(state: AgentState) -> dict:
    if "unsupported" in state.get("intents", []):
        response = (
            "I can't answer that from this dataset — it only contains ledger "
            "entries (revenue/expenses by property, tenant and period), not "
            "market valuations or prices. I can help with P&L, comparisons "
            "between properties or periods, top tenants, or unusual entries."
        )
        return {"response": response}
    llm = get_llm()
    msg = llm.invoke(
        [
            SystemMessage(content="Answer briefly and factually. This is a side question, not about the user's data."),
            HumanMessage(content=state["query"]),
        ]
    )
    return {"response": msg.content}


# --------------------------------------------------------------------------
# Node: extractor — precise slot filling
# --------------------------------------------------------------------------
EXTRACTOR_SYSTEM = """Extract structured entities from a real-estate ledger question.
Known properties in the dataset: {properties}
Known tenants: {tenants}
Data covers years {years}, quarters {quarters} (format YYYY-Qn), months (format YYYY-Mnn).
Today's context: assume "this year"/"this quarter" refers to the most recent period present in the data ({years}).

Return ONLY a JSON object with this shape (omit keys you have no info for):
{{
  "properties": ["..."],       // property names as mentioned, even if misspelled
  "tenants": ["..."],
  "period_a": {{"year": "...", "quarter": "...", "month": "..."}},
  "period_b": {{"year": "...", "quarter": "...", "month": "..."}}   // only if comparing two periods
}}
If nothing is mentioned for a slot, omit it — do not guess a property or tenant that wasn't referenced."""


def extractor_node(state: AgentState) -> dict:
    llm = get_llm()
    msg = llm.invoke(
        [
            SystemMessage(
                content=EXTRACTOR_SYSTEM.format(
                    properties=dt.get_properties(),
                    tenants=dt.get_tenants(),
                    years=dt.get_years(),
                    quarters=dt.get_quarters(),
                )
            ),
            HumanMessage(content=state["query"]),
        ]
    )
    try:
        entities = json.loads(_strip_fences(msg.content))
    except Exception:
        entities = {}
    return {"entities": entities}


# --------------------------------------------------------------------------
# Node: validator — check entities against the real dataset
# --------------------------------------------------------------------------
def validator_node(state: AgentState) -> dict:
    entities = dict(state.get("entities", {}))
    errors: list[str] = []
    clarification = None

    if entities.get("properties"):
        match = dt.fuzzy_match(entities["properties"], dt.get_properties())
        entities["properties"] = match.matched
        if match.unmatched:
            for u in match.unmatched:
                suggestion = match.suggestions.get(u)
                if suggestion:
                    errors.append(f"'{u}' isn't a property in the data — did you mean '{suggestion}'?")
                else:
                    errors.append(f"'{u}' isn't a property in the data. Available: {', '.join(dt.get_properties())}")

    if entities.get("tenants"):
        match = dt.fuzzy_match(entities["tenants"], dt.get_tenants())
        entities["tenants"] = match.matched
        if match.unmatched:
            for u in match.unmatched:
                errors.append(f"'{u}' isn't a tenant in the data. Available: {', '.join(dt.get_tenants())}")

    for pk in ("period_a", "period_b"):
        period = entities.get(pk)
        if period and period.get("year") and str(period["year"]) not in dt.get_years():
            errors.append(f"No data for year {period['year']}. Available years: {', '.join(dt.get_years())}")

    needs_clarification = False
    if errors:
        needs_clarification = True
        clarification = " ".join(errors) + " Could you clarify?"

    # period_comparison intent needs two periods — ask if only one was given
    if "period_comparison" in state.get("intents", []) and not entities.get("period_b"):
        needs_clarification = True
        clarification = (
            "Which two periods would you like to compare? For example "
            "'2025-Q2 vs 2024-Q2', or say 'this quarter vs last year'."
        )

    return {
        "entities": entities,
        "validation_errors": errors,
        "needs_clarification": needs_clarification,
        "clarification_question": clarification or "",
    }


def route_after_validator(state: AgentState) -> Literal["clarification", "retrieval"]:
    return "clarification" if state.get("needs_clarification") else "retrieval"


def clarification_node(state: AgentState) -> dict:
    return {"response": state["clarification_question"]}


# --------------------------------------------------------------------------
# Node: retrieval + calculator — merged since each intent's "fetch" and
# "compute" step is a single deterministic data_tools call
# --------------------------------------------------------------------------
def calculator_node(state: AgentState) -> dict:
    entities = state.get("entities", {})
    properties = entities.get("properties") or None
    period = entities.get("period_a") or None
    results: dict = {}

    for intent in state.get("intents", []):
        if intent == "pnl_summary":
            results["pnl_summary"] = dt.pnl_summary(properties=properties, period=period)

        elif intent == "property_comparison":
            props = properties or dt.get_properties()
            results["property_comparison"] = dt.compare_properties(props, period=period)

        elif intent == "period_comparison":
            results["period_comparison"] = dt.compare_periods(
                properties[0] if properties else None,
                entities.get("period_a") or {},
                entities.get("period_b") or {},
            )

        elif intent == "top_tenants":
            results["top_tenants"] = dt.top_tenants(n=5, period=period)

        elif intent == "anomaly_detection":
            results["anomalies"] = dt.detect_anomalies(period=period)

        elif intent == "property_details":
            props = properties or dt.get_properties()
            results["property_details"] = dt.compare_properties(props, period=period)

    return {"results": results}


# --------------------------------------------------------------------------
# Node: responder — turn results into natural language
# --------------------------------------------------------------------------
RESPONDER_SYSTEM = """You are a real-estate asset-management assistant. Turn the JSON results
below into a clear, concise answer to the user's question. Rules:
- Use $ with thousands separators for money.
- If a scope defaulted to "all properties" or "all available period" because the user
  didn't specify one, say so explicitly in one short clause.
- 'corporate-level' entries in anomalies are company-wide costs not tied to one building —
  mention that distinction if you surface any.
- Be direct. No filler, no restating the question back verbatim.
- If results are empty for an intent, say plainly that there's nothing to report for it."""


def responder_node(state: AgentState) -> dict:
    llm = get_llm()
    payload = {
        "question": state["query"],
        "entities_used": state.get("entities", {}),
        "results": state.get("results", {}),
    }
    msg = llm.invoke(
        [
            SystemMessage(content=RESPONDER_SYSTEM),
            HumanMessage(content=json.dumps(payload, default=str)),
        ]
    )
    return {"response": msg.content}


# --------------------------------------------------------------------------
# Graph assembly
# --------------------------------------------------------------------------
def build_graph():
    g = StateGraph(AgentState)
    g.add_node("router", router_node)
    g.add_node("general_knowledge", general_knowledge_node)
    g.add_node("extractor", extractor_node)
    g.add_node("validator", validator_node)
    g.add_node("clarification", clarification_node)
    g.add_node("calculator", calculator_node)
    g.add_node("responder", responder_node)

    g.add_edge(START, "router")
    g.add_conditional_edges("router", route_after_router, {
        "extractor": "extractor",
        "general_knowledge": "general_knowledge",
    })
    g.add_edge("general_knowledge", END)
    g.add_edge("extractor", "validator")
    g.add_conditional_edges("validator", route_after_validator, {
        "clarification": "clarification",
        "retrieval": "calculator",
    })
    g.add_edge("clarification", END)
    g.add_edge("calculator", "responder")
    g.add_edge("responder", END)

    return g.compile()


graph = build_graph()


def run(query: str) -> AgentState:
    return graph.invoke({"query": query})
