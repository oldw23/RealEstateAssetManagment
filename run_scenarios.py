"""Live test scenarios for the LangGraph assistant. Requires ANTHROPIC_API_KEY."""
from __future__ import annotations

import json
import os
import traceback

os.environ.setdefault("LLM_PROVIDER", "anthropic")

from graph import run


SCENARIOS = [
    {
        "name": "P&L summary",
        "query": "What is the total P&L for Building 17 in 2025?",
        "expect": "pnl_summary",
    },
    {
        "name": "Property comparison",
        "query": "Compare Building 17 to Building 140.",
        "expect": "property_comparison",
    },
    {
        "name": "Compound: top tenants + anomalies",
        "query": "Who are my top tenants, and is anything unusual in the numbers?",
        "expect": "compound",
    },
    {
        "name": "Period comparison",
        "query": "How does 2025-Q2 compare to 2024-Q2 for Building 160?",
        "expect": "period_comparison",
    },
    {
        "name": "Clarification (unknown property)",
        "query": "Compare Building 17 to Building 500",
        "expect": "clarification",
    },
    {
        "name": "Unsupported (valuation)",
        "query": "What is the market price of Building 17?",
        "expect": "unsupported",
    },
    {
        "name": "General knowledge",
        "query": "What is P&L?",
        "expect": "general_knowledge",
    },
]


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set")
        return 1

    failed = 0
    for i, s in enumerate(SCENARIOS, 1):
        print("=" * 72)
        print(f"SCENARIO {i}/{len(SCENARIOS)}: {s['name']}")
        print(f"Q: {s['query']}")
        print("-" * 72)
        try:
            state = run(s["query"])
            internals = {
                "intents": state.get("intents"),
                "entities": state.get("entities"),
                "needs_clarification": state.get("needs_clarification"),
                "validation_errors": state.get("validation_errors"),
                "results_keys": list((state.get("results") or {}).keys()),
            }
            print("Internals:")
            print(json.dumps(internals, indent=2, default=str))
            print()
            print("Answer:")
            print(state.get("response") or "(empty)")
        except Exception:
            failed += 1
            print("FAILED")
            traceback.print_exc()
        print()
    print("=" * 72)
    print(f"Done. {len(SCENARIOS) - failed}/{len(SCENARIOS)} scenarios completed without exception.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
