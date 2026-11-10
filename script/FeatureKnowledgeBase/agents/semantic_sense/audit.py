from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from agents.semantic_extractor.llm_client import LLMClientFactory


SCHEMA_VERSION = "semantic-sense-audit/v1"
PROMPT_VERSION = "semantic-sense/v1"
SENSE_FIELDS = (
    "description",
    "formal_syntax",
    "parameters",
    "return_type",
    "notes",
    "official_examples",
    "generated_examples",
)
SENSE_LABELS = ("COMPARISON", "ASSIGNMENT")

# These are semantic role markers, not feature-name knowledge.  They are used
# only to decide whether two official definition blocks describe different
# meanings of the same identity.  All evidence still has to be retained and
# reviewed by the downstream audit.
_ROLE_MARKERS = {
    "COMPARISON": re.compile(
        r"\b(?:equal(?:ity|s)?|comparison|compare(?:s|d)?|relational|matches?|not\s+equal|inequal)\b",
        re.IGNORECASE,
    ),
    "ASSIGNMENT": re.compile(
        r"\b(?:assign(?:s|ed|ment)?|assignment|set\s+(?:a|the)\s+value|variable\s+assignment|stores?\s+(?:a\s+)?value)\b",
        re.IGNORECASE,
    ),
}
_SYMBOL_RE = re.compile(r"^[^A-Za-z0-9_]+$")


def run_sense_audit(
    semantic_root: str | Path,
    output_root: str | Path,
    *,
    identity_root: str | Path | None = None,
    run_llm: bool = False,
    model: str | None = None,
    timeout: int = 60,
    max_retries: int = 2,
    workers: int = 4,
) -> dict[str, Any]:
    """Audit frozen semantic features without rerunning any earlier stage."""

    semantic_root = Path(semantic_root).resolve()
    output_root = Path(output_root).resolve()
    if not semantic_root.is_dir():
        raise FileNotFoundError(f"Semantic stage root not found: {semantic_root}")
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError(f"sense-audit output must be new and empty: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)

    features = _read_jsonl(semantic_root / "semantic" / "semantic_features.jsonl")
    bundles = _read_jsonl(semantic_root / "evidence" / "bundles.jsonl")
    if not features:
        raise ValueError("No semantic features found in the supplied semantic root")

    bundle_by_group = {str(x.get("identity_group_id") or ""): x for x in bundles}
    bundle_by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for bundle in bundles:
        bundle_by_key[_feature_key(bundle)].append(bundle)

    grouped = _group_features(features, bundle_by_group, bundle_by_key)
    candidates: list[dict[str, Any]] = []
    candidate_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for key, context in grouped.items():
        candidate = detect_sense_candidate(context)
        if candidate is not None:
            candidates.append(candidate)
            candidate_by_key[key] = candidate
    _write_jsonl(output_root / "sense" / "sense_candidates.jsonl", candidates)

    llm_client, llm_state, model_name = _make_llm_client(
        run_llm=run_llm,
        model=model,
        timeout=timeout,
        max_retries=max_retries,
    )

    decisions: dict[tuple[str, str], dict[str, Any]] = {}
    pending: list[tuple[tuple[str, str], dict[str, Any], dict[str, Any]]] = []
    for key, context in sorted(grouped.items()):
        candidate = candidate_by_key.get(key)
        deterministic = _deterministic_decision(context, candidate)
        if deterministic.get("decision") == "MULTI_SENSE":
            decisions[key] = deterministic
        elif candidate is None:
            decisions[key] = deterministic
        elif deterministic.get("decision") == "REVIEW_REQUIRED":
            no_defining_evidence = "NO_DEFINING_EVIDENCE" in set(candidate.get("candidate_reason", [])) if candidate else False
            if llm_client is not None and llm_state.get("ready") and not no_defining_evidence:
                pending.append((key, candidate, context))
            else:
                decisions[key] = deterministic
        else:
            decisions[key] = deterministic

    if pending and llm_client is not None and llm_state.get("ready") and len(pending) > 1:
        with ThreadPoolExecutor(max_workers=max(1, int(workers))) as executor:
            responses = list(executor.map(
                lambda item: _call_sense_llm(llm_client, item[1], item[2]),
                pending,
            ))
    else:
        responses = [
            _call_sense_llm(llm_client, candidate, context)
            for _, candidate, context in pending
        ]

    for (key, candidate, context), response in zip(pending, responses):
        decisions[key] = _apply_ai_decision(candidate, context, response, model_name)
        llm_state["calls"] = int(llm_state.get("calls", 0)) + 1
        if decisions[key]["decision"] == "SINGLE_SENSE":
            llm_state["single"] = int(llm_state.get("single", 0)) + 1
        elif decisions[key]["decision"] == "MULTI_SENSE":
            llm_state["multi"] = int(llm_state.get("multi", 0)) + 1
        else:
            llm_state["unresolved"] = int(llm_state.get("unresolved", 0)) + 1

    for decision in decisions.values():
        decision.setdefault("model", model_name if decision.get("resolution_method") == "AI" else None)
        decision.setdefault("prompt_version", PROMPT_VERSION if decision.get("resolution_method") == "AI" else None)
        decision.setdefault("timestamp", _now())

    decision_rows = [
        _decision_row(key, grouped[key], candidate_by_key.get(key), decisions[key])
        for key in sorted(grouped)
    ]
    _write_jsonl(output_root / "sense" / "sense_decisions.jsonl", decision_rows)

    sense_clusters: list[dict[str, Any]] = []
    sense_features: list[dict[str, Any]] = []
    for key in sorted(grouped):
        context = grouped[key]
        decision = decisions[key]
        projected, clusters = _project_group(context, decision)
        sense_features.extend(projected)
        sense_clusters.extend(clusters)
    _write_jsonl(output_root / "sense" / "sense_clusters.jsonl", sense_clusters)
    _write_jsonl(output_root / "sense" / "semantic_features_sense_aware.jsonl", sense_features)

    review_rows = _build_review_pack(grouped, candidate_by_key, decisions, sense_features, sense_clusters)
    _write_jsonl(output_root / "evaluation" / "sense_review_pack.jsonl", review_rows)
    _write_review_markdown(output_root / "evaluation" / "sense_review_pack.md", review_rows)

    summary = _build_summary(
        features=features,
        grouped=grouped,
        candidates=candidates,
        decisions=decisions,
        sense_features=sense_features,
        sense_clusters=sense_clusters,
        review_rows=review_rows,
        llm_state=llm_state,
        semantic_root=semantic_root,
        identity_root=identity_root,
        model=model_name,
    )
    _write_json(output_root / "evaluation" / "sense_summary.json", summary)
    _write_report(output_root / "evaluation" / "sense_report.md", summary, grouped, decisions)
    _write_json(output_root / "runtime" / "run_manifest.json", {
        "schema_version": "semantic-sense-run/v1",
        "pipeline_id": "generic-semantic-sense-audit/v1",
        "semantic_root": str(semantic_root),
        "identity_root": str(Path(identity_root).resolve()) if identity_root else None,
        "output_root": str(output_root),
        "input_feature_count": len(features),
        "input_bundle_count": len(bundles),
        "discovery_run": False,
        "identity_rerun": False,
        "semantic_extraction_rerun": False,
        "canonical_run": False,
        "frontend_run": False,
        "legacy_profile_used": False,
        "formal_pipeline_used": False,
        "db_specific_logic_used": False,
        "inventory_substitution_used": False,
        "llm_state": llm_state,
    })
    return {"status": "accepted", "summary": summary, "output_root": str(output_root)}


def detect_sense_candidate(context: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return a deterministic possible-multi-sense ledger row, if warranted."""

    key = context["key"]
    definitions = context["definitions"]
    identity, feature_type = key
    if not definitions:
        return {
            "candidate_id": "sense_candidate:" + _hash({"key": key, "reason": "NO_DEFINING_EVIDENCE"})[:24],
            "normalized_identity": identity,
            "feature_type": feature_type,
            "feature_ids": context["feature_ids"],
            "identity_group_ids": context["identity_group_ids"],
            "candidate_status": "POSSIBLE_MULTI_SENSE",
            "candidate_reason": ["NO_DEFINING_EVIDENCE"],
            "defining_evidence_refs": [],
            "definition_contexts": [],
            "source_urls": sorted(context["source_urls"]),
            "provenance": {"producer": "semantic-sense-deterministic-detector", "version": SCHEMA_VERSION},
        }

    # Comparison/assignment are operator roles, not a general definition of
    # polysemy. Their occurrence in function/type prose proves no new sense.
    if feature_type != 'OPERATOR':
        return None
    role_to_refs: dict[str, set[str]] = defaultdict(set)
    contexts: list[dict[str, Any]] = []
    for definition in definitions:
        signals = sorted(_sense_signals(definition["combined_text"]))
        # A paragraph mentioning both roles is not two separable definitions.
        for signal in signals if len(signals)==1 else []:
            role_to_refs[signal].add(definition["evidence_ref"])
        contexts.append({
            "evidence_ref": definition["evidence_ref"],
            "source_url": definition.get("source_url"),
            "heading_ancestry": definition.get("heading_ancestry", []),
            "signals": signals,
            "text": definition.get("raw_text") or definition.get("block_text") or "",
        })
    conflicting_roles = [role for role in SENSE_LABELS if role_to_refs.get(role)]
    shared=set.intersection(*(role_to_refs[role] for role in conflicting_roles)) if len(conflicting_roles)>1 else set()
    for refs in role_to_refs.values():refs.difference_update(shared)
    conflicting_roles = [role for role in SENSE_LABELS if role_to_refs.get(role)]
    distinct_definitions = sorted({_definition_fingerprint(x) for x in definitions if _definition_fingerprint(x)})
    # A candidate is deliberately conservative: wording variants by themselves
    # are not enough.  Distinct definitions with opposing semantic role markers
    # are the generic high-confidence trigger.
    if len(conflicting_roles) < 2:
        return None
    return {
        "candidate_id": "sense_candidate:" + _hash({"key": key, "refs": sorted({x["evidence_ref"] for x in definitions})})[:24],
        "normalized_identity": identity,
        "feature_type": feature_type,
        "feature_ids": context["feature_ids"],
        "identity_group_ids": context["identity_group_ids"],
        "candidate_status": "POSSIBLE_MULTI_SENSE",
        "candidate_reason": [
            "CONFLICTING_DEFINITION_SEMANTICS",
            "MULTIPLE_DEFINITION_CONTEXTS" if len({str(x.get("source_url") or "") for x in contexts}) > 1 else "DISTINCT_DEFINING_TEXTS",
        ],
        "defining_evidence_refs": sorted({x["evidence_ref"] for x in definitions}),
        "definition_fingerprints": distinct_definitions,
        "definition_contexts": contexts,
        "role_signal_refs": {role: sorted(refs) for role, refs in sorted(role_to_refs.items())},
        "source_urls": sorted(context["source_urls"]),
        "provenance": {"producer": "semantic-sense-deterministic-detector", "version": SCHEMA_VERSION},
    }


def _deterministic_decision(context: Mapping[str, Any], candidate: Mapping[str, Any] | None) -> dict[str, Any]:
    identity, feature_type = context["key"]
    base = {
        "normalized_identity": identity,
        "feature_type": feature_type,
        "feature_ids": list(context["feature_ids"]),
        "identity_group_ids": list(context["identity_group_ids"]),
        "resolution_method": "DETERMINISTIC",
        "confidence": 0.98,
    }
    if not context["definitions"]:
        return {
            **base,
            "decision": "REVIEW_REQUIRED",
            "reason": "no defining identity evidence available for sense resolution",
            "clusters": [],
        }
    if candidate is None:
        return {
            **base,
            "decision": "SINGLE_SENSE",
            "reason": "no conflicting semantic definition signals",
            "clusters": [_single_cluster(context, reason="default semantic sense")],
        }
    role_refs = candidate.get("role_signal_refs") or {}
    clusters = []
    for label in SENSE_LABELS:
        refs = sorted(set(str(x) for x in role_refs.get(label, [])))
        if refs:
            clusters.append(_cluster_from_refs(label, refs, context, reason="conflicting official definition semantics"))
    if len(clusters) >= 2 and all(x["defining_evidence_refs"] for x in clusters):
        return {
            **base,
            "decision": "MULTI_SENSE",
            "reason": "opposing semantic role markers occur in separate official definitions",
            "confidence": 0.96,
            "clusters": clusters,
        }
    return {
        **base,
        "decision": "REVIEW_REQUIRED",
        "reason": "candidate has multiple definition evidence but no safely separable deterministic clusters",
        "confidence": 0.55,
        "clusters": [],
    }


def _apply_ai_decision(candidate: Mapping[str, Any], context: Mapping[str, Any], response: Mapping[str, Any], model: str) -> dict[str, Any]:
    raw = str(response.get("_raw_response") or "")
    parsed, parse_error = _parse_json_object(raw)
    base = {
        "normalized_identity": context["key"][0],
        "feature_type": context["key"][1],
        "feature_ids": list(context["feature_ids"]),
        "identity_group_ids": list(context["identity_group_ids"]),
        "resolution_method": "AI",
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "response_hash": _hash(raw) if raw else None,
        "response_status": response.get("_status", "unknown"),
        "raw_response": raw or None,
    }
    if response.get("_status") != "ok" or parse_error:
        return {**base, "decision": "REVIEW_REQUIRED", "reason": response.get("_error") or parse_error or "AI sense response unavailable", "clusters": []}
    decision = str(parsed.get("decision") or "REVIEW_REQUIRED").upper()
    if decision == "SINGLE_SENSE":
        return {**base, "decision": "SINGLE_SENSE", "reason": str(parsed.get("reason") or "AI found one semantic sense"), "confidence": _confidence(parsed), "clusters": [_single_cluster(context, reason="AI single-sense decision")]}
    if decision != "MULTI_SENSE":
        return {**base, "decision": "REVIEW_REQUIRED", "reason": str(parsed.get("reason") or "AI requested manual review"), "confidence": _confidence(parsed), "clusters": []}
    clusters = _validate_ai_clusters(parsed.get("clusters"), context)
    if len(clusters) < 2:
        return {**base, "decision": "REVIEW_REQUIRED", "reason": "AI multi-sense response did not provide two valid evidence-backed clusters", "confidence": _confidence(parsed), "clusters": []}
    return {**base, "decision": "MULTI_SENSE", "reason": str(parsed.get("reason") or "AI found multiple evidence-backed senses"), "confidence": _confidence(parsed), "clusters": clusters}


def _project_group(context: Mapping[str, Any], decision: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    output: list[dict[str, Any]] = []
    clusters_out: list[dict[str, Any]] = []
    if decision.get("decision") == "MULTI_SENSE":
        clusters = _assign_cluster_fields(context, decision.get("clusters") or [])
        for feature in context["features"]:
            feature_refs = _feature_evidence_refs(feature, context)
            relevant = [cluster for cluster in clusters if _cluster_relevant(cluster, feature_refs)]
            if not relevant:
                # Never delete a feature because a sense could not be assigned.
                relevant = [_review_cluster(context, feature, "no cluster contained this feature's evidence")]
            for cluster in relevant:
                projected = _project_feature(feature, context, cluster, decision)
                output.append(projected)
        clusters_out.extend(clusters)
    elif decision.get("decision") == "REVIEW_REQUIRED":
        for feature in context["features"]:
            cluster = _review_cluster(context, feature, str(decision.get("reason") or "manual sense review required"))
            clusters_out.append(cluster)
            output.append(_project_feature(feature, context, cluster, decision))
    else:
        cluster = _assign_cluster_fields(context, (decision.get("clusters") or [_single_cluster(context, reason="default semantic sense")]))[0]
        clusters_out.append(cluster)
        for feature in context["features"]:
            output.append(_project_feature(feature, context, cluster, decision))
    return output, clusters_out


def _assign_cluster_fields(context: Mapping[str, Any], clusters: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    block_map = context["blocks"]
    result = [deepcopy(dict(cluster)) for cluster in clusters]
    for cluster in result:
        cluster.setdefault("defining_evidence_refs", [])
        cluster["defining_evidence_refs"] = sorted(set(str(x) for x in cluster["defining_evidence_refs"]))
        for field in SENSE_FIELDS:
            cluster.setdefault(_cluster_field(field), [])
        cluster["_all_refs"] = set(cluster["defining_evidence_refs"])

    for feature in context["features"]:
        for field in SENSE_FIELDS:
            for claim in _claims(feature.get(field)):
                refs = _claim_refs(claim)
                if not refs:
                    continue
                if len(result) == 1 and result[0].get("sense_label") == "DEFAULT":
                    result[0][_cluster_field(field)].extend(refs)
                    result[0]["_all_refs"].update(refs)
                    continue
                matching = []
                for cluster in result:
                    if set(refs) & set(cluster["defining_evidence_refs"]):
                        matching.append(cluster)
                        continue
                    labels = set()
                    claim_text=str(claim.get('text') or claim.get('sql') or claim.get('value') or '')
                    local_signals=_sense_signals(claim_text)
                    for ref in refs:
                        block = block_map.get(ref)
                        if block:
                            # An explicit local contrast can belong to the
                            # other role despite its broader ancestor title.
                            # Proven claim text outranks block/heading context.
                            labels.update(local_signals or _sense_signals(str(block.get('text') or ''))
                                          or _sense_signals(_block_text(block)))
                    if cluster.get("sense_label") in labels:
                        matching.append(cluster)
                if len(matching) == 1:
                    cluster = matching[0]
                    cluster[_cluster_field(field)].extend(refs)
                    cluster["_all_refs"].update(refs)
                elif len(matching) > 1:
                    cluster.setdefault("ambiguous_evidence_refs", []).extend(refs)
    for cluster in result:
        for field in SENSE_FIELDS:
            field_name = _cluster_field(field)
            cluster[field_name] = sorted(set(cluster.get(field_name) or []))
            cluster["_all_refs"].update(cluster[field_name])
        refs = sorted(cluster.pop("_all_refs", set()))
        cluster["evidence_refs"] = refs
        cluster["source_urls"] = sorted({str(block_map[x].get("source_url") or "") for x in refs if x in block_map and block_map[x].get("source_url")})
        cluster["owner_ids"] = sorted({str(owner) for x in refs if x in block_map for owner in block_map[x].get("owner_ids") or []})
        cluster["confidence"] = float(cluster.get("confidence") or 0.8)
        cluster["resolution_method"] = cluster.get("resolution_method") or "DETERMINISTIC"
        cluster["reason"] = cluster.get("reason") or "evidence-backed semantic sense"
    return result


def explicit_application_namesake(text: str, identity: str) -> bool:
    """Literal self-identification of an application-supplied namesake."""
    return bool(re.search(r'\b(?:application|user)\s+(?:supplies?|defines?|creates?|registers?)\b.{0,140}?'
        r'\b(?:user(?:[- ]defined)?|custom)\s+function\s+(?:called|named)\s+[\"\'`]?'
        +re.escape(identity)+r'(?![\w$])',text,re.I|re.S))


def _project_feature(feature: Mapping[str, Any], context: Mapping[str, Any], cluster: Mapping[str, Any], decision: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(feature))
    label = str(cluster.get("sense_label") or "DEFAULT")
    sense_id = str(cluster.get("sense_id") or _sense_id(context["key"], label))
    original_id = str(feature.get("feature_id") or "")
    result["feature_id"] = "feature:" + _hash({"feature_id": original_id, "sense_id": sense_id})[:24]
    result["parent_feature_id"] = original_id
    result["semantic_sense"] = label
    result["sense_id"] = sense_id
    result["sense_feature_key"] = f"{feature.get('normalized_identity') or context['key'][0]}|{feature.get('feature_type') or context['key'][1]}|{label}"
    result["sense_resolution"] = decision.get("resolution_method") or "DETERMINISTIC"
    result["sense_confidence"] = decision.get("confidence")
    result["sense_reason"] = decision.get("reason")
    is_multi = decision.get("decision") == "MULTI_SENSE"
    if is_multi:
        result["evidence_refs"] = list(cluster.get("evidence_refs") or [])
        result["sources"] = list(cluster.get("source_urls") or [])
        owners = result.get("owners") or []
        owner_ids = set(cluster.get("owner_ids") or [])
        result["owners"] = [owner for owner in owners if str(owner.get("owner_id") or "") in owner_ids] if owner_ids else []
    if decision.get("decision") == "REVIEW_REQUIRED":
        result["semantic_diagnostics"] = sorted(set((result.get("semantic_diagnostics") or []) + ["SENSE_REVIEW_REQUIRED"]))

    if is_multi:
        for field in SENSE_FIELDS:
            allowed = set(cluster.get(_cluster_field(field)) or [])
            if field in {"description", "return_type"} and not isinstance(result.get(field),list):
                value = result.get(field)
                result[field] = value if _claim_matches(value, allowed) else None
            else:
                result[field] = [item for item in (result.get(field) or []) if _claim_matches(item, allowed)]
    # Explicit application-defined namesakes are not built-in examples.
    # Only quarantine a literal claim that identifies the current subject as
    # an application/user-supplied function, never an incidental keyword.
    if context['key'][1]=='FUNCTION':
        for field in ['description','official_examples','notes']:
            value=result.get(field)
            kept=[]
            for claim in _claims(value):
                raw=str(claim.get('text') or claim.get('sql') or '')
                if explicit_application_namesake(raw,context['key'][0]):
                    result.setdefault('field_reviews',[]).append({'field':field,'status':'REVIEW_REQUIRED',
                        'reason':'EXPLICIT_APPLICATION_DEFINED_NAMESAKE','raw_text':raw,
                        'evidence_refs':_claim_refs(claim)})
                else:kept.append(claim)
            if isinstance(value,list):result[field]=kept
            elif value:result[field]=kept[0] if kept else None
    result.setdefault("provenance", {})
    result["provenance"] = {
        **(result.get("provenance") or {}),
        "semantic_sense": label,
        "sense_id": sense_id,
        "parent_feature_id": original_id,
        "sense_audit_version": SCHEMA_VERSION,
    }
    # Filtering changes content availability. Never leave an empty projection
    # labelled COMPLETE merely because the pre-sense feature was complete.
    if feature.get('semantic_status') in {'COMPLETE','PARTIAL','STRUCTURAL_ONLY'}:
        from agents.semantic_stage.grounding import semantic_status
        result['pre_sense_semantic_status']=feature.get('semantic_status')
        result['semantic_status']=semantic_status(result,result.get('semantic_diagnostics') or [])
    return result


def _build_review_pack(grouped, candidates, decisions, sense_features, sense_clusters):
    by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sense_features:
        by_parent[str(row.get("parent_feature_id") or row.get("feature_id") or "")].append(row)
    clusters_by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for cluster in sense_clusters:
        clusters_by_key[(str(cluster.get("normalized_identity") or ""), str(cluster.get("feature_type") or ""))].append(cluster)

    selected: list[tuple[tuple[str, str], str]] = []
    for key, decision in sorted(decisions.items()):
        if decision.get("decision") in {"MULTI_SENSE", "REVIEW_REQUIRED"}:
            selected.append((key, "MULTI_SENSE" if decision.get("decision") == "MULTI_SENSE" else "REVIEW_REQUIRED"))

    singles = [
        (key, "HIGH_RISK_SINGLE_SENSE")
        for key, decision in sorted(decisions.items())
        if decision.get("decision") == "SINGLE_SENSE"
    ]
    symbol_singles = [x for x in singles if _is_symbol_operator(x[0])]
    other_singles = [x for x in singles if not _is_symbol_operator(x[0])]
    other_singles.sort(key=lambda x: (0 if x[0] in {("ABS", "FUNCTION")} else 1, x[0]))
    # Every symbolic operator is reviewable; then fill the pack to at least
    # twenty single-sense high-risk rows when the corpus has that many.
    selected.extend(symbol_singles)
    remaining = max(0, 20 - len(symbol_singles))
    selected.extend(other_singles[:remaining])
    seen = set()
    rows = []
    for key, review_reason in selected:
        if key in seen:
            continue
        seen.add(key)
        context = grouped[key]
        feature_rows = []
        for feature in context["features"]:
            feature_rows.extend(by_parent.get(str(feature.get("feature_id") or ""), []))
        rows.append({
            "review_id": "sense_review:" + _hash({"key": key, "reason": review_reason})[:24],
            "normalized_identity": key[0],
            "feature_type": key[1],
            "review_reason": review_reason,
            "candidate": candidates.get(key),
            "decision": decisions[key],
            "definitions": context["definitions"],
            "source_blocks": _review_blocks(context, decisions[key]),
            "sense_aware_features": feature_rows,
            "cluster_count": len(clusters_by_key.get(key, [])),
        })
    return rows


def _build_summary(*, features, grouped, candidates, decisions, sense_features, sense_clusters, review_rows, llm_state, semantic_root, identity_root, model):
    decision_counts = Counter(str(x.get("decision") or "UNKNOWN") for x in decisions.values())
    type_multi = Counter()
    for decision in decisions.values():
        if decision.get("decision") == "MULTI_SENSE":
            type_multi[str(decision.get("feature_type") or "UNCERTAIN")] += 1
    split_count = sum(1 for x in decisions.values() if x.get("decision") == "MULTI_SENSE")
    return {
        "schema_version": SCHEMA_VERSION,
        "pipeline_id": "generic-semantic-sense-audit/v1",
        "input_semantic_features": len(features),
        "semantic_identity_type_keys": len(grouped),
        "deterministic_candidates": len(candidates),
        "single_sense": decision_counts.get("SINGLE_SENSE", 0),
        "multi_sense": decision_counts.get("MULTI_SENSE", 0),
        "review_required": decision_counts.get("REVIEW_REQUIRED", 0),
        "features_split": split_count,
        "new_sense_total": len(sense_features),
        "sense_cluster_total": len(sense_clusters),
        "multi_sense_by_feature_type": dict(sorted(type_multi.items())),
        "review_pack_total": len(review_rows),
        "llm": {
            "requested": bool(llm_state.get("requested")),
            "ready": bool(llm_state.get("ready")),
            "model": model,
            "calls": int(llm_state.get("calls", 0)),
            "single": int(llm_state.get("single", 0)),
            "multi": int(llm_state.get("multi", 0)),
            "unresolved": int(llm_state.get("unresolved", 0)),
            "reason": llm_state.get("reason"),
        },
        "integrity": {
            "discovery_run": False,
            "identity_rerun": False,
            "semantic_extraction_rerun": False,
            "identity_modified_by_llm": _identity_or_type_changed(features, sense_features, "normalized_identity"),
            "feature_type_modified_by_llm": _identity_or_type_changed(features, sense_features, "feature_type"),
            "canonical_run": False,
            "frontend_run": False,
            "legacy_profile_used": False,
            "formal_pipeline_used": False,
            "db_specific_logic_used": False,
            "inventory_substitution_used": False,
            "input_features_retained": {
                "value": {str(x.get("feature_id") or "") for x in features} <= {
                    str(x.get("parent_feature_id") or x.get("feature_id") or "") for x in sense_features
                },
                "input_count": len(features),
                "projected_parent_count": len({str(x.get("parent_feature_id") or x.get("feature_id") or "") for x in sense_features}),
            },
            "evidence_leakage_detected": _has_evidence_leakage(sense_features),
        },
        "inputs": {
            "semantic_root": str(semantic_root),
            "identity_root": str(Path(identity_root).resolve()) if identity_root else None,
        },
    }


def _group_features(features, bundle_by_group, bundle_by_key):
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for feature in features:
        key = _feature_key(feature)
        context = grouped.setdefault(key, {
            "key": key,
            "features": [],
            "feature_ids": [],
            "identity_group_ids": [],
            "definitions": [],
            "blocks": {},
            "source_urls": set(),
        })
        context["features"].append(feature)
        context["feature_ids"].append(str(feature.get("feature_id") or ""))
        group_id = str(feature.get("identity_group_id") or "")
        if group_id:
            context["identity_group_ids"].append(group_id)
        bundle = bundle_by_group.get(group_id)
        if bundle is None:
            alternatives = bundle_by_key.get(key) or []
            bundle = alternatives[0] if alternatives else None
        if not bundle:
            continue
        for block in bundle.get("source_blocks") or []:
            ref = str(block.get("evidence_ref") or block.get("block_id") or "")
            if ref:
                context["blocks"].setdefault(ref, dict(block))
                if block.get("source_url"):
                    context["source_urls"].add(str(block.get("source_url")))
        for item in bundle.get("defining_evidence") or []:
            ref = str(item.get("evidence_ref") or "")
            if not ref:
                continue
            block = context["blocks"].get(ref, {})
            context["definitions"].append({
                **dict(item),
                "feature_id": str(feature.get("feature_id") or ""),
                # Definition signals must come from the definition/table row
                # itself.  Heading ancestry is useful context for assigning
                # claims later, but a catalog heading such as "Assignment
                # Operators" must not turn a generic "Name Description"
                # table header into a second semantic sense.
                "combined_text": " ".join(x for x in [
                    str(item.get("raw_text") or ""),
                    str(block.get("text") or block.get("raw_text") or ""),
                    " ".join(str(x) for x in block.get("table_headers") or []),
                ] if x),
                "block_text": block.get("text", ""),
                "heading_ancestry": block.get("heading_ancestry") or [],
                "source_url": item.get("source_url") or block.get("source_url"),
            })
    for context in grouped.values():
        context["feature_ids"] = sorted(set(context["feature_ids"]))
        context["identity_group_ids"] = sorted(set(context["identity_group_ids"]))
        deduped = {}
        for definition in context["definitions"]:
            deduped.setdefault((definition.get("evidence_ref"), definition.get("raw_text")), definition)
        context["definitions"] = list(deduped.values())
        context["source_urls"] = set(context["source_urls"])
    return grouped


def _feature_key(value: Mapping[str, Any]) -> tuple[str, str]:
    from agents.contracts.identity_key import normalize_identity
    return (normalize_identity(value.get("normalized_identity") or value.get("canonical_identity") or value.get("name")), str(value.get("feature_type") or "UNCERTAIN").upper())


def _definition_fingerprint(definition: Mapping[str, Any]) -> str:
    text = str(definition.get("raw_text") or definition.get("block_text") or "")
    text = re.sub(r"\s+", " ", text).strip().casefold()
    return text


def _sense_signals(text: str) -> set[str]:
    return {label for label, pattern in _ROLE_MARKERS.items() if pattern.search(text or "")}


def _block_text(block: Mapping[str, Any]) -> str:
    return " ".join([
        str(block.get("text") or block.get("raw_text") or ""),
        " ".join(str(x) for x in block.get("heading_ancestry") or []),
        " ".join(str(x) for x in block.get("table_headers") or []),
    ]).strip()


def _cluster_from_refs(label: str, refs: list[str], context: Mapping[str, Any], *, reason: str) -> dict[str, Any]:
    return {
        "sense_id": _sense_id(context["key"], label),
        "sense_label": label,
        "identity": context["key"][0],
        "feature_type": context["key"][1],
        "defining_evidence_refs": sorted(set(refs)),
        "description_evidence_refs": [],
        "syntax_evidence_refs": [],
        "example_evidence_refs": [],
        "source_urls": [],
        "reason": reason,
        "confidence": 0.96,
        "resolution_method": "DETERMINISTIC",
    }


def _single_cluster(context: Mapping[str, Any], *, reason: str) -> dict[str, Any]:
    refs = sorted({str(x.get("evidence_ref")) for x in context["definitions"] if x.get("evidence_ref")})
    return {
        **_cluster_from_refs("DEFAULT", refs, context, reason=reason),
        "confidence": 0.98,
    }


def _review_cluster(context: Mapping[str, Any], feature: Mapping[str, Any], reason: str) -> dict[str, Any]:
    refs = _feature_evidence_refs(feature, context)
    return {
        "sense_id": _sense_id(context["key"], "DEFAULT"),
        "sense_label": "DEFAULT",
        "identity": context["key"][0],
        "feature_type": context["key"][1],
        "defining_evidence_refs": sorted(refs & {str(x.get("evidence_ref")) for x in context["definitions"]}),
        "description_evidence_refs": [],
        "syntax_evidence_refs": [],
        "example_evidence_refs": [],
        "source_urls": [],
        "evidence_refs": sorted(refs),
        "reason": reason,
        "confidence": 0.4,
        "resolution_method": "HUMAN_REVIEW",
    }


def _validate_ai_clusters(raw_clusters: Any, context: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(raw_clusters, list):
        return []
    allowed = {str(x.get("evidence_ref")) for x in context["definitions"] if x.get("evidence_ref")}
    result = []
    seen_labels = set()
    for raw in raw_clusters:
        if not isinstance(raw, Mapping):
            continue
        label = str(raw.get("sense_label") or "").strip().upper()
        refs = {str(x) for x in raw.get("defining_evidence_refs") or []}
        if not label or not refs or not refs <= allowed or label in seen_labels:
            return []
        seen_labels.add(label)
        result.append({
            **_cluster_from_refs(label, sorted(refs), context, reason="AI evidence-backed sense cluster"),
            "confidence": _confidence(raw),
            "resolution_method": "AI",
        })
    return result


def _cluster_relevant(cluster: Mapping[str, Any], feature_refs: set[str]) -> bool:
    return bool(feature_refs & (set(cluster.get("evidence_refs") or []) | set(cluster.get("defining_evidence_refs") or [])))


def _feature_evidence_refs(feature: Mapping[str, Any], context: Mapping[str, Any]) -> set[str]:
    refs = {str(x) for x in feature.get("evidence_refs") or []}
    for field in SENSE_FIELDS:
        for claim in _claims(feature.get(field)):
            refs.update(_claim_refs(claim))
    if not refs:
        refs = set(context["blocks"])
    return refs


def _claims(value: Any) -> list[Mapping[str, Any]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, list):
        return [x for x in value if isinstance(x, Mapping)]
    return []


def _claim_refs(value: Mapping[str, Any]) -> list[str]:
    raw = value.get("evidence_refs") or value.get("evidence_refs_ids") or []
    return sorted({str(x) for x in raw if str(x).strip()}) if isinstance(raw, list) else []


def _claim_matches(value: Any, allowed: set[str]) -> bool:
    if not value:
        return False
    claims = _claims(value)
    return bool(claims and set().union(*(_claim_refs(x) for x in claims)) & allowed)


def _cluster_field(field: str) -> str:
    return {
        "description": "description_evidence_refs",
        "formal_syntax": "syntax_evidence_refs",
        "official_examples": "example_evidence_refs",
        "generated_examples": "example_evidence_refs",
        "parameters": "parameter_evidence_refs",
        "return_type": "return_type_evidence_refs",
        "notes": "notes_evidence_refs",
    }[field]


def _sense_id(key: tuple[str, str], label: str) -> str:
    return "sense:" + _hash({"identity": key[0], "feature_type": key[1], "sense": label})[:24]


def _is_symbol_operator(key: tuple[str, str]) -> bool:
    return key[1] == "OPERATOR" and bool(_SYMBOL_RE.match(key[0].strip()))


def _review_blocks(context: Mapping[str, Any], decision: Mapping[str, Any]) -> list[dict[str, Any]]:
    refs = set()
    for cluster in decision.get("clusters") or []:
        refs.update(cluster.get("defining_evidence_refs") or [])
    if not refs:
        refs = {str(x.get("evidence_ref")) for x in context["definitions"] if x.get("evidence_ref")}
    return [context["blocks"][ref] for ref in sorted(refs) if ref in context["blocks"]]


def _make_llm_client(*, run_llm: bool, model: str | None, timeout: int, max_retries: int):
    model_name = model or os.environ.get("FEATURE_CRAWLER_LLM_MODEL", "") or "NOT_CONFIGURED"
    if not run_llm:
        return None, {"requested": False, "ready": False, "reason": "LLM_NOT_REQUESTED", "calls": 0}, model_name
    config = {
        "provider": "openai_compatible",
        "model": model or "",
        "temperature": 0.0,
        "timeout": timeout,
        "max_retries": max_retries,
        "system_prompt": (
            "You are a narrow semantic-sense classifier. Return only JSON. "
            "Use only the supplied official definition evidence. Never change identity or feature type."
        ),
    }
    client = LLMClientFactory.create(config)
    check = getattr(client, "_check_ready", None)
    reason = check() if callable(check) else None
    if reason:
        return None, {"requested": True, "ready": False, "reason": reason, "calls": 0}, model_name
    return client, {"requested": True, "ready": True, "reason": None, "calls": 0, "single": 0, "multi": 0, "unresolved": 0}, model_name


def _call_sense_llm(client: Any, candidate: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    prompt = _sense_prompt(candidate, context)
    try:
        return dict(client.call(prompt, str(candidate.get("candidate_id") or "sense")))
    except Exception as exc:
        return {"_status": "error", "_raw_response": "", "_error": f"{type(exc).__name__}: {exc}"}


def _sense_prompt(candidate: Mapping[str, Any], context: Mapping[str, Any]) -> str:
    definitions = []
    for item in context["definitions"]:
        definitions.append({
            "evidence_ref": item.get("evidence_ref"),
            "raw_text": item.get("raw_text"),
            "heading_ancestry": item.get("heading_ancestry"),
            "source_url": item.get("source_url"),
        })
    payload = {
        "identity": context["key"][0],
        "feature_type": context["key"][1],
        "candidate_reasons": candidate.get("candidate_reason"),
        "definition_evidence": definitions,
        "allowed_definition_refs": sorted({str(x.get("evidence_ref")) for x in context["definitions"] if x.get("evidence_ref")}),
    }
    schema = {
        "decision": "SINGLE_SENSE | MULTI_SENSE | REVIEW_REQUIRED",
        "reason": "short evidence-based reason",
        "confidence": 0.0,
        "clusters": [{
            "sense_label": "short semantic label",
            "defining_evidence_refs": ["only allowed refs"],
        }],
    }
    return "Classify whether the supplied official definitions express one meaning or multiple meanings for the same identity and type. Do not infer from examples alone. Every multi-sense cluster must contain at least one defining evidence ref, and refs may not be invented. Return ONLY JSON.\nINPUT:\n" + _json(payload) + "\nSCHEMA:\n" + _json(schema)


def _decision_row(key, context, candidate, decision):
    return {
        "schema_version": SCHEMA_VERSION,
        "normalized_identity": key[0],
        "feature_type": key[1],
        "feature_ids": context["feature_ids"],
        "identity_group_ids": context["identity_group_ids"],
        "candidate_id": candidate.get("candidate_id") if candidate else None,
        "candidate_reason": candidate.get("candidate_reason", []) if candidate else [],
        "decision": decision.get("decision"),
        "reason": decision.get("reason"),
        "confidence": decision.get("confidence"),
        "resolution_method": decision.get("resolution_method"),
        "clusters": decision.get("clusters", []),
        "model": decision.get("model"),
        "prompt_version": decision.get("prompt_version"),
        "response_hash": decision.get("response_hash"),
        "response_status": decision.get("response_status"),
        "raw_response": decision.get("raw_response"),
        "timestamp": decision.get("timestamp") or _now(),
    }


def _has_evidence_leakage(features: list[Mapping[str, Any]]) -> bool:
    claim_owners: dict[tuple[str, str], set[str]] = defaultdict(set)
    for feature in features:
        refs = set(str(x) for x in feature.get("evidence_refs") or [])
        claim_refs = set()
        for field in SENSE_FIELDS:
            for claim in _claims(feature.get(field)):
                claim_refs.update(_claim_refs(claim))
        if not claim_refs <= refs:
            return True
        parent = str(feature.get("parent_feature_id") or feature.get("feature_id") or "")
        sense = str(feature.get("sense_id") or feature.get("semantic_sense") or "")
        for ref in claim_refs:
            claim_owners[(parent, ref)].add(sense)
    return any(len(senses) > 1 for senses in claim_owners.values())


def _identity_or_type_changed(input_features: list[Mapping[str, Any]], projected: list[Mapping[str, Any]], field: str) -> bool:
    expected = {str(x.get("feature_id") or ""): str(x.get(field) or "") for x in input_features}
    for row in projected:
        parent = str(row.get("parent_feature_id") or row.get("feature_id") or "")
        if parent in expected and str(row.get(field) or "") != expected[parent]:
            return True
    return False


def _parse_json_object(raw: str) -> tuple[dict[str, Any], str | None]:
    if not raw.strip():
        return {}, "empty response"
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, str(exc)
    if not isinstance(value, dict):
        return {}, "response is not an object"
    return value, None


def _confidence(value: Mapping[str, Any]) -> float:
    try:
        return max(0.0, min(1.0, float(value.get("confidence", 0.5))))
    except (TypeError, ValueError):
        return 0.5


def _hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _write_review_markdown(path: Path, rows: list[Mapping[str, Any]]) -> None:
    lines = ["# Semantic Sense Review Pack", "", f"Review rows: {len(rows)}", ""]
    for row in rows:
        lines.extend([
            f"## {row.get('normalized_identity')} [{row.get('feature_type')}]",
            "",
            f"Review reason: `{row.get('review_reason')}`",
            f"Decision: `{(row.get('decision') or {}).get('decision')}` via `{(row.get('decision') or {}).get('resolution_method')}`",
            "",
            "### Defining evidence",
            "",
        ])
        for item in row.get("definitions") or []:
            lines.append(f"- `{item.get('evidence_ref')}`: {item.get('raw_text')}")
        lines.extend(["", "### Sense-aware rows", ""])
        for feature in row.get("sense_aware_features") or []:
            lines.append(f"- `{feature.get('sense_feature_key')}` status={feature.get('semantic_status')} evidence={feature.get('evidence_refs')}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_report(path: Path, summary: Mapping[str, Any], grouped, decisions) -> None:
    lines = [
        "# Semantic Sense Audit",
        "",
        "This is a shadow-only audit over frozen Semantic Features and Evidence Bundles.",
        "Discovery, Identity, Semantic Extraction, Canonicalization, and Frontend were not rerun.",
        "",
        "## Summary",
        "",
    ]
    for key in (
        "input_semantic_features", "deterministic_candidates", "single_sense", "multi_sense",
        "review_required", "features_split", "new_sense_total", "sense_cluster_total", "review_pack_total",
    ):
        lines.append(f"- {key}: {summary.get(key)}")
    lines.extend(["", "## Multi-sense decisions", ""])
    multi = [(key, decision) for key, decision in sorted(decisions.items()) if decision.get("decision") == "MULTI_SENSE"]
    if not multi:
        lines.append("- None")
    for key, decision in multi:
        lines.append(f"### {key[0]} [{key[1]}]")
        lines.append("")
        lines.append(f"- reason: {decision.get('reason')}")
        lines.append(f"- resolution: {decision.get('resolution_method')}")
        for cluster in decision.get("clusters") or []:
            lines.append(f"- {cluster.get('sense_label')}: {cluster.get('defining_evidence_refs')}")
        lines.append("")
    lines.extend(["## Representative cases", ""])
    representative: list[tuple[str, tuple[str, str]]] = []
    for label, predicate in (
        ("ABS repeated definitions", lambda key, context: key == ("ABS", "FUNCTION")),
        ("symbol operator", lambda key, context: key == ("=", "OPERATOR")),
        ("function overload", lambda key, context: key[0].upper() == "ST_X" or (key[1] == "FUNCTION" and any(len(x.get("variants") or []) > 1 and any(
            "," in str(v) or "[" in str(v) for v in x.get("variants") or []
        ) for x in context["features"]))),
        ("datatype variants", lambda key, context: key[0].upper() in {"DECIMAL", "VARCHAR"} or (key[1] == "DATATYPE" and any(len(x.get("variants") or []) > 1 for x in context["features"]))),
    ):
        match = next(((key, context) for key, context in sorted(grouped.items()) if predicate(key, context)), None)
        if match:
            representative.append((label, match[0]))
    for label, key in representative:
        context = grouped[key]
        decision = decisions[key]
        lines.extend([
            f"### {label}: {key[0]} [{key[1]}]",
            "",
            f"- variants: {sorted({str(v) for feature in context['features'] for v in feature.get('variants') or []})}",
            f"- decision: {decision.get('decision')} ({decision.get('resolution_method')})",
            f"- reason: {decision.get('reason')}",
        ])
        for item in context.get("definitions") or []:
            lines.append(f"- defining `{item.get('evidence_ref')}`: {item.get('raw_text')}")
        for cluster in decision.get("clusters") or []:
            lines.append(f"- sense `{cluster.get('sense_label')}` refs: {cluster.get('defining_evidence_refs')}")
        lines.append("")
    lines.extend([
        "## Required case checks",
        "",
        "- `=` is expected to split only when separate official definition evidence supports comparison and assignment.",
        "- Repeated ABS definitions, overload signatures, and datatype size/alias variants remain one DEFAULT sense unless defining semantics conflict.",
        "- A sense-aware row never copies a semantic claim whose evidence refs are outside that sense cluster.",
        "",
        "## Integrity",
        "",
    ])
    integrity = summary.get("integrity") or {}
    for key, value in integrity.items():
        lines.append(f"- {key}: {value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
