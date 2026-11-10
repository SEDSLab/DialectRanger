"""CanonicalFeature/v2 contract."""
from __future__ import annotations

from typing import Any, Iterable, Mapping
import copy
import json

from agents.contracts.base import build_provenance, require_fields, stable_id, validate_provenance
from agents.contracts.type_decision import FEATURE_TYPES


SCHEMA_VERSION = "CanonicalFeature/v2"


def make_canonical_feature(
    *,
    dbms: str,
    page_id: str,
    source_url: str,
    page_hash: str,
    html_path: str,
    identity_unit_id: str,
    name: str,
    feature_type: str,
    admission_id: str,
    semantic_record_id: str,
    evidence_ids: Iterable[str] = (),
    fields: Mapping[str, Any] | None = None,
    quality_status: str = "REVIEW_REQUIRED",
    parent_ids: Iterable[str] = (),
    semantic_extensions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if feature_type not in FEATURE_TYPES:
        raise ValueError(f"unknown canonical feature type: {feature_type}")
    value = {
        "schema_version": SCHEMA_VERSION,
        "canonical_feature_id": stable_id("canonical_feature", [identity_unit_id, feature_type]),
        "identity_unit_id": identity_unit_id,
        "name": name,
        "feature_type": feature_type,
        "admission_id": admission_id,
        "semantic_record_id": semantic_record_id,
        "evidence_ids": sorted({str(item) for item in evidence_ids if item}),
        "fields": dict(fields or {}),
        "quality_status": quality_status,
        "provenance": build_provenance(
            dbms=dbms, source_url=source_url, page_id=page_id,
            page_hash=page_hash, html_path=html_path,
            producer_stage="canonical",
            producer_version="canonical-contract/v2",
            parent_ids=parent_ids,
        ),
    }
    if semantic_extensions is not None:
        value['semantic_extensions'] = copy.deepcopy(dict(semantic_extensions))
    validate_canonical_feature(value)
    return value


def validate_canonical_feature(value: Mapping[str, Any]) -> None:
    require_fields(
        value,
        ("schema_version", "canonical_feature_id", "identity_unit_id", "name",
         "feature_type", "admission_id", "semantic_record_id", "evidence_ids",
         "fields", "quality_status", "provenance"),
        "CanonicalFeature",
    )
    if value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("CanonicalFeature schema_version must be CanonicalFeature/v2")
    if value["feature_type"] not in FEATURE_TYPES:
        raise ValueError(f"unknown CanonicalFeature feature_type: {value['feature_type']}")
    validate_provenance(value["provenance"], label="CanonicalFeature.provenance")
    if 'semantic_extensions' in value:
        if value['feature_type'] != 'DATATYPE':
            raise ValueError('datatype knowledge requires a DATATYPE record')
        from agents.contracts.datatype_knowledge import validate_datatype_extensions
        validate_datatype_extensions(value['semantic_extensions'])


def canonical_schema() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "required": [
            "canonical_feature_id", "identity_unit_id", "name", "feature_type",
            "admission_id", "semantic_record_id", "evidence_ids", "fields",
            "quality_status", "provenance",
        ],
        "optional": {"semantic_extensions": {
            "schema_version": "canonical-semantic-extensions/v1",
            "datatype": ["definition_names", "mappings", "notes", "uncertain_observations"],
            "identity_or_publication_authority": False,
        }},
    }


def read_canonical_record(value: Mapping[str, Any]) -> dict[str, Any]:
    """Lossless reader for current contracts and recognized historical records.

    Reading a historical shape is not migration or endorsement of its contents.
    No absent extension or legacy field is synthesized, renamed or removed.
    """
    result=copy.deepcopy(dict(value))
    if result.get('schema_version')==SCHEMA_VERSION:
        validate_canonical_feature(result)
    elif not ('canonical_identity' in result or any(k in result for k in ('canonical_entry_id','canonical_feature_id'))):
        raise ValueError('unsupported canonical record shape')
    elif 'semantic_extensions' in result:
        if str(result.get('feature_type') or '').upper() != 'DATATYPE':
            raise ValueError('datatype knowledge requires a DATATYPE record')
        from agents.contracts.datatype_knowledge import validate_datatype_extensions
        validate_datatype_extensions(result['semantic_extensions'])
    return result


def serialize_canonical_record(value: Mapping[str, Any]) -> str:
    return json.dumps(read_canonical_record(value),ensure_ascii=False,sort_keys=True)


def deserialize_canonical_record(value: str) -> dict[str, Any]:
    return read_canonical_record(json.loads(value))
