from pathlib import Path

import pytest

from agents.document_structure_v2 import default_profile_v2, evaluate_gold_paths, run_stage1
from agents.document_structure_v2.profile import build_induction_payload, validate_induction_output
from agents.document_structure_v2.topology import build_page_topology


FIXTURES = Path(__file__).parent / "fixtures" / "document_structure_v2"
BASE = "https://docs.example.test/reference/"


def _spec(name, context, depth=0):
    return {"url": BASE + name, "html_path": str(FIXTURES / name),
            "feature_context": context, "crawl_depth": depth}


def _run(tmp_path=None):
    specs = [
        _spec("function_index.html", ["function", "operator"]),
        _spec("function_detail.html", ["function"], 1),
        _spec("datatype_index.html", ["datatype"]),
        _spec("datatype_detail.html", ["datatype"], 1),
        _spec("operator_index.html", ["operator"]),
        _spec("operator_detail.html", ["operator"], 1),
    ]
    return run_stage1(default_profile_v2("exampledb"), specs, tmp_path)


def _features(result, kind, name):
    return [row for row in result["canonical_features"]
            if row["identity"]["feature_type"] == kind and row["identity"]["name"] == name]


def test_gold_paths_are_owner_aware_and_canonicalized_once(tmp_path):
    result = _run(tmp_path)
    function = _features(result, "function", "abs")
    datatype = _features(result, "datatype", "bit")
    operator = _features(result, "operator", "between")

    assert len(function) == len(datatype) == len(operator) == 1
    assert function[0]["identity"]["discriminator"] == {
        "kind": "arity_range", "arity_min": 1, "arity_max": 1,
    }
    assert set(function[0]["syntax_variants"]) == {"ABS()", "ABS( X )"}
    assert datatype[0]["identity"]["discriminator"] == {"kind": "base_datatype", "parameterized": True}
    assert set(datatype[0]["syntax_variants"]) == {"BIT[( M )]", "BIT( M )"}
    assert operator[0]["identity"]["discriminator"] == {"kind": "fixed", "operands": 3}
    assert set(operator[0]["syntax_variants"]) == {"BETWEEN ... AND ...", "expr BETWEEN min AND max"}
    assert not any(row["identity"]["name"] in {"day", "hour", "month", "year"}
                   for row in result["canonical_features"])
    assert result["evidence_errors"] == []
    assert result["diagnostics"]["duplicate_identities"] == []
    assert all(row["relation_ids"] for row in function + datatype + operator)
    assert all(row["trace"]["mention_to_href_relation"] for row in function + datatype + operator)
    assert all(row["trace"]["evidence_refs"] for row in function + datatype + operator)
    assert (tmp_path / "feature_mentions.jsonl").exists()

    evaluation = evaluate_gold_paths(result, [
        {"feature_type": "function", "name": "ABS", "discriminator": {
            "kind": "arity_range", "arity_min": 1, "arity_max": 1,
         },
         "required_fields": ["syntax", "description", "examples", "reference_summary"]},
        {"feature_type": "datatype", "name": "BIT", "discriminator": {"kind": "base_datatype", "parameterized": True},
         "required_fields": ["syntax", "description", "constraint"]},
        {"feature_type": "operator", "name": "BETWEEN", "discriminator": {"kind": "fixed", "operands": 3},
         "required_fields": ["syntax", "description", "examples", "reference_summary"]},
    ])
    assert evaluation["status"] == "PASS", evaluation["failures"]


def test_topology_preserves_href_and_separates_depths():
    result = _run()
    page = next(row for row in result["pages"] if row["url"].endswith("function_index.html"))
    link = next(row for row in page["links"] if row["anchor_text"] == "ABS()")
    assert page["crawl_depth"] == 0
    assert page["toc_depth"] is None
    assert link["raw_href"] == "function_detail.html#feature_abs"
    assert link["resolved_url"] == BASE + "function_detail.html"
    assert link["fragment"] == "feature_abs"
    assert link["source_block_id"]
    assert link["dom_path"]
    assert link["resolution_status"] == "resolved_anchor"


def test_layout_table_row_does_not_swallow_nested_feature_rows(tmp_path):
    page_path = tmp_path / "dense.html"
    page_path.write_text("""<html><body><table><tr><td><nav>Home</nav>
        <h2>Functions</h2><table><tr><td>ABS(X)</td><td>Absolute value</td></tr>
        <tr><td>ACOS(X)</td><td>Arc cosine</td></tr></table>
        <h3>ABS</h3><pre>ABS(X)</pre></td></tr></table></body></html>""", encoding="utf-8")
    page = build_page_topology({"url": BASE + "dense.html", "html_path": str(page_path),
                                "feature_context": ["FUNCTION"], "crawl_depth": 0}, {})
    rows = [b for b in page["blocks"] if b["block_type"] == "table_row"]
    assert len(rows) == 2
    assert [b["cells"][0] for b in rows] == ["ABS(X)", "ACOS(X)"]
    assert any(b["block_type"] == "heading" and b["text"] == "ABS" for b in page["blocks"])


def test_topology_materializes_leaf_list_record_without_nested_duplicate(tmp_path):
    page_path = tmp_path / "inventory.html"
    page_path.write_text("""<html><body><main><h1>Function inventory</h1><ul>
        <li><a href="#alpha">alpha</a>(<i>X</i>)</li>
        <li><p><code>beta(X)</code></p><p>Description.</p></li>
        </ul><h2 id="alpha">alpha function</h2></main></body></html>""", encoding="utf-8")
    page = build_page_topology({"url": BASE + "inventory.html", "html_path": str(page_path),
                                "feature_context": [], "crawl_depth": 0}, {})
    leaf = next(row for row in page["blocks"] if row["block_type"] == "list_item")
    assert leaf["text"] == "alpha ( X )"
    assert not any(row["block_type"] == "list_item" and "beta" in row["text"]
                   for row in page["blocks"])
    link = next(row for row in page["links"] if row["anchor_text"] == "alpha")
    assert link["source_block_id"] == leaf["block_id"]


def test_induction_validator_rejects_hallucinated_structure():
    result = _run()
    payload = build_induction_payload(result["pages"])
    with pytest.raises(ValueError, match="unknown block"):
        validate_induction_output({"pages": [{
            "page_id": payload["pages"][0]["page_id"],
            "page_archetype": "feature_reference_index",
            "owner_groups": [{"identity_blocks": ["invented-block"]}],
        }]}, payload)


def test_profile_has_recipes_not_feature_inventory():
    profile = default_profile_v2("exampledb")
    assert "feature_inventory" not in profile
    assert {row["recipe"] for row in profile["page_templates"]} >= {
        "reference_table_recipe", "aggregate_syntax_recipe",
        "definition_list_recipe", "single_feature_detail_recipe",
    }
