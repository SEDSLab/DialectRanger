"""DocumentIR-backed generic owner induction.

This module deliberately works on structural block kinds only.  It does not
know a database's feature inventory and it does not assign a feature type.
The owner is a bounded evidence container; identity and type are later stages.
"""
from __future__ import annotations

from collections import defaultdict
import re
from typing import Any, Iterable, Mapping

from agents.contracts.owner import make_owner_hypothesis
from agents.identity.quality import is_atomic_identifier, is_type_context
from agents.profile_bootstrap.cold_start import profile_hints_for_page
from agents.owner.definition_scope import definition_scopes, scope_for_owner


_HEADING = "HEADING"
_CHILD_OWNER_KINDS = {"TABLE_ROW", "LIST_ITEM", "DEFINITION"}
_SIGNATURE_TEXT_RE = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_$]*\s*\([^\n]*\)|[+*/<>=!|&^~%?-]{1,3})$")
_HEADING_IDENTITY_RE = re.compile(r"(?<![A-Za-z0-9_$])[A-Za-z_][A-Za-z0-9_$]*\s*\([^\n)]*\)")


def _text(block: Mapping[str, Any]) -> str:
    return re.sub(r"\s+", " ", str(block.get("text") or "")).strip()


def _is_heading(block: Mapping[str, Any]) -> bool:
    return block.get("block_kind") == _HEADING


def _boundary_key(block: Mapping[str, Any]) -> str | None:
    """Return the nearest topology boundary carried by a DocumentIR block."""
    parent_ids = ((block.get("provenance") or {}).get("parent_ids") or [])
    for value in reversed(parent_ids):
        if str(value).startswith("boundary:"):
            return str(value)
    return None


def _is_signature_surface(block: Mapping[str, Any]) -> bool:
    """Recognize a standalone signature-shaped text surface generically."""
    text = _text(block)
    if not text:
        return False
    return bool(_SIGNATURE_TEXT_RE.fullmatch(text))


def _is_identity_heading(block: Mapping[str, Any]) -> bool:
    """Recognize headings that carry one or more bounded feature labels."""
    text = _text(block)
    return bool(_HEADING_IDENTITY_RE.search(text)) or bool(re.fullmatch(r"[+*/<>=!|&^~%?-]{1,3}", text))


def _is_typed_leading_label(block: Mapping[str, Any]) -> bool:
    if not is_type_context(block):
        return False
    text = _text(block)
    first = re.split(r"\s*[.]\s*|\s*[:;]\s*", text, maxsplit=1)[0].strip()
    return is_atomic_identifier(first)


def _signature_identity(block: Mapping[str, Any]) -> str:
    """Return only the callable/operator head of a signature surface."""
    text = _text(block)
    from agents.contracts.identity_key import normalize_identity
    from agents.contracts.definition_entry import CALLABLE_HEAD
    return normalize_identity(text) if CALLABLE_HEAD.match(text) else text


def _logical_signature_member(blocks: list[Mapping[str, Any]], index: int, *, scopes=None) -> list[Mapping[str, Any]]:
    """Collect one feature's local surface from its signature anchor.

    Some official references render a feature signature as a paragraph rather
    than a heading or ``CODE`` block.  In that layout the following prose,
    example, and note blocks share one topology boundary.  A signature owner
    must retain that complete local surface until the next peer boundary.
    """
    anchor = blocks[index]
    # Repeated definitions are stronger boundaries than their shared chapter
    # heading. Reuse exactly the same proof that Evidence/Publication consume.
    proof = next((p for p in (definition_scopes({'blocks': blocks}) if scopes is None else scopes)
                  if p.get('definition_pattern') in {'SIGNATURE_LED_LIST_ITEM','OPERATOR_LED_LIST_ITEM'}
                  and anchor.get('block_id') in p['definition_anchor_block_ids']), None)
    if proof:
        ids = set(proof['block_ids'])
        return [b for b in blocks if b['block_id'] in ids]
    # Use the nearest heading as the logical start, not the signature node.
    # This is important for component-rendered references where the order is
    # ``heading -> data-as=p description -> data-as=p labels -> code``.  The
    # heading section is the generic owner primitive; it does not depend on a
    # database vocabulary or a site-specific selector.
    start = index
    heading_level = None
    for cursor in range(index, -1, -1):
        if _is_heading(blocks[cursor]):
            # A prior peer catalog/definition is not part of this signature's
            # section. Never lift across it to a page-level chapter heading.
            intervening = blocks[cursor+1:index]
            if not any(b.get('block_kind') in _CHILD_OWNER_KINDS or
                       (_is_signature_surface(b) and _signature_identity(b) != _signature_identity(anchor))
                       for b in intervening):
                start = cursor
                heading_level = int(blocks[cursor].get("heading_level") or 1)
            break
    end = len(blocks)
    anchor_identity = _signature_identity(anchor)
    for cursor in range(start + 1, len(blocks)):
        block = blocks[cursor]
        if _is_heading(block):
            if heading_level is None or int(block.get("heading_level") or 1) <= heading_level:
                end = cursor
                break
        if str(block.get("block_kind") or "OTHER") == "TABLE_ROW":
            end = cursor
            break
        # A page may expose several signature owners without any heading
        # ancestry.  Stop at the next different signature head, while keeping
        # overloads of the current head in the same owner.  Example code such
        # as ``SELECT OTHER(x)`` does not match the standalone signature
        # grammar and therefore cannot create a false sibling boundary.
        if _is_signature_surface(block) and _signature_identity(block) != anchor_identity:
            end = cursor
            break
    members = list(blocks[start:end])
    return members if anchor in members else [anchor]


def _has_previous_logical_signature(blocks: list[Mapping[str, Any]], index: int) -> bool:
    """Whether a CODE block is already covered by a preceding text anchor."""
    if index <= 0:
        return False
    for cursor in range(index - 1, -1, -1):
        block = blocks[cursor]
        if _is_heading(block) or str(block.get("block_kind") or "OTHER") == "TABLE_ROW":
            return False
        if str(block.get("block_kind") or "OTHER") == "CODE":
            return False
        if _is_signature_surface(block):
            return True
    return False


def _has_previous_same_signature(blocks: list[Mapping[str, Any]], index: int) -> bool:
    """Avoid a second overlapping owner for the same overload surface."""
    anchor = blocks[index]
    identity = _signature_identity(anchor)
    for cursor in range(index - 1, -1, -1):
        block = blocks[cursor]
        if _is_heading(block) or str(block.get("block_kind") or "OTHER") == "TABLE_ROW":
            return False
        if str(block.get("block_kind") or "OTHER") == "CODE":
            return False
        if _is_signature_surface(block):
            return _signature_identity(block) == identity
    return False


def _section_end(blocks: list[Mapping[str, Any]], index: int) -> int:
    level = int(blocks[index].get("heading_level") or 1)
    for cursor in range(index + 1, len(blocks)):
        if _is_heading(blocks[cursor]) and int(blocks[cursor].get("heading_level") or 1) <= level:
            return cursor - 1
    return len(blocks) - 1


def _tier(confidence: float) -> str:
    if confidence >= 0.85:
        return "STRONG"
    if confidence >= 0.60:
        return "PROBABLE"
    return "WEAK"


def _candidate_owner_specs(
    document: Mapping[str, Any],
    profile: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    blocks = list(document.get("blocks") or [])
    from agents.owner.structure_execution import execute_structures
    execution_result=execute_structures(document,profile)
    proven_sections = execution_result['proofs']
    controlled=set(execution_result.get('controlled_block_ids',[]))
    specs: list[dict[str, Any]] = []
    represented: set[tuple[str, ...]] = set()
    hints = profile_hints_for_page(document, profile or {}) if profile else []
    context_types = {
        str(value).upper()
        for value in document.get("feature_context", [])
        if str(value).upper() in {"FUNCTION", "OPERATOR", "DATATYPE"}
    }
    hints_by_strategy: dict[str, dict[str, Any]] = {}
    for hint in hints:
        strategy = str(hint.get("owner_strategy") or "")
        if not strategy:
            continue
        hint_type = str(hint.get("type_hint") or "UNCERTAIN").upper()
        current = hints_by_strategy.get(strategy)
        if current is None or (
            context_types and hint_type in context_types
            and str(current.get("type_hint") or "UNCERTAIN").upper() not in context_types
        ):
            hints_by_strategy[strategy] = hint

    def add(
        kind: str,
        template: str,
        member: list[Mapping[str, Any]],
        confidence: float,
        signals: list[str],
        profile_hint: Mapping[str, Any] | None = None,
        proof_selector: str | None = None,
    ) -> None:
        member_ids = tuple(str(row.get("block_id")) for row in member if row.get("block_id"))
        representation=(member_ids,proof_selector)
        if not member_ids or representation in represented:
            return
        represented.add(representation)
        from agents.profile_bootstrap.local_structure import owner_local_hint
        profile_hint = owner_local_hint(document, member, profile, profile_hint)
        specs.append({
            "owner_kind": kind, "template": template, "member": member,
            "confidence": confidence, "signals": signals,
            "profile_hint": dict(profile_hint or {}),
            "profile_proof_selector":proof_selector,
        })

    # A page singleton is an explicit generic primitive.  Use it only when
    # structural observation found no more specific repeated surface.
    singleton_hint = hints_by_strategy.get("page_singleton")
    if singleton_hint and blocks and not controlled:
        add(
            "PageOwner", "page", blocks, 0.84,
            ["PAGE_SINGLETON", "PROFILE_GUIDED"], singleton_hint,
        )
        return specs

    for index, block in enumerate(blocks):
        block_kind = str(block.get("block_kind") or "OTHER")
        # Reuse the same physical navigation boundary as the explicit adapters.
        # Sidebar words are references, not independent definition Owners.
        from agents.owner.definition_scope import navigation_block
        if navigation_block(block):continue
        if (block.get('definition_context') or {}).get('role')=='DEVELOPER_API_DECLARATION':continue
        collection_scopes=[p for p in proven_sections if p.get('member_collection')
                           and p.get('heading_block_id')==block.get('block_id')]
        if collection_scopes:
            for scope in collection_scopes:
                ids=set(scope['block_ids'])
                add('SignatureOwner','grammar_member',[b for b in blocks if b['block_id'] in ids],
                    0.92,scope['boundary_evidence'],hints_by_strategy.get('heading_section'),
                    proof_selector=scope['section_id'])
            continue
        from agents.owner.structure_execution import composed_definition
        row_scopes=[p for p in proven_sections if p.get('heading_block_id')==block.get('block_id')
            and (p.get('structure_execution') or {}).get('schema_version')=='StructureExecution/v2'
            and p['structure_execution']['structure']=='D']
        if len(row_scopes)>1:
            for scope in row_scopes:
                ids=set(scope['block_ids'])
                add('TableRowOwner','table_row:'+scope['section_id'],[b for b in blocks if b['block_id'] in ids],
                    0.95,scope['boundary_evidence'],hints_by_strategy.get('table_row'),proof_selector=scope['section_id'])
            continue
        list_proof = next((p for p in proven_sections
                           if (composed_definition(p) or p.get('definition_pattern') in {'SIGNATURE_LED_LIST_ITEM','OPERATOR_LED_LIST_ITEM','DATATYPE_SELF_DEFINITION','PARAGRAPH_LABEL_SIGNATURE_SECTION','PROFILE_LOCAL_DEFINITION','SOURCE_RELATION_DEFINITION','FUNCTION_DECLARATION_SECTION','REFERENCE_DECLARATION_SECTION'})
                           and block.get('block_id') in p['definition_anchor_block_ids']), None)
        if list_proof:
            if block['block_id'] == list_proof['heading_block_id']:
                ids = set(list_proof['block_ids'])
                execution=list_proof.get('structure_execution') or {}
                if not list_proof.get('definition_pattern') and composed_definition(list_proof):
                    # Native heading / term-body proofs use a structural
                    # contract, not the legacy pattern-name discriminator.
                    # Keep their verified range and boundary evidence instead
                    # of inventing list containment or a legacy pattern.
                    family=execution['structure']
                    kind,template,strategy={
                        'A':('PageOwner','page','page_singleton'),
                        'B':('HeadingOwner','heading','heading_section'),
                        'C':('DefinitionListOwner','definition_list','definition_list'),
                        'D':('TableRowOwner','table_row','table_row'),
                        'E':('TypedLabelOwner','typed_label','heading_section'),
                    }[family]
                    add(kind,template,[b for b in blocks if b['block_id'] in ids],
                        0.92,list_proof['boundary_evidence'],hints_by_strategy.get(strategy))
                    continue
                row=execution.get('schema_version')=='StructureExecution/v2' and execution.get('structure')=='D'
                add('TableRowOwner' if row else 'TypedLabelOwner' if list_proof.get('feature_type')=='DATATYPE' else 'SignatureOwner',
                    'table_row' if row else list_proof['definition_pattern'].lower(), [b for b in blocks if b['block_id'] in ids],
                    0.92, (list_proof['boundary_evidence'] if list_proof['definition_pattern'] in {'PARAGRAPH_LABEL_SIGNATURE_SECTION','PROFILE_LOCAL_DEFINITION','SOURCE_RELATION_DEFINITION','FUNCTION_DECLARATION_SECTION','REFERENCE_DECLARATION_SECTION'}
                           else ['NATIVE_LI_CONTAINMENT', 'LEADING_FORMAL_SIGNATURE']), hints_by_strategy.get('list_item'))
            continue
        if block_kind == "TABLE_ROW":
            add(
                "TableRowOwner", "table_row", [block], 0.95,
                ["TABLE_ROW", "TABLE_CATALOG", "PROFILE_GUIDED"],
                hints_by_strategy.get("table_row"),
            )
        elif block_kind == "LIST_ITEM":
            add(
                "ListItemOwner", "list_item", [block], 0.78,
                ["LIST_ITEM", "PROFILE_GUIDED"],
                hints_by_strategy.get("list_item"),
            )
        elif block_kind == "DEFINITION":
            if block.get('source_node_kind') == 'DD':
                continue
            member = [block]
            if block.get('source_node_kind') == 'DT':
                proof = next((p for p in proven_sections if p['heading_block_id'] == block['block_id']), None)
                if proof:
                    member = [b for b in blocks if b['block_id'] in proof['block_ids']]
            elif index + 1 < len(blocks) and blocks[index + 1].get("block_kind") in {"DEFINITION", "TEXT"}:
                member.append(blocks[index + 1])
            add(
                "DefinitionListOwner", "definition_list", member, 0.90,
                ["DEFINITION_LIST", "PROFILE_GUIDED"],
                hints_by_strategy.get("definition_list"),
            )
        elif block_kind == 'TEXT' and (block.get('definition_context') or {}).get('role') == 'STORAGE_CLASS_DEFINITION':
            add('TypedLabelOwner','typed_label',[block],0.90,['EXPLICIT_DATATYPE_DEFINITION'],hints_by_strategy.get('heading_section'))
        elif block_kind == 'TEXT' and (block.get('definition_context') or {}).get('role') == 'PAGE_FUNCTION_DEFINITION':
            add('SignatureOwner', 'explicit_self_definition', _logical_signature_member(blocks, index, scopes=proven_sections), 0.90,
                ['EXPLICIT_SELF_DEFINITION', 'TITLE_SUBJECT_CORROBORATION'], hints_by_strategy.get('signature'))
        elif block_kind == "TEXT" and _is_signature_surface(block):
            if _has_previous_same_signature(blocks, index):
                continue
            add(
                "SignatureOwner", "signature", _logical_signature_member(blocks, index, scopes=proven_sections), 0.88,
                ["SIGNATURE", "TEXT_SIGNATURE", "LOGICAL_FEATURE_SURFACE", "PROFILE_GUIDED"],
                hints_by_strategy.get("signature"),
            )
        elif block_kind == "TEXT" and _is_typed_leading_label(block):
            add(
                "TypedLabelOwner", "typed_label", [block], 0.88,
                ["TEXT", "TYPED_LEADING_LABEL", "TYPE_CONTEXT", "PROFILE_GUIDED"],
                hints_by_strategy.get("heading_section") or hints_by_strategy.get("table_row"),
            )
        elif block_kind == "HEADING" and _is_identity_heading(block):
            add(
                "HeadingOwner", "heading", [block], 0.92,
                ["HEADING", "IDENTITY_HEADING", "BOUNDED_IDENTITY_SURFACE", "PROFILE_GUIDED"],
                hints_by_strategy.get("heading_section") or hints_by_strategy.get("signature"),
            )
        elif block_kind == "CODE":
            # If a standalone text signature already owns this topology
            # boundary, do not create a second code-only owner for the same
            # feature surface.
            if _has_previous_logical_signature(blocks, index):
                continue
            # A formal signature is frequently rendered as a standalone CODE
            # block after a component paragraph/label (for example
            # ``Syntax`` followed by ``abs(x)``).  Treat that code block as
            # the anchor for the same bounded heading-local section used by
            # text signatures.  Keeping the fallback for non-signature code
            # preserves the existing example/code behavior and avoids
            # promoting arbitrary snippets into full feature owners.
            if _is_signature_surface(block):
                member = _logical_signature_member(blocks, index, scopes=proven_sections)
            else:
                member = []
                if index and _is_heading(blocks[index - 1]):
                    member.append(blocks[index - 1])
                member.append(block)
                if index + 1 < len(blocks) and blocks[index + 1].get("block_kind") == "TEXT" and not _is_signature_surface(blocks[index + 1]):
                    member.append(blocks[index + 1])
            add(
                "SignatureOwner", "signature", member, 0.88,
                ["SIGNATURE", "CODE", "PROFILE_GUIDED"],
                hints_by_strategy.get("signature"),
            )

    for index, block in enumerate(blocks):
        if not _is_heading(block):
            continue
        end = _section_end(blocks, index)
        section = blocks[index:end + 1]
        kinds = {str(row.get("block_kind") or "OTHER") for row in section}
        # A leaf section is the generic primitive for prose pages.  Pages with
        # a more specific repeated surface already have bounded owners above;
        # retaining a second full-page section owner would only rescan the same
        # blocks and would create owner explosion without adding provenance.
        if (kinds & _CHILD_OWNER_KINDS) or "CODE" in kinds or any(_is_heading(row) for row in section[1:]):
            # Fine-grained discovery anchors do not prove complete coverage.
            # Retain their IDs and attach the proven range below. Only create
            # a heading anchor when there is no existing owner for this range.
            proof = next((p for p in proven_sections if p['heading_block_id'] == block.get('block_id')), None)
            if proof and not any(set(b['block_id'] for b in s['member']) <= set(proof['block_ids']) and scope_for_owner(
                {'owner_kind': s['owner_kind'], 'member_block_ids': [b['block_id'] for b in s['member']]},
                document, [proof]).get('owner_definition_anchor_compatible') for s in specs):
                add("HeadingOwner", "heading", [block], 0.90,
                    ["HEADING", "PROVEN_DEFINITION_ANCHOR", "PROFILE_GUIDED"],
                    hints_by_strategy.get("heading_section"))
            continue
        add(
            "SectionOwner", "section", section, 0.72,
            ["HEADING", "SECTION", "PROFILE_GUIDED"],
            hints_by_strategy.get("heading_section"),
        )

    if not specs and blocks:
        add(
            "PageOwner", "page", blocks, 0.40, ["PAGE_SECTION", "PROFILE_GUIDED"],
            hints_by_strategy.get("page_singleton"),
        )
    for spec in specs:
        spec['definition_scope'] = scope_for_owner(
            {'owner_kind': spec['owner_kind'], 'member_block_ids': [b['block_id'] for b in spec['member']],
             'profile_proof_selector':spec.get('profile_proof_selector')},
            document, proven_sections)
        if controlled:
            spec['definition_scope']['profile_controlled_block_ids']=sorted(controlled)
    return specs


def _make_owner(document: Mapping[str, Any], spec: Mapping[str, Any]) -> dict[str, Any]:
    member = list(spec["member"])
    ids = [str(row["block_id"]) for row in member]
    confidence = float(spec["confidence"])
    owner = make_owner_hypothesis(
        dbms=str(document["dbms"]), page_id=str(document["page_id"]),
        source_url=str(document["url"]), page_hash=str(document["page_hash"]),
        html_path=str(document["html_path"]), start_block_id=ids[0],
        end_block_id=ids[-1], root_block_id=ids[0], member_block_ids=ids,
        owner_kind=str(spec["owner_kind"]), identity_signals=spec["signals"],
        confidence=confidence, tier=_tier(confidence), template=str(spec["template"]),
        status=_tier(confidence), parent_ids=[str(document["document_id"])],
    )
    owner["profile_hint"] = dict(spec.get("profile_hint") or {})
    owner.update(spec.get('definition_scope') or {})
    if spec.get('profile_proof_selector'):
        from agents.contracts.base import stable_id
        owner['owner_id']=stable_id('owner',[owner['owner_id'],spec['profile_proof_selector']])
    if document.get("discovery_provenance"):
        owner["discovery_provenance"] = dict(document["discovery_provenance"])
    return owner


def generate_owner_hypotheses(
    documents: Iterable[Mapping[str, Any]],
    profile: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Generate bounded generic owners for every supplied DocumentIR page."""
    owners: list[dict[str, Any]] = []
    for document in documents:
        owners.extend(_make_owner(document, spec) for spec in _candidate_owner_specs(document, profile))
    return sorted(owners, key=lambda row: (row["provenance"]["source_url"], row["owner_id"]))


def consolidate_owner_hypotheses(owners: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate exact structural owners without merging adjacent owners."""
    selected: dict[tuple[str, tuple[str, ...], str], dict[str, Any]] = {}
    for raw in owners:
        row = dict(raw)
        key = (str(row.get("page_id")), tuple(sorted(row.get("member_block_ids", []))), str(row.get("owner_kind")),row.get('profile_proof_selector'))
        current = selected.get(key)
        if current is None or (float(row.get("confidence", 0)), row.get("owner_id", "")) > (float(current.get("confidence", 0)), current.get("owner_id", "")):
            selected[key] = row
        elif current is not None:
            current["identity_signals"] = sorted(set(current.get("identity_signals", [])) | set(row.get("identity_signals", [])))
    return sorted(selected.values(), key=lambda row: (row.get("provenance", {}).get("source_url", ""), row.get("owner_id", "")))


def owner_block_index(owners: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for owner in owners:
        for block_id in owner.get("member_block_ids", []):
            index.setdefault(str(block_id), dict(owner))
    return index
