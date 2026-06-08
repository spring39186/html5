"""
tests/test_memory.py — stdlib-only self-tests for memory.py
============================================================
Run with:  python3 tests/test_memory.py
All tests use only the Python standard library; no third-party packages.
Prints "ALL TESTS PASSED" on success, raises AssertionError on failure.
"""
from __future__ import annotations

import json
import sys
import os

# Allow running from any working directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from memory import ConversationMemory, summarize_for_prompt


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _assert(condition: bool, msg: str) -> None:
    if not condition:
        raise AssertionError(msg)


# ---------------------------------------------------------------------------
# 1. mark_parsed / is_parsed
# ---------------------------------------------------------------------------

def test_is_parsed_returns_false_initially() -> None:
    mem = ConversationMemory()
    _assert(not mem.is_parsed("report.pdf"), "New memory must not have any parsed files")


def test_mark_parsed_then_is_parsed() -> None:
    mem = ConversationMemory()
    mem.mark_parsed("report_2024.pdf")
    _assert(mem.is_parsed("report_2024.pdf"), "File must be found after mark_parsed")


def test_mark_parsed_multiple_files() -> None:
    mem = ConversationMemory()
    files = ["a.pdf", "b.pdf", "c.pdf"]
    for f in files:
        mem.mark_parsed(f)
    for f in files:
        _assert(mem.is_parsed(f), f"File '{f}' must be in parsed_files after mark_parsed")
    _assert(not mem.is_parsed("d.pdf"), "Unparsed file must not appear in parsed_files")


def test_mark_parsed_idempotent() -> None:
    """Calling mark_parsed twice for the same file must not cause errors or duplication."""
    mem = ConversationMemory()
    mem.mark_parsed("same.pdf")
    mem.mark_parsed("same.pdf")
    _assert(mem.is_parsed("same.pdf"), "File still parsed after duplicate mark")
    # parsed_files is a set — no duplicates
    _assert(len(mem.parsed_files) == 1, "Duplicate mark_parsed must not add duplicate entries")


# ---------------------------------------------------------------------------
# 2. remember_entities — merge & list union
# ---------------------------------------------------------------------------

def test_remember_entities_scalar_new_key() -> None:
    mem = ConversationMemory()
    mem.remember_entities({"company": "ACME Corp"})
    _assert(mem.entities.get("company") == "ACME Corp", "Scalar entity must be stored")


def test_remember_entities_scalar_overwrite() -> None:
    mem = ConversationMemory()
    mem.remember_entities({"company": "OldName"})
    mem.remember_entities({"company": "NewName"})
    _assert(mem.entities["company"] == "NewName", "Later scalar must overwrite earlier one")


def test_remember_entities_list_union() -> None:
    """Lists must be unioned (deduplicated, preserving order)."""
    mem = ConversationMemory()
    mem.remember_entities({"years": [2022, 2023]})
    mem.remember_entities({"years": [2023, 2024]})
    years = mem.entities["years"]
    _assert(2022 in years, "2022 must survive after union")
    _assert(2023 in years, "2023 must survive after union")
    _assert(2024 in years, "2024 must be added in second call")
    _assert(years.count(2023) == 1, "Duplicate 2023 must appear only once in union")


def test_remember_entities_list_preserves_order() -> None:
    mem = ConversationMemory()
    mem.remember_entities({"tickers": ["AAA", "BBB"]})
    mem.remember_entities({"tickers": ["BBB", "CCC"]})
    tickers = mem.entities["tickers"]
    _assert(tickers.index("AAA") < tickers.index("BBB"), "AAA must come before BBB")
    _assert(tickers.index("BBB") < tickers.index("CCC"), "BBB must come before CCC")


def test_remember_entities_mixed_update() -> None:
    """Multiple keys with different types updated in one call."""
    mem = ConversationMemory()
    mem.remember_entities({"company": "ACME", "years": [2023], "tickers": ["ACM"]})
    mem.remember_entities({"years": [2024], "tickers": ["ACM", "XYZ"]})
    _assert(mem.entities["company"] == "ACME", "Scalar company must persist")
    _assert(set(mem.entities["years"]) == {2023, 2024}, "Years must be unioned")
    _assert("ACM" in mem.entities["tickers"], "ACM must be in tickers")
    _assert("XYZ" in mem.entities["tickers"], "XYZ must be added")
    _assert(mem.entities["tickers"].count("ACM") == 1, "ACM must not be duplicated")


def test_recall_entities_returns_copy() -> None:
    """recall_entities must return a copy, not a reference to the internal dict."""
    mem = ConversationMemory()
    mem.remember_entities({"company": "ACME"})
    recalled = mem.recall_entities()
    recalled["company"] = "MUTATED"
    _assert(mem.entities["company"] == "ACME", "Mutation of recalled dict must not affect memory")


# ---------------------------------------------------------------------------
# 3. file_year_map
# ---------------------------------------------------------------------------

def test_file_year_map_assignment() -> None:
    mem = ConversationMemory()
    mem.file_year_map["report_2024.pdf"] = 2024
    _assert(mem.file_year_map.get("report_2024.pdf") == 2024, "file_year_map must store year")


# ---------------------------------------------------------------------------
# 4. notes
# ---------------------------------------------------------------------------

def test_notes_append() -> None:
    mem = ConversationMemory()
    mem.notes.append("page 3 is scanned")
    mem.notes.append("translation normalised to EN")
    _assert(len(mem.notes) == 2, "notes must have 2 entries")
    _assert("page 3 is scanned" in mem.notes, "First note must be present")


# ---------------------------------------------------------------------------
# 5. to_dict / from_dict round-trip via json
# ---------------------------------------------------------------------------

def test_to_dict_from_dict_round_trip() -> None:
    mem = ConversationMemory()
    mem.mark_parsed("report_2024.pdf")
    mem.mark_parsed("report_2023.pdf")
    mem.remember_entities({"company": "ACME Corp", "years": [2023, 2024], "tickers": ["ACM"]})
    mem.file_year_map["report_2024.pdf"] = 2024
    mem.file_year_map["report_2023.pdf"] = 2023
    mem.notes.append("page 5 is scanned")

    # Serialise to dict → JSON string → dict (full JSON round-trip)
    d = mem.to_dict()
    json_str = json.dumps(d)
    d2 = json.loads(json_str)

    mem2 = ConversationMemory.from_dict(d2)

    _assert(mem2.is_parsed("report_2024.pdf"), "round-trip: report_2024.pdf must be parsed")
    _assert(mem2.is_parsed("report_2023.pdf"), "round-trip: report_2023.pdf must be parsed")
    _assert(not mem2.is_parsed("other.pdf"), "round-trip: other.pdf must not be parsed")
    _assert(mem2.entities["company"] == "ACME Corp", "round-trip: company entity")
    _assert(set(mem2.entities["years"]) == {2023, 2024}, "round-trip: years entity")
    _assert("ACM" in mem2.entities["tickers"], "round-trip: tickers entity")
    _assert(mem2.file_year_map.get("report_2024.pdf") == 2024, "round-trip: file_year_map 2024")
    _assert(mem2.file_year_map.get("report_2023.pdf") == 2023, "round-trip: file_year_map 2023")
    _assert("page 5 is scanned" in mem2.notes, "round-trip: notes")


def test_from_dict_empty() -> None:
    """from_dict({}) must produce a blank ConversationMemory without errors."""
    mem = ConversationMemory.from_dict({})
    _assert(len(mem.parsed_files) == 0, "Empty dict: parsed_files must be empty")
    _assert(len(mem.entities) == 0, "Empty dict: entities must be empty")
    _assert(len(mem.file_year_map) == 0, "Empty dict: file_year_map must be empty")
    _assert(len(mem.notes) == 0, "Empty dict: notes must be empty")


def test_to_dict_parsed_files_is_json_serialisable() -> None:
    """to_dict must produce a JSON-serialisable structure (set → list)."""
    mem = ConversationMemory()
    mem.mark_parsed("z.pdf")
    mem.mark_parsed("a.pdf")
    d = mem.to_dict()
    # Must not raise
    json_str = json.dumps(d)
    parsed = json.loads(json_str)
    _assert(isinstance(parsed["parsed_files"], list), "parsed_files must be list in JSON output")
    _assert(sorted(parsed["parsed_files"]) == ["a.pdf", "z.pdf"], "parsed_files must be sorted")


def test_to_dict_parsed_files_sorted() -> None:
    """to_dict must sort parsed_files for deterministic output."""
    mem = ConversationMemory()
    for name in ["z.pdf", "m.pdf", "a.pdf"]:
        mem.mark_parsed(name)
    d = mem.to_dict()
    _assert(d["parsed_files"] == sorted(d["parsed_files"]), "parsed_files must be sorted in to_dict")


# ---------------------------------------------------------------------------
# 6. summarize_for_prompt
# ---------------------------------------------------------------------------

def test_summarize_includes_parsed_files() -> None:
    mem = ConversationMemory()
    mem.mark_parsed("annual_report_2024.pdf")
    summary = summarize_for_prompt(mem)
    _assert("annual_report_2024.pdf" in summary, "summarize_for_prompt must include parsed file name")


def test_summarize_includes_multiple_parsed_files() -> None:
    mem = ConversationMemory()
    mem.mark_parsed("report_2023.pdf")
    mem.mark_parsed("report_2024.pdf")
    summary = summarize_for_prompt(mem)
    _assert("report_2023.pdf" in summary, "Summary must include report_2023.pdf")
    _assert("report_2024.pdf" in summary, "Summary must include report_2024.pdf")


def test_summarize_includes_company_entity() -> None:
    mem = ConversationMemory()
    mem.remember_entities({"company": "ACME Corp"})
    summary = summarize_for_prompt(mem)
    _assert("ACME Corp" in summary, "summarize_for_prompt must include company name")


def test_summarize_includes_years() -> None:
    mem = ConversationMemory()
    mem.remember_entities({"years": [2023, 2024]})
    summary = summarize_for_prompt(mem)
    _assert("2023" in summary, "summarize_for_prompt must include year 2023")
    _assert("2024" in summary, "summarize_for_prompt must include year 2024")


def test_summarize_includes_tickers() -> None:
    mem = ConversationMemory()
    mem.remember_entities({"tickers": ["ACME", "XYZ"]})
    summary = summarize_for_prompt(mem)
    _assert("ACME" in summary, "summarize_for_prompt must include ticker ACME")
    _assert("XYZ" in summary, "summarize_for_prompt must include ticker XYZ")


def test_summarize_no_parsed_files_label() -> None:
    """With no parsed files, a suitable placeholder must appear."""
    mem = ConversationMemory()
    summary = summarize_for_prompt(mem)
    _assert(
        "已解析檔案" in summary,
        "summarize_for_prompt must contain '已解析檔案' label even when empty",
    )


def test_summarize_returns_string() -> None:
    mem = ConversationMemory()
    _assert(isinstance(summarize_for_prompt(mem), str), "summarize_for_prompt must return str")


def test_summarize_full_memory() -> None:
    """Full memory produces a non-empty summary containing all key sections."""
    mem = ConversationMemory()
    mem.mark_parsed("q1_2024.pdf")
    mem.remember_entities({
        "company": "Globex",
        "years": [2023, 2024],
        "tickers": ["GBX"],
    })
    mem.file_year_map["q1_2024.pdf"] = 2024
    mem.notes.append("page 2 is a scanned chart")

    summary = summarize_for_prompt(mem)
    _assert("q1_2024.pdf" in summary, "Full summary must include parsed file")
    _assert("Globex" in summary, "Full summary must include company")
    _assert("2023" in summary and "2024" in summary, "Full summary must include years")
    _assert("GBX" in summary, "Full summary must include ticker")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        # mark_parsed / is_parsed
        test_is_parsed_returns_false_initially,
        test_mark_parsed_then_is_parsed,
        test_mark_parsed_multiple_files,
        test_mark_parsed_idempotent,
        # remember_entities
        test_remember_entities_scalar_new_key,
        test_remember_entities_scalar_overwrite,
        test_remember_entities_list_union,
        test_remember_entities_list_preserves_order,
        test_remember_entities_mixed_update,
        test_recall_entities_returns_copy,
        # file_year_map / notes
        test_file_year_map_assignment,
        test_notes_append,
        # to_dict / from_dict
        test_to_dict_from_dict_round_trip,
        test_from_dict_empty,
        test_to_dict_parsed_files_is_json_serialisable,
        test_to_dict_parsed_files_sorted,
        # summarize_for_prompt
        test_summarize_includes_parsed_files,
        test_summarize_includes_multiple_parsed_files,
        test_summarize_includes_company_entity,
        test_summarize_includes_years,
        test_summarize_includes_tickers,
        test_summarize_no_parsed_files_label,
        test_summarize_returns_string,
        test_summarize_full_memory,
    ]

    failed = []
    for test_fn in tests:
        try:
            test_fn()
        except AssertionError as exc:
            failed.append((test_fn.__name__, str(exc)))
            print(f"  FAIL  {test_fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed.append((test_fn.__name__, repr(exc)))
            print(f"  ERROR {test_fn.__name__}: {exc}")
        else:
            print(f"  ok    {test_fn.__name__}")

    if failed:
        print(f"\n{len(failed)} test(s) FAILED.")
        sys.exit(1)
    else:
        print("\nALL TESTS PASSED")
