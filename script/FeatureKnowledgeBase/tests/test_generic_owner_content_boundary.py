"""Regression tests for the generic feature-section owner primitive."""
from __future__ import annotations

from agents.owner.generic import generate_owner_hypotheses


def _document(text_blocks):
    boundary = "boundary:feature-a"
    blocks = []
    for index, (kind, text) in enumerate(text_blocks):
        blocks.append({
            "block_id": f"block:{index}",
            "block_kind": kind,
            "text": text,
            "order": index,
            "heading_level": None,
            "provenance": {"parent_ids": [boundary]},
        })
    return {
        "dbms": "fixture",
        "page_id": "page:1",
        "document_id": "document:1",
        "url": "https://docs.test/functions",
        "page_hash": "hash",
        "html_path": "fixture.html",
        "blocks": blocks,
    }


def test_signature_owner_keeps_prose_and_example_until_next_peer():
    document = _document([
        ("TEXT", "FUNC_A(x)"),
        ("TEXT", "Returns the value for the argument."),
        ("TEXT", "The result is nullable."),
        ("CODE", "SELECT OTHER_FUNC(x);"),
        ("TEXT", "This is a compatibility note."),
        ("TEXT", "FUNC_B(x)"),
        ("TEXT", "Description for B."),
    ])

    owners = generate_owner_hypotheses([document])
    a = next(owner for owner in owners if owner["start_block_id"] == "block:0")
    assert a["member_block_ids"] == [f"block:{index}" for index in range(5)]
    assert "block:5" not in a["member_block_ids"]


def test_signature_owner_binds_prose_without_repeated_identity():
    document = _document([
        ("TEXT", "FUNC(x)"),
        ("TEXT", "Returns the value."),
        ("TEXT", "If the argument is NULL, the result is NULL."),
        ("CODE", "SELECT FUNC(1);"),
    ])

    owners = generate_owner_hypotheses([document])
    assert len(owners) == 1
    assert owners[0]["member_block_ids"] == ["block:0", "block:1", "block:2", "block:3"]


def test_same_head_overloads_share_one_surface_but_different_head_stops():
    document = _document([
        ("TEXT", "FUNC(x)"),
        ("TEXT", "FUNC(x, y)"),
        ("TEXT", "Overload-specific description."),
        ("TEXT", "OTHER(x)"),
        ("TEXT", "Other description."),
    ])

    owners = generate_owner_hypotheses([document])
    func_owners = [owner for owner in owners if owner["start_block_id"] == "block:0"]
    assert len(func_owners) == 1
    assert func_owners[0]["member_block_ids"] == ["block:0", "block:1", "block:2"]
    assert "block:3" not in func_owners[0]["member_block_ids"]


def test_code_example_with_other_function_text_does_not_create_peer_boundary():
    document = _document([
        ("TEXT", "FUNC_A(x)"),
        ("CODE", "SELECT FUNC_B(x);"),
        ("TEXT", "FUNC_A returns a value."),
        ("TEXT", "FUNC_B(x)"),
    ])

    owners = generate_owner_hypotheses([document])
    a = next(owner for owner in owners if owner["start_block_id"] == "block:0")
    assert a["member_block_ids"] == ["block:0", "block:1", "block:2"]

