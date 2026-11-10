"""Aggregate Identity Quality records into group-level evidence bundles."""
from __future__ import annotations

import re
import copy
from collections import defaultdict
from typing import Any, Mapping
from agents.semantic_stage.definition_section import project_definition_owner
from agents.owner.definition_scope import definition_scopes, navigation_block
from agents.semantic_stage.source_structure import SourceStructureIndex


ROLE_BUCKETS = {
    "DEFINING_IDENTITY": "defining_evidence",
    "FORMAL_VARIANT": "formal_variant_evidence",
    "EXAMPLE_TOKEN": "example_evidence",
    "MENTION": "mention_evidence",
}


def restore_source_lineage(bundles, documents):
    """Lossless old-bundle migration from matching frozen DocumentIR citations.

    Restores only transport metadata. No text, role, owner, claim or decision
    is inferred, and incompatible source identity/hash/locator is an error.
    """
    index={b['block_id']:(d,b) for d in documents for b in d.get('blocks',[])}
    restored=0
    for bundle in bundles:
        for citation in bundle.get('source_blocks',[]):
            if citation.get('provenance'):continue
            found=index.get(citation.get('block_id'))
            if not found:continue
            document,block=found
            if (citation.get('source_hash')!=document.get('page_hash')
                    or citation.get('source_url')!=document.get('url')
                    or citation.get('dom_path')!=block.get('dom_path')):
                raise ValueError('EVIDENCE_SOURCE_LINEAGE_MISMATCH')
            citation['provenance']=copy.deepcopy(block.get('provenance') or {})
            restored+=1
    return restored


_DESCRIPTION_HEADER_RE = re.compile(r"\b(?:description|definition|meaning|details?|reads\s+as|result)\b", re.I)
_SYNTAX_HEADER_RE = re.compile(r"\b(?:syntax|signature|grammar|form|forms|invocation)\b", re.I)
_PARAMETER_HEADER_RE = re.compile(r"\b(?:parameter|parameters|argument|arguments|operand|operands)\b", re.I)
_RETURN_HEADER_RE = re.compile(r"\b(?:return|returns|result|output|type)\b", re.I)
_EXAMPLE_HEADER_RE = re.compile(r"\b(?:example|examples|sample|samples|usage|use\s+case)\b", re.I)
_SECTION_LABELS = {
    "syntax": "SYNTAX",
    "signature": "SYNTAX",
    "grammar": "SYNTAX",
    "form": "SYNTAX",
    "forms": "SYNTAX",
    "invocation": "SYNTAX",
    "parameter": "PARAMETER",
    "parameters": "PARAMETER",
    "argument": "PARAMETER",
    "arguments": "PARAMETER",
    "operand": "PARAMETER",
    "operands": "PARAMETER",
    "return": "RETURN_VALUE",
    "returns": "RETURN_VALUE",
    "return value": "RETURN_VALUE",
    "returned value": "RETURN_VALUE",
    "return type": "RETURN_VALUE",
    "result": "RETURN_VALUE",
    "output": "RETURN_VALUE",
    "type": "RETURN_VALUE",
    "description": "DESCRIPTION",
    "definition": "DESCRIPTION",
    "meaning": "DESCRIPTION",
    "details": "DESCRIPTION",
    "example": "EXAMPLE",
    "examples": "EXAMPLE",
    "sample": "EXAMPLE",
    "samples": "EXAMPLE",
    "usage": "EXAMPLE",
    "usage example": "EXAMPLE",
    "use case": "EXAMPLE",
    "note": "NOTE",
    "notes": "NOTE",
}


def _clean_section_label(value: Any) -> str:
    """Normalize a rendered section label without using a DB vocabulary."""
    text = str(value or "")
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    text = re.sub(r"\s+", " ", text).strip().strip(":").strip()
    return text


def _section_label_role(value: Any) -> tuple[str, str] | None:
    label = _clean_section_label(value)
    key = re.sub(r"[\s_/-]+", " ", label.casefold()).strip()
    role = _SECTION_LABELS.get(key)
    return (label, role) if role else None


def _is_intro_metadata(text: str) -> bool:
    return bool(re.match(r"^\s*(?:introduced|added|available|deprecated|removed|changed)\s+in\s*:", text, re.I))


def _owner_section_contexts(
    owner: Mapping[str, Any],
    blocks_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Propagate explicit semantic labels through one owner-local section.

    Some documentation renderers expose labels such as ``Syntax`` and
    ``Arguments`` as paragraph-like component nodes rather than headings.
    The label is therefore part of the owner-local sequence, not of the DOM
    heading ancestry.  This state machine is intentionally bounded by the
    selected Owner and cannot cross a sibling identity section.
"""
    member_ids = [
        str(value) for value in (owner.get("owner_scope_block_ids") or owner.get("member_block_ids") or [])
        if str(value) in blocks_by_id
    ]
    ordered = sorted((blocks_by_id[value] for value in member_ids), key=_block_order)
    contexts: dict[str, dict[str, Any]] = {}
    active_role: str | None = None
    active_label = ""
    seen_identity_heading = False
    for block in ordered:
        block_id = str(block.get("block_id") or "")
        if not block_id:
            continue
        kind = str(block.get("block_kind") or block.get("block_type") or "").upper()
        text = str(block.get("text") or block.get("raw_text") or "").strip()
        label = _section_label_role(text)
        if kind == "HEADING":
            # The heading itself belongs to the identity surface.  A selected
            # owner should never inherit a label from a prior owner section.
            active_role = None
            active_label = ""
            seen_identity_heading = True
        if label:
            active_label, active_role = label
            contexts[block_id] = {
                "section_label": active_label,
                "section_role": "",
                "section_label_node": True,
                "owner_id": owner.get("owner_id"),
            }
            continue

        section_role = active_role or ""
        section_label = active_label
        if not section_role and seen_identity_heading and kind == "CODE" and owner.get("owner_scope_mode") == "DEFINITION_SECTION_PROJECTION":
            section_role = "SYNTAX"
        if not section_role and seen_identity_heading and kind in {"TEXT", "PARAGRAPH", "LIST_ITEM"}:
            # The first prose after an identity heading is the natural
            # definition unless it is only version metadata.  Once selected,
            # consecutive prose remains in the same definition surface until
            # an explicit semantic label changes the state.
            if not _is_intro_metadata(text):
                active_role = "DESCRIPTION"
                active_label = ""
                section_role = active_role
                section_label = active_label
        contexts[block_id] = {
            "section_label": section_label,
            "section_role": section_role,
            "section_label_node": False,
            "owner_id": owner.get("owner_id"),
        }
    return contexts


def _section_contexts_for_block(
    block_id: str,
    owners: list[Mapping[str, Any]],
    blocks_by_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    for owner in owners:
        context = _owner_section_contexts(owner, blocks_by_id).get(block_id)
        if context:
            contexts.append(context)
    return contexts


def _block_order(block: Mapping[str, Any]) -> int:
    try:
        return int(block.get("order") or 0)
    except (TypeError, ValueError):
        return 0


def _scope_block_ids(
    owner: Mapping[str, Any],
    *,
    bound_block_ids: set[str],
    blocks_by_id: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    """Return the smallest generic structural block scope for one Owner."""
    members = [str(x) for x in (owner.get("owner_scope_block_ids") or owner.get("member_block_ids") or []) if str(x)]
    if owner.get("owner_scope_mode") == "DEFINITION_SECTION_PROJECTION" and owner.get("definition_section_proofs"):
        return set(members)
    if str(owner.get("owner_scope_locality") or "") != "BROAD" and len(members) <= 32:
        return set(members)
    anchors = {str(x) for x in (owner.get("owner_scope_anchor_block_ids") or []) if str(x)}
    targets = (bound_block_ids & set(members)) | anchors
    if not targets:
        return set()
    ordered = sorted((blocks_by_id[x] for x in members if x in blocks_by_id), key=_block_order)
    positions = {str(block.get("block_id") or ""): index for index, block in enumerate(ordered)}
    selected: set[str] = set(targets)
    for target in list(targets):
        index = positions.get(target)
        if index is None:
            continue
        # Keep a small local prose window for definition/return text, never an
        # entire page owner.
        for neighbour in ordered[max(0, index - 1): min(len(ordered), index + 3)]:
            selected.add(str(neighbour.get("block_id") or ""))
    return selected


def _structural_roles(
    block: Mapping[str, Any],
    occurrences: list[Mapping[str, Any]],
    *,
    section_contexts: list[Mapping[str, Any]] | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Infer structural evidence roles without promoting incidental examples."""
    kind = str(block.get("block_kind") or block.get("block_type") or "").upper()
    text = str(block.get("text") or block.get("raw_text") or "").strip()
    headers = " ".join(str(x) for x in _table_headers(block))
    headings = " ".join(str(x) for x in (block.get("heading_ancestry") or []))
    section_contexts = list(section_contexts or [])
    section_roles = {
        str(context.get("section_role") or "").upper()
        for context in section_contexts
        if not bool(context.get("section_label_node"))
    }
    section_labels = " ".join(
        str(context.get("section_label") or "")
        for context in section_contexts
        if context.get("section_label")
    )
    section_label_node = any(bool(context.get("section_label_node")) for context in section_contexts)
    role_names: set[str] = set()
    reasons: dict[str, str] = {}
    occurrence_roles = {str(x.get("occurrence_role") or "") for x in occurrences}
    if "DEFINING_IDENTITY" in occurrence_roles or kind in {"DEFINITION", "HEADING"}:
        role_names.add("IDENTITY")
        reasons["IDENTITY"] = "owner_local_identity_surface"
    if not section_label_node and ("SYNTAX" in section_roles or "FORMAL_VARIANT" in occurrence_roles or _SYNTAX_HEADER_RE.search(headers)):
        role_names.add("FORMAL_SYNTAX")
        reasons["FORMAL_SYNTAX"] = "owner_local_semantic_section_or_signature"
    if not section_label_node and ("PARAMETER" in section_roles or _PARAMETER_HEADER_RE.search(headers) or _PARAMETER_HEADER_RE.search(headings)):
        role_names.add("PARAMETER")
        reasons["PARAMETER"] = "owner_local_parameter_section_or_header"
    if not section_label_node and ("RETURN_VALUE" in section_roles or (_RETURN_HEADER_RE.search(headers) and not _EXAMPLE_HEADER_RE.search(headers))):
        role_names.add("RETURN_TYPE")
        reasons["RETURN_TYPE"] = "owner_local_return_section_or_header"
    if not section_label_node and ("DESCRIPTION" in section_roles or _DESCRIPTION_HEADER_RE.search(headers)):
        role_names.add("DEFINITION")
        reasons["DEFINITION"] = "owner_local_description_section_or_header"
    elif kind == "DEFINITION" and text and not _EXAMPLE_HEADER_RE.search(headings):
        role_names.add("DEFINITION")
        reasons["DEFINITION"] = "definition_structural_block"
    elif "DEFINING_IDENTITY" in occurrence_roles and kind in {"TEXT", "PARAGRAPH"} and re.search(
        r"\b(?:returns?|is|means|used\s+to|evaluates?|produces?)\b", text, re.I,
    ):
        role_names.add("DEFINITION")
        reasons["DEFINITION"] = "owner_local_definition_prose"

    # Official Example is deliberately stricter than EXAMPLE_TOKEN.  A code
    # block in a reference page is only an official example when its section,
    # table header, or explicit block role says so.  Incidental uses remain a
    # separate EXAMPLE_OCCURRENCE role for review.
    explicit_example = bool("EXAMPLE" in section_roles or _EXAMPLE_HEADER_RE.search(f"{headers} {headings} {section_labels}"))
    block_role = str(block.get("semantic_role") or block.get("structural_role") or "").upper()
    if not section_label_node and (explicit_example or block_role in {"EXAMPLE", "OFFICIAL_EXAMPLE"}):
        role_names.add("EXAMPLE")
        reasons["EXAMPLE"] = "owner_local_example_section_or_block_role"
    if not section_label_node and "NOTE" in section_roles:
        role_names.add("NOTE")
        reasons["NOTE"] = "owner_local_note_section"
    if "EXAMPLE" in section_roles or ("DESCRIPTION" in section_roles and kind in {"TEXT", "PARAGRAPH"}):
        role_names.discard("FORMAL_SYNTAX")
        reasons.pop("FORMAL_SYNTAX", None)
    if "EXAMPLE_TOKEN" in occurrence_roles:
        role_names.add("EXAMPLE_OCCURRENCE")
        reasons["EXAMPLE_OCCURRENCE"] = "incidental_identity_occurrence"
    if not role_names and text:
        role_names.add("SUPPORTING")
        reasons["SUPPORTING"] = "owner_local_supporting_block"
    return sorted(role_names), reasons


def build_indexes(
    groups: list[Mapping[str, Any]],
    occurrences: list[Mapping[str, Any]],
    owners: list[Mapping[str, Any]],
    documents: list[Mapping[str, Any]],
    *, profile=None,
) -> dict[str, dict[str, Any]]:
    blocks_by_id: dict[str, Mapping[str, Any]] = {}
    documents_by_block: dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
    for document in documents:
        for block in document.get("blocks") or []:
            block_id = str(block.get("block_id") or "")
            if block_id:
                blocks_by_id[block_id] = block
                documents_by_block[block_id] = (document, block)
    occurrences_by_block: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for occurrence in occurrences:
        block_id = str(occurrence.get("source_block_id") or "")
        if block_id:
            occurrences_by_block[block_id].append(occurrence)
    return {
        "groups": {str(x.get("identity_unit_id")): x for x in groups},
        "occurrences": {str(x.get("occurrence_id")): x for x in occurrences},
        "owners": {str(x.get("owner_id")): x for x in owners},
        "documents": {str(x.get("page_id")): x for x in documents},
        "blocks_by_id": blocks_by_id,
        "documents_by_block": documents_by_block,
        "occurrences_by_block": occurrences_by_block,
        "definition_scopes": {str(d.get('page_id')): definition_scopes(d,profile) for d in documents},
        "definition_profile": profile,
        "source_structure": SourceStructureIndex(),
    }


def build_evidence_bundle(
    group: Mapping[str, Any],
    *,
    indexes: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    occurrences_by_id = indexes["occurrences"]
    owners_by_id = indexes["owners"]
    documents_by_id = indexes["documents"]
    indexed_blocks_by_id = indexes.get("blocks_by_id") or {}
    indexed_documents_by_block = indexes.get("documents_by_block") or {}
    indexed_occurrences_by_block = indexes.get("occurrences_by_block") or {}
    group_id = str(group.get("identity_unit_id") or "")
    occurrence_ids = sorted({str(x) for x in (group.get("occurrence_ids") or [])})
    occurrence_id_set = set(occurrence_ids)
    occurrences = [
        occurrences_by_id[x] for x in occurrence_ids
        if x in occurrences_by_id and occurrences_by_id[x].get("owner_local_binding", True) is not False
    ]
    # Prefer the owner ids carried by bound occurrences.  Group-level owner
    # lists can contain complementary/broad hypotheses and are only a fallback
    # for legacy groups with no occurrence lineage.
    owner_ids = {str(x.get("owner_id")) for x in occurrences if x.get("owner_id")}
    if not owner_ids:
        owner_ids = set(str(x) for x in (group.get("owner_ids") or []))
    owners = [owners_by_id[x] for x in sorted(owner_ids) if x in owners_by_id]
    owners = [project_definition_owner(group, owner, documents_by_id.get(str(owner.get("page_id") or ""), {}),
              scopes=(indexes.get('definition_scopes') or {}).get(str(owner.get('page_id') or ''))) for owner in owners]
    excluded_owners = [o for o in owners if str(o.get('evidence_scope_status') or '').startswith('EXCLUDED_')]
    excluded_owner_ids = {str(o.get('owner_id')) for o in excluded_owners}
    owners = [o for o in owners if str(o.get('owner_id')) not in excluded_owner_ids]
    occurrences = [o for o in occurrences if str(o.get('owner_id')) not in excluded_owner_ids]

    block_owner_ids: dict[str, set[str]] = defaultdict(set)
    block_page_ids: dict[str, set[str]] = defaultdict(set)
    block_occurrences: dict[str, list[str]] = defaultdict(list)
    block_roles: dict[str, set[str]] = defaultdict(set)
    bound_owner_by_block: dict[str, set[str]] = defaultdict(set)
    wanted_block_ids: set[str] = set()
    bound_blocks_by_owner: dict[str, set[str]] = defaultdict(set)
    section_contexts_by_block: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for occurrence in occurrences:
        oid = str(occurrence.get("owner_id") or "")
        bid = str(occurrence.get("source_block_id") or "")
        if oid and bid:
            bound_blocks_by_owner[oid].add(bid)
    for owner in owners:
        oid = str(owner.get("owner_id") or "")
        pid = str(owner.get("page_id") or "")
        for block_id, context in _owner_section_contexts(owner, indexed_blocks_by_id).items():
            section_contexts_by_block[block_id].append(context)
        for block_id in _scope_block_ids(
            owner,
            bound_block_ids=bound_blocks_by_owner.get(oid, set()),
            blocks_by_id=indexed_blocks_by_id,
        ):
            bid = str(block_id)
            wanted_block_ids.add(bid)
            block_owner_ids[bid].add(oid)
            block_page_ids[bid].add(pid)
    for occurrence in occurrences:
        bid = str(occurrence.get("source_block_id") or "")
        if not bid:
            continue
        wanted_block_ids.add(bid)
        oid = str(occurrence.get("owner_id") or "")
        pid = str(occurrence.get("page_id") or "")
        if oid:
            block_owner_ids[bid].add(oid)
            bound_owner_by_block[bid].add(oid)
        if pid:
            block_page_ids[bid].add(pid)
        block_occurrences[bid].append(str(occurrence.get("occurrence_id") or ""))
        block_roles[bid].add(str(occurrence.get("occurrence_role") or "UNKNOWN"))
    for evidence in group.get("evidence") or []:
        if str(evidence.get('owner_id') or '') in excluded_owner_ids:
            continue
        for block_id in evidence.get("source_block_ids") or []:
            bid = str(block_id)
            # Legacy summaries may omit owner_id. They may supplement a
            # selected Owner's members, never introduce an unrelated subject.
            related = [o for o in owners if not evidence.get('owner_id') or str(o.get('owner_id')) == str(evidence['owner_id'])]
            if any(bid in (o.get('owner_scope_block_ids') or o.get('member_block_ids') or []) for o in related):
                wanted_block_ids.add(bid)

    from agents.semantic_stage.applicability import supplemental_operator_blocks
    supplemental=supplemental_operator_blocks(group,owners,documents_by_id,indexes['source_structure'],indexes.get('definition_profile'))
    # Supplement missing evidence only. Do not replace an already scoped
    # definition paragraph with a shorter explanation excerpt.
    supplemental={bid:p for bid,p in supplemental.items() if bid not in wanted_block_ids}
    for bid,proof in supplemental.items():
        wanted_block_ids.add(bid)
        block_owner_ids[bid].add(proof['owner_id']);block_page_ids[bid].add(proof['page_id'])

    from agents.semantic_stage.catalog_sources import bind_catalog_sources
    catalog_sources=bind_catalog_sources(group,owners,indexes)
    catalog_proofs={v['unit']['member_block_ids'][0]:v['unit']['catalog_reference_proof']for v in catalog_sources}
    for association in catalog_sources:
        unit=association['unit'];owner=association['owner'];bid=unit['member_block_ids'][0]
        wanted_block_ids.add(bid)
        block_owner_ids[bid].add(owner['owner_id']);block_page_ids[bid].add(unit['page_id'])
        if owner['owner_id'] not in {o['owner_id']for o in owners}:owners.append(owner)

    # Preserve valid original Owner discovery citations without widening the
    # semantic definition. These rows are lineage-only and not claimable.
    discovery_only_ids=set()
    citation_audit=[]
    for citation in group.get('adaptation_provenance') or []:
        oid=str(citation.get('source_owner_id') or citation.get('owner_id') or '')
        original=owners_by_id.get(oid,{})
        for bid in citation.get('source_block_ids') or [citation.get('block_id')]:
            bid=str(bid or '')
            reason=('FOREIGN_CITED_OWNER' if oid not in set(group.get('owner_ids') or []) else
                    'EXCLUDED_DEFINITION_OWNER' if oid in excluded_owner_ids else
                    'INVALID_ORIGINAL_CITATION' if bid not in original.get('member_block_ids',[]) or bid not in indexed_blocks_by_id else None)
            citation_audit.append({'owner_id':oid,'block_id':bid,'disposition':reason or ('DISCOVERY_ONLY' if bid not in wanted_block_ids else 'IN_DEFINITION_SCOPE')})
            if reason:continue
            if bid not in wanted_block_ids:discovery_only_ids.add(bid)
            wanted_block_ids.add(bid);block_owner_ids[bid].add(oid)
            block_page_ids[bid].add(str(original.get('page_id') or ''))

    # An occurrence or legacy nested evidence reference cannot bypass an
    # explicit navigation/foreign-owner exclusion established above.
    article_pages = {str(d.get('page_id')) for d in documents_by_id.values()
                     if any('article' in str(b.get('dom_path') or '').split(' > ') for b in d.get('blocks') or [])}
    navigation_ids = {bid for bid in wanted_block_ids if bid in indexed_blocks_by_id and navigation_block(
        indexed_blocks_by_id[bid], article_present=str((indexed_documents_by_block.get(bid) or ({}, {}))[0].get('page_id')) in article_pages)}
    wanted_block_ids -= navigation_ids

    documents_by_block = indexed_documents_by_block
    if not documents_by_block:
        documents_by_block = {}
        for document in documents_by_id.values():
            for block in document.get("blocks") or []:
                bid = str(block.get("block_id") or "")
                if bid:
                    documents_by_block[bid] = (document, block)

    # Expand only a source-verified compound entry, not every descendant of an
    # arbitrary owner. Keep actual IR IDs and expose unresolved child roles.
    from agents.contracts.definition_entry import compound_entry
    entry_children = {}
    entry_parents = {}
    entry_dispositions = []
    for row_id in list(wanted_block_ids - discovery_only_ids):
        pair = documents_by_block.get(row_id)
        if not pair or pair[1].get('block_kind') != 'TABLE_ROW':
            continue
        document, row = pair
        reader = indexes.get('source_structure')
        if not isinstance(reader, SourceStructureIndex):
            continue
        observed = reader.observe(document, row)
        entry = compound_entry({**row, **observed, 'source_url': document.get('url')},
                               group.get('canonical_identity'), str(group.get('feature_type') or '').upper())
        if not entry:
            continue
        entry_parents[row_id] = entry
        local = {b.get('dom_path'): b for b in document.get('blocks', [])}
        for fragment in entry['fragments']:
            child = local.get(fragment['dom_locator'])
            disposition = {'parent_block_id': row_id, 'dom_locator': fragment['dom_locator'],
                           'role': fragment['role'], 'status': 'UNRESOLVED'}
            if child and fragment['text'] == str(child.get('text') or '').strip():
                bid = child['block_id']
                wanted_block_ids.add(bid)
                block_owner_ids[bid].update(block_owner_ids[row_id])
                block_page_ids[bid].update(block_page_ids[row_id])
                entry_children[bid] = {**fragment, 'subject': entry['subject'],
                    'feature_type': entry['feature_type'], 'parent_block_id': row_id}
                disposition.update(block_id=bid, status='BOUND' if fragment['role'] != 'UNRESOLVED' else 'UNRESOLVED')
            else:
                start = str(row.get('text') or '').find(fragment['text'])
                if start >= 0:
                    entry.setdefault('parent_span_fragments', []).append(dict(fragment, span_start=start,
                        span_end=start+len(fragment['text'])))
                    disposition.update(status='BOUND_PARENT_SPAN',block_id=row_id,
                                       span_start=start,span_end=start+len(fragment['text']))
                else:
                    disposition['status'] = 'FROZEN_FRAGMENT_NOT_IN_DOCUMENT_IR'
            entry_dispositions.append(disposition)

    source_blocks: list[dict[str, Any]] = []
    for bid in sorted(wanted_block_ids, key=lambda value: (str((documents_by_block.get(value) or ({}, {}))[0].get("page_id", "")), _block_order((documents_by_block.get(value) or ({}, {}))[1]))):
        pair = documents_by_block.get(bid)
        if not pair:
            continue
        document, block = pair
        text = str(block.get("text") or block.get("raw_text") or "").strip()
        related_occurrences = [
            occurrence for occurrence in indexed_occurrences_by_block.get(bid, [])
            if str(occurrence.get("occurrence_id") or "") in occurrence_id_set
        ] if indexed_occurrences_by_block else [
            occurrence for occurrence in occurrences
            if str(occurrence.get("source_block_id") or "") == bid
        ]
        section_contexts = section_contexts_by_block.get(bid, [])
        structural_roles, structural_reasons = _structural_roles(
            block, related_occurrences, section_contexts=section_contexts,
        )
        item = {
            "evidence_ref": bid,
            "block_id": bid,
            "group_id": group_id,
            "text": text,
            "source_text": block.get('source_text', text),
            "normalized_text": block.get('normalized_text', text),
            "source_text_origin": block.get('source_text_origin'),
            "source_text_node_indexes": block.get('source_text_node_indexes'),
            "provenance": dict(block.get('provenance') or {}),
            "block_kind": str(block.get("block_kind") or block.get("block_type") or "UNKNOWN"),
            "heading_ancestry": list(block.get("heading_ancestry") or []),
            "section_contexts": section_contexts,
            "semantic_role": block.get("semantic_role"),
            "table_headers": _table_headers(block),
            "table_metadata": block.get('table_metadata'),
            "inline_nodes": list(block.get('inline_nodes') or []),
            "page_id": str(document.get("page_id") or ""),
            "source_url": str(document.get("url") or document.get("canonical_url") or ""),
            "owner_ids": sorted(bound_owner_by_block.get(bid) or block_owner_ids.get(bid, set())),
            "occurrence_ids": sorted(set(block_occurrences.get(bid, []))),
            "occurrence_roles": sorted(block_roles.get(bid, set())),
            "structural_roles": structural_roles,
            "structural_role_reasons": structural_reasons,
            "owner_local_binding": True,
            "owner_binding_reasons": sorted({
                str(occurrence.get("owner_binding_reason") or "")
                for occurrence in related_occurrences
                if occurrence.get("owner_binding_reason")
            }),
            "noise_excluded_from_main": is_noise_block(block),
            "source_order": _block_order(block),
            "dom_path": block.get("dom_path"),
            "section_id": block.get("section_id"),
            "definition_section_proofs": [proof for owner in owners for proof in owner.get("definition_section_proofs", []) if bid in proof["block_ids"]],
        }
        # Add facts without replacing legacy inferred roles or raw IR text.
        # Observation must not introduce blocks outside the existing scope.
        reader = indexes.get('source_structure')
        observations = reader.observe(document, block) if isinstance(reader, SourceStructureIndex) else {
            'source_structure_status': 'NOT_AVAILABLE',
            'observed_context': {'container_kind': 'UNKNOWN', 'visible_label': None,
                                 'label_status': 'UNKNOWN', 'labels': [],
                                 'heading_path': list(block.get('heading_ancestry') or [])},
            'table_context': None,
        }
        # Exact visible section labels already tracked by the bounded Owner
        # are observations, not new final semantic-role assignments.
        observations = copy.deepcopy(observations)
        for context in section_contexts:
            label = context.get('section_label')
            if label:
                label_blocks = [b for b in indexed_blocks_by_id.values()
                                if str(b.get('text') or '').strip().rstrip(':').strip() == label
                                and str(b.get('block_id')) in wanted_block_ids
                                and context.get('owner_id') in block_owner_ids.get(str(b.get('block_id')), set())
                                and _block_order(b) <= _block_order(block)]
                if label_blocks:
                    label_block = max(label_blocks, key=_block_order)
                    observations['observed_context']['labels'].append({
                        'text': label, 'origin': 'OWNER_LOCAL_VISIBLE_LABEL',
                        'block_id': label_block.get('block_id'), 'dom_locator': label_block.get('dom_path')})
                    if observations['observed_context'].get('visible_label') is None:
                        observations['observed_context'].update(visible_label=label, label_status='OBSERVED')
        item.update(observations)
        if bid in catalog_proofs:item['catalog_reference_proof']=catalog_proofs[bid]
        # Keep normalized matching text separately. The source reader has
        # already verified this exact node and page hash; executable literals
        # and physical cells must not inherit IR whitespace normalization.
        literal_key = {'CODE':'literal_code_text','TABLE_ROW':'literal_row_text'}.get(item.get('block_kind'))
        literal = item.get('observed_context',{}).get(literal_key) if literal_key else None
        if item.get('source_structure_status')=='RESOLVED' and isinstance(literal,str) and literal.strip():
            # Source citations were constructed from IR matching text. Keep
            # that structural contract immutable; consumers select raw values
            # through this separate content witness.
            item['literal_text']=literal.strip()
            item['source_text_origin']='HASH_VERIFIED_LITERAL_NODE'
        if bid in entry_parents:
            item['compound_definition_entry'] = entry_parents[bid]
        if bid in entry_children:
            item['definition_entry_member'] = entry_children[bid]
            role = entry_children[bid]['role']
            role_map = {'DESCRIPTION': 'DESCRIPTION', 'FORMAL_SYNTAX': 'FORMAL_SYNTAX', 'EXAMPLE': 'EXAMPLE'}
            if role in role_map:
                item['structural_roles'] = sorted(set(item['structural_roles']) | {role_map[role]})
        if bid in supplemental:
            item['content_applicability']=supplemental[bid]
            item['structural_roles']=sorted(set(item['structural_roles'])|{'DESCRIPTION'})
        # Source observations arrive after legacy role inference. Preserve an
        # explicit adjacent example introduction in the role contract as well
        # as the semantic input; a callable result is not formal grammar.
        if any(label.get('origin')=='ADJACENT_EXAMPLE_INTRODUCTION'
               for label in item.get('observed_context',{}).get('labels',[])):
            item['structural_roles']=sorted((set(item['structural_roles'])-{'FORMAL_SYNTAX'})|{'EXAMPLE'})
            item['structural_role_reasons'].pop('FORMAL_SYNTAX',None)
            item['structural_role_reasons']['EXAMPLE']='SOURCE_ADJACENT_EXAMPLE_INTRODUCTION'
        if bid in discovery_only_ids:
            item['evidence_usage']='DISCOVERY_ONLY'
            item['noise_excluded_from_main']=True
            item['structural_roles']=['SUPPORTING']
            item['definition_section_proofs']=[]
        if item.get('table_context'):
            item['table_context']['row_block_id'] = bid
        item['identity_unit_id'] = group_id
        item['contributing_owner_ids'] = sorted(block_owner_ids.get(bid, set()))
        item['definition_scope_status'] = 'COMPLETE' if item['definition_section_proofs'] else 'UNKNOWN'
        source_blocks.append(item)

    block_map = {x["block_id"]: x for x in source_blocks}
    structural_evidence: dict[str, list[dict[str, Any]]] = {
        "definition_evidence": [],
        "formal_syntax_evidence": [],
        "parameter_evidence": [],
        "return_type_evidence": [],
        "official_example_evidence": [],
    }
    role_to_bucket = {
        "DEFINITION": "definition_evidence",
        "FORMAL_SYNTAX": "formal_syntax_evidence",
        "PARAMETER": "parameter_evidence",
        "RETURN_TYPE": "return_type_evidence",
        "EXAMPLE": "official_example_evidence",
    }
    for block in source_blocks:
        for role in block.get("structural_roles") or []:
            bucket = role_to_bucket.get(str(role))
            if not bucket:
                continue
            structural_evidence[bucket].append({
                **block,
                "evidence_ref": block.get("evidence_ref"),
                "block_id": block.get("block_id"),
                "owner_ids": list(block.get("owner_ids") or []),
                "owner_local_binding": block.get("owner_local_binding", True),
                "raw_text": block.get("text") or "",
                "block_kind": block.get("block_kind"),
                "role": role,
                "reason": (block.get("structural_role_reasons") or {}).get(role, ""),
                "source_url": block.get("source_url"),
            })
    evidence_by_bucket: dict[str, list[dict[str, Any]]] = {
        "defining_evidence": [],
        "formal_variant_evidence": [],
        "mention_evidence": [],
        "example_evidence": [],
        "supporting_evidence": [],
    }
    for occurrence in sorted(occurrences, key=lambda x: str(x.get("occurrence_id") or "")):
        bid = str(occurrence.get("source_block_id") or "")
        if bid not in block_map:
            continue
        role = str(occurrence.get("occurrence_role") or "UNKNOWN")
        bucket = ROLE_BUCKETS.get(role, "supporting_evidence")
        evidence_by_bucket[bucket].append({
            "evidence_ref": bid,
            "occurrence_id": occurrence.get("occurrence_id"),
            "owner_id": occurrence.get("owner_id"),
            "source_url": occurrence.get("source_url"),
            "raw_identity": occurrence.get("raw_identity"),
            "raw_text": occurrence.get("raw_evidence"),
            "role": role,
        })

    main_evidence = _ranked_main_blocks(source_blocks, evidence_by_bucket)
    required_ids = {bid for o in owners for p in o.get('definition_section_proofs', []) for bid in p.get('block_ids', [])} | set(entry_children) | set(entry_parents)
    actual_ids = {b['block_id'] for b in source_blocks}
    missing_ids = sorted((required_ids | wanted_block_ids) - actual_ids)
    windowed_ids = sorted({str(bid) for o in owners for bid in (o.get('owner_scope_block_ids') or o.get('member_block_ids') or [])}
                          - actual_ids - navigation_ids)
    source_urls = sorted({
        str(x.get("source_url") or "") for x in source_blocks if str(x.get("source_url") or "")
    } | {str(x) for x in (group.get("source_urls") or []) if str(x).strip()})
    owner_records = []
    for owner in owners:
        document = documents_by_id.get(str(owner.get("page_id") or ""), {})
        owner_records.append({
            "owner_id": owner.get("owner_id"),
            "owner_kind": owner.get("owner_kind"),
            "template": owner.get("template"),
            "status": owner.get("status"),
            "confidence": owner.get("confidence"),
            "page_id": owner.get("page_id"),
            "source_url": document.get("url") or document.get("canonical_url") or owner.get("provenance", {}).get("source_url"),
            "root_block_id": owner.get("root_block_id"),
            "start_block_id": owner.get("start_block_id"),
            "end_block_id": owner.get("end_block_id"),
            "member_block_ids": list(owner.get("member_block_ids") or []),
            "original_member_block_ids": list(owner.get("original_member_block_ids") or owner.get("member_block_ids") or []),
            "definition_section_proofs": list(owner.get("definition_section_proofs") or []),
            "owner_scope_version": owner.get("owner_scope_version"),
            "owner_scope_identity_values": list(owner.get("owner_scope_identity_values") or []),
            "owner_scope_mode": owner.get("owner_scope_mode"),
            "owner_scope_locality": owner.get("owner_scope_locality"),
            "owner_scope_block_ids": list(owner.get("owner_scope_block_ids") or []),
            "owner_scope_anchor_block_ids": list(owner.get("owner_scope_anchor_block_ids") or []),
            "evidence_scope_status": owner.get('evidence_scope_status', 'UNKNOWN'),
        })

    from agents.semantic_stage.definition_sources import organize_definition_sources
    page_ids={o.get('page_id') for o in owners}
    definition_sources=organize_definition_sources(group,owners,
        [d for pid,d in documents_by_id.items() if pid in page_ids],source_blocks,
        profile=indexes.get('definition_profile'),reader=indexes.get('source_structure'),
        reference_documents=list(documents_by_id.values()),catalog_units=[v['unit']for v in catalog_sources])
    from agents.owner.composite_definition import verified_fields
    for unit in definition_sources['definition_units']:
        for member in verified_fields(unit, block_map, str(group.get('canonical_identity') or '')):
            role = member['field_role']
            canonical_role = {'DESCRIPTION':'DEFINITION','FORMAL_SYNTAX':'FORMAL_SYNTAX',
                              'EXAMPLE':'EXAMPLE','PARAMETER':'PARAMETER','RETURN_TYPE':'RETURN_TYPE'}.get(role)
            if not canonical_role:
                continue
            block = block_map[member['block_id']]
            block['structural_roles'] = [canonical_role]
            block['structural_role_reasons'] = {canonical_role:'VERIFIED_COMPOSITE_FIELD_CELL'}
            for bucket in structural_evidence:
                structural_evidence[bucket] = [b for b in structural_evidence[bucket] if b.get('block_id') != member['block_id']]
            structural_evidence[role_to_bucket[canonical_role]].append(dict(block,
                raw_text=block['text'], role=canonical_role, reason='VERIFIED_COMPOSITE_FIELD_CELL'))
    return {
        "schema_version": "identity-group-evidence-bundle/v1",
        **definition_sources,
        "structure_contract_version": "observable-evidence/v1",
        "adaptation_citation_audit": citation_audit,
        "compound_entry_dispositions": entry_dispositions,
        "identity_group_id": group_id,
        "normalized_identity": group.get("canonical_identity") or "",
        "feature_type": str(group.get("feature_type") or group.get("type_hint") or "UNCERTAIN").upper(),
        "group_status": group.get("group_status"),
        "defining_variants": list(group.get("defining_variants") or []),
        "other_variants": sorted(set(group.get("variants") or []) - set(group.get("defining_variants") or [])),
        **evidence_by_bucket,
        "owners": owner_records,
        "excluded_owners": [{'owner_id': o.get('owner_id'), 'reason': o.get('evidence_scope_status'),
                             'source_block_ids': list(o.get('member_block_ids') or [])} for o in excluded_owners],
        "source_urls": source_urls,
        "source_blocks": source_blocks,
        "main_evidence": main_evidence,
        **structural_evidence,
        "evidence_completeness": {
            "source_block_count": len(source_blocks),
            "bound_occurrence_count": len(occurrences),
            "available_roles": {
                key: len(value) for key, value in structural_evidence.items()
            },
            "example_occurrence_count": sum(
                1 for block in source_blocks if "EXAMPLE_OCCURRENCE" in (block.get("structural_roles") or [])
            ),
            "official_example_auto_promotion": False,
            "definition_scope_status": 'INCOMPLETE' if missing_ids or windowed_ids else 'COMPLETE' if required_ids else 'UNKNOWN',
            "required_definition_block_ids": sorted(required_ids),
            "missing_block_ids": missing_ids,
            "scope_window_omitted_block_ids": windowed_ids,
            "navigation_excluded_block_ids": sorted(navigation_ids),
            "definition_content_dispositions": [
                {"block_id":bid,"status":"BOUND" if bid in actual_ids else
                    "EXCLUDED_NAVIGATION" if bid in navigation_ids else "EVIDENCE_BINDING_GAP",
                 "reason":"SOURCE_VERIFIED_DEFINITION_MEMBER" if bid in actual_ids else
                    "NAVIGATION_OR_CHROME" if bid in navigation_ids else "REQUIRED_MEMBER_NOT_IN_BUNDLE"}
                for bid in sorted(required_ids)],
            "semantic_prompt_main_budget": 80,
            "semantic_prompt_main_overflow": max(0, len(main_evidence) - 80),
            "semantic_input_status": 'INCOMPLETE' if missing_ids or windowed_ids or len(main_evidence) > 80 else 'UNKNOWN_NOT_ROLE_VALIDATED',
        },
        "provenance": {
            "dbms": (group.get("provenance") or {}).get("dbms", "unknown"),
            "group_id": group_id,
            "candidate_ids": list(group.get("candidate_ids") or []),
            "occurrence_ids": occurrence_ids,
            "owner_ids": sorted(owner_ids),
            "source_page_ids": sorted({str(x.get("page_id") or "") for x in source_blocks if x.get("page_id")}),
            "producer_stage": "aggregate_owner_evidence",
            "producer_version": "semantic-stage/v1",
        },
    }


def is_noise_block(block: Mapping[str, Any]) -> bool:
    kind = str(block.get("block_kind") or block.get("block_type") or "").upper()
    text = str(block.get("text") or block.get("raw_text") or "").strip()
    heading = " ".join(str(x) for x in (block.get("heading_ancestry") or []))
    if kind in {"NAV", "NAVIGATION", "HEADER", "FOOTER", "ASIDE"}:
        return True
    if re.search(r"^\s*(?:\$|#|PS>|shell>|cmd>|bash>)\s*", text, re.I):
        return True
    if re.search(r"\b(?:pip|npm|apt(?:-get)?|brew)\s+install\b", text, re.I):
        return True
    if re.search(r"\b(?:table of contents|on this page|breadcrumb|previous|next)\b", heading, re.I):
        return True
    return False


def _ranked_main_blocks(source_blocks: list[dict[str, Any]], buckets: Mapping[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    priority = {"DEFINING_IDENTITY": 0, "FORMAL_VARIANT": 1, "FEATURE_DEFINITION": 2,
                "FEATURE_CATALOG": 3, "MENTION": 4, "EXAMPLE_TOKEN": 5, "UNKNOWN": 6}
    role_rank: dict[str, int] = {}
    for bucket, items in buckets.items():
        for item in items:
            role_rank[item["evidence_ref"]] = min(role_rank.get(item["evidence_ref"], 99), {
                "defining_evidence": 0, "formal_variant_evidence": 1,
                "mention_evidence": 4, "example_evidence": 5,
                "supporting_evidence": 6,
            }.get(bucket, 6))
    result = []
    for block in source_blocks:
        if block.get("noise_excluded_from_main"):
            continue
        rank = role_rank.get(block["block_id"], 6)
        if "FEATURE_DEFINITION" in block.get("occurrence_roles", []):
            rank = min(rank, 2)
        if "FEATURE_CATALOG" in block.get("occurrence_roles", []):
            rank = min(rank, 3)
        result.append({**block, "evidence_priority": rank})
    return sorted(result, key=lambda x: (x.get("evidence_priority", 99), x.get("page_id", ""), x.get("source_order", 0)))


def _table_headers(block: Mapping[str, Any]) -> list[str]:
    metadata = block.get("table_metadata") or block.get("table") or {}
    if isinstance(metadata, Mapping):
        value = metadata.get("headers") or metadata.get("table_headers") or []
        if isinstance(value, list):
            return [str(x) for x in value]
    value = block.get("table_headers") or []
    return [str(x) for x in value] if isinstance(value, list) else []
