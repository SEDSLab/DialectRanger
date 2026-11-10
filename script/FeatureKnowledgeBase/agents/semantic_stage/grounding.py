"""Deterministic grounding validation for group-level semantic claims."""
from __future__ import annotations

import html
import re
from typing import Any, Mapping
from agents.contracts.field_evidence import build_field_evidence,bind_claim,comparable


def validate_semantic_output(parsed: Mapping[str, Any], bundle: Mapping[str, Any]) -> dict[str, Any]:
    witnesses=build_field_evidence(bundle)
    source_blocks = {
        str(item.get("evidence_ref")): item for item in (bundle.get("source_blocks") or [])
        if item.get("evidence_ref")
    }
    accepted: dict[str, Any] = {
        "description": None,
        "formal_syntax": [],
        "parameters": [],
        "return_type": None,
        "notes": [],
        "official_examples": [],
        "generated_examples": [],
    }
    diagnostics: list[str] = []
    claim_results: list[dict[str, Any]] = []

    description = parsed.get("description")
    if description:
        item, status, reason = _validate_text_claim(description, source_blocks, field="description",bundle=bundle,witnesses=witnesses)
        claim_results.append(_claim_result('description',description,status,reason))
        if item is not None:
            accepted["description"] = item
        else:
            diagnostics.append("DESCRIPTION_" + reason)

    for field in ("formal_syntax", "notes"):
        values = parsed.get(field) or []
        if not isinstance(values, list):
            diagnostics.append(f"{field.upper()}_MALFORMED")
            continue
        for value in values:
            item, status, reason = _validate_text_claim(value, source_blocks, field=field,bundle=bundle,witnesses=witnesses)
            claim_results.append(_claim_result(field,value,status,reason))
            if item is not None:
                accepted[field].append(item)
            else:
                diagnostics.append(f"{field.upper()}_{reason}")

    parameters = parsed.get("parameters") or []
    if not isinstance(parameters, list):
        diagnostics.append("PARAMETERS_MALFORMED")
        parameters = []
    for value in parameters:
        item, status, reason = _validate_parameter(value, source_blocks,bundle=bundle,witnesses=witnesses)
        claim_results.append(_claim_result('parameters',value,status,reason))
        if item is not None:
            accepted["parameters"].append(item)
        else:
            diagnostics.append("PARAMETER_" + reason)

    return_type = parsed.get("return_type")
    if return_type:
        retained=[]
        for value in return_type if isinstance(return_type,list) else [return_type]:
            item,status,reason=_validate_text_claim(value,source_blocks,field='return_type',bundle=bundle,witnesses=witnesses)
            claim_results.append(_claim_result('return_type',value,status,reason))
            if item is not None:retained.append(item)
            else:diagnostics.append('RETURN_TYPE_'+reason)
        accepted['return_type']=(retained if isinstance(return_type,list) else retained[0] if retained else None)

    examples = parsed.get("official_examples") or []
    if not isinstance(examples, list):
        diagnostics.append("OFFICIAL_EXAMPLES_MALFORMED")
        examples = []
    for value in examples:
        item, status, reason = _validate_example(value, source_blocks,bundle=bundle,witnesses=witnesses)
        claim_results.append(_claim_result('official_examples',value,status,reason))
        if item is not None:
            accepted["official_examples"].append(item)
        else:
            diagnostics.append("OFFICIAL_EXAMPLE_" + reason)

    generated = parsed.get("generated_examples") or []
    if not isinstance(generated, list):
        diagnostics.append("GENERATED_EXAMPLES_MALFORMED")
        generated = []
    for value in generated:
        item, status, reason = _validate_generated_example(value)
        claim_results.append(_claim_result('generated_examples',value,status,reason))
        if item is not None:
            accepted["generated_examples"].append(item)
        else:
            diagnostics.append("GENERATED_EXAMPLE_" + reason)

    accepted_count = sum(1 for item in claim_results if item["status"] == "ACCEPTED")
    rejected_count = sum(1 for item in claim_results if item["status"] != "ACCEPTED")
    return {
        "fields": accepted,
        "diagnostics": sorted(set(diagnostics)),
        "claim_results": claim_results,
        "accepted_claims": accepted_count,
        "rejected_claims": rejected_count,
        "invalid_evidence_refs": sum(1 for x in claim_results if x["reason"] == "INVALID_EVIDENCE_REF"),
        "ungrounded_claims": sum(1 for x in claim_results if x["reason"] == "UNGROUNDED"),
    }


def _claim_result(field,value,status,reason):
    return {'field':field,'status':status,'reason':reason,'raw_claim':value,
            'evidence_refs':_refs(value) if isinstance(value,Mapping) else [],
            'disposition':'ACCEPTED' if status=='ACCEPTED' else 'QUARANTINED'}


def semantic_status(fields: Mapping[str, Any], diagnostics: list[str], *, not_run: bool = False) -> str:
    if not_run:
        return "NOT_RUN"
    # Rejected claims remain in validation diagnostics. They do not invalidate
    # other accepted fields or decide whether the identity exists.
    has_description = bool(fields.get("description"))
    has_syntax = bool(fields.get("formal_syntax"))
    has_example = bool(fields.get("official_examples") or fields.get("generated_examples"))
    if has_description and has_syntax and has_example:
        return "COMPLETE"
    # Description, formal syntax, and an official example are the core
    # semantic evidence.  Parameters, return type, and notes are optional
    # enrichment and must not make a structurally-only feature look semantic.
    if has_description or has_syntax or has_example:
        return "PARTIAL"
    return "STRUCTURAL_ONLY"


def normalize_for_match(value: Any) -> str:
    text = html.unescape(str(value or "")).replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip().casefold()
    text = re.sub(r"[\s\.,;:!?()\[\]{}<>`'\"\\]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _validate_text_claim(value: Any, source_blocks: Mapping[str, Mapping[str, Any]], *, field: str,bundle=None,witnesses=None):
    if isinstance(value, str):
        text = value.strip()
        refs: list[str] = []
        item = {"text": text, "evidence_refs": refs}
    elif isinstance(value, Mapping):
        text = str(value.get("text") or value.get("value") or "").strip()
        refs = _refs(value)
        item = {"text": text, "evidence_refs": refs}
        for key in ("confidence", "source_url"):
            if key in value:
                item[key] = value[key]
    else:
        return None, "REJECTED", "MALFORMED"
    if not text:
        return None, "REJECTED", "EMPTY"
    valid, reason = _bind(item,source_blocks,field,bundle,witnesses,value)
    return (item, "ACCEPTED", "OK") if valid else (None, "REJECTED", reason)


def _validate_parameter(value: Any, source_blocks: Mapping[str, Mapping[str, Any]],*,bundle=None,witnesses=None):
    if not isinstance(value, Mapping):
        return None, "REJECTED", "MALFORMED"
    name = str(value.get("name") or value.get("text") or "").strip()
    refs = _refs(value)
    if not name:
        return None, "REJECTED", "EMPTY"
    probe={'text':name,'evidence_refs':refs}
    valid, reason = _bind(probe,source_blocks,'parameters',bundle,witnesses,value)
    if not valid:
        return None, "REJECTED", reason
    item = {**{k:v for k,v in probe.items() if k!='text'},"name": name, "description": None, "evidence_refs": refs}
    description = value.get("description")
    if description:
        desc = str(description).strip()
        desc_probe={'text':desc,'evidence_refs':refs}
        desc_valid, desc_reason = _bind(desc_probe,source_blocks,'parameters',bundle,witnesses,None)
        if desc_valid:
            item["description"] = desc
            for key in ('field_evidence','fragment_refs','source_locations'):
                for entry in desc_probe.get(key,[]):
                    if entry not in item.setdefault(key,[]):item[key].append(entry)
        else:
            return None, "REJECTED", "PARAMETER_DESCRIPTION_" + desc_reason
    return item, "ACCEPTED", "OK"


def _validate_example(value: Any, source_blocks: Mapping[str, Mapping[str, Any]],*,bundle=None,witnesses=None):
    if not isinstance(value, Mapping):
        return None, "REJECTED", "MALFORMED"
    sql = str(value.get("sql") or value.get("text") or "").strip()
    refs = _refs(value)
    if not sql:
        return None, "REJECTED", "EMPTY"
    item = {"sql": sql, "evidence_refs": refs}
    valid, reason = _bind(item,source_blocks,'official_examples',bundle,witnesses,value)
    if not valid:
        return None, "REJECTED", reason
    for key in ("source_url", "title", "role"):
        if value.get(key):
            item[key] = value[key]
    return item, "ACCEPTED", "OK"


def _validate_generated_example(value: Any):
    if not isinstance(value, Mapping):
        return None, "REJECTED", "MALFORMED"
    sql = str(value.get("sql") or value.get("text") or "").strip()
    origin = str(value.get("origin") or "").upper()
    if not sql:
        return None, "REJECTED", "EMPTY"
    if origin != "AI_GENERATED":
        return None, "REJECTED", "ORIGIN_REQUIRED"
    result = {
        "sql": sql,
        "origin": "AI_GENERATED",
        "generation_basis": list(value.get("generation_basis") or []),
        "generation_basis_refs": list(value.get("generation_basis_refs") or []),
        "verification_status": str(value.get("verification_status") or "UNVERIFIED"),
    }
    if value.get("evidence_refs"):
        return None, "REJECTED", "OFFICIAL_REF_ON_GENERATED"
    return result, "ACCEPTED", "OK"


def _grounded(text: str, refs: list[str], source_blocks: Mapping[str, Mapping[str, Any]]) -> tuple[bool, str]:
    if not refs:
        return False, "MISSING_EVIDENCE_REF"
    normalized = comparable(text)
    if not normalized:
        return False, "EMPTY"
    for ref in refs:
        if ref not in source_blocks:
            return False, "INVALID_EVIDENCE_REF"
    for ref in refs:
        from agents.contracts.literal_source import verified_literal_text
        literal=verified_literal_text(source_blocks[ref])
        evidence_text = comparable(literal if literal is not None else source_blocks[ref].get("text"))
        if normalized in evidence_text:
            return True, "OK"
    return False, "UNGROUNDED"


def _bind(item,source_blocks,field,bundle,witnesses,proposal):
    refs=item['evidence_refs']
    if not refs:return False,'MISSING_EVIDENCE_REF'
    if any(ref not in source_blocks for ref in refs):return False,'INVALID_EVIDENCE_REF'
    if bundle is None:return _grounded(item.get('text') or item.get('sql'),refs,source_blocks)
    matches,reason=bind_claim(item.get('text') or item.get('sql'),refs,bundle,field,
        proposal if isinstance(proposal,Mapping) else None,fragments=witnesses)
    if not matches:return False,reason
    item['field_evidence']=matches
    item['fragment_refs']=[f['fragment_id'] for f in matches]
    item['source_locations']=[{'block_id':f['block_id'],'dom_locator':f['dom_locator'],
        'cell_index':f['cell_index'],'source_hash':f['source_hash']} for f in matches]
    return True,'OK'


def _refs(value: Mapping[str, Any]) -> list[str]:
    refs = value.get("evidence_refs") or value.get("evidence_refs_ids") or []
    return sorted({str(x) for x in refs if str(x).strip()}) if isinstance(refs, list) else []
