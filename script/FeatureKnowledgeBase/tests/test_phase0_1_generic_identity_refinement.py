from agents.identity.production import run_identity_owner_path


def _document(blocks, *, context=None, title="Generic Reference"):
    return {
        "schema_version": "DocumentIR/v2",
        "dbms": "fixturedb",
        "document_id": "page:fixture",
        "page_id": "page:fixture",
        "url": "https://fixture.invalid/reference",
        "page_hash": "fixture-hash",
        "html_path": "fixture.html",
        "title": title,
        "feature_context": list(context or []),
        "blocks": blocks,
    }


def _block(block_id, kind, text, **extra):
    value = {
        "block_id": f"block:{block_id}",
        "block_kind": kind,
        "text": text,
        "heading_ancestry": [],
        "provenance": {"parent_ids": []},
    }
    value.update(extra)
    return value


def _units(document):
    result = run_identity_owner_path([document])
    return result["units"]


def test_symbolic_and_word_operators_are_recovered_from_operator_catalog():
    row = _block(
        "operator-row", "TABLE_ROW", "@@ AND description",
        heading_ancestry=["Operator Reference"],
        table_metadata={
            "headers": ["Operators", "Description"],
            "cells": ["@@ AND", "description"],
            "cell_items": [["@@", "AND"], ["description"]],
        },
    )
    units = _units(_document([row], context=["operator"]))
    typed = {(row["canonical_identity"], row["type_hint"]) for row in units}
    assert ("@@", "OPERATOR") in typed
    assert ("AND", "OPERATOR") in typed


def test_callable_heading_forms_a_generic_function_identity():
    heading = _block(
        "function-heading", "HEADING", "sample_func(X,Y)",
        heading_ancestry=["Functions"],
    )
    body = _block(
        "function-body", "TEXT", "Returns the documented result.",
        heading_ancestry=["Functions", "sample_func(X,Y)"],
    )
    units = _units(_document([heading, body], context=["function"]))
    sample = [row for row in units if row["canonical_identity"] == "SAMPLE_FUNC"]
    assert sample and sample[0]["type_hint"] == "FUNCTION"
    assert sample[0]["group_status"] == "CONFIRMED_FEATURE"


def test_native_api_prototype_is_not_a_sql_function_identity():
    code = _block(
        "api-prototype", "CODE", "int db_api_call(db_handle*, const char*);",
        heading_ancestry=["Native C API Reference"],
    )
    units = _units(_document([code], title="Native API Reference"))
    assert not any(
        row["canonical_identity"] == "DB_API_CALL"
        and row["group_status"] in {"CONFIRMED_FEATURE", "PROBABLE_FEATURE"}
        for row in units
    )


def test_non_signature_parenthetical_heading_is_not_callable():
    heading = _block(
        "year-heading", "HEADING", "Release Notes (2025)",
        heading_ancestry=["Release History"],
    )
    units = _units(_document([heading], title="Release History"))
    assert not any(row["canonical_identity"] == "RELEASE" for row in units)


def test_virtual_table_method_definition_is_not_sql_feature_identity():
    block = _block(
        "vtab-update", "DEFINITION",
        "UPDATE: The row identified by the key is updated with new values.",
        heading_ancestry=["Virtual Table Methods", "xUpdate Method"],
    )
    units = _units(_document([block], title="Virtual Table Reference"))
    assert not any(
        row["canonical_identity"] == "UPDATE"
        and row["group_status"] in {"CONFIRMED_FEATURE", "PROBABLE_FEATURE"}
        for row in units
    )


def test_sentence_label_on_datatype_page_is_not_a_datatype():
    block = _block(
        "datatype-note", "TEXT",
        "Update: the feature is available in strict tables.",
        heading_ancestry=["Datatypes"],
    )
    units = _units(_document([block], context=["datatype"], title="Data Types"))
    assert not any(row["canonical_identity"] == "UPDATE" for row in units)


def test_affinity_result_table_does_not_promote_result_label_to_datatype():
    block = _block(
        "cast-row", "TABLE_ROW",
        "NONE Casting a value to a type-name with no affinity.",
        heading_ancestry=["CAST expressions"],
        table_metadata={
            "headers": ["Affinity of type-name", "Conversion Processing"],
            "cells": ["NONE", "Casting a value to a type-name with no affinity."],
            "cell_items": [["NONE"], ["Casting a value to a type-name with no affinity."]],
        },
    )
    units = _units(_document([block], context=["operator", "datatype"]))
    assert not any(row["canonical_identity"] == "NONE" and row["type_hint"] == "DATATYPE" for row in units)
