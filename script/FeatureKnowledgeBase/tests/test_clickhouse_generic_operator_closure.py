from __future__ import annotations

from agents.type.operator_closure import operator_surface_observations


def _surfaces(text: str, *, heading: str = "Operators") -> set[str]:
    rows = operator_surface_observations({
        "block_id": "synthetic:block",
        "block_type": "text",
        "text": text,
        "heading_ancestry": [heading],
    })
    return {str(row["surface"]) for row in rows}


def test_keyword_infix_surface_is_reconciled_from_operand_slots():
    assert "MATCHES" in _surfaces("expr MATCHES pattern")


def test_multi_token_operator_is_one_identity_surface():
    assert _surfaces("lhs GLOBAL IN ...") == {"GLOBAL IN"}


def test_three_token_operator_phrase_is_one_identity_surface():
    assert _surfaces("expr AT TIME ZONE zone") == {"AT TIME ZONE"}


def test_postfix_predicate_surface_is_retained():
    assert "IS UNKNOWN" in _surfaces("expr IS UNKNOWN – predicate")


def test_plaintext_operator_label_is_retained_without_operand_text():
    assert "IS NULL" in _surfaces("IS NULL")


def test_ternary_between_does_not_create_and_identity():
    surfaces = _surfaces("expr BETWEEN low AND high")
    assert "BETWEEN" in surfaces
    assert "AND" not in surfaces


def test_function_equivalence_reference_does_not_replace_operator_surface():
    surfaces = _surfaces("expr MATCHES pattern; equivalent to matches(expr, pattern)")
    assert "MATCHES" in surfaces
    assert "EQUIVALENT" not in surfaces


def test_clause_noise_without_operand_slot_is_rejected():
    assert not _surfaces("BETWEEN is described here; AND is a clause connector")
