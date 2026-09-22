"""Live test scenarios for the LangGraph assistant. Requires ANTHROPIC_API_KEY.

Prints each scenario to the terminal and writes the same transcript to
scenario_output.md (overwritten on each run).
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timezone

os.environ.setdefault("LLM_PROVIDER", "anthropic")

from graph import run

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "scenario_output.md")

SCENARIOS = [
    {
        "name": "Price comparison (street addresses, not in dataset)",
        "query": "What is the price of my asset at 123 Main St compared to the one at 456 Oak Ave?",
    },
    {
        "name": "Total P&L this year (all properties)",
        "query": "What is the total P&L for all my properties this year?",
    },
    {
        "name": "This quarter vs same period last year",
        "query": "How does this quarter compare to the same period last year?",
    },
    {
        "name": "Compound: top tenants + unusual numbers",
        "query": "Who are my top tenants, and is anything unusual in the numbers?",
    },
    {
        "name": "Vague / incomplete",
        "query": "How are my properties doing?",
    },
    {
        "name": "Property not in data (error handling)",
        "query": "What is the P&L for the property at 789 Pine Ln?",
    },
    {
        "name": "General knowledge",
        "query": "What is a P&L statement?",
    },
    {
        "name": "P&L summary (named building)",
        "query": "What is the total P&L for Building 17 in 2025?",
    },
    {
        "name": "Property comparison",
        "query": "Compare Building 17 to Building 140.",
    },
    {
        "name": "Clarification (unknown building number)",
        "query": "Compare Building 17 to Building 500",
    },
]


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)
            s.flush()

    def flush(self):
        for s in self.streams:
            s.flush()


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
        print("Set ANTHROPIC_API_KEY (or OPENAI_API_KEY with LLM_PROVIDER=openai)")
        return 1

    lines: list[str] = []
    buf = _ListWriter(lines)
    tee = Tee(sys.stdout, buf)
    sys_stdout = sys.stdout
    sys.stdout = tee

    failed = 0
    try:
        print("# Scenario run output")
        print()
        print(f"- Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"- Provider: `{os.environ.get('LLM_PROVIDER', 'anthropic')}`")
        print(f"- Dataset: `ledger.parquet` via `data_tools.py` (not a price/valuation API)")
        print()
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
        print(
            f"Done. {len(SCENARIOS) - failed}/{len(SCENARIOS)} scenarios completed without exception."
        )
    finally:
        sys.stdout = sys_stdout

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write("".join(lines))
        if not lines or not lines[-1].endswith("\n"):
            f.write("\n")
    print(f"Wrote {OUTPUT_PATH}")
    return 1 if failed else 0


class _ListWriter:
    def __init__(self, lines: list[str]):
        self.lines = lines

    def write(self, data):
        self.lines.append(data)

    def flush(self):
        pass


if __name__ == "__main__":
    raise SystemExit(main())
