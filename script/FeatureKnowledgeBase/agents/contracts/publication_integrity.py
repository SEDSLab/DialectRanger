"""Independent subject/type eligibility. Never infer identity from Semantic fields.

This is an additive release contract, not a discovery, grouping or type algorithm.
Evidence references are resolved to their underlying owner-local source blocks;
relabeling a previous vote does not create independent evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping
from agents.contracts.identity_key import identity_text,normalize_identity


def _norm(value):
    return identity_text(str(value or '').replace('\u200b',''))


def _reference_set(value):
    # Target proposals are untrusted parsed JSON, not source observations.
    if not isinstance(value, (list, tuple, set)) or any(not isinstance(v, str) for v in value):
        return set()
    return set(value)


def _category(labels):
    result = set()
    for label in labels:
        local = set()
        for kind, pattern in (
            ("FUNCTION", r"\b(?:functions?|routines?)\b"),
            ("OPERATOR", r"\boperators?\b"),
            ("DATATYPE", r"\b(?:data[ -]?types?|type declarations?|storage classes?)\b"),
        ):
            if re.search(pattern, str(label), re.I):
                local.add(kind)
        if len(local) == 1:
            result = local
    return result


def _call_head(value):
    from agents.contracts.definition_entry import CALLABLE_HEAD
    match=CALLABLE_HEAD.match(re.sub(r'^\s*Syntax\s*:\s*','',str(value),flags=re.I))
    return normalize_identity(match.group('name')) if match else None


def _term_names(value):
    """Whole native definition-term signatures, not callable mentions in prose."""
    text = str(value or '')
    names = re.findall(r'\b([A-Za-z_][\w$]*)\s*\([^()]*\)', text)
    remainder = re.sub(r'\b[A-Za-z_][\w$]*\s*\([^()]*\)', '', text)
    remainder = re.sub(r'\b(?:and|or)\b|[,;|/\s]', '', remainder, flags=re.I)
    return {_norm(n) for n in names} if names and not remainder else set()


def _formal_alternative(value, name):
    """Exact declaration head/alternative in a proven formal grammar block.

    This must never be applied to arbitrary prose or example invocations.
    """
    return bool(re.search(r"(?:^|[|{])\s*" + re.escape(name) + r"\s*(?=\(|[|}]|$)", _norm(value)))


def _operator_head(value):
    # Operator-position recognition, not a list of permissible Feature names.
    # A complete symbolic catalog cell is valid regardless of its characters;
    # brackets inside another feature's grammar are not a complete head.
    if re.fullmatch(r"[^\w\s]+", str(value).strip()):
        return _norm(value)
    text = re.sub(r"^\s*[a-z][a-z_0-9]*(?:\s+|$)", "", str(value)).strip()
    match = re.match(r"^([^\w\s'\"(),\[\]{}]+)(?=\s|[a-z]|$)", text)
    if match:
        return _norm(match.group(1))
    match = re.match(r"^([A-Z][A-Z_]*(?:\s+[A-Z][A-Z_]*)*)(?=\s|\(|$)", text)
    return _norm(match.group(1)) if match else None


def _non_definition(block):
    if block.get('evidence_usage')=='DISCOVERY_ONLY':return True
    observed = block.get("observed_context") or {}
    labels = [observed.get("visible_label") or ""]
    labels += [r.get("text", "") for r in observed.get("labels", [])]
    return bool(block.get("noise_excluded_from_main") or any(
        re.search(r"\b(?:examples?|results?|output|navigation|contents)\b", str(t), re.I)
        for t in labels
    ))


def subject_witnesses(unit, bundle, *, source_identity_valid=True):
    """Return positive defining observations and material incompatible subjects.

    Existing structural roles/defining booleans alone are not truth. Check full
    heading/cell/term and active owner membership against observed source data.
    """
    name = _norm(unit.get("canonical_identity"))
    uid = str(unit.get("identity_unit_id") or "")
    owners = set(unit.get("owner_ids") or [])
    blocks = {b["block_id"]: b for b in bundle.get("source_blocks", []) if b.get("block_id")}
    witnesses = []
    conflicts = []

    def add(block, kind, surface, origin, locator=None, refs=None):
        witnesses.append({"block_id": block["block_id"], "evidence_refs": refs or [block["block_id"]],
            "owner_ids": sorted(owners.intersection(block.get("owner_ids") or [])),
            "feature_type": kind, "surface": surface, "origin": origin,
            "dom_locator": locator or block.get("dom_path"), "source_url": block.get("source_url"),
            "source_hash": block.get("source_hash"), "identity_unit_id": uid})
        witnesses[-1]['definition_targets']=list(block.get('definition_targets') or [])

    for block in blocks.values():
        if not owners.intersection(block.get("owner_ids") or []) or _non_definition(block):
            continue
        observed=block.get('observed_context') or {}
        local=observed.get('local_definition') or {}
        if (str(unit.get('feature_type') or '').upper()=='DATATYPE'
            and any(name in {_norm(v) for v in p.get('identity_values',[])} for p in block.get('definition_section_proofs',[]))
            and re.search(r'\b(?:does\s+not\s+(?:have|support|provide)|has\s+no)\s+'
                r'(?:(?:a|an|separate|native|built-in)\s+)*'+re.escape(name)+r'\s+(?:data\s*type|type|storage\s+class)\b',str(block.get('text') or ''),re.I)):
            conflicts.append({'block_id':block['block_id'],'reason':'NATIVE_TYPE_EXPLICITLY_NEGATED','defined_identity':name})
        if observed.get('feature_domain')=='EMBEDDED_QUERY_LANGUAGE' or local.get('role')=='DEVELOPER_API_DECLARATION':
            continue
        bid = block["block_id"]
        for proof in block.get("definition_section_proofs") or []:
            heading = blocks.get(proof.get("heading_block_id"))
            own_anchors=(proof.get('identity_anchor_block_ids') or {}).get(unit.get('canonical_identity'),[])
            if heading and bid!=heading.get('block_id') and bid in own_anchors:
                from agents.owner.definition_patterns import verified_pattern
                declared=verified_pattern(proof,block,name)
                if declared:
                    add(block,declared['feature_type'],declared.get('formal_value') or block.get('text',''),'PHYSICAL_CO_DECLARATION')
                    continue
            if not heading or bid != heading.get("block_id"):
                continue
            if proof.get("identity_unit_id") not in (None, uid):
                conflicts.append({"block_id": bid, "reason": "FOREIGN_DEFINITION_UNIT"})
                continue
            values = {_norm(v) for v in proof.get("identity_values", [])}
            from agents.owner.definition_patterns import verified_pattern
            structural=verified_pattern(proof,heading,name)
            if structural:
                add(heading,structural['feature_type'],heading.get('text',''),'DEFINITION_SECTION')
                continue
            from agents.owner.definition_scope import normalized_name
            from agents.owner.surface_patterns import operator_label, datatype_definition_name
            op=operator_label(heading.get('text'))
            typed_sentence=(proof.get('definition_pattern')=='DATATYPE_SELF_DEFINITION'
                            and datatype_definition_name(heading.get('text'))==name)
            operator_definition=(proof.get('definition_pattern')=='OPERATOR_LED_LIST_ITEM' and op and _norm(op[0])==name)
            exact = (_norm(heading.get("text")) == name or _call_head(heading.get("text", "")) == name
                     or typed_sentence or operator_definition
                     or ((heading.get('observed_context') or {}).get('local_definition',{}).get('role')=='STORAGE_CLASS_DEFINITION'
                         and _norm((heading.get('observed_context') or {})['local_definition'].get('identity'))==name)
                     or (heading.get('block_kind') == 'HEADING' and normalized_name(heading.get('text')) == name)
                     or ((heading.get('observed_context') or {}).get('raw_node_kind') == 'DT' and name in _term_names(heading.get('text'))))
            alias_refs = (proof.get("identity_anchor_block_ids") or {}).get(unit.get("canonical_identity"), [])
            alias = name in values and any(
                not _non_definition(blocks.get(ref, {})) and (
                    _norm(blocks.get(ref, {}).get("text")) == name
                    or _formal_alternative(blocks.get(ref, {}).get("text", ""), name)
                    or verified_pattern(proof,blocks.get(ref,{}),name))
                for ref in alias_refs)
            if not exact and not alias:
                conflicts.append({"block_id": bid, "reason": "FOREIGN_DEFINITION_SUBJECT",
                                  "defined_identity": proof.get("primary_identity")})
                continue
            kinds = _category([*(heading.get("heading_ancestry") or []), heading.get('text','') if heading.get('block_kind') == 'HEADING' else ''])
            if typed_sentence and re.search(r'\btypes?\b',' / '.join(heading.get('heading_ancestry') or []),re.I):
                kinds={'DATATYPE'}
            if operator_definition and re.search(r'\boperators?\b',' / '.join(heading.get('heading_ancestry') or []),re.I):
                kinds={'OPERATOR'}
            refs = [bid] + list(alias_refs if alias else [])
            from agents.owner.typed_page import nominal_heading
            if (heading.get('block_kind')=='HEADING' and not nominal_heading(heading)
                    and not typed_sentence and not operator_definition):
                # Category ancestry describes the document, not a declaration
                # of every prose subheading. Actual signatures can still prove
                # a callable below, and verified structural proofs handled above.
                kinds=set()
            if not kinds:
                # A formal signature in this same proven section can support a
                # callable; arbitrary example CODE outside the proof cannot.
                for ref in proof.get("definition_anchor_block_ids") or []:
                    candidate = blocks.get(ref, {})
                    if not _non_definition(candidate) and _call_head(candidate.get("text", "")) == name:
                        kinds.add("FUNCTION")
                        refs.append(ref)
            for kind in kinds:
                add(heading, kind, heading.get("text", ""), "DEFINITION_SECTION", refs=sorted(set(refs)))

        # Subsections (e.g. numeric/date overloads) may have no complete global
        # section proof. An observed formal label + exact local defining heading
        # + callable head is still positive subject evidence, not a prose match.
        observed = block.get("observed_context") or {}
        local = observed.get('local_definition') or {}
        role = local.get('role')
        if block.get('source_structure_status') == 'RESOLVED':
            from agents.contracts.catalog_membership import catalog_citation_valid
            cited_declaration=source_identity_valid and catalog_citation_valid(unit,block)
            if role=='DECLARATION_CATALOG' and local.get('domain')!='EMBEDDED_QUERY_LANGUAGE' and cited_declaration:
                for member in local.get('members',[]):
                    if _norm(member['identity'])==name and source_identity_valid:
                        add(block,member['feature_type'],member['raw_entry'],'OBSERVED_DECLARATION_CELL')
            elif role=='OPERATOR_CATALOG' and local.get('domain')!='EMBEDDED_QUERY_LANGUAGE' and cited_declaration:
                for member in local.get('members',[]):
                    if _norm(member['identity'])==name:
                        add(block,'OPERATOR',member['identity'],'OBSERVED_OPERATOR_CATALOG_MEMBER')
            elif role in {'STORAGE_CLASS_DEFINITION','DATATYPE_DEFINITION_HEADING'} and _norm(local.get('identity'))==name:
                add(block,'DATATYPE',block.get('text',''),'OBSERVED_LOCAL_DATATYPE_DEFINITION')
            if role == 'PAGE_FUNCTION_DEFINITION' and observed.get('raw_node_kind') == 'P' and _norm(local.get('identity')) == name:
                add(block,'FUNCTION',block.get('text',''),'OBSERVED_PAGE_SELF_DEFINITION')
            elif role == 'FUNCTION_DECLARATION_LIST' and observed.get('raw_node_kind') == 'LI' and _call_head(local.get('item_text')) == name:
                add(block,'FUNCTION',local['item_text'],'OBSERVED_LOCAL_FUNCTION_DECLARATION')
            elif role == 'DATATYPE_DECLARATION_LIST' and observed.get('raw_node_kind') == 'LI' and _norm(local.get('item_text')) == name:
                add(block,'DATATYPE',local['item_text'],'OBSERVED_LOCAL_DATATYPE_DECLARATION')
        local_headings = observed.get("heading_path") or block.get("heading_ancestry") or []
        syntax_labels = [label for label in observed.get("labels") or []
                         if re.fullmatch(r"(?:formal\s+)?syntax\s*:?", str(label.get("text") or ""), re.I)]
        if (syntax_labels and _call_head(block.get("text", "")) == name
                and any(_norm(label) == name for label in local_headings)
                and _category(local_headings) == {"FUNCTION"}):
            add(block, "FUNCTION", block.get("text", ""), "LABELED_LOCAL_DECLARATION")

        context = block.get("table_context") or {}
        from agents.contracts.catalog_membership import catalog_membership, catalog_citation_valid
        for proof in catalog_membership(block, name) if source_identity_valid and catalog_citation_valid(unit, block) else []:
            add(block, proof['feature_type'], proof['surface'],
                'VERIFIED_CATALOG_MEMBERSHIP', proof['dom_locator'])
            witnesses[-1]['catalog_proof'] = proof
            witnesses[-1]['definition_targets'] = proof['definition_targets']
        for cell in context.get("cells") or []:
            if cell.get("header_role") == "COLUMN_HEADER":
                continue
            headers = " / ".join(h.get("text", "") for h in cell.get("headers", []))
            text = str(cell.get("text") or "")
            kind = None
            from agents.contracts.type_slots import datatype_usage_slot
            if re.search(r"\boperator\b", headers, re.I) and _operator_head(text) == name:
                kind = "OPERATOR"
            elif not datatype_usage_slot(block,name) and re.fullmatch(r"\s*(?:SQL\s+)?(?:data\s*)?type(?:\s+name)?\s*", headers, re.I) and (
                _norm(text) == name or (_norm(text).startswith(name + "(") and re.fullmatch(r"[A-Z][A-Z_ 0-9]*", name))):
                kind = "DATATYPE"
            elif re.search(r"\b(?:function|signature|routine)\b", headers, re.I) and (
                _call_head(text) == name or _norm(text) == name):
                kind = "FUNCTION"
            if kind:
                add(block, kind, text, "TYPED_CATALOG_CELL", cell.get("dom_locator"))
        observed = block.get("observed_context") or {}
        if observed.get("raw_node_kind") == "DT" and (_norm(block.get("text")) == name or name in _term_names(block.get('text'))):
            for kind in _category(block.get("heading_ancestry") or []):
                add(block, kind, block.get("text", ""), "DEFINITION_TERM")

    # A known defining occurrence in a different proven section is not made
    # compatible merely by merging its string with a real definition elsewhere.
    for source in unit.get("evidence") or []:
        for occurrence in source.get("occurrence_records") or []:
            block = blocks.get(occurrence.get("source_block_id"))
            if not block or not occurrence.get("defining") or _non_definition(block):
                continue
            for proof in block.get("definition_section_proofs") or []:
                values = {_norm(v) for v in proof.get("identity_values") or []}
                if values and name not in values and _norm(proof.get("primary_identity")) != name:
                    conflicts.append({"block_id": block["block_id"], "reason": "CROSS_SUBJECT_DEFINING_OCCURRENCE",
                                      "defined_identity": proof.get("primary_identity"), "owner_id": source.get("owner_id")})
    unique = {json.dumps(w, sort_keys=True): w for w in witnesses}
    return list(unique.values()), list({json.dumps(c, sort_keys=True): c for c in conflicts}.values())


def evaluate_publication_integrity(unit: Mapping[str, Any], bundle: Mapping[str, Any],
                                   type_decision: Mapping[str, Any] | None,
                                   target: Mapping[str, Any] | None = None,
                                   typed_evidence=(), source_identity_valid=True):
    """Evaluate release state from actual source proof, never Semantic richness."""
    from agents.contracts.profile_authority import verified_authority
    authority = verified_authority(unit, bundle)
    if authority:
        kind = authority['feature_type']
        reasons = [] if source_identity_valid else ['SOURCE_IDENTITY_INVALID']
        if str(unit.get('feature_type') or 'UNCERTAIN').upper() != kind:
            reasons.append('UNIT_TYPE_AUTHORITY_MISMATCH')
        result = {
            'contract_version': 'PublicationIntegrity/v1',
            'identity_unit_id': unit.get('identity_unit_id'),
            'source_identity_valid': bool(source_identity_valid),
            'state': 'REVIEW' if reasons else 'CONFIRMED', 'reason_codes': reasons,
            'subject_state': 'DEFINED', 'subject_conflicts': [],
            'subject_witnesses': [dict(authority, origin='VERIFIED_EXPLICIT_PROFILE')],
            'type_hypothesis': kind, 'source_type_status': 'CONFIRMED' if kind != 'UNCERTAIN' else 'UNCERTAIN',
            'independent_resolution_verified': False,
            'explicit_profile_authority': authority,
        }
        result['decision_fingerprint'] = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return result
    decision = type_decision or {}
    target = target or {}
    hypothesis = str(decision.get("primary_type") or unit.get("feature_type") or "UNCERTAIN").upper()
    status = str(decision.get("type_status") or decision.get("status") or "NOT_EVALUATED").upper()
    witnesses, conflicts = subject_witnesses(unit, bundle, source_identity_valid=source_identity_valid)
    matched = [w for w in witnesses if w["feature_type"] == hypothesis]
    kinds = {w["feature_type"] for w in witnesses}
    reasons = []
    if unit.get('profile_definition_required'):
        reasons.append('OUTSIDE_CONFIGURED_DEFINITION')
    if not matched:
        reasons.append("INDEPENDENT_SUBJECT_TYPE_PROOF_MISSING")
    if kinds - {hypothesis}:
        reasons.append("DEFINING_TYPE_CONFLICT")
    if conflicts:
        reasons.append("SUBJECT_CONFLICT")
    unit_id = unit.get("identity_unit_id")
    votes = [r for r in typed_evidence if r.get("identity_unit_id") == unit_id]
    dependencies = sorted(set(decision.get("supporting_evidence_ids") or []) |
                          set(decision.get("conflicting_evidence_ids") or []) |
                          set(decision.get("type_evidence_ids") or []))
    dependent_blocks = {r.get("block_id") for r in votes if r.get("block_id")}
    # Missing underlying votes cannot prove independence just because their
    # references happen to have different strings.
    dependency_resolved = bool(votes) and not (set(dependencies) - {r.get("evidence_id") for r in votes})
    prior_text = [_norm(r.get("owner_text")) for r in votes if r.get("owner_text")]
    resolution = target.get("resolution_evidence") or {}
    requested_refs = _reference_set(resolution.get("evidence_refs")) if isinstance(resolution, Mapping) else set()
    independent = [w for w in matched if set(w["evidence_refs"]).issubset(requested_refs)
                   and not set(w["evidence_refs"]).intersection(dependent_blocks)
                   and not any(_norm(w["surface"]) in t for t in prior_text)]
    conflict_refs = set(decision.get("conflicting_evidence_ids") or []) | set(decision.get("reason_codes") or decision.get("conflict_flags") or [])
    resolved_conflicts = _reference_set(resolution.get("resolved_conflict_refs")) if isinstance(resolution, Mapping) else set()
    resolved = bool(status in {"REVIEW", "REVIEW_REQUIRED", "UNCERTAIN"} and dependency_resolved and independent
                    and resolution.get("feature_type") == hypothesis and conflict_refs.issubset(resolved_conflicts)
                    and not conflicts and not (kinds - {hypothesis}))
    if status != "CONFIRMED" and not resolved:
        reasons.append("TYPE_REVIEW_UNRESOLVED" if status != "NOT_EVALUATED" else "TYPE_DECISION_NOT_EVALUATED")
        if target.get("target_resolution") == "AI_CLASSIFIED":
            reasons.append("TARGET_HYPOTHESIS_IS_NOT_INDEPENDENT_EVIDENCE")
    if str(unit.get("feature_type") or "").upper() != hypothesis:
        reasons.append("UNIT_TYPE_DECISION_MISMATCH")
    if ((target.get("admission_status") and target.get("admission_status") != "ADMITTED")
            or target.get("publication_gate_allowed") is False
            or target.get("target_classification") in {"NON_FEATURE", "NON_TARGET"}):
        reasons.append("TARGET_NOT_CONFIRMED")
    result = {
        "contract_version": "PublicationIntegrity/v1", "identity_unit_id": unit_id,
        "source_identity_valid": bool(source_identity_valid),
        "candidate_ids": list(unit.get("candidate_ids") or []), "owner_ids": list(unit.get("owner_ids") or []),
        "state": "REVIEW" if reasons else "CONFIRMED", "reason_codes": sorted(set(reasons)),
        "subject_state": "CONFLICT" if conflicts else ("DEFINED" if matched else "UNRESOLVED"),
        "subject_witnesses": witnesses, "subject_conflicts": conflicts,
        "type_hypothesis": hypothesis,
        "type_decision_ref": {"identity_unit_id": unit_id, "provenance": decision.get("provenance"),
                              "sha256": hashlib.sha256(json.dumps(decision, sort_keys=True, ensure_ascii=False).encode()).hexdigest()},
        "source_type_status": status, "type_evidence_refs": dependencies,
        "type_dependency_block_ids": sorted(dependent_blocks), "type_dependencies_resolved": dependency_resolved,
        "target_hypothesis": target.get("feature_type"), "target_resolution": target.get("target_resolution"),
        "target_admission_status": target.get("admission_status"),
        "target_source_record_sha256": hashlib.sha256(json.dumps(target, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        "target_input_evidence_refs": sorted(_reference_set(target.get("input_evidence_refs"))),
        "target_evidence_refs": sorted(requested_refs),
        "independent_resolution_verified": resolved,
        "resolution_evidence_refs": sorted(set(ref for w in independent for ref in w["evidence_refs"])) if resolved else [],
        "grounding_establishes_subject": False,
    }
    result["decision_fingerprint"] = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return result


def publication_integrity_reasons(contract, identity_unit_id):
    """Fail closed for missing/malformed additive release decisions."""
    if not isinstance(contract, Mapping):
        return ["PUBLICATION_INTEGRITY_NOT_EVALUATED"]
    reasons = set(contract.get("reason_codes") or [])
    content = {k: v for k, v in contract.items() if k != "decision_fingerprint"}
    fingerprint = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if (contract.get("contract_version") != "PublicationIntegrity/v1"
            or contract.get("identity_unit_id") != identity_unit_id
            or contract.get("decision_fingerprint") != fingerprint):
        reasons.add("PUBLICATION_INTEGRITY_CONTRACT_INVALID")
    if (contract.get("state") != "CONFIRMED" or contract.get("subject_state") != "DEFINED"
            or contract.get("subject_conflicts") or not contract.get("subject_witnesses")
            or (contract.get("source_type_status") != "CONFIRMED" and not contract.get("independent_resolution_verified")
                 and not contract.get("explicit_profile_authority"))):
        reasons.add("PUBLICATION_INTEGRITY_UNRESOLVED")
    return sorted(reasons)
