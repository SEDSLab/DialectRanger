"""Release subject/type tests: no network, no provider, no discovery replay."""
import copy
import json
from pathlib import Path

import pytest

from agents.contracts.publication_integrity import evaluate_publication_integrity, publication_integrity_reasons
from agents.pipeline.publication import select_publication
from agents.semantic_stage.target_admission import apply_target_decision, target_classification_prompt


def fixture(name="DEMO", kind="FUNCTION", status="CONFIRMED"):
    unit = {"identity_unit_id": "u", "canonical_identity": name, "feature_type": kind,
            "candidate_ids": ["c"], "owner_ids": ["o"], "group_status": "CONFIRMED_FEATURE"}
    heading = {"block_id": "h", "text": name, "block_kind": "HEADING", "owner_ids": ["o"],
               "heading_ancestry": [{"FUNCTION": "Functions", "DATATYPE": "Data types", "OPERATOR": "Operators"}[kind]],
               "source_url": "https://official.invalid/ref", "definition_section_proofs": [{
                   "identity_unit_id": "u", "heading_block_id": "h", "primary_identity": name,
                   "identity_values": [name], "block_ids": ["h"], "definition_anchor_block_ids": ["h"],
                   "scope_status": "UNKNOWN", "section_id": "s"}]}
    bundle = {"source_blocks": [heading], "owners": [{"owner_id": "o", "scope_status": "UNKNOWN"}]}
    decision = {"identity_unit_id": "u", "primary_type": kind, "status": status,
                "supporting_evidence_ids": ["vote"], "conflicting_evidence_ids": [], "reason_codes": []}
    votes = [{"identity_unit_id": "u", "evidence_id": "vote", "block_id": "old", "owner_text": "old independent context"}]
    target = {"feature_type": kind, "admission_status": "ADMITTED", "target_resolution": "AI_CLASSIFIED"}
    return unit, bundle, decision, target, votes


def evaluate(parts):
    return evaluate_publication_integrity(*parts[:4], typed_evidence=parts[4])


@pytest.mark.parametrize("kind,name", [("FUNCTION", "DEMO"), ("DATATYPE", "INTEGER"), ("OPERATOR", "+")])
def test_exact_definition_unknown_scope_no_examples(kind, name):
    assert evaluate(fixture(name, kind))["state"] == "CONFIRMED"


def test_review_same_hypothesis_does_not_confirm():
    result = evaluate(fixture(status="REVIEW_REQUIRED"))
    assert result["state"] == "REVIEW"
    assert "TYPE_REVIEW_UNRESOLVED" in result["reason_codes"]


def test_independent_resolution_is_supported():
    parts = fixture(status="REVIEW_REQUIRED")
    parts[3]["resolution_evidence"] = {"feature_type": "FUNCTION", "evidence_refs": ["h"], "resolved_conflict_refs": []}
    result = evaluate(parts)
    assert result["state"] == "CONFIRMED"
    assert result["independent_resolution_verified"]


@pytest.mark.parametrize("bad_refs", ["h", [{"block_id": "h"}], {"h": True}, None])
def test_malformed_target_resolution_never_promotes_or_crashes(bad_refs):
    parts = fixture(status="REVIEW_REQUIRED")
    parts[3]["resolution_evidence"] = {"feature_type": "FUNCTION", "evidence_refs": bad_refs}
    assert evaluate(parts)["state"] == "REVIEW"


@pytest.mark.parametrize("repeat", ["block", "text", "missing_vote", "missing_conflict"])
def test_relabeling_evidence_does_not_create_independence(repeat):
    parts = fixture(status="REVIEW_REQUIRED")
    parts[3]["resolution_evidence"] = {"feature_type": "FUNCTION", "evidence_refs": ["h"], "resolved_conflict_refs": []}
    if repeat == "block": parts[4][0]["block_id"] = "h"
    if repeat == "text": parts[4][0]["owner_text"] = "DEMO"
    if repeat == "missing_vote": parts[4].clear()
    if repeat == "missing_conflict": parts[2]["conflicting_evidence_ids"] = ["contradiction"]
    assert evaluate(parts)["state"] == "REVIEW"


@pytest.mark.parametrize("symbol", ["+", "-", "*", "/", "||", "->"])
def test_symbolic_operator_catalog_not_blacklisted(symbol):
    parts = fixture(symbol, "OPERATOR")
    parts[1]["source_blocks"] = [{"block_id": "b", "owner_ids": ["o"], "text": "x " + symbol + " y",
        "table_context": {"cells": [{"text": "x " + symbol + " y", "headers": [{"text": "Operator"}]}]}}]
    assert evaluate(parts)["state"] == "CONFIRMED"


@pytest.mark.parametrize("symbol", ["[", "]", "{", "}", "|"])
def test_grammar_fragment_is_not_a_function_definition(symbol):
    parts = fixture(symbol)
    parts[1]["source_blocks"] = [{"block_id": "b", "owner_ids": ["o"], "text": "DEMO(x[, y])",
        "table_context": {"cells": [{"text": "DEMO(x[, y])", "headers": [{"text": "Function"}]}]}}]
    assert evaluate(parts)["state"] == "REVIEW"


def test_example_invocation_without_definition_is_not_subject():
    parts = fixture()
    parts[1]["source_blocks"] = [{"block_id": "b", "text": "SELECT DEMO(x);", "owner_ids": ["o"],
        "observed_context": {"visible_label": "Example"}, "structural_roles": ["DEFINITION", "IDENTITY"]}]
    assert evaluate(parts)["state"] == "REVIEW"


def test_characters_alone_do_not_decide_subject_role():
    parts = fixture("[", "OPERATOR")
    parts[1]["source_blocks"] = [{"block_id": "b", "owner_ids": ["o"], "text": "[",
        "table_context": {"cells": [{"text": "[", "headers": [{"text": "Operator"}]}]}}]
    assert evaluate(parts)["state"] == "CONFIRMED"


def test_exact_alias_in_formal_alternative_is_preserved():
    parts = fixture("ALTERNATE")
    h = parts[1]["source_blocks"][0]
    h["text"] = "PRIMARY"
    proof = h["definition_section_proofs"][0]
    proof.update(primary_identity="PRIMARY", identity_values=["PRIMARY", "ALTERNATE"],
                 identity_anchor_block_ids={"ALTERNATE": ["grammar"]})
    parts[1]["source_blocks"].append({"block_id": "grammar", "text": "{ PRIMARY | ALTERNATE } ( x )", "owner_ids": ["o"]})
    assert evaluate(parts)["state"] == "CONFIRMED"


def test_formal_subsection_without_global_complete_proof():
    parts = fixture()
    parts[1]["source_blocks"] = [{"block_id": "b", "owner_ids": ["o"], "text": "Syntax: DEMO(x)",
        "observed_context": {"heading_path": ["Functions", "DEMO", "Numeric"], "labels": [{"text": "Syntax"}]}}]
    assert evaluate(parts)["state"] == "CONFIRMED"


def test_cross_subject_same_string_remains_review_without_regrouping():
    parts = fixture()
    original = copy.deepcopy(parts[0])
    foreign = copy.deepcopy(parts[1]["source_blocks"][0])
    foreign.update(block_id="foreign", text="OTHER")
    foreign["definition_section_proofs"][0].update(heading_block_id="foreign", primary_identity="OTHER", identity_values=["OTHER"])
    parts[1]["source_blocks"].append(foreign)
    result = evaluate(parts)
    assert result["state"] == "REVIEW" and result["subject_conflicts"]
    assert parts[0] == original


def test_type_conflict_cannot_be_resolved_by_semantic_complete():
    parts = fixture()
    parts[1]["source_blocks"][0]["heading_ancestry"] = ["Data Types"]
    parts[1]["semantic_status"] = "COMPLETE"
    parts[1]["description"] = "nonempty grounded official prose"
    assert "DEFINING_TYPE_CONFLICT" in evaluate(parts)["reason_codes"]


def test_same_name_function_and_datatype_cannot_silently_share_release_subject():
    parts = fixture()
    other = copy.deepcopy(parts[1]["source_blocks"][0])
    other.update(block_id="datatype", heading_ancestry=["Data Types"])
    other["definition_section_proofs"][0]["heading_block_id"] = "datatype"
    parts[1]["source_blocks"].append(other)
    assert "DEFINING_TYPE_CONFLICT" in evaluate(parts)["reason_codes"]


def test_repeated_compatible_owners_are_not_a_subject_conflict():
    parts = fixture()
    parts[0]["owner_ids"].append("second")
    other = copy.deepcopy(parts[1]["source_blocks"][0])
    other.update(block_id="second_definition", owner_ids=["second"])
    other["definition_section_proofs"][0]["heading_block_id"] = "second_definition"
    parts[1]["source_blocks"].append(other)
    assert evaluate(parts)["state"] == "CONFIRMED"


def test_missing_upstream_decision_cannot_be_inferred_from_fields():
    parts = fixture()
    parts = (*parts[:2], {}, *parts[3:])
    assert "TYPE_DECISION_NOT_EVALUATED" in evaluate(parts)["reason_codes"]


def test_target_resolution_proposal_is_not_self_confirmation():
    parts = fixture(status="REVIEW_REQUIRED")
    record = {**parts[0], "previous_type": "FUNCTION", "group_status": "PROBABLE_FEATURE"}
    proposal = {"feature_type": "FUNCTION", "evidence_refs": ["h"], "resolved_conflict_refs": []}
    applied = apply_target_decision(record, "FUNCTION", "reason", model="no-provider", prompt_version="test", response_hash=None,
                                    resolution_evidence=proposal)
    assert applied["resolution_evidence"] == proposal
    payload = target_classification_prompt(parts[0], parts[1], type_decision=parts[2])
    assert "REVIEW_REQUIRED" in payload and "resolution_evidence" in payload


def test_strict_publication_blocks_grounded_wrong_subject_keeps_fields():
    record = {"feature_id": "f", "canonical_identity": {"key": "k"}, "provenance": {"identity_unit_id": "u"},
              "descriptions": ["Grounded official text"]}
    a = {"identity_unit_id": "u", "decision": "ADMITTED", "identity_credible": True, "official_source": True,
         "owner_evidence_correct": True, "lineage_complete": True}
    v = {"identity_group_id": "u", "semantic_status": "COMPLETE", "invalid_evidence_refs": 0, "ungrounded_claims": 0}
    assert len(select_publication([record], [a], [v], {"status": "PASS"})[0]) == 1
    assert not select_publication([record], [a], [v], {"status": "PASS"}, integrity_required=True)[0]
    a["publication_integrity"] = {"state": "REVIEW", "reason_codes": ["SUBJECT_CONFLICT"]}
    final, review, decisions = select_publication([record], [a], [v], {"status": "PASS"}, integrity_required=True)
    assert not final and review[0]["record"] == record
    assert "SUBJECT_CONFLICT" in decisions[0]["reason_codes"]


def test_unified_production_calls_strict_publication():
    import inspect
    from agents.pipeline.generic_unified import run_generic_downstream, _make_contract_downstream
    assert "integrity_required=True" in inspect.getsource(run_generic_downstream)
    assert "publication_integrity=integrity" in inspect.getsource(_make_contract_downstream)


def test_release_requires_a_complete_matching_decision_not_just_confirmed_string():
    contract = evaluate(fixture())
    assert publication_integrity_reasons(contract, "u") == []
    assert publication_integrity_reasons(contract, "another-unit")
    assert publication_integrity_reasons({"state": "CONFIRMED"}, "u")
    contract["source_type_status"] = "REVIEW_REQUIRED"
    assert publication_integrity_reasons(contract, "u")




