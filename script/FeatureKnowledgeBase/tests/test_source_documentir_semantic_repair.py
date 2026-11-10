from __future__ import annotations

from agents.contracts.document_ir import from_v2_page
from agents.document_structure_v2.topology import build_page_topology
from agents.owner.production import build_production_owner_layer
from agents.semantic_stage.evidence_bundle import build_evidence_bundle, build_indexes


PROFILE = {
    "main_selectors": ["main", "article", "body"],
    "noise_selectors": ["nav", "header", "footer", "aside", "script", "style"],
}


def _document(tmp_path, body: str):
    path = tmp_path / "fixture.html"
    path.write_text(f"<html><body><main>{body}</main></body></html>", encoding="utf-8")
    topology = build_page_topology(
        {"url": "https://example.test/docs/fixture", "html_path": str(path)}, PROFILE
    )
    return from_v2_page(topology, dbms="fixture", html_path=path), topology


def test_native_and_component_paragraphs_have_same_text_surface(tmp_path):
    document, _ = _document(
        tmp_path,
        "<h2>foo()</h2><p>Returns foo.</p>"
        "<span data-as='p'>Returns component foo.</span>"
        "<div data-as='p'>Returns div foo.</div>",
    )
    texts = [block["text"] for block in document["blocks"] if block["block_kind"] == "TEXT"]
    assert texts == ["Returns foo.", "Returns component foo.", "Returns div foo."]
    assert all(block.get("semantic_role") == "PARAGRAPH" for block in document["blocks"] if block["block_kind"] == "TEXT")


def test_nested_inline_code_is_not_promoted_to_duplicate_block(tmp_path):
    document, topology = _document(
        tmp_path,
        "<h2>abs()</h2><span data-as='p'>Returns absolute value of <code>x</code>.</span>",
    )
    text_blocks = [block for block in document["blocks"] if block["block_kind"] == "TEXT"]
    assert len(text_blocks) == 1
    assert text_blocks[0]["text"] == "Returns absolute value of x ."
    assert any(node["text"] == "x" for node in text_blocks[0]["inline_nodes"])
    assert not any(block["block_kind"] == "CODE" and block["text"] == "x" for block in document["blocks"])
    assert sum(1 for block in topology["blocks"] if block.get("semantic_role") == "PARAGRAPH") == 1


def test_owner_isolation_and_parameter_subsection_are_heading_local(tmp_path):
    document, _ = _document(
        tmp_path,
        "<h2>foo()</h2><h3>Arguments</h3><ul><li>x — first value</li></ul>"
        "<h3>Returned value</h3><span data-as='p'>An integer.</span>"
        "<h2>bar()</h2><span data-as='p'>Description of bar.</span>",
    )
    owners = build_production_owner_layer([document], profile={})["owners"]
    foo_owners = [row for row in owners if row.get("primary_identity") == "FOO"]
    assert foo_owners
    foo_text = " ".join(
        block["text"]
        for owner in foo_owners
        for block in document["blocks"]
        if block["block_id"] in set(owner.get("member_block_ids") or [])
    )
    assert "first value" in foo_text
    assert "An integer." in foo_text
    assert "Description of bar." not in foo_text


def test_cross_owner_description_cannot_become_foo_owner_content(tmp_path):
    document, _ = _document(
        tmp_path,
        "<h2>foo()</h2><pre>foo(x)</pre>"
        "<h2>bar()</h2><span data-as='p'>Description of bar.</span>",
    )
    owners = build_production_owner_layer([document], profile={})["owners"]
    foo_owners = [row for row in owners if row.get("primary_identity") == "FOO"]
    assert foo_owners
    for owner in foo_owners:
        member_ids = set(owner.get("member_block_ids") or [])
        assert all(
            block["text"] != "Description of bar."
            for block in document["blocks"]
            if block["block_id"] in member_ids
        )


def test_component_section_labels_propagate_to_owner_local_evidence(tmp_path):
    document, _ = _document(
        tmp_path,
        "<h2>abs</h2><span data-as='p'>Introduced in: v1.0</span>"
        "<span data-as='p'>Calculates the absolute value.</span>"
        "<span data-as='p'>Syntax</span><pre>abs(x)</pre>"
        "<span data-as='p'>Arguments</span><ul><li>x — input value</li></ul>"
        "<span data-as='p'>Returned value</span><span data-as='p'>The absolute value.</span>"
        "<span data-as='p'>Examples</span><pre>SELECT abs(x)</pre>",
    )
    owners = build_production_owner_layer([document], profile={})["owners"]
    abs_owners = [row for row in owners if row.get("primary_identity") == "ABS"]
    assert abs_owners
    owner = max(abs_owners, key=lambda row: len(row.get("member_block_ids") or []))
    blocks = {row["block_id"]: row for row in document["blocks"]}
    member_ids = list(owner.get("member_block_ids") or [])
    signature_id = next(row["block_id"] for row in document["blocks"] if row["text"] == "abs(x)")
    group = {
        "identity_unit_id": "identity_unit:abs",
        "canonical_identity": "ABS",
        "feature_type": "FUNCTION",
        "occurrence_ids": ["occ:abs"],
        "owner_ids": [owner["owner_id"]],
    }
    occurrence = {
        "occurrence_id": "occ:abs",
        "normalized_identity": "ABS",
        "raw_identity": "abs(x)",
        "raw_evidence": "abs(x)",
        "occurrence_role": "DEFINING_IDENTITY",
        "owner_id": owner["owner_id"],
        "page_id": document["page_id"],
        "source_block_id": signature_id,
        "source_url": document["url"],
        "owner_local_binding": True,
    }
    bundle = build_evidence_bundle(
        group,
        indexes=build_indexes([group], [occurrence], [owner], [document]),
    )
    assert len(set(member_ids) & {row["block_id"] for row in bundle["source_blocks"]}) == len(member_ids)
    assert bundle["definition_evidence"]
    assert bundle["formal_syntax_evidence"]
    assert bundle["parameter_evidence"]
    assert bundle["return_type_evidence"]
    assert bundle["official_example_evidence"]
    assert all(row.get("owner_local_binding") is True for row in bundle["source_blocks"])
