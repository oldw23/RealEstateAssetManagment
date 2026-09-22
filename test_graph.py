"""Graph routing/validation tests — no LLM/API key required."""
from graph import (
    calculator_node,
    clarification_node,
    route_after_router,
    validator_node,
)


def test_price_comparison_goes_through_extractor():
    assert route_after_router({"intents": ["price_comparison"]}) == "extractor"


def test_general_knowledge_short_circuits():
    assert route_after_router({"intents": ["general_knowledge"]}) == "general_knowledge"


def test_unsupported_alone_short_circuits():
    assert route_after_router({"intents": ["unsupported"]}) == "general_knowledge"


def test_street_addresses_need_clarification():
    out = validator_node(
        {
            "intents": ["price_comparison"],
            "entities": {"properties": ["123 Main St", "456 Oak Ave"]},
        }
    )
    assert out["needs_clarification"] is True
    blob = " ".join(out["validation_errors"])
    assert "123 Main St" in blob
    assert "456 Oak Ave" in blob


def test_price_clarification_does_not_invent_a_sale_price():
    validated = validator_node(
        {
            "intents": ["price_comparison"],
            "entities": {"properties": ["123 Main St", "456 Oak Ave"]},
        }
    )
    msg = clarification_node({**validated, "intents": ["price_comparison"]})["response"]
    assert "123 Main St" in msg
    assert "sale price" in msg.lower() or "P&L ledger" in msg
    assert "Building 17" in msg
    assert "$500,000" not in msg


def test_price_comparison_calculator_returns_pnl_not_market_price():
    out = calculator_node(
        {
            "intents": ["price_comparison"],
            "entities": {"properties": ["Building 17", "Building 140"]},
        }
    )
    pc = out["results"]["price_comparison"]
    assert pc["sale_price_available"] is False
    assert pc["appraisal_date_available"] is False
    assert len(pc["pnl"]) == 2
    assert "net" in pc["pnl"][0]
