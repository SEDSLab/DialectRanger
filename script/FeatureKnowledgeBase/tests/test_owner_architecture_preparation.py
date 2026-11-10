from __future__ import annotations

import json
from pathlib import Path

from agents.contracts.document_ir import from_v2_page
from agents.contracts.owner_inference import (
    deterministic_candidate_key,
    make_owner_candidate,
    owner_candidate_schema,
    owner_ranking_schema,
)
from agents.document_structure_v2.topology import build_page_topology
from agents.pipeline.production_identity import execute_production_identity_path
from agents.profile_bootstrap.cold_start import (
    build_cold_start_profile,
    profile_structural_hints,
    validate_cold_start_profile,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "owner_architecture"


def _topology(name: str, *, feature_context: list[str] | None = None) -> dict:
    path = FIXTURES / name
    return build_page_topology(
        {
            "url": f"https://example.test/{name}",
            "html_path": str(path),
            "feature_context": feature_context or [],
            "crawl_depth": 0,
        },
        {"main_selectors": ["main", "body"], "noise_selectors": []},
    )


def _document(name: str, *, feature_context: list[str] | None = None) -> dict:
    page = _topology(name, feature_context=feature_context)
    return from_v2_page(page, dbms="fixture-db", html_path=FIXTURES / name)


def _candidate(*, page_id: str, owner_kind: str, features: dict[str, float], profile_hints=()):
    return make_owner_candidate(
        dbms="fixture-db",
        page_id=page_id,
        source_url="https://example.test/page.html",
        page_hash="hash",
        html_path="page.html",
        owner_kind=owner_kind,
        template=owner_kind.casefold(),
        start_block_id="block:start",
        end_block_id="block:end",
        root_block_id="block:root",
        member_block_ids=["block:start", "block:end"],
        structural_parent_id="boundary:1",
        structural_parent_kind="repeated_unit",
        ancestor_chain=[{"id": "boundary:1", "kind": "repeated_unit"}],
        identity_anchor_ids=["block:start"],
        structural_signals=["DOM_CONTAINMENT"],
        profile_hints=profile_hints,
        ranking_features=features,
    )


def test_document_ir_preserves_list_containment_and_sibling_order():
    page = _topology("list_item.html")
    repeated = [row for row in page["blocks"] if row["boundary_kind"] == "repeated_unit"]
    assert len(repeated) >= 3
    groups = {}
    for row in repeated:
        groups.setdefault(row["boundary_id"], []).append(row)
    assert len(groups) == 2
    assert all(row["boundary_kind"] == "repeated_unit" for row in repeated)
    assert any("result type" in row["text"] for row in repeated)

    document = from_v2_page(page, dbms="fixture-db", html_path=FIXTURES / "list_item.html")
    for rows in groups.values():
        member_ids = {row["block_id"] for row in rows}
        document_rows = [row for row in document["blocks"] if row["block_id"] in member_ids]
        assert {row["structural_parent_kind"] for row in document_rows} == {"repeated_unit"}
        assert {row["structural_parent_id"] for row in document_rows} == {rows[0]["boundary_id"]}
        assert [row["sibling_index"] for row in document_rows] == sorted(row["sibling_index"] for row in document_rows)
        assert all(row["ancestor_chain"] for row in document_rows)


def test_table_rows_have_independent_structural_parents():
    page = _topology("table_row.html")
    data_rows = [row for row in page["blocks"] if row["block_type"] == "table_row" and ("ABS" in row["text"] or "ACOS" in row["text"])]
    assert len(data_rows) == 2
    assert data_rows[0]["boundary_id"] != data_rows[1]["boundary_id"]
    document = from_v2_page(page, dbms="fixture-db", html_path=FIXTURES / "table_row.html")
    converted = [row for row in document["blocks"] if row["block_id"] in {item["block_id"] for item in data_rows}]
    assert all(row["block_kind"] == "TABLE_ROW" for row in converted)
    assert all(row["structural_parent_kind"] == "table_row" for row in converted)


def test_heading_ancestry_and_single_feature_context_are_retained():
    document = _document("heading_section.html")
    abs_blocks = [row for row in document["blocks"] if "ABS" in row["text"]]
    assert abs_blocks
    assert any("ABS(X)" in row["heading_ancestry"] for row in abs_blocks if row["block_kind"] != "HEADING")

    single = _topology("single_feature.html")
    non_heading = [row for row in single["blocks"] if row["block_type"] in {"paragraph", "code"}]
    # block_id identifies a source block; boundary_id identifies its logical
    # section. Adjacent paragraphs must stay distinct without fragmenting
    # their shared Description section.
    assert len({row["block_id"] for row in single["blocks"]}) == len(single["blocks"])
    paragraphs = [row for row in non_heading if row["block_type"] == "paragraph"]
    assert [row["text"] for row in paragraphs] == [
        "Returns the absolute value of X .",
        "If the argument is NULL, the result is NULL.",
    ]
    assert paragraphs[0]["order"] < paragraphs[1]["order"]
    description = next(row for row in single["blocks"] if row["text"] == "Description")
    assert {row["boundary_id"] for row in paragraphs} == {description["boundary_id"]}
    example = next(row for row in non_heading if row["block_type"] == "code")
    examples_heading = next(row for row in single["blocks"] if row["text"] == "Examples")
    assert example["text"] == "SELECT ABS(-32);"
    assert example["boundary_id"] == examples_heading["boundary_id"]
    assert example["boundary_id"] != description["boundary_id"]
    converted = from_v2_page(single, dbms="fixture-db", html_path=FIXTURES / "single_feature.html")
    converted_blocks = {row["block_id"]: row for row in converted["blocks"]}
    for row in non_heading:
        retained = converted_blocks[row["block_id"]]
        assert retained["text"] == row["text"]
        assert retained["dom_path"] == row["dom_path"]
        assert retained["structural_boundary_id"] == row["boundary_id"]
        assert "ABS(X)" in retained["heading_ancestry"]
    page_owner = make_owner_candidate(
        dbms="fixture-db", page_id=single["page_id"], source_url=single["url"],
        page_hash=single["page_hash"], html_path=str(FIXTURES / "single_feature.html"),
        owner_kind="PAGE_OWNER", template="single_feature_page",
        start_block_id=single["blocks"][0]["block_id"], end_block_id=single["blocks"][-1]["block_id"],
        root_block_id=single["blocks"][0]["block_id"],
        member_block_ids=[row["block_id"] for row in single["blocks"]],
        structural_signals=["SINGLE_FEATURE_PAGE"],
    )
    assert page_owner["owner_kind"] == "PAGE_OWNER"


def test_definition_term_body_fixture_has_explicit_pair_contract():
    assert "INTEGER" in (FIXTURES / "definition_list.html").read_text(encoding="utf-8")
    page = {
        "page_id": "page:definitions",
        "url": "https://example.test/definition_list.html",
        "title": "Data types",
        "page_hash": "hash",
        "blocks": [
            {"block_id": "block:term", "block_type": "definition_term", "text": "INTEGER",
             "dom_path": "main > dl > dt", "order": 0, "boundary_id": "boundary:integer",
             "boundary_kind": "repeated_unit", "section_id": "page:definitions#document"},
            {"block_id": "block:body", "block_type": "definition_description", "text": "A signed integer type.",
             "dom_path": "main > dl > dd", "order": 1, "boundary_id": "boundary:integer",
             "boundary_kind": "repeated_unit", "section_id": "page:definitions#document"},
        ],
    }
    document = from_v2_page(page, dbms="fixture-db", html_path=FIXTURES / "definition_list.html")
    assert [row["block_kind"] for row in document["blocks"]] == ["DEFINITION", "DEFINITION"]
    assert all(row["structural_parent_id"] == "boundary:integer" for row in document["blocks"])
    assert [row["sibling_index"] for row in document["blocks"]] == [0, 1]


def test_signature_fallback_and_example_context_are_explicitly_distinct():
    page = _topology("signature_span.html")
    signature = next(row for row in page["blocks"] if "ST_X" in row["text"])
    body = next(row for row in page["blocks"] if "coordinate" in row["text"])
    next_signature = next(row for row in page["blocks"] if "ST_Y" in row["text"])
    fallback = make_owner_candidate(
        dbms="fixture-db", page_id=page["page_id"], source_url=page["url"],
        page_hash=page["page_hash"], html_path=str(FIXTURES / "signature_span.html"),
        owner_kind="SIGNATURE_SPAN_OWNER", template="signature_span",
        start_block_id=signature["block_id"], end_block_id=body["block_id"],
        root_block_id=signature["block_id"], member_block_ids=[signature["block_id"], body["block_id"]],
        identity_anchor_ids=[signature["block_id"]],
        structural_signals=["STANDALONE_SIGNATURE", "NO_STRONGER_CONTAINER"],
        ranking_features={"structural_containment_score": 0.35, "defining_anchor_score": 0.8,
                          "peer_symmetry_score": 0.2, "semantic_completeness_score": 0.4,
                          "example_penalty": 0.0, "contamination_penalty": 0.0,
                          "boundary_confidence": 0.5},
    )
    assert fallback["owner_kind"] == "SIGNATURE_SPAN_OWNER"
    assert next_signature["order"] > body["order"]
    assert next_signature["boundary_id"] != signature["boundary_id"]

    example_page = _topology("example_conflict.html")
    code = next(row for row in example_page["blocks"] if "ABS(-32)" in row["text"])
    heading = next(row for row in example_page["blocks"] if row["block_type"] == "heading")
    assert code["block_type"] == "code"
    assert code["block_id"] != heading["block_id"]
    func_example = next(row for row in example_page["blocks"] if "FUNC_B(FUNC_A" in row["text"])
    func_peer = next(row for row in example_page["blocks"] if "FUNC_B(X)" in row["text"])
    assert func_example["block_type"] == "code"
    assert func_peer["block_type"] == "paragraph"
    assert func_example["block_id"] != func_peer["block_id"]
    example_candidate = _candidate(
        page_id=example_page["page_id"], owner_kind="HEADING_SECTION_OWNER",
        features={"structural_containment_score": 0.9, "defining_anchor_score": 0.95,
                  "peer_symmetry_score": 0.8, "semantic_completeness_score": 0.8,
                  "example_penalty": 1.0, "contamination_penalty": 0.0,
                  "boundary_confidence": 0.9},
    )
    assert code["block_id"] not in example_candidate["identity_anchor_ids"]
    assert example_candidate["ranking_features"]["example_penalty"] == 1.0


def test_mentions_remain_in_the_current_structural_block():
    page = _topology("mention_conflict.html")
    avg = next(row for row in page["blocks"] if row["block_type"] == "heading" and "AVG" in row["text"])
    prose = next(row for row in page["blocks"] if "See" in row["text"])
    assert avg["block_id"] != prose["block_id"]
    assert avg["boundary_id"] == prose["boundary_id"]
    assert any(link["source_block_id"] == prose["block_id"] for link in page["links"] if link["anchor_text"] == "ABS()")
    assert prose["text"].startswith("Returns the average")

    # Exercise the production Owner -> Identity path, not just topology or a
    # manually constructed owner. The cross-reference must not steal AVG's
    # definition or turn into an ABS identity on this page.
    document = from_v2_page(page, dbms="fixture-db", html_path=FIXTURES / "mention_conflict.html")
    result = execute_production_identity_path([document])
    assert {unit["canonical_identity"] for unit in result["units"]} == {"AVG"}
    defining = [row for row in result["occurrences"] if row["defining"]]
    assert defining
    assert {row["normalized_identity"] for row in defining} == {"AVG"}
    assert {row["source_block_id"] for row in defining} == {avg["block_id"]}
    assert all("ABS" not in owner.get("owner_definition_identities", []) for owner in result["owners"])
    unit = result["units"][0]
    owners = {owner["owner_id"]: owner for owner in result["owners"]}
    assert unit["owner_ids"]
    for owner_id in unit["owner_ids"]:
        owner = owners[owner_id]
        assert "AVG" in owner["owner_definition_identities"]
        assert {avg["block_id"], prose["block_id"]}.issubset(owner["owner_scope_block_ids"])
    assert result["lineage"]["status"] == "PASS"


def test_profile_exposes_multiple_hints_without_forcing_owner_selection():
    documents = [_document("list_item.html"), _document("table_row.html")]
    profile = build_cold_start_profile(dbms="fixture-db", documents=documents, run_id="owner-prep")
    hints = profile_structural_hints(profile)
    assert len(hints["structural_region_hints"]) >= 2
    assert hints["selection_authority"] == "OWNER_CANDIDATE_INFERENCE"
    assert "EXAMPLE_CODE" in hints["negative_surface_hints"]
    validate_cold_start_profile(profile)

    custom = dict(profile)
    custom["hint_contract"] = {
        "schema_version": "profile-structural-hints/v1",
        "page_granularity": "MIXED",
        "structural_region_hints": ["TABLE_ROW", "HEADING_SECTION"],
        "identity_surface_hints": ["FIRST_CELL_OR_LINK", "HEADING"],
        "boundary_hints": ["SIBLING_TABLE_ROW", "HEADING_BOUNDED_SECTION"],
        "negative_surface_hints": ["EXAMPLE_CODE"],
        "example_surface_hints": ["CODE_BLOCK"],
        "legacy_owner_strategy_hints": ["table_row", "heading_section"],
        "selection_authority": "OWNER_CANDIDATE_INFERENCE",
    }
    assert profile_structural_hints(custom)["page_granularity"] == "MIXED"
    validate_cold_start_profile(custom)


def test_dom_evidence_beats_profile_hint_in_deterministic_shadow_ranking():
    weak_dom = _candidate(
        page_id="page:weak", owner_kind="SIGNATURE_SPAN_OWNER",
        profile_hints=["TABLE_ROW"],
        features={"structural_containment_score": 0.2, "defining_anchor_score": 0.7,
                  "peer_symmetry_score": 0.1, "semantic_completeness_score": 0.4,
                  "example_penalty": 0.0, "contamination_penalty": 0.0,
                  "boundary_confidence": 0.3},
    )
    strong_dom = _candidate(
        page_id="page:strong", owner_kind="TABLE_ROW_OWNER",
        profile_hints=["SIGNATURE"],
        features={"structural_containment_score": 0.95, "defining_anchor_score": 0.8,
                  "peer_symmetry_score": 0.9, "semantic_completeness_score": 0.7,
                  "example_penalty": 0.0, "contamination_penalty": 0.0,
                  "boundary_confidence": 0.95},
    )
    assert sorted([weak_dom, strong_dom], key=deterministic_candidate_key)[0] is strong_dom
    assert owner_candidate_schema()["selection_authority"] == "deterministic_owner_ranking"
    assert owner_ranking_schema()["profile_hints_are_decisive"] is False
    same_page_candidates = [
        _candidate(page_id="page:same", owner_kind="TABLE_ROW_OWNER", features=strong_dom["ranking_features"]),
        _candidate(page_id="page:same", owner_kind="SIGNATURE_SPAN_OWNER", features=weak_dom["ranking_features"]),
    ]
    assert len({row["candidate_id"] for row in same_page_candidates}) == 2


def test_abs_frozen_region_is_a_candidate_coverage_regression():
    path = ROOT / "output" / "mysql_identity_quality_v3" / "document_ir" / "documents.jsonl"
    if not path.is_file():
        return
    target = None
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("url", "").endswith("/mathematical-functions.html"):
            target = row
            break
    assert target is not None
    texts = {block["text"] for block in target["blocks"]}
    expected_fragments = (
        "ABS( X )", "Returns the absolute value", "All mathematical functions return NULL",
        "The result type is derived from the argument type", "SELECT ABS(-32)",
        "safe to use with BIGINT",
    )
    assert all(any(fragment in text for text in texts) for fragment in expected_fragments)
    by_text = lambda fragment: next(block["block_id"] for block in target["blocks"] if fragment in block["text"])
    signature_id = by_text("ABS( X )")
    example_id = by_text("SELECT ABS(-32)")
    covered_ids = [
        by_text("ABS( X )"),
        by_text("Returns the absolute value"),
        by_text("All mathematical functions return NULL"),
        by_text("The result type is derived from the argument type"),
        by_text("SELECT ABS(-32)"),
        by_text("safe to use with BIGINT"),
    ]
    candidate = make_owner_candidate(
        dbms="mysql", page_id=target["page_id"], source_url=target["url"],
        page_hash=target["page_hash"], html_path=target["html_path"],
        owner_kind="HEADING_SECTION_OWNER", template="abs-frozen-region",
        start_block_id=signature_id, end_block_id=covered_ids[-1], root_block_id=signature_id,
        member_block_ids=covered_ids, identity_anchor_ids=[signature_id],
        identity_shaped_token_ids=[example_id], structural_signals=["FROZEN_ABS_REGION"],
        ranking_features={"structural_containment_score": 0.95, "defining_anchor_score": 0.95,
                          "peer_symmetry_score": 0.8, "semantic_completeness_score": 0.9,
                          "example_penalty": 0.8, "contamination_penalty": 0.0,
                          "boundary_confidence": 0.95},
    )
    assert signature_id in candidate["identity_anchor_ids"]
    assert example_id not in candidate["identity_anchor_ids"]
    assert set(covered_ids).issubset(set(candidate["member_block_ids"]))
