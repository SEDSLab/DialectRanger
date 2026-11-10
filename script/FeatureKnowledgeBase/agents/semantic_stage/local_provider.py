from __future__ import annotations
import json
import re
import hashlib
import threading
from typing import Any, Mapping

def _text(value: Any) -> str:
    return " ".join(str(value or "").replace("\u200b", "").split()).strip()


def _hash(value: Any) -> str:
    raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _payload_from_prompt(prompt: str, marker: str = "INPUT:") -> dict[str, Any]:
    text = str(prompt or "")
    start = text.find(marker)
    if start >= 0:
        text = text[start + len(marker):]
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _roles(block: Mapping[str, Any]) -> set[str]:
    result = {
        str(value).upper()
        for key in ("structural_roles", "occurrence_roles")
        for value in (block.get(key) or [])
    }
    for key in ("semantic_role", "evidence_role"):
        value = block.get(key)
        if value:
            result.add(str(value).upper())
    return result


def _heading(block: Mapping[str, Any]) -> str:
    return " ".join(str(value) for value in block.get("heading_ancestry") or []).casefold()


def _section_roles(block: Mapping[str, Any]) -> set[str]:
    result = set()
    for item in block.get("section_contexts") or []:
        if isinstance(item, Mapping):
            result.add(str(item.get("section_role") or "").upper())
    return result


def _block_kind(block: Mapping[str, Any]) -> str:
    return str(block.get("block_kind") or "").upper()


def _block_text(block: Mapping[str, Any]) -> str:
    return _text(block.get("text") or block.get("raw_text") or block.get("value") or "")


def _is_example(block: Mapping[str, Any]) -> bool:
    roles = _roles(block)
    sections = _section_roles(block)
    heading = _heading(block)
    text = _block_text(block)
    if "EXAMPLE" in sections or "OFFICIAL_EXAMPLE" in sections:
        return True
    if sections & {"DESCRIPTION", "SYNTAX"}:
        return False
    if roles & {"FORMAL_SYNTAX", "SYNTAX", "DESCRIPTION", "DEFINITION", "DETAIL_DESCRIPTION", "CATALOG_SUMMARY"}:
        return False
    if roles & {"EXAMPLE", "OFFICIAL_EXAMPLE"}:
        return True
    if any(word in heading for word in ("example", "usage", "sample", "query")):
        return _block_kind(block) in {"CODE", "TEXT", "LIST_ITEM", "TABLE_ROW"}
    return False


def _target_callable_surface(block: Mapping[str, Any], identity: str) -> bool:
    roles = _roles(block)
    sections = _section_roles(block)
    heading = _heading(block)
    text = _block_text(block)
    if "EXAMPLE" in sections or "EXAMPLE" in roles or any(word in heading for word in ("example", "usage", "sample")):
        return False
    if roles & {"FORMAL_SYNTAX", "FORMAL_VARIANT", "SYNTAX"} or "SYNTAX" in sections:
        return True
    if _block_kind(block) not in {"CODE", "TEXT", "TABLE_ROW", "LIST_ITEM"}:
        return False
    return bool(re.search(r"\b[A-Za-z_][A-Za-z0-9_$]*\s*\([^\n;]{0,300}\)", text) and
                (not identity or identity.casefold() in text.casefold()))


def _is_syntax(block: Mapping[str, Any], identity: str) -> bool:
    roles, sections = _roles(block), _section_roles(block)
    if _block_kind(block) == "HEADING" or sections & {"EXAMPLE", "DESCRIPTION"}:
        return False
    if "SYNTAX" in sections:
        return True
    if roles & {"FORMAL_SYNTAX", "SYNTAX"} and not roles & {"EXAMPLE", "OFFICIAL_EXAMPLE"}:
        return True
    if roles & {"EXAMPLE", "OFFICIAL_EXAMPLE", "DESCRIPTION", "DEFINITION", "DETAIL_DESCRIPTION", "CATALOG_SUMMARY"}:
        return False
    if roles & {"FORMAL_SYNTAX", "SYNTAX", "FORMAL_VARIANT"}:
        return True
    return _block_kind(block) in {"CODE", "SIGNATURE"} and _target_callable_surface(block, identity)


def _is_description(block: Mapping[str, Any]) -> bool:
    roles = _roles(block)
    heading = _heading(block)
    text = _block_text(block)
    if _block_kind(block) in {"HEADING", "CODE", "TABLE_ROW"}:
        return False
    sections = _section_roles(block)
    if sections & {"EXAMPLE", "SYNTAX"}:
        return False
    if "DESCRIPTION" in sections:
        return bool(text) and not text.rstrip().endswith(":")
    if roles & {"PARAMETER", "PARAMETER_INFO", "RETURN_TYPE", "TYPE_OR_RETURN_INFO", "FORMAL_SYNTAX", "FORMAL_VARIANT", "EXAMPLE"}:
        return False
    if roles & {"DEFINITION", "DESCRIPTION", "DETAIL_DESCRIPTION", "BEHAVIOR_OR_SEMANTICS", "CATALOG_SUMMARY"}:
        return len(text) >= 18 and not text.rstrip().endswith(":")
    if any(word in heading for word in ("syntax", "example", "usage", "argument", "parameter", "return", "note", "warning")):
        return False
    return _block_kind(block) in {"TEXT", "PARAGRAPH", "PROSE", "P", "LIST_ITEM"} and len(text) >= 28 and not text.rstrip().endswith(":")


def _is_parameter(block: Mapping[str, Any]) -> bool:
    roles = _roles(block)
    heading = _heading(block)
    return bool(roles & {"PARAMETER", "PARAMETER_INFO"} or any(word in heading for word in ("argument", "parameter", "operand")))


def _is_return(block: Mapping[str, Any]) -> bool:
    roles = _roles(block)
    heading = _heading(block)
    return bool(roles & {"RETURN_TYPE", "TYPE_OR_RETURN_INFO", "RETURN_VALUE"} or re.search(r"\breturn(?:s|ed|ing)?(?: value| type)?\b", heading, re.I))


def _explicit_return_type(text: str) -> str | None:
    """Extract only a type-shaped suffix already present in return evidence."""
    value = _text(text)
    match = re.search(r"([A-Z][A-Za-z0-9_*]*(?:\([^\n]*\))?)\s*$", value)
    if not match:
        return None
    token = match.group(1).strip()
    if token in {"A", "An", "The", "Returns", "Return"}:
        return None
    return token


def _is_note(block: Mapping[str, Any]) -> bool:
    roles = _roles(block)
    heading = _heading(block)
    return bool(roles & {"NOTE", "NOTE_OR_CAVEAT", "CAVEAT", "WARNING", "LIMITATION"} or
                any(word in heading for word in ("note", "warning", "important", "limitation", "caveat")))


def _ref(block: Mapping[str, Any]) -> str:
    return str(block.get("evidence_ref") or block.get("block_id") or "")


def _claim_text(block: Mapping[str, Any]) -> str:
    return _block_text(block)


def _legacy_semantic_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    blocks = [row for row in payload.get("evidence") or [] if isinstance(row, Mapping) and _ref(row) and _claim_text(row)]
    identity = str(payload.get("normalized_identity") or "")
    result: dict[str, Any] = {
        "identity_group_id": payload.get("identity_group_id"),
        "description": None,
        "formal_syntax": [],
        "parameters": [],
        "return_type": None,
        "notes": [],
        "official_examples": [],
        "generated_examples": [],
    }

    descriptions = sorted([block for block in blocks if _is_description(block)],
                          key=lambda b: (str(b.get("source_url") or ""), b.get("source_order", 0)))
    if descriptions:
        unique = { }
        for block in descriptions:
            unique.setdefault(_ref(block), block)
        result["description"] = [{"text": _claim_text(b), "evidence_refs": [_ref(b)]} for b in unique.values()]

    syntax = [block for block in blocks if _is_syntax(block, identity)]
    seen: set[str] = set()
    for block in syntax:
        ref = _ref(block)
        value = _claim_text(block)
        if ref not in seen and value:
            seen.add(ref)
            result["formal_syntax"].append({"text": value, "evidence_refs": [ref]})

    parameter_blocks = [block for block in blocks if _is_parameter(block)]
    for block in parameter_blocks:
        ref = _ref(block)
        value = _claim_text(block)
        if not value:
            continue
        match = re.match(r"^\s*(.+?)\s+(?:—|–|-|:)\s+(.+?)\s*$", value)
        if match:
            result["parameters"].append({"name": match.group(1).strip(), "description": match.group(2).strip(), "evidence_refs": [ref]})
        else:
            result["parameters"].append({"name": value, "evidence_refs": [ref]})

    returns = [block for block in blocks if _is_return(block) and not _is_example(block)]
    if returns:
        typed_returns = [
            (block, _explicit_return_type(_claim_text(block)))
            for block in returns
        ]
        typed_returns = [(block, value) for block, value in typed_returns if value]
        if typed_returns:
            chosen, value = max(typed_returns, key=lambda pair: len(pair[1]))
            result["return_type"] = {"text": value, "evidence_refs": [_ref(chosen)]}

    notes = [block for block in blocks if _is_note(block)]
    for block in notes:
        result["notes"].append({"text": _claim_text(block), "evidence_refs": [_ref(block)]})

    examples = [block for block in blocks if _is_example(block)]
    seen.clear()
    for block in examples:
        ref = _ref(block)
        value = _claim_text(block)
        if ref in seen or not value:
            continue
        seen.add(ref)
        result["official_examples"].append({"sql": value, "evidence_refs": [ref], "source_url": block.get("source_url")})
    return result


def _semantic_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    # Preserve the legacy input contract; new production inputs carry the
    # explicit Phase2 observable-structure contract. Target logic is unchanged.
    if (payload.get('structure_contract') or {}).get('version') == 'observable-evidence/v1' or any(
        'observed_context' in b or b.get('table_context') for b in payload.get('evidence') or []
    ):
        from agents.semantic_stage.structured_semantics import extract_structured_semantics
        return extract_structured_semantics(payload)
    return _legacy_semantic_from_payload(payload)


def _target_decision(payload: Mapping[str, Any]) -> tuple[str, str]:
    evidence = [row for row in payload.get("evidence") or [] if isinstance(row, Mapping)]
    if not evidence:
        return "NON_TARGET", "no owner-local target evidence"
    previous = str(payload.get("previous_type") or "UNCERTAIN").upper()
    identity = str(payload.get("identity") or "")
    from agents.owner.definition_patterns import verified_pattern
    proven={parsed['feature_type'] for row in evidence for proof in row.get('definition_section_proofs') or []
            for parsed in [verified_pattern(proof,row,identity.upper())] if parsed}
    if len(proven)==1:
        kind=next(iter(proven))
        if previous in {kind,'UNCERTAIN'}:return kind,'source-verified local definition grammar'
        return 'UNRESOLVED','definition grammar conflicts with upstream type'
    identity_surface = bool(identity) and any(
        identity.casefold() in (_claim_text(row) + " " + _heading(row)).casefold()
        for row in evidence
    )
    feature_surface = any(
        _roles(row) & {"IDENTITY", "FEATURE_NAME", "DEFINING_IDENTITY", "FORMAL_SYNTAX", "FORMAL_VARIANT", "SIGNATURE"}
        for row in evidence
    )
    if not feature_surface and not identity_surface:
        return "NON_TARGET", "owner-local evidence has no defining feature surface"
    if previous in {"FUNCTION", "OPERATOR", "DATATYPE"}:
        return previous, "preserve owner-local upstream target type; evidence-bound target confirmation"
    texts = " ".join(_claim_text(row) for row in evidence)
    headings = " ".join(_heading(row) for row in evidence)
    callable_surface = any(_target_callable_surface(row, str(payload.get("identity") or "")) for row in evidence)
    if callable_surface:
        return "FUNCTION", "owner-local callable signature surface"
    if re.search(r"\b(?:data[- ]?type|datatype|declared type|type declaration|column type|type constructor)\b", headings + " " + texts, re.I):
        return "DATATYPE", "owner-local declaration/type surface"
    if re.search(r"(?:operator|predicate|precedence|infix|postfix|comparison|logical)\b", headings + " " + texts, re.I) and re.search(r"[!<>=~%&|^+*/-]{1,3}|\b(?:AND|OR|IN|LIKE|IS|BETWEEN)\b", texts, re.I):
        return "OPERATOR", "owner-local operator grammar surface"
    return "UNRESOLVED", "evidence is insufficient for a target type decision"


class DeterministicEvidenceClient:
    """Shared local evidence-bound provider; no network or model calls.

    It never accesses the network or model knowledge.  Its output is an
    extractive deterministic review of the JSON evidence included in the
    production prompt, while keeping the provider contract exercised.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def call(self, prompt: str, feature_id: str) -> dict[str, Any]:
        is_target = str(prompt).startswith("Classify only whether")
        payload = _payload_from_prompt(prompt, "" if is_target else "INPUT:")
        if is_target:
            if not payload:
                payload = _payload_from_prompt(prompt, "evidence.")
            decision, reason = _target_decision(payload)
            parsed = {"decision": decision, "reason": reason}
            refs = [str(row.get("evidence_ref") or "") for row in payload.get("evidence") or [] if row.get("evidence_ref")]
            stage = "target_classification"
        else:
            parsed = _semantic_from_payload(payload)
            refs = [str(row.get("evidence_ref") or "") for row in payload.get("evidence") or [] if row.get("evidence_ref")]
            stage = "semantic_extraction"
            reason = "all emitted fields copied from supplied owner-local evidence"
        raw = json.dumps(parsed, ensure_ascii=False, sort_keys=True)
        with self._lock:
            self.calls.append({
                "provider": "deterministic",
                "mode": "LOCAL_DETERMINISTIC_EXTRACTIVE_ADAPTER",
                "stage": stage,
                "identity_unit_id": feature_id,
                "decision": parsed.get("decision") if is_target else "SEMANTIC_REVIEW",
                "reason": reason,
                "evidence_block_ids": sorted(set(refs)),
                "input_hash": _hash(payload),
                "response_hash": _hash(raw),
            })
        return {"_status": "ok", "_raw_response": raw, "_error": None}


