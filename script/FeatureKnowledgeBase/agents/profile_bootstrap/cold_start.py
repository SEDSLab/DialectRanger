"""Cold-start profile bootstrap for the generic identity development path.

This module intentionally has no database inventory, selector allowlist, or
legacy Profile DSL dependency.  It observes the structural DocumentIR pages
already produced by generic discovery and emits a small, auditable profile
that can guide the generic owner extractor.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from agents.contracts.base import stable_id
from agents.identity.quality import SURFACE_ROLES, assess_surface


SCHEMA_VERSION = "featurecrawler-cold-profile/v1"
PROFILE_HINT_SCHEMA_VERSION = "profile-structural-hints/v1"
OWNER_STRATEGIES = (
    "table_row",
    "definition_list",
    "list_item",
    "heading_section",
    "signature",
    "page_singleton",
)
TYPE_HINTS = ("FUNCTION", "OPERATOR", "DATATYPE", "UNCERTAIN")
PAGE_GRANULARITIES = ("SINGLE_FEATURE", "MULTI_FEATURE", "MIXED", "UNKNOWN")

_STRUCTURAL_REGION_NAMES = {
    "table_row": "TABLE_ROW",
    "definition_list": "DEFINITION_LIST",
    "list_item": "LIST_ITEM",
    "heading_section": "HEADING_SECTION",
    "signature": "SIGNATURE_BLOCK_OR_SPAN",
    "page_singleton": "PAGE",
}
_IDENTITY_SURFACE_NAMES = {
    "table_row": "FIRST_CELL_OR_LINK",
    "definition_list": "DEFINITION_TERM",
    "list_item": "LABEL_OR_INLINE_LITERAL",
    "heading_section": "HEADING",
    "signature": "SIGNATURE_TEXT_OR_HEADING",
    "page_singleton": "PAGE_TITLE_OR_PRIMARY_HEADING",
}
_BOUNDARY_HINT_NAMES = {
    "table_row": "SIBLING_TABLE_ROW",
    "definition_list": "TERM_BODY_PAIR",
    "list_item": "SIBLING_LIST_ITEM",
    "heading_section": "HEADING_BOUNDED_SECTION",
    "signature": "SIGNATURE_SPAN_OR_BLOCK",
    "page_singleton": "PAGE_BOUNDARY",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _page_type_hint(page: Mapping[str, Any]) -> str:
    values = [str(value).casefold() for value in page.get("feature_context", [])]
    if len(values) == 1 and values[0].upper() in TYPE_HINTS:
        return values[0].upper()
    if len(values) == 1 and values[0] in {"function", "operator", "datatype"}:
        return values[0].upper()
    return "UNCERTAIN"


def _surface_observation(page: Mapping[str, Any]) -> Counter[str]:
    counts: Counter[str] = Counter()
    blocks = list(page.get("blocks") or [])
    for block in blocks:
        kind = str(block.get("block_kind") or block.get("block_type") or "OTHER")
        if kind!='LIST_ITEM' and re.search(r'(?:^| > )li(?:[.#:]| > |$)',str(block.get('dom_path') or '')):
            counts['list_item']+=1
        if kind == "TABLE_ROW":
            counts["table_row"] += 1
        elif kind == "DEFINITION":
            counts["definition_list"] += 1
        elif kind == "LIST_ITEM":
            counts["list_item"] += 1
        elif kind == "CODE":
            counts["signature"] += 1
        elif kind == "HEADING":
            counts["heading_section"] += 1
    if not counts and blocks:
        counts["page_singleton"] = 1
    return counts


def _representative_pages(documents: list[Mapping[str, Any]], limit: int = 12) -> list[Mapping[str, Any]]:
    """Select structural representatives deterministically, not by feature name."""
    selected: list[Mapping[str, Any]] = []
    seen_strategies: set[str] = set()
    seen_types: set[str] = set()
    from .local_structure import page_strata
    strata = {str(page.get('page_id')): page_strata(page) for page in documents}
    seen_strata = set()
    remaining = list(documents)
    # Greedy structural coverage: type-diverse pages are preferred, but the
    # type is used only when discovery produced one unambiguous type.  Mixed
    # pages remain useful representatives and are explicitly UNCERTAIN.
    while remaining and len(selected) < limit:
        ranked = []
        for page in remaining:
            strategies = set(_surface_observation(page))
            page_type = _page_type_hint(page)
            new_types = int(page_type != "UNCERTAIN" and page_type not in seen_types)
            local_strata = strata[str(page.get('page_id'))]
            new_local_types = len({kind for kind, _ in local_strata if kind != 'UNKNOWN'} - seen_types)
            new_pairs = len(local_strata - seen_strata)
            new_strategies = len(strategies - seen_strategies)
            block_count = len(page.get("blocks") or [])
            ranked.append((
                -(max(new_types, new_local_types) * 100 + new_pairs * 10 + new_strategies),
                -min(block_count, 200),
                str(page.get("url") or ""),
                page,
            ))
        ranked.sort(key=lambda row: row[:3])
        page = ranked[0][3]
        remaining.remove(page)
        selected.append(page)
        seen_strategies.update(_surface_observation(page))
        seen_strata.update(strata[str(page.get('page_id'))])
        seen_types.update(kind for kind, _ in strata[str(page.get('page_id'))] if kind != 'UNKNOWN')
        page_type = _page_type_hint(page)
        if page_type != "UNCERTAIN":
            seen_types.add(page_type)
    return selected


def _surface_rows(documents: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    observed: dict[tuple[str, str], dict[str, Any]] = {}
    for page in documents:
        page_type = _page_type_hint(page)
        for strategy, count in _surface_observation(page).items():
            key = (strategy, page_type)
            row = observed.setdefault(key, {
                "page_hint": strategy,
                "owner_strategy": strategy,
                "identity_slot": {
                    "table_row": "first_cell",
                    "definition_list": "definition_term",
                    "list_item": "inline_literal_or_label",
                    "heading_section": "heading",
                    "signature": "code_or_heading",
                    "page_singleton": "page_title_or_primary_heading",
                }[strategy],
                "type_hint": page_type,
                "observed_pages": [],
                "observed_block_count": 0,
                "surface_roles": [],
                "surface_confidences": [],
                "feature_evidence_counts": Counter(),
                "surface_evidence_counts": Counter(),
            })
            row["observed_block_count"] += int(count)
            if len(row["observed_pages"]) < 12:
                row["observed_pages"].append(str(page.get("url") or ""))
            matching_blocks = [
                block for block in page.get("blocks", [])
                if {
                    "TABLE_ROW": "table_row",
                    "TABLE": "table_row",
                    "DEFINITION": "definition_list",
                    "LIST_ITEM": "list_item",
                    "HEADING": "heading_section",
                    "CODE": "signature",
                }.get(str(block.get("block_kind") or "")) == strategy
            ]
            for block in matching_blocks:
                assessment = assess_surface(block, page=page)
                row["surface_roles"].append(assessment["surface_role"])
                row["surface_confidences"].append(float(assessment["surface_confidence"]))
                for evidence in assessment.get("feature_evidence", []):
                    row["feature_evidence_counts"][evidence] += 1
                for evidence in assessment.get("surface_evidence", []):
                    row["surface_evidence_counts"][evidence] += 1

    result: list[dict[str, Any]] = []
    for row in observed.values():
        roles = Counter(row.pop("surface_roles", []))
        confidences = list(row.pop("surface_confidences", []))
        feature_evidence_counts = row.pop("feature_evidence_counts", Counter())
        surface_evidence_counts = row.pop("surface_evidence_counts", Counter())
        feature_roles = {"FEATURE_DEFINITION", "FEATURE_CATALOG", "FEATURE_REFERENCE"}
        feature_count = sum(count for role, count in roles.items() if role in feature_roles)
        total = max(sum(roles.values()), 1)
        dominant_role, dominant_count = roles.most_common(1)[0] if roles else ("UNKNOWN", 0)
        if feature_count / total >= 0.60:
            if row["owner_strategy"] in {"table_row", "list_item"}:
                surface_role = "FEATURE_CATALOG"
            elif row["owner_strategy"] in {"signature", "definition_list", "heading_section", "page_singleton"}:
                surface_role = "FEATURE_DEFINITION"
            else:
                surface_role = dominant_role
        elif feature_count:
            surface_role = "FEATURE_REFERENCE"
        elif dominant_role in SURFACE_ROLES:
            surface_role = dominant_role
        else:
            surface_role = "UNKNOWN"
        average_confidence = sum(confidences) / max(len(confidences), 1)
        role_confidence = dominant_count / total
        surface_confidence = round(max(0.05, min(0.99, average_confidence * (0.65 + 0.35 * role_confidence))), 4)
        row.update({
            "surface_role": surface_role,
            "surface_confidence": surface_confidence,
            "surface_confidence_label": (
                "HIGH" if surface_confidence >= 0.80
                else "MEDIUM" if surface_confidence >= 0.50
                else "LOW"
            ),
            "feature_bearing": bool(feature_count),
            "feature_evidence": [
                {"signal": signal, "count": int(count)}
                for signal, count in feature_evidence_counts.most_common()
            ],
            "surface_evidence": [
                {"signal": signal, "count": int(count)}
                for signal, count in surface_evidence_counts.most_common()
            ],
            "role_distribution": dict(sorted(roles.items())),
        })
        result.append(row)
    return sorted(result, key=lambda row: (
        row["owner_strategy"], row["type_hint"], row["page_hint"],
    ))


def _build_hint_contract(surfaces: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Convert observed surfaces into non-decisive structural hints.

    ``owner_strategy`` remains in each legacy surface for compatibility with
    the current extractor.  The new contract explicitly marks that value as
    an observation/hint; Owner candidate generation remains the selection
    authority.
    """
    strategies = sorted({
        str(surface.get("owner_strategy") or "")
        for surface in surfaces
        if str(surface.get("owner_strategy") or "") in OWNER_STRATEGIES
    })
    non_page_strategies = [strategy for strategy in strategies if strategy != "page_singleton"]
    if strategies == ["page_singleton"]:
        granularity = "SINGLE_FEATURE"
    elif non_page_strategies:
        granularity = "MULTI_FEATURE"
    elif strategies:
        granularity = "UNKNOWN"
    else:
        granularity = "UNKNOWN"
    from agents.owner.definition_patterns import default_patterns
    from agents.owner.scope_plan import default_scope_patterns
    return {
        "schema_version": PROFILE_HINT_SCHEMA_VERSION,
        "page_granularity": granularity,
        "structural_region_hints": [_STRUCTURAL_REGION_NAMES[strategy] for strategy in strategies],
        "identity_surface_hints": sorted({_IDENTITY_SURFACE_NAMES[strategy] for strategy in strategies}),
        "boundary_hints": sorted({_BOUNDARY_HINT_NAMES[strategy] for strategy in strategies}),
        "negative_surface_hints": ["EXAMPLE_CODE", "NAVIGATION", "CROSS_REFERENCE"],
        "example_surface_hints": ["CODE_BLOCK", "INLINE_LITERAL"],
        "legacy_owner_strategy_hints": strategies,
        "selection_authority": "OWNER_CANDIDATE_INFERENCE",
        "definition_patterns": default_patterns() if 'list_item' in strategies else [],
        "definition_scope_patterns": default_scope_patterns(),
    }


def profile_structural_hints(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Return normalized structural hints without selecting an Owner.

    Older profiles are upgraded in memory from their observed
    ``owner_strategy`` values.  This function is intentionally pure and is
    not called by the current production Owner extractor.
    """
    existing = profile.get("hint_contract")
    if isinstance(existing, Mapping):
        result = dict(existing)
        result.setdefault("schema_version", PROFILE_HINT_SCHEMA_VERSION)
        result.setdefault("page_granularity", "UNKNOWN")
        result.setdefault("structural_region_hints", [])
        result.setdefault("identity_surface_hints", [])
        result.setdefault("boundary_hints", [])
        result.setdefault("negative_surface_hints", [])
        result.setdefault("example_surface_hints", [])
        result.setdefault("legacy_owner_strategy_hints", [])
        result.setdefault("selection_authority", "OWNER_CANDIDATE_INFERENCE")
        return result
    return _build_hint_contract(profile.get("surfaces", []))


def build_cold_start_profile(
    *,
    dbms: str,
    documents: Iterable[Mapping[str, Any]],
    run_id: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    documents = list(documents)
    representatives = _representative_pages(documents)
    source_pages = [{
        "page_id": str(page.get("page_id") or ""),
        "url": str(page.get("url") or ""),
        "page_hash": str(page.get("page_hash") or ""),
        "block_kinds": sorted({str(block.get("block_kind") or "OTHER") for block in page.get("blocks", [])}),
    } for page in representatives]
    surfaces = _surface_rows(representatives)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "profile_id": stable_id("cold_profile", [dbms, run_id, surfaces, source_pages]),
        "dbms": str(dbms),
        "generated": True,
        "generated_at": generated_at or _now_iso(),
        "run_id": str(run_id),
        "generation_mode": "AUTO_GENERATED",
        "manual_edit": False,
        "source_pages": source_pages,
        "surfaces": surfaces,
        "hint_contract": _build_hint_contract(surfaces),
        "bootstrap": {
            "method": "deterministic_structural_observation",
            "input_document_count": len(documents),
            "representative_page_count": len(representatives),
            "inventory_used": False,
            "db_specific_repair_used": False,
        },
    }
    from agents.owner.definition_patterns import observed_container_patterns
    payload['hint_contract']['definition_patterns'].extend(observed_container_patterns(representatives))
    from .local_structure import observe_regions, build_local_patterns, page_strata
    regions = observe_regions(representatives)
    payload['hint_contract']['local_type_patterns'] = build_local_patterns(regions)
    payload['local_structure_regions'] = regions
    available = set().union(*(page_strata(page) for page in documents)) if documents else set()
    covered = set().union(*(page_strata(page) for page in representatives)) if representatives else set()
    payload['bootstrap']['sampling'] = {
        'method': 'MULTILABEL_CONTENT_X_STRUCTURE',
        'available_strata': sorted([list(pair) for pair in available]),
        'covered_strata': sorted([list(pair) for pair in covered]),
        'uncovered_strata': sorted([list(pair) for pair in available - covered]),
        'selected_pages': [{'page_id': page.get('page_id'), 'url': page.get('url'),
                            'strata': sorted([list(pair) for pair in page_strata(page)])} for page in representatives],
        'type_labels_are_context_only': True,
    }
    return validate_cold_start_profile(payload)


def validate_cold_start_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version", "profile_id", "dbms", "generated", "generated_at",
        "run_id", "generation_mode", "manual_edit", "source_pages", "surfaces",
    }
    missing = required - set(profile)
    if missing:
        raise ValueError(f"cold-start profile missing fields: {sorted(missing)}")
    if profile.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported cold-start profile schema")
    mode=profile.get('generation_mode')
    if mode=='AUTO_GENERATED':
        if profile.get('generated') is not True or profile.get('manual_edit') is not False:
            raise ValueError('auto-generated profile provenance is inconsistent')
    elif mode in {'MANUAL_PREPARED','MANUAL_REVISED'}:
        if profile.get('generated') is not False or profile.get('manual_edit') is not (mode=='MANUAL_REVISED'):
            raise ValueError('manual profile must declare its true origin')
        provenance=profile.get('profile_provenance') or {}
        if (set(provenance)-{'author','authored_at','revision','derived_from_digest'}
                or not all(isinstance(provenance.get(k),str) and provenance[k].strip() for k in ['author','authored_at','revision'])):
            raise ValueError('manual profile requires author, authored_at and revision')
        if mode=='MANUAL_REVISED' and not re.fullmatch(r'[a-f0-9]{64}',str(provenance.get('derived_from_digest',''))):
            raise ValueError('revised profile requires original profile digest')
        known=required|{'hint_contract','bootstrap','local_structure_regions','profile_provenance'}
        if set(profile)-known:raise ValueError('unknown manual profile fields')
        hints=profile.get('hint_contract')
        if not isinstance(hints,Mapping) or not {'definition_patterns','definition_scope_patterns'}<=set(hints):
            raise ValueError('manual profile requires explicit definition execution plans')
        allowed={'schema_version','page_granularity','structural_region_hints','identity_surface_hints','boundary_hints',
            'negative_surface_hints','example_surface_hints','legacy_owner_strategy_hints','selection_authority',
            'definition_patterns','definition_scope_patterns','local_type_patterns','content_binding_patterns',
            'definition_structure_patterns','member_collection_patterns','linked_syntax_patterns','authority_scope'}
        if set(hints)-allowed:raise ValueError('unknown manual structural hint fields')
        if 'authority_scope' in hints and (hints['authority_scope'] != 'CONFIGURED_PLANS_ONLY'
                or hints.get('selection_authority') != 'OWNER_CANDIDATE_INFERENCE'):
            raise ValueError('scoped authority requires compatible inference outside configured plans')
    else:
        raise ValueError('unsupported profile generation mode')
    forbidden = {"known_features", "feature_inventory", "allowlist", "identity_list", "repair_rules"}
    def forbidden_keys(value):
        if isinstance(value,Mapping):return bool(forbidden & set(value)) or any(forbidden_keys(v) for v in value.values())
        if isinstance(value,list):return any(forbidden_keys(v) for v in value)
        return False
    if forbidden_keys(profile):
        raise ValueError("cold-start profile contains forbidden prior knowledge")
    surfaces = profile.get("surfaces")
    if not isinstance(surfaces, list):
        raise ValueError("cold-start profile surfaces must be a list")
    for surface in surfaces:
        if surface.get("owner_strategy") not in OWNER_STRATEGIES:
            raise ValueError(f"unsupported owner strategy: {surface.get('owner_strategy')}")
        if surface.get("type_hint") not in TYPE_HINTS:
            raise ValueError(f"unsupported type hint: {surface.get('type_hint')}")
        if mode in {'MANUAL_PREPARED','MANUAL_REVISED'}:
            allowed={'page_hint','owner_strategy','identity_slot','type_hint','observed_pages','observed_block_count',
                'surface_role','surface_confidence','surface_confidence_label','feature_bearing','feature_evidence',
                'surface_evidence','role_distribution'}
            if set(surface)-allowed:raise ValueError('unknown manual surface fields')
    hint_contract = profile_structural_hints(profile)
    from agents.owner.linked_syntax import patterns as validate_linked_syntax
    validate_linked_syntax(profile)
    from agents.owner.structure_execution import compile_structure_plan
    compile_structure_plan(profile)
    from agents.owner.member_collections import validate_plans
    validate_plans(hint_contract.get('member_collection_patterns',[]))
    from agents.semantic_stage.applicability import validate_binding_patterns
    validate_binding_patterns(hint_contract.get('content_binding_patterns'))
    if 'definition_patterns' in hint_contract:
        from agents.owner.definition_patterns import validate_patterns
        validate_patterns(hint_contract['definition_patterns'])
    if 'definition_scope_patterns' in hint_contract:
        from agents.owner.scope_plan import validate_scope_patterns
        validate_scope_patterns(hint_contract['definition_scope_patterns'])
    if 'local_type_patterns' in hint_contract:
        from .local_structure import validate_local_patterns
        validate_local_patterns(hint_contract['local_type_patterns'])
    if hint_contract.get("schema_version") != PROFILE_HINT_SCHEMA_VERSION:
        raise ValueError("unsupported profile structural hint schema")
    if hint_contract.get("page_granularity") not in PAGE_GRANULARITIES:
        raise ValueError("unsupported profile page granularity")
    selection=hint_contract.get("selection_authority")
    if selection == 'EXPLICIT_PROFILE_DEFINITIONS':
        plans=hint_contract.get('definition_structure_patterns',[])
        if profile.get('generated') is not False or not plans or any(
                (p.get('execution') or {}).get('authority')!='EXPLICIT_DEFINITION' for p in plans):
            raise ValueError('explicit Profile authority requires manual definition plans')
    elif selection != "OWNER_CANDIDATE_INFERENCE":
        raise ValueError("unsupported Profile selection authority")
    for field in (
        "structural_region_hints", "identity_surface_hints", "boundary_hints",
        "negative_surface_hints", "example_surface_hints", "legacy_owner_strategy_hints",
    ):
        if not isinstance(hint_contract.get(field), list):
            raise ValueError(f"profile hint field must be a list: {field}")
    return dict(profile)


def write_cold_start_profile(path: str | Path, profile: Mapping[str, Any]) -> None:
    validated = validate_cold_start_profile(profile)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(validated, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_cold_start_profile(path: str | Path) -> dict[str, Any]:
    profile = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_cold_start_profile(profile)


def profile_hints_for_page(page: Mapping[str, Any], profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return observed generic surface hints applicable to one page."""
    page_type = _page_type_hint(page)
    observations = _surface_observation(page)
    result = []
    for surface in profile.get("surfaces", []):
        strategy = str(surface.get("owner_strategy") or "")
        if strategy not in observations:
            continue
        hint_type = str(surface.get("type_hint") or "UNCERTAIN")
        if page_type == "UNCERTAIN":
            # A mixed/ambiguous page must not inherit a typed hint merely
            # because the same structural surface appeared on a typed page.
            if hint_type != "UNCERTAIN":
                continue
        elif hint_type not in {page_type, "UNCERTAIN"}:
            continue
        result.append(dict(surface))
    return result


def profile_digest(profile: Mapping[str, Any]) -> str:
    encoded = json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
