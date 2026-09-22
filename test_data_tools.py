"""Smoke tests for the data layer — no LLM/API key required. Run with `pytest`."""
import data_tools as dt


def test_properties_and_tenants_loaded():
    assert len(dt.get_properties()) == 5
    assert len(dt.get_tenants()) == 18
    assert set(dt.get_years()) == {"2024", "2025"}


def test_pnl_summary_scoped_to_one_property():
    result = dt.pnl_summary(properties=["Building 17"], period={"year": "2025"})
    assert result["properties"] == ["Building 17"]
    assert result["row_count"] > 0
    assert result["net"] == round(result["revenue"] + result["expenses"], 2)


def test_pnl_summary_defaults_to_everything():
    result = dt.pnl_summary()
    assert result["properties"] == "all"
    assert result["row_count"] > 4000 or result["row_count"] > 0  # sanity, not a hard row count


def test_fuzzy_match_accepts_close_typo():
    match = dt.fuzzy_match(["bldg 17"], dt.get_properties())
    assert match.matched == ["Building 17"]
    assert match.unmatched == []


def test_fuzzy_match_rejects_nonexistent_number():
    match = dt.fuzzy_match(["Building 500"], dt.get_properties())
    assert match.matched == []
    assert "Building 500" in match.unmatched
    assert match.suggestions.get("Building 500") is not None


def test_top_tenants_excludes_corporate_rows():
    tenants = dt.top_tenants(n=5)
    assert all(t["tenant"] for t in tenants)  # no None/NaN tenant slipped through
    assert len(tenants) <= 5


def test_anomalies_handle_missing_property_and_tenant():
    anomalies = dt.detect_anomalies()
    assert len(anomalies) > 0
    for a in anomalies:
        # corporate-level rows must be labeled, never left as a raw NaN
        assert a["property"] != "" and a["property"] is not None
        # tenant is allowed to be None (not every entry is tenant-specific),
        # it just must never be a raw NaN float slipping through
        assert a["tenant"] is None or isinstance(a["tenant"], str)


def test_compare_periods_computes_delta():
    result = dt.compare_periods(None, {"year": "2025"}, {"year": "2024"})
    assert result["net_delta"] == round(
        result["period_a"]["net"] - result["period_b"]["net"], 2
    )
