"""
tests/test_retrieval_debug.py — Self-tests for retrieval_debug.py (Phase 7)
===========================================================================
Stdlib-only. No pytest required — run directly:
    python3 tests/test_retrieval_debug.py
"""

from __future__ import annotations

import json
import os
import sys

# Allow running from repo root or from inside tests/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from retrieval_debug import RetrievalDebug, is_enabled, maybe_new


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _assert(condition: bool, msg: str) -> None:
    if not condition:
        raise AssertionError(msg)


def _clear_env() -> None:
    """Remove FA_RETRIEVAL_DEBUG from os.environ if present."""
    os.environ.pop("FA_RETRIEVAL_DEBUG", None)


def _set_env(value: str) -> None:
    os.environ["FA_RETRIEVAL_DEBUG"] = value


# ─────────────────────────────────────────────────────────────────
# is_enabled
# ─────────────────────────────────────────────────────────────────

def test_is_enabled_default_off() -> None:
    """FA_RETRIEVAL_DEBUG not set → is_enabled() returns False."""
    _clear_env()
    _assert(not is_enabled(), "is_enabled() should be False when env var is unset")


def test_is_enabled_empty_string_off() -> None:
    """FA_RETRIEVAL_DEBUG='' → is_enabled() returns False."""
    _set_env("")
    _assert(not is_enabled(), "is_enabled() should be False for empty string")
    _clear_env()


def test_is_enabled_whitespace_off() -> None:
    """FA_RETRIEVAL_DEBUG='   ' (whitespace only) → False."""
    _set_env("   ")
    _assert(not is_enabled(), "is_enabled() should be False for whitespace-only value")
    _clear_env()


def test_is_enabled_one_on() -> None:
    """FA_RETRIEVAL_DEBUG='1' → is_enabled() returns True."""
    _set_env("1")
    _assert(is_enabled(), "is_enabled() should be True for '1'")
    _clear_env()


def test_is_enabled_true_on() -> None:
    """FA_RETRIEVAL_DEBUG='true' → is_enabled() returns True."""
    _set_env("true")
    _assert(is_enabled(), "is_enabled() should be True for 'true'")
    _clear_env()


def test_is_enabled_any_nonempty_on() -> None:
    """FA_RETRIEVAL_DEBUG='debug' → is_enabled() returns True."""
    _set_env("debug")
    _assert(is_enabled(), "is_enabled() should be True for 'debug'")
    _clear_env()


# ─────────────────────────────────────────────────────────────────
# maybe_new — env-driven behaviour
# ─────────────────────────────────────────────────────────────────

def test_maybe_new_disabled_by_default() -> None:
    """Disabled by default — maybe_new() returns None without env var."""
    _clear_env()
    result = maybe_new()
    _assert(result is None, f"expected None when disabled, got {result!r}")


def test_maybe_new_enabled_via_env() -> None:
    """maybe_new() returns a RetrievalDebug when env var is set."""
    _set_env("1")
    result = maybe_new()
    _assert(
        isinstance(result, RetrievalDebug),
        f"expected RetrievalDebug instance, got {type(result).__name__}",
    )
    _clear_env()


# ─────────────────────────────────────────────────────────────────
# maybe_new — explicit override
# ─────────────────────────────────────────────────────────────────

def test_maybe_new_force_enabled() -> None:
    """maybe_new(enabled=True) returns a dataclass even without env var."""
    _clear_env()
    result = maybe_new(enabled=True)
    _assert(
        isinstance(result, RetrievalDebug),
        f"expected RetrievalDebug with enabled=True, got {type(result).__name__}",
    )


def test_maybe_new_force_disabled() -> None:
    """maybe_new(enabled=False) returns None even when env var is set."""
    _set_env("1")
    result = maybe_new(enabled=False)
    _assert(result is None, f"expected None with enabled=False, got {result!r}")
    _clear_env()


# ─────────────────────────────────────────────────────────────────
# RetrievalDebug dataclass defaults
# ─────────────────────────────────────────────────────────────────

def test_default_field_values() -> None:
    """All fields should have sensible empty defaults."""
    dbg = RetrievalDebug()
    _assert(dbg.translated_query == "", f"translated_query default: {dbg.translated_query!r}")
    _assert(dbg.expanded_terms == [], f"expanded_terms default: {dbg.expanded_terms!r}")
    _assert(dbg.entities == {}, f"entities default: {dbg.entities!r}")
    _assert(dbg.vector_hits == [], f"vector_hits default: {dbg.vector_hits!r}")
    _assert(dbg.bm25_hits == [], f"bm25_hits default: {dbg.bm25_hits!r}")
    _assert(dbg.rrf_ranking == [], f"rrf_ranking default: {dbg.rrf_ranking!r}")
    _assert(dbg.reranked_results == [], f"reranked_results default: {dbg.reranked_results!r}")
    _assert(dbg.selected_context == [], f"selected_context default: {dbg.selected_context!r}")


def test_mutable_defaults_are_independent() -> None:
    """Two instances must not share mutable default containers."""
    a = RetrievalDebug()
    b = RetrievalDebug()
    a.expanded_terms.append("foo")
    _assert(
        b.expanded_terms == [],
        f"mutable default shared between instances: {b.expanded_terms}",
    )


# ─────────────────────────────────────────────────────────────────
# to_dict
# ─────────────────────────────────────────────────────────────────

def test_to_dict_keys() -> None:
    """to_dict() must return all expected keys."""
    dbg = RetrievalDebug()
    d = dbg.to_dict()
    expected_keys = {
        "translated_query",
        "expanded_terms",
        "entities",
        "vector_hits",
        "bm25_hits",
        "rrf_ranking",
        "reranked_results",
        "selected_context",
    }
    _assert(set(d.keys()) == expected_keys, f"unexpected keys: {set(d.keys())}")


def test_to_dict_default_values() -> None:
    """to_dict() defaults must match the dataclass defaults."""
    dbg = RetrievalDebug()
    d = dbg.to_dict()
    _assert(d["translated_query"] == "", "translated_query")
    _assert(d["expanded_terms"] == [], "expanded_terms")
    _assert(d["entities"] == {}, "entities")
    _assert(d["vector_hits"] == [], "vector_hits")
    _assert(d["bm25_hits"] == [], "bm25_hits")
    _assert(d["rrf_ranking"] == [], "rrf_ranking")
    _assert(d["reranked_results"] == [], "reranked_results")
    _assert(d["selected_context"] == [], "selected_context")


def test_to_dict_round_trips_json() -> None:
    """to_dict() output must serialise and deserialise through json.dumps/loads."""
    dbg = RetrievalDebug(
        translated_query="revenue Q3 2024",
        expanded_terms=["income", "earnings"],
        entities={"company": "ACME", "year": 2024, "quarter": "Q3"},
        vector_hits=["doc_1", "doc_3"],
        bm25_hits=["doc_2", "doc_1"],
        rrf_ranking=[("doc_1", 0.9), ("doc_2", 0.7)],
        reranked_results=["doc_1", "doc_2"],
        selected_context=["doc_1"],
    )
    d = dbg.to_dict()
    serialised = json.dumps(d)
    recovered = json.loads(serialised)

    _assert(recovered["translated_query"] == "revenue Q3 2024", "translated_query round-trip")
    _assert(recovered["expanded_terms"] == ["income", "earnings"], "expanded_terms round-trip")
    _assert(recovered["entities"]["company"] == "ACME", "entities round-trip")
    _assert(recovered["vector_hits"] == ["doc_1", "doc_3"], "vector_hits round-trip")
    _assert(recovered["bm25_hits"] == ["doc_2", "doc_1"], "bm25_hits round-trip")
    _assert(len(recovered["rrf_ranking"]) == 2, "rrf_ranking length round-trip")
    _assert(recovered["reranked_results"] == ["doc_1", "doc_2"], "reranked_results round-trip")
    _assert(recovered["selected_context"] == ["doc_1"], "selected_context round-trip")


def test_to_dict_is_json_serialisable_empty() -> None:
    """An empty RetrievalDebug must be JSON-serialisable without error."""
    dbg = RetrievalDebug()
    try:
        json.dumps(dbg.to_dict())
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"json.dumps failed: {exc}") from exc


def test_to_dict_does_not_alias_internal_state() -> None:
    """Modifying the returned dict's lists must not mutate the dataclass."""
    dbg = RetrievalDebug()
    d = dbg.to_dict()
    d["expanded_terms"].append("leaked")
    _assert(
        dbg.expanded_terms == [],
        f"to_dict leaked a reference to internal list: {dbg.expanded_terms}",
    )


# ─────────────────────────────────────────────────────────────────
# Integration: LangGraph AgentState pattern
# ─────────────────────────────────────────────────────────────────

def test_agentstate_pattern_disabled() -> None:
    """When disabled, state['retrieval_debug'] should be None."""
    _clear_env()
    dbg = maybe_new()
    state_value = dbg.to_dict() if dbg is not None else None
    _assert(state_value is None, "AgentState.retrieval_debug should be None when disabled")


def test_agentstate_pattern_enabled() -> None:
    """When enabled, state['retrieval_debug'] is a JSON-serialisable dict."""
    _clear_env()
    dbg = maybe_new(enabled=True)
    _assert(dbg is not None, "dbg must not be None when force-enabled")
    dbg.translated_query = "net income 2023"
    dbg.selected_context = ["chunk_42"]
    state_value = dbg.to_dict()
    _assert(isinstance(state_value, dict), "to_dict() must return a dict")
    serialised = json.dumps(state_value)  # must not raise
    recovered = json.loads(serialised)
    _assert(recovered["translated_query"] == "net income 2023", "AgentState round-trip")
    _assert(recovered["selected_context"] == ["chunk_42"], "selected_context round-trip")


# ─────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Ensure a clean env before starting
    _clear_env()

    tests = [
        # is_enabled
        test_is_enabled_default_off,
        test_is_enabled_empty_string_off,
        test_is_enabled_whitespace_off,
        test_is_enabled_one_on,
        test_is_enabled_true_on,
        test_is_enabled_any_nonempty_on,
        # maybe_new — env-driven
        test_maybe_new_disabled_by_default,
        test_maybe_new_enabled_via_env,
        # maybe_new — explicit override
        test_maybe_new_force_enabled,
        test_maybe_new_force_disabled,
        # dataclass defaults
        test_default_field_values,
        test_mutable_defaults_are_independent,
        # to_dict
        test_to_dict_keys,
        test_to_dict_default_values,
        test_to_dict_round_trips_json,
        test_to_dict_is_json_serialisable_empty,
        test_to_dict_does_not_alias_internal_state,
        # integration
        test_agentstate_pattern_disabled,
        test_agentstate_pattern_enabled,
    ]

    for test_fn in tests:
        test_fn()

    print("ALL TESTS PASSED")
