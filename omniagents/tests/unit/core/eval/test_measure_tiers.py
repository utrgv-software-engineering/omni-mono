from __future__ import annotations

import pytest

from omniagents.core.eval.measure_tiers import (
    DEFAULT_TIER,
    GATING_TIERS,
    KNOWN_TIERS,
    gates_ci,
    measure_names,
    parse_measures,
    tag_results,
)


class TestParseMeasures:
    def test_none_returns_empty(self):
        names, tiers = parse_measures(None)
        assert names == []
        assert tiers == {}

    def test_empty_list(self):
        names, tiers = parse_measures([])
        assert names == []
        assert tiers == {}

    def test_empty_dict(self):
        names, tiers = parse_measures({})
        assert names == []
        assert tiers == {}

    def test_flat_list_defaults_to_outcome(self):
        names, tiers = parse_measures(["a", "b", "c"])
        assert names == ["a", "b", "c"]
        assert tiers == {"a": DEFAULT_TIER, "b": DEFAULT_TIER, "c": DEFAULT_TIER}

    def test_dict_shape_assigns_per_tier(self):
        names, tiers = parse_measures({
            "outcome": ["bug_caught_by_tests", "db_row_exists"],
            "process": ["ticket_moved_through_pipeline"],
            "guard": ["no_remote_operations"],
        })
        assert set(names) == {
            "bug_caught_by_tests",
            "db_row_exists",
            "ticket_moved_through_pipeline",
            "no_remote_operations",
        }
        assert tiers == {
            "bug_caught_by_tests": "outcome",
            "db_row_exists": "outcome",
            "ticket_moved_through_pipeline": "process",
            "no_remote_operations": "guard",
        }

    def test_dict_shape_with_empty_tier_value(self):
        names, tiers = parse_measures({"outcome": ["a"], "process": None, "guard": []})
        assert names == ["a"]
        assert tiers == {"a": "outcome"}

    def test_unknown_tier_name_passes_through(self):
        # Custom tier names are accepted; gating semantics default to True
        # (legacy-safe) since unknown tiers aren't in GATING_TIERS.
        names, tiers = parse_measures({"weird": ["m1"]})
        assert names == ["m1"]
        assert tiers == {"m1": "weird"}

    def test_duplicate_name_last_tier_wins(self):
        names, tiers = parse_measures({
            "outcome": ["dupe"],
            "process": ["dupe"],
        })
        assert names == ["dupe"]  # only listed once
        assert tiers["dupe"] == "process"  # last tier wins

    def test_unrecognized_value_type_returns_empty(self):
        names, tiers = parse_measures("not a list or dict")
        assert names == []
        assert tiers == {}

    def test_none_entries_in_list_are_dropped(self):
        names, tiers = parse_measures(["a", None, "b"])
        assert names == ["a", "b"]


class TestMeasureNames:
    def test_list(self):
        assert measure_names(["a", "b"]) == ["a", "b"]

    def test_dict(self):
        result = measure_names({"outcome": ["a"], "process": ["b"]})
        assert set(result) == {"a", "b"}

    def test_none(self):
        assert measure_names(None) == []


class TestGatesCi:
    def test_known_gating_tiers(self):
        assert gates_ci("outcome") is True
        assert gates_ci("quality") is True
        assert gates_ci("guard") is True

    def test_process_does_not_gate(self):
        assert gates_ci("process") is False

    def test_unknown_tier_gates_legacy_safe(self):
        assert gates_ci("weird") is True

    def test_none_or_empty_gates_legacy_safe(self):
        assert gates_ci(None) is True
        assert gates_ci("") is True


class TestTagResults:
    def test_tags_each_result_with_tier(self):
        results = [
            {"name": "a", "passed": True},
            {"name": "b", "passed": False},
        ]
        tag_results(results, {"a": "outcome", "b": "process"})
        assert results[0]["tier"] == "outcome"
        assert results[1]["tier"] == "process"

    def test_unknown_name_gets_default(self):
        results = [{"name": "ghost", "passed": True}]
        tag_results(results, {})
        assert results[0]["tier"] == DEFAULT_TIER

    def test_missing_name_gets_default(self):
        results = [{"passed": True}]
        tag_results(results, {"a": "outcome"})
        assert results[0]["tier"] == DEFAULT_TIER

    def test_returns_same_list(self):
        results = [{"name": "a"}]
        out = tag_results(results, {"a": "outcome"})
        assert out is results


class TestTierConstants:
    def test_known_tiers_includes_the_four(self):
        assert "outcome" in KNOWN_TIERS
        assert "quality" in KNOWN_TIERS
        assert "process" in KNOWN_TIERS
        assert "guard" in KNOWN_TIERS

    def test_gating_tiers_excludes_process(self):
        assert "outcome" in GATING_TIERS
        assert "quality" in GATING_TIERS
        assert "guard" in GATING_TIERS
        assert "process" not in GATING_TIERS
