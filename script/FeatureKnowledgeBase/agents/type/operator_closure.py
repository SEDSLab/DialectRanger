"""Bounded generic operator-surface closure over an existing identity run.

The closure is intentionally downstream of the existing Owner/Identity and
Generic Type adaptation path. It only reconciles explicit operator-shaped
surfaces that are already present in DocumentIR. It does not consult an
inventory, a DBMS vocabulary, or the network.
"""

from __future__ import annotations

from collections import defaultdict
import copy
import hashlib
import re
from typing import Any, Iterable, Mapping, Sequence

from agents.type.adaptation import (
    _OPERATOR_NOISE_WORDS,
    _OPERATOR_STRONG_ROLE,
    _SURFACE_TOKEN,
    _TOKEN,
    _clean,
    _normal_surface,
    _surface_from_block,
)
from agents.contracts.identity_credibility import make_adaptation_provenance_record
from agents.contracts.base import build_provenance


def _stable_id(parts: Iterable[Any]) -> str:
    value = "|".join(_clean(part) for part in parts)
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:24]


def _typed_block(block: Mapping[str, Any]) -> dict[str, Any]:
    metadata = block.get("table_metadata") or {}
    return {
        "block_id": str(block.get("block_id") or ""),
        "block_type": str(block.get("block_kind") or block.get("block_type") or "text").casefold(),
        "text": str(block.get("text") or ""),
        "cells": list(metadata.get("cells") or []),
        "table_headers": list(metadata.get("headers") or []),
        "heading_ancestry": list(block.get("heading_ancestry") or []),
        "definition_context": dict(block.get("definition_context") or {}),
    }


def _operator_context(block: Mapping[str, Any]) -> str:
    return " | ".join(
        value
        for value in [
            " ".join(str(value) for value in block.get("heading_ancestry") or []),
            " ".join(str(value) for value in block.get("table_headers") or []),
            _clean(block.get("text")),
        ]
        if _clean(value)
    )


def _add_observation(
    rows: list[dict[str, str]],
    surface: str,
    category: str,
    matched: str,
    *,
    allow_noise_tokens: bool = False,
) -> None:
    surface = _normal_surface(surface)
    if not surface:
        return
    tokens = surface.split()
    if not tokens or not all(_SURFACE_TOKEN.fullmatch(token) for token in tokens):
        return
    if not allow_noise_tokens and any(token.casefold() in _OPERATOR_NOISE_WORDS for token in tokens):
        return
    row = {
        "surface": surface,
        "category": str(category),
        "matched_text": _clean(matched),
    }
    if row not in rows:
        rows.append(row)


def _closure_slot_candidates(text: str) -> list[dict[str, str]]:
    """Recover explicit slots whose RHS is a function call or ellipsis."""

    text = _clean(text)
    if not text:
        return []
    operand = r"(?i:(?:expr|expression|value|values|operand|left|right|lhs|rhs|pattern|condition|low|high|zone|arr|array|argument|input|output|column|field|a|b|c|x|y|z))"
    token = r"(?:[A-Z][A-Z0-9_$]*|[!<>=~%&|^+*/-]{1,3})"
    rhs = rf"(?:{operand}|\.{{3}}|[A-Za-z_][A-Za-z0-9_$]*\s*\()"
    pattern = re.compile(
        rf"\b{operand}\b\s+(?P<surface>{token}(?:\s+{token}){{0,3}})\s+(?={rhs})"
    )
    rows: list[dict[str, str]] = []
    for match in pattern.finditer(text):
        surface = match.group("surface")
        _add_observation(
            rows,
            surface,
            "MULTI_TOKEN_INFIX" if len(surface.split()) > 1 else "KEYWORD_INFIX",
            match.group(0),
        )
    return rows


def _closure_postfix_candidates(text: str) -> list[dict[str, str]]:
    """Recognize postfix predicates followed by a prose dash or terminator."""

    text = _clean(text)
    operand = r"(?i:(?:expr|expression|value|operand|left|right|lhs|rhs|a|b|c|x|y|z))"
    token = r"(?:[A-Z][A-Z0-9_$]*|[!<>=~%&|^+*/-]{1,3})"
    pattern = re.compile(
        rf"\b{operand}\b\s+(?P<surface>{token}(?:\s+{token}){{0,3}})"
        rf"(?=\s*(?:returns?|means?|is|equivalent|[.,;:\u2013\u2014-]|$))"
    )
    rows: list[dict[str, str]] = []
    for match in pattern.finditer(text):
        _add_observation(rows, match.group("surface"), "POSTFIX_PREDICATE", match.group(0))
    return rows


def _heading_phrase_candidates(block: Mapping[str, Any]) -> list[dict[str, str]]:
    """Keep explicit uppercase/symbol phrases enumerated by an operator heading."""

    if str(block.get("block_type") or "").casefold() != "heading":
        return []
    headings = [str(value).lstrip("\u200b") for value in block.get("heading_ancestry") or []]
    text = _clean(block.get("text")).lstrip("\u200b")
    context = " | ".join(headings + [text])
    if not _OPERATOR_STRONG_ROLE.search(context) or not text:
        return []
    rows: list[dict[str, str]] = []
    for part in re.split(r"\s+(?:and|/)\s+", text, flags=re.I):
        tokens = [_normal_surface(token) for token in _TOKEN.findall(part)]
        if not tokens or len(tokens) > 4:
            continue
        if not all(_SURFACE_TOKEN.fullmatch(token) for token in tokens):
            continue
        if any(token.casefold() in _OPERATOR_NOISE_WORDS for token in tokens):
            continue
        if not any(token == token.upper() or not token.isalpha() for token in tokens):
            continue
        _add_observation(
            rows,
            " ".join(tokens),
            "MULTI_TOKEN_INFIX" if len(tokens) > 1 else "KEYWORD_INFIX",
            text,
        )
    return rows


def _standalone_phrase_candidates(block: Mapping[str, Any]) -> list[dict[str, str]]:
    """Recover a Markdown/plaintext operator label without requiring operands.

    Some documentation adapters expose a definition heading as a plain text
    block (for example ``IS NULL``) rather than as a heading node.  An exact,
    uppercase multi-token line under an operator context is still structural
    evidence; prose and clause sentences do not satisfy this shape.
    """

    block_type = str(block.get("block_type") or "").casefold()
    if block_type not in {"text", "heading", "list_item", "table_row"}:
        return []
    text = _clean(block.get("text"))
    context = _operator_context(block)
    if not text or not _OPERATOR_STRONG_ROLE.search(context):
        return []
    tokens = text.split()
    if len(tokens) < 2 or len(tokens) > 4:
        return []
    if not all(_SURFACE_TOKEN.fullmatch(token) for token in tokens):
        return []
    if not all(token == token.upper() or not token.isalpha() for token in tokens):
        return []
    category = "POSTFIX_PREDICATE" if tokens[0] == "IS" else "MULTI_TOKEN_INFIX"
    return [{
        "surface": " ".join(tokens),
        "category": category,
        "matched_text": text,
        "allow_noise_tokens": True,
    }]


def operator_surface_observations(block: Mapping[str, Any]) -> list[dict[str, str]]:
    """Return generic, owner-local operator observations from one block."""

    observed=block.get('definition_context') or {}
    if (observed.get('role')=='DECLARATION_CATALOG' or
            (observed.get('role')=='OPERATOR_CATALOG' and observed.get('declaration_origin')=='EXPLICIT_OPERATOR_ENUMERATION')) and observed.get('domain')=='DOCUMENT_FEATURE':
        return [{'surface':m['identity'],'category':'OBSERVED_DECLARATION_CELL','matched_text':m['raw_entry']}
                for m in observed.get('members',[]) if m.get('feature_type','OPERATOR')=='OPERATOR']
    context = _operator_context(block)
    if not _OPERATOR_STRONG_ROLE.search(context):
        return []
    rows: list[dict[str, str]] = []
    for surface, category, matched in _surface_from_block(block):
        _add_observation(rows, surface, category, matched)
    for row in _closure_slot_candidates(_clean(block.get("text"))):
        _add_observation(rows, row["surface"], row["category"], row["matched_text"])
    for row in _closure_postfix_candidates(_clean(block.get("text"))):
        _add_observation(rows, row["surface"], row["category"], row["matched_text"])
    for row in _heading_phrase_candidates(block):
        _add_observation(rows, row["surface"], row["category"], row["matched_text"])
    for row in _standalone_phrase_candidates(block):
        _add_observation(
            rows,
            row["surface"],
            row["category"],
            row["matched_text"],
            allow_noise_tokens=bool(row.get("allow_noise_tokens")),
        )
    # In a BETWEEN-shaped slot, AND is the ternary connector, not a second
    # operator identity. This guard is grammar-shaped and does not depend on
    # a database vocabulary.
    if re.search(r"\b(?:not\s+)?between\b[^.;]{0,100}\band\b", _clean(block.get("text")), re.I):
        rows = [row for row in rows if row.get("surface") != "AND"]
    return rows


def _new_unit(surface: str, owner: Mapping[str, Any], page: Mapping[str, Any], block: Mapping[str, Any]) -> dict[str, Any]:
    key = _normal_surface(surface)
    return {
        "identity_unit_id": "identity_unit:adapted:" + _stable_id((key, "OPERATOR")),
        "canonical_identity": key,
        "variants": [surface],
        "owner_ids": [str(owner.get("owner_id") or "")],
        "candidate_ids": [],
        "occurrence_ids": [],
        "defining_occurrence_ids": [],
        "evidence": [],
        "occurrence_records": [],
        "aliases": [],
        "alias_relations": [],
        "merge_decisions": [],
        "non_defining_occurrence_ids": [],
        "group_key": f"{key}|ADAPTED_OPERATOR",
        "adapted_identity_kind": "OPERATOR",
        "group_status": "PROBABLE_FEATURE",
        "feature_type": "UNCERTAIN",
        "type_hint": "UNCERTAIN",
        "semantic_ready": False,
        "operator_closure_observations": [],
        "adaptation_provenance": [],
        "provenance": {
            **build_provenance(
                dbms=str(page.get("dbms") or ""),
                source_url=str(page.get("url") or ""),
                page_id=str(page.get("page_id") or ""),
                page_hash=str(page.get("page_hash") or ""),
                html_path=str(page.get("html_path") or ""),
                producer_stage="identity_unit",
                producer_version="identity-unit-adapted/v1",
                parent_ids=[str(owner.get("owner_id") or ""), str(block.get("block_id") or "")],
                reason_codes=["GENERIC_OPERATOR_CLOSURE"],
            ),
        },
    }


def _synthetic_owner(page: Mapping[str, Any], block: Mapping[str, Any]) -> dict[str, Any]:
    owner_id = "owner:operator-closure:" + _stable_id((page.get("page_id"), block.get("block_id")))
    return {
        "owner_id": owner_id,
        "owner_kind": "OperatorSurfaceOwner",
        "template": "operator_surface",
        "page_id": page.get("page_id"),
        "source_url": page.get("url"),
        "member_block_ids": [block.get("block_id")],
        "owner_text": block.get("text") or "",
        "status": "SELECTED",
        "selection_status": "SELECTED",
        "profile_hint": {
            "feature_bearing": True,
            "identity_slot": "operator_surface",
            "owner_strategy": "operator_surface_closure",
            "surface_role": "DEFINING_IDENTITY",
            "type_hint": "OPERATOR",
            "surface_confidence_label": "STRUCTURAL",
        },
        "operator_closure": True,
        "provenance": build_provenance(
            dbms=str(page.get("dbms") or ""),
            source_url=str(page.get("url") or ""),
            page_id=str(page.get("page_id") or ""),
            page_hash=str(page.get("page_hash") or ""),
            html_path=str(page.get("html_path") or ""),
            producer_stage="owner_hypothesis",
            producer_version="owner-contract/v1",
            parent_ids=[str(page.get("document_id") or page.get("page_id") or "")],
            reason_codes=["GENERIC_OPERATOR_CLOSURE"],
        ),
    }


def _append_owner(unit: dict[str, Any], owner_id: str) -> None:
    if owner_id:
        unit["owner_ids"] = sorted(set(unit.get("owner_ids") or []) | {owner_id})


def augment_identity_units_for_operator_closure(
    identity: Mapping[str, Any],
    documents: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Reconcile explicit operator surfaces without reopening general Identity."""

    result = copy.deepcopy(dict(identity))
    units = list(result.get("units") or [])
    owners = list(result.get("owners") or [])
    owner_by_id = {str(row.get("owner_id") or ""): row for row in owners}
    owners_by_block: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for owner in owners:
        for block_id in owner.get("member_block_ids") or []:
            owners_by_block[str(block_id)].append(owner)

    operator_units = {
        _normal_surface(unit.get("canonical_identity")): unit
        for unit in units
        if str(unit.get("adapted_identity_kind") or "") == "OPERATOR"
        and _normal_surface(unit.get("canonical_identity"))
    }
    audit: list[dict[str, Any]] = []
    from agents.owner.surface_patterns import native_definition_coverage
    covered_native=native_definition_coverage(units,owners,documents)
    for page in documents:
        for raw_block in page.get("blocks") or []:
            block = _typed_block(raw_block)
            observations = operator_surface_observations(block)
            if not observations:
                continue
            block_id = str(block.get("block_id") or "")
            local_owners = list(owners_by_block.get(block_id) or [])
            if not local_owners:
                if any(o.get('page_id')==page.get('page_id') and
                       block_id in o.get('profile_controlled_block_ids',[]) for o in owners):
                    audit.append({'block_id':block_id,'page_id':page.get('page_id'),
                                  'identity_action':'BLOCKED_BY_EXPLICIT_PROFILE_LOCATION'})
                    continue
                owner = _synthetic_owner(page, block)
                owner_by_id[str(owner["owner_id"])] = owner
                owners.append(owner)
                owners_by_block[block_id].append(owner)
                local_owners = [owner]
            for owner in local_owners:
                for observation in observations:
                    surface = observation["surface"]
                    key = _normal_surface(surface)
                    from agents.owner.profile_execution import identity_authorized
                    if not identity_authorized(owner,block_id,key):
                        audit.append({'identity':key,'owner_id':owner.get('owner_id'),'block_id':block_id,
                                      'identity_action':'BLOCKED_BY_EXPLICIT_PROFILE_LOCATION'})
                        continue
                    native_id=covered_native.get((str(page.get('page_id')),block_id,key,'OPERATOR'))
                    if native_id:
                        audit.append({'identity':key,'surface':surface,'page_id':page.get('page_id'),
                            'block_id':block_id,'owner_id':owner.get('owner_id'),
                            'source_url':page.get('url'),'identity_unit_id':native_id,
                            'identity_action':'COVERED_BY_PROVEN_NATIVE_DEFINITION'})
                        continue
                    unit = operator_units.get(key)
                    action = "ATTACHED"
                    if unit is None:
                        unit = _new_unit(surface, owner, page, block)
                        operator_units[key] = unit
                        units.append(unit)
                        action = "CREATED"
                    _append_owner(unit, str(owner.get("owner_id") or ""))
                    unit.setdefault("variants", [])
                    if surface not in unit["variants"]:
                        unit["variants"].append(surface)
                    closure_row = {
                        "surface": surface,
                        "normalized_surface": key,
                        "category": observation["category"],
                        "matched_text": observation["matched_text"],
                        "owner_id": owner.get("owner_id"),
                        "page_id": page.get("page_id"),
                        "block_id": block_id,
                        "source_url": page.get("url"),
                    }
                    seen = {
                        (
                            str(row.get("owner_id") or ""),
                            str(row.get("block_id") or ""),
                            str(row.get("normalized_surface") or ""),
                            str(row.get("category") or ""),
                        )
                        for row in unit.setdefault("operator_closure_observations", [])
                    }
                    obs_key = (
                        str(owner.get("owner_id") or ""),
                        block_id,
                        key,
                        observation["category"],
                    )
                    if obs_key not in seen:
                        unit["operator_closure_observations"].append(closure_row)
                    unit.setdefault("adaptation_provenance", []).append(
                        make_adaptation_provenance_record(
                            adaptation_kind="OPERATOR_CLOSURE",
                            adaptation_rule_id="GENERIC_OPERATOR_CLOSURE_SURFACE",
                            owner_id=owner.get("owner_id"),
                            page_id=page.get("page_id"),
                            source_url=page.get("url"),
                            block_id=block_id,
                            raw_text=_clean(block.get("text")),
                            page_hash=page.get("page_hash"),
                            category=observation["category"],
                            surface=surface,
                            matched_text=observation["matched_text"],
                        )
                    )
                    audit.append({
                        "identity": key,
                        "surface": surface,
                        "normalized_surface": key,
                        "category": observation["category"],
                        "matched_text": observation["matched_text"],
                        "owner_id": owner.get("owner_id"),
                        "page_id": page.get("page_id"),
                        "block_id": block_id,
                        "source_url": page.get("url"),
                        "identity_unit_id": unit.get("identity_unit_id"),
                        "identity_action": action,
                        "owner_synthetic": bool(owner.get("operator_closure")),
                        "reconciliation": "OWNER_LOCAL_OPERATOR_SURFACE",
                    })
    for unit in units:
        unit["owner_ids"] = sorted(set(str(value) for value in unit.get("owner_ids") or [] if str(value)))
        unit["variants"] = sorted(set(str(value) for value in unit.get("variants") or [] if str(value)))
        if unit.get("operator_closure_observations"):
            unit["operator_closure_observations"] = sorted(
                unit["operator_closure_observations"],
                key=lambda row: (
                    str(row.get("source_url") or ""),
                    str(row.get("block_id") or ""),
                    str(row.get("normalized_surface") or ""),
                    str(row.get("category") or ""),
                ),
            )
    result["units"] = units
    result["owners"] = sorted(owners, key=lambda row: str(row.get("owner_id") or ""))
    result.setdefault("stats", {})
    result["stats"].update({
        "operator_closure_units_after": len(units),
        "operator_closure_synthetic_owners": sum(1 for row in owners if row.get("operator_closure")),
    })
    return result, audit


__all__ = ["augment_identity_units_for_operator_closure", "operator_surface_observations"]
