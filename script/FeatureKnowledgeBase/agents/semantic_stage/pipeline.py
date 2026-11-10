"""Identity-group Target Admission -> Semantic Feature pipeline.

The runner is deliberately offline with respect to Discovery.  It consumes
only the four authoritative JSONL artifacts from an Identity Quality run and
never falls back to the historical rule semantic extractor.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from agents.runtime_config import load_project_env
from agents.semantic_extractor.llm_client import LLMClientFactory
from agents.semantic_stage.evidence_bundle import build_evidence_bundle, build_indexes
from agents.semantic_stage.grounding import semantic_status, validate_semantic_output
from agents.semantic_stage.extractive_fragments import validate_extractive_fragments
from agents.semantic_stage.content_completeness import summary as content_summary
from agents.semantic_stage.target_admission import (
    TARGET_TYPES,
    apply_target_decision,
    deterministic_admission,
    group_status,
    target_classification_prompt,
)
from agents.semantic_stage.type_aware_target_gate import (
    derive_risk,
    normalize_confirmed_classifier_result,
    route_for_confirmed,
)


PROMPT_VERSION = "identity-group-semantic/v3-verified-field-consumption"
PIPELINE_ID = "generic-identity-semantic-stage/v1"
# Keep provider work bounded for unseen DBMS runs.  This is a runtime
# resource boundary only; it does not change routing, prompts, cache keys, or
# any semantic/admission decision.
SEMANTIC_PROVIDER_BATCH_SIZE = 32


def _materialize_semantic_response(
    group: Mapping[str, Any],
    admission: Mapping[str, Any],
    bundle: Mapping[str, Any],
    prompt: str,
    cache_key: str,
    response: Mapping[str, Any],
    model: str,
    config: Mapping[str, Any],
    workers: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Turn one provider response into the existing three semantic artifacts."""
    group_id = str(admission["identity_group_id"])
    input_hash = _hash({"cache_key": cache_key, "group_id": group_id, "bundle": bundle})
    raw = str(response.get("_raw_response") or "")
    response_hash = _hash(raw) if raw else None
    parsed: dict[str, Any] = {}
    parse_error: str | None = None
    diagnostics: list[str] = []
    execution_status = "COMPLETE"
    validation: dict[str, Any] = {
        "fields": _empty_fields(),
        "diagnostics": [],
        "claim_results": [],
        "accepted_claims": 0,
        "rejected_claims": 0,
        "invalid_evidence_refs": 0,
        "ungrounded_claims": 0,
    }
    not_run = False
    if response.get("_status") == "skipped":
        not_run = True
        error = str(response.get("_error") or "LLM_NOT_REQUESTED")
        if error == "LLM_NOT_REQUESTED":
            execution_status = "NOT_REQUESTED"
            diagnostics.append("LLM_NOT_REQUESTED")
        elif error == "LLM_BUDGET_EXHAUSTED":
            execution_status = "NOT_REQUESTED"
            diagnostics.append("LLM_BUDGET_EXHAUSTED")
        else:
            execution_status = "NOT_CONFIGURED"
            diagnostics.extend(["LLM_NOT_CONFIGURED", error])
    elif response.get("_status") == "error":
        execution_status = "CALL_FAILED"
        diagnostics.extend(["LLM_CALL_FAILED", str(response.get("_error") or "unknown LLM error")])
    elif response.get("_status") == "offline_evidence":
        # Provider-independent replay.  The parsed object was built only from
        # owner-local EvidenceBundle text and still goes through the same
        # grounding validator below.  This is intentionally distinct from an
        # LLM response so accounting never reports a provider call.
        parsed = dict(response.get("_parsed") or {})
        diagnostics.append("OFFLINE_EVIDENCE_REUSE")
        raw = json.dumps(parsed, ensure_ascii=False, sort_keys=True)
        response_hash = _hash(raw)
        validation = validate_extractive_fragments(parsed, bundle)
        diagnostics.extend(validation.get("diagnostics") or [])
    else:
        parsed, parse_error = _parse_json_object(raw)
        if parse_error:
            execution_status = "PARSE_FAILED"
            diagnostics.extend(["SEMANTIC_RESPONSE_PARSE_FAILED", parse_error])
        elif str(parsed.get("identity_group_id") or group_id) != group_id:
            execution_status = "PARSE_FAILED"
            diagnostics.append("IDENTITY_GROUP_ID_MISMATCH")
        else:
            validation = validate_extractive_fragments(parsed, bundle)
            diagnostics.extend(validation.get("diagnostics") or [])

    if not_run:
        status = "NOT_RUN"
        fields = _empty_fields()
    elif response.get("_status") == "error" or parse_error or execution_status=='PARSE_FAILED':
        status = "REVIEW_REQUIRED"
        fields = _empty_fields()
    else:
        fields = validation["fields"]
        status = semantic_status(fields, diagnostics)
        execution_status = status

    feature = _semantic_feature(
        group, admission, bundle, fields, status, diagnostics,
        response_hash=response_hash, execution_status=execution_status,
    )
    from agents.contracts.field_validation import make_field_contract
    field_contract=make_field_contract(group_id,fields)
    feature['field_validation_status']=('NOT_EVALUATED' if response.get('_status') in {'skipped','error'} or parse_error or execution_status=='PARSE_FAILED'
                                        else 'QUARANTINED' if validation.get('rejected_claims') or bundle.get('field_source_quarantine') else 'PASS')
    feature['source_quarantine']=[{k:r[k] for k in ('block_id','reason','status')} for r in bundle.get('field_source_quarantine',[])]
    feature['semantic_execution_state']=execution_status
    feature['rejected_fields']=[dict(r) for r in validation.get('claim_results',[]) if r['status']!='ACCEPTED']
    feature['field_consumption']=[{k:r.get(k) for k in ('fragment_id','block_id','field','status','reason')}
        for r in (parsed.get('semantic_extensions') or {}).get('verified_field_consumption',[])]
    # Field quarantine is separate from feature existence and accepted claims.
    # Keep only source-resolvable review entries, never provider-invented text.
    by_ref={str(b.get('evidence_ref') or b.get('block_id')):b for b in bundle.get('source_blocks',[])}
    feature['field_reviews']=[r for r in (parsed.get('semantic_extensions') or {}).get('uncertain_fields',[])
        if r.get('status')=='REVIEW_REQUIRED' and r.get('evidence_refs')
        and all(ref in by_ref and r.get('raw_text')==by_ref[ref].get('text') for ref in r['evidence_refs'])]
    from agents.semantic_stage.content_completeness import audit_semantic_content
    feature['content_completeness']=audit_semantic_content(prompt,fields)
    raw_row = {
        "identity_group_id": group_id,
        "feature_id": feature["feature_id"],
        "prompt_version": PROMPT_VERSION,
        "model": model,
        "temperature": 0.0,
        "config": dict(config),
        "workers": workers,
        "input_hash": input_hash,
        "cache_key": cache_key,
        "prompt": prompt,
        "response_status": response.get("_status", "not_run"),
        "execution_status": execution_status,
        "raw_response": raw or None,
        "response_hash": response_hash,
        "error": response.get("_error"),
        "parsed": parsed or None,
        "timestamp": _now(),
    }
    validation_row = {
        "identity_group_id": group_id,
        "feature_id": feature["feature_id"],
        "semantic_status": status,
        "execution_status": execution_status,
        "accepted_claims": validation.get("accepted_claims", 0),
        "rejected_claims": validation.get("rejected_claims", 0),
        "invalid_evidence_refs": validation.get("invalid_evidence_refs", 0),
        "ungrounded_claims": validation.get("ungrounded_claims", 0),
        "diagnostics": diagnostics,
        "claim_results": validation.get("claim_results", []),
        "content_completeness": feature['content_completeness'],
        "response_hash": response_hash,
        "field_isolation": field_contract,
        "semantic_execution_state": execution_status,
        "field_validation_status":feature['field_validation_status'],
    }
    return feature, raw_row, validation_row


def _offline_evidence_response(group_id: str, bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Build a strictly extractive semantic payload from a frozen bundle.

    This is a generic provider-unavailable replay mechanism.  It does not
    infer a description, return type, or example from model knowledge: every
    value is copied from an owner-local evidence item and is then checked by
    ``validate_semantic_output`` exactly like a provider response.
    """

    def ref(item: Mapping[str, Any]) -> str:
        return str(item.get("evidence_ref") or item.get("block_id") or "")

    def text(item: Mapping[str, Any]) -> str:
        return str(item.get("raw_text") or item.get("text") or "").strip()

    def items(key: str) -> list[Mapping[str, Any]]:
        return [
            item for item in bundle.get(key) or []
            if isinstance(item, Mapping) and ref(item) and text(item)
            and bool(item.get("owner_local_binding", True))
        ]

    definitions = items("definition_evidence")
    syntaxes = items("formal_syntax_evidence")
    parameters = items("parameter_evidence")
    examples = items("official_example_evidence")

    parsed: dict[str, Any] = {
        "identity_group_id": group_id,
        "description": None,
        "formal_syntax": [
            {"text": text(item), "evidence_refs": [ref(item)]}
            for item in syntaxes
        ],
        "parameters": [],
        # A generic return-value paragraph is not necessarily a declared type.
        # Keep it in the evidence bundle rather than publishing a false type.
        "return_type": None,
        "notes": [],
        "official_examples": [
            {"sql": text(item), "evidence_refs": [ref(item)]}
            for item in examples
        ],
        "generated_examples": [],
    }
    if definitions:
        item = definitions[0]
        parsed["description"] = {"text": text(item), "evidence_refs": [ref(item)]}

    for item in parameters:
        value = text(item)
        # ClickHouse and other documentation generators commonly use an em
        # dash, en dash, or colon between a parameter name and its prose.  The
        # split is structural and the validator still grounds both fragments
        # against the same frozen block.
        match = re.match(r"^\s*(.+?)\s+(?:—|–|-|:)\s+(.+?)\s*$", value)
        if match:
            name, description = match.groups()
            parsed["parameters"].append({
                "name": name.strip(), "description": description.strip(),
                "evidence_refs": [ref(item)],
            })
        else:
            parsed["parameters"].append({
                "name": value, "evidence_refs": [ref(item)],
            })
    return {"_status": "offline_evidence", "_parsed": parsed, "_error": None}


def run_semantic_stage(
    identity_root: str | Path,
    output_root: str | Path,
    *,
    evidence_root: str | Path | None = None,
    run_llm: bool = False,
    provider: str = "openai_compatible",
    model: str | None = None,
    timeout: int = 60,
    max_retries: int = 2,
    workers: int = 8,
    semantic_budget: int | None = None,
    resume: bool = False,
    retry_failed: bool = False,
    offline_evidence_fallback: bool = False,
) -> dict[str, Any]:
    """Run the semantic stage from a frozen Identity Quality run.

    ``run_llm=False`` is a first-class mode: it writes target decisions,
    evidence bundles, prompt payloads, and structural Semantic Features with
    ``NOT_RUN``.  It never creates deterministic semantic placeholders.
    """
    identity_root = Path(identity_root).resolve()
    output_root = Path(output_root).resolve()
    if not identity_root.is_dir():
        raise FileNotFoundError(f"Identity Quality root not found: {identity_root}")
    if output_root.exists() and any(output_root.iterdir()) and not resume:
        raise ValueError(f"semantic-stage output must be new and empty: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)

    groups = _read_jsonl(identity_root / "identity" / "groups.jsonl")
    occurrences = _read_jsonl(identity_root / "identity" / "occurrences.jsonl")
    owners = _read_jsonl(identity_root / "owners" / "owners.jsonl")
    documents = _read_jsonl(identity_root / "document_ir" / "documents.jsonl")
    typed_path = identity_root / "type" / "typed_feature_evidence.jsonl"
    type_path = identity_root / "type" / "type_decisions.jsonl"
    type_decisions = {str(r.get("identity_unit_id") or ""): r for r in
                      (_read_jsonl(type_path) if type_path.is_file() else [])}
    typed_rows = _read_jsonl(typed_path) if typed_path.is_file() else []
    if not groups:
        raise ValueError(f"No identity groups found under {identity_root}")
    profile_path=next((p for p in (identity_root/'presemantic'/'profile'/'generated_profile.json',
                                  identity_root/'profile'/'generated_profile.json') if p.is_file()),None)
    definition_profile=json.loads(profile_path.read_text(encoding='utf-8')) if profile_path else None
    indexes = build_indexes(groups, occurrences, owners, documents,profile=definition_profile)
    def source_bundle(group):
        from agents.contracts.source_binding import isolate_optional_sources
        return isolate_optional_sources(group,build_evidence_bundle(group,indexes=indexes),owners,documents,indexes['source_structure'])
    owners_by_id = {str(row.get("owner_id") or ""): row for row in owners}
    typed_by_group: dict[str, list[dict[str, Any]]] = {}
    for row in typed_rows:
        typed_by_group.setdefault(str(row.get("identity_unit_id") or ""), []).append(row)
    risk_by_group = {
        str(group.get("identity_unit_id") or ""): derive_risk(
            group, owners_by_id, typed_by_group.get(str(group.get("identity_unit_id") or ""), [])
        )
        for group in groups
    }
    from agents.contracts.evidence_cache import reuse_contracts,seal_bundle
    expected_reuse=reuse_contracts(groups,owners,documents,definition_profile)
    reuse_audit=[]
    reusable_bundles = _load_reusable_bundles(evidence_root, groups,expected=expected_reuse,audit=reuse_audit)
    _write_jsonl(output_root/'evaluation'/'evidence_reuse.jsonl',reuse_audit)
    evidence_reuse = {
        "requested": bool(evidence_root),
        "root": str(Path(evidence_root).resolve()) if evidence_root else None,
        "loaded": len(reusable_bundles),
        "reused": 0,
        "rebuilt": 0,
    }

    llm_client, llm_state, model_name = _make_llm_client(
        run_llm=run_llm,
        provider=provider,
        model=model,
        timeout=timeout,
        max_retries=max_retries,
    )
    budget_total = None if semantic_budget is None else max(0, int(semantic_budget))
    budget_remaining = budget_total
    budget_accounting: dict[str, Any] = {
        "provider_call_budget_total": budget_total,
        "classification_calls": 0,
        "classification_calls_total": 0,
        "confirmed_direct_semantic": 0,
        "probable_classification_calls": 0,
        "uncertain_classification_calls": 0,
        "semantic_extraction_calls": 0,
        "repair_retry_calls": 0,
        "provider_calls_total": 0,
        "provider_calls_succeeded": 0,
        "provider_calls_failed": 0,
        "features_semantic_attempted": 0,
        "features_semantic_completed": 0,
        "route_counts": {},
        "target_cache_hits": 0,
        "semantic_cache_hits": 0,
        "resume": bool(resume),
        "retry_failed": bool(retry_failed),
    }

    def execute_jobs(jobs: list[tuple[Any, ...]], stage: str) -> list[dict[str, Any]]:
        """Execute bounded provider calls and account for their stage."""
        nonlocal budget_remaining
        if llm_client is None or not llm_state.get("ready"):
            return [
                {"_status": "skipped", "_raw_response": "", "_error": llm_state.get("reason")}
                for _ in jobs
            ]
        if budget_remaining is not None and budget_remaining <= 0:
            return [
                {"_status": "skipped", "_raw_response": "", "_error": "LLM_BUDGET_EXHAUSTED"}
                for _ in jobs
            ]
        allowed_count = len(jobs) if budget_remaining is None else min(len(jobs), budget_remaining)
        allowed = jobs[:allowed_count]
        skipped = jobs[allowed_count:]
        if budget_remaining is not None:
            budget_remaining -= allowed_count
        if stage == "classification":
            budget_accounting["classification_calls"] += allowed_count
        elif stage == "semantic_extraction":
            budget_accounting["semantic_extraction_calls"] += allowed_count
        else:
            raise ValueError(f"Unknown semantic provider stage: {stage}")
        budget_accounting["provider_calls_total"] += allowed_count
        if len(allowed) > 1:
            with ThreadPoolExecutor(max_workers=max(1, int(workers))) as executor:
                responses = list(executor.map(
                    lambda job: _call_llm(llm_client, job[3], job[1]["identity_group_id"], llm_state),
                    allowed,
                ))
        else:
            responses = [
                _call_llm(llm_client, job[3], job[1]["identity_group_id"], llm_state)
                for job in allowed
            ]
        responses.extend(
            {"_status": "skipped", "_raw_response": "", "_error": "LLM_BUDGET_EXHAUSTED"}
            for _ in skipped
        )
        for response in responses[:allowed_count]:
            if response.get("_status") == "ok":
                budget_accounting["provider_calls_succeeded"] += 1
            elif response.get("_status") == "error":
                budget_accounting["provider_calls_failed"] += 1
            try:
                budget_accounting["repair_retry_calls"] += max(0, int(response.get("_retry_count") or 0))
            except (TypeError, ValueError):
                pass
        if stage == "classification":
            for job, response in zip(allowed, responses[:allowed_count]):
                if response.get("_status") not in {"ok", "error"}:
                    continue
                status = group_status(job[0])
                budget_accounting["classification_calls_total"] += 1
                if status == "PROBABLE_FEATURE":
                    budget_accounting["probable_classification_calls"] += 1
                elif status == "UNCERTAIN_FEATURE":
                    budget_accounting["uncertain_classification_calls"] += 1
        return responses

    admission_rows: list[dict[str, Any]] = []
    uncertain_rows: list[dict[str, Any]] = []
    admitted_groups: list[tuple[Mapping[str, Any], dict[str, Any]]] = []
    provisional_bundles: dict[str, dict[str, Any]] = {}
    admission_by_group: dict[str, dict[str, Any]] = {}
    pending_targets: list[tuple[Mapping[str, Any], dict[str, Any], dict[str, Any], str, str, str, str]] = []
    route_decisions: list[dict[str, Any]] = []

    existing_admissions = {
        str(row.get("identity_group_id") or ""): row
        for row in (_read_jsonl(output_root / "target" / "admission.jsonl") if resume and (output_root / "target" / "admission.jsonl").is_file() else [])
    }
    existing_target_audits = {
        str(row.get("identity_group_id") or ""): row
        for row in (_read_jsonl(output_root / "target" / "target_classification.jsonl") if resume and (output_root / "target" / "target_classification.jsonl").is_file() else [])
    }

    for group in sorted(groups, key=lambda x: str(x.get("identity_unit_id") or "")):
        group_id = str(group.get("identity_unit_id") or "")
        status = group_status(group)
        admission = deterministic_admission(group)
        risk = risk_by_group.get(group_id) or {
            "identity_unit_id": group_id, "feature_type": admission.get("feature_type"),
            "risk_stratum": "AMBIGUOUS_CONFIRMED", "risk_reason_codes": ["RISK_NOT_AVAILABLE"],
        }
        if status == "CONFIRMED_FEATURE":
            route = route_for_confirmed(group, risk)
        elif status == "PROBABLE_FEATURE":
            route = "CLASSIFY_PROBABLE"
        elif status == "UNCERTAIN_FEATURE" or admission.get("feature_type") == "UNCERTAIN":
            route = "CLASSIFY_UNCERTAIN"
        elif status == "NON_FEATURE_ONLY":
            route = "STOP_NON_FEATURE"
        else:
            route = "REVIEW_UNSUPPORTED_STATUS"
        budget_accounting["route_counts"][route] = budget_accounting["route_counts"].get(route, 0) + 1

        admission.update({
            "risk_stratum": risk.get("risk_stratum"),
            "risk_reason_codes": list(risk.get("risk_reason_codes") or []),
            "risk_reason_support": risk.get("risk_reason_support") or {},
            "route": route,
        })
        route_decisions.append({
            "identity_group_id": group_id,
            "identity": admission.get("identity"),
            "group_status": status,
            "feature_type": admission.get("feature_type"),
            "risk_stratum": risk.get("risk_stratum"),
            "risk_reason_codes": list(risk.get("risk_reason_codes") or []),
            "route": route,
            "classifier_required": route.startswith("CLASSIFY_"),
            "semantic_direct": route == "DIRECT_STRONG_FUNCTION",
        })

        if route == "DIRECT_STRONG_FUNCTION":
            admission.update({
                "semantic_ready": True,
                "admission_status": "ADMITTED",
                "target_resolution": "DIRECT_STRONG",
                "target_classification": "TARGET",
                "target_classification_type": "FUNCTION",
                "reason": "strong confirmed function skips target classifier",
            })
            admission_by_group[group_id] = admission
            continue
        if route in {"STOP_NON_FEATURE", "REVIEW_UNSUPPORTED_STATUS"}:
            admission["reason"] = "identity group is not a semantic target" if route == "STOP_NON_FEATURE" else "unsupported target gate status/type"
            admission_by_group[group_id] = admission
            continue

        bundle = source_bundle(group)
        # Do not retain every large owner-local bundle in memory.  The
        # semantic phase rebuilds the same deterministic bundle from the
        # frozen indexes after Target Admission; the target prompt only needs
        # the current batch's bundle.
        prompt = target_classification_prompt(group, bundle, type_decision=type_decisions.get(group_id))
        cache_key = _cache_key(
            "target_classification", group_id, admission.get("feature_type"), bundle,
            PROMPT_VERSION, model_name,
        )
        # A confirmed group is classified through the existing target
        # contract, but its original TypeDecision remains authoritative.
        base = admission
        if status == "CONFIRMED_FEATURE":
            classifier_group = dict(group)
            classifier_group["group_status"] = "PROBABLE_FEATURE"
            base = deterministic_admission(classifier_group)
        base.update({
            "risk_stratum": risk.get("risk_stratum"),
            "risk_reason_codes": list(risk.get("risk_reason_codes") or []),
            "risk_reason_support": risk.get("risk_reason_support") or {},
            "route": route,
            "cache_key": cache_key,
        })

        cached = existing_admissions.get(group_id) if resume else None
        cache_reusable = bool(cached and cached.get("cache_key") == cache_key and cached.get("admission_status") != "PENDING_TARGET_CLASSIFICATION")
        if cache_reusable and not (retry_failed and cached.get("admission_status") == "REVIEW_REQUIRED"):
            reused = dict(cached)
            reused.update({
                "risk_stratum": risk.get("risk_stratum"),
                "risk_reason_codes": list(risk.get("risk_reason_codes") or []),
                "risk_reason_support": risk.get("risk_reason_support") or {},
                "route": route,
                "cache_key": cache_key,
            })
            admission_by_group[group_id] = reused
            if existing_target_audits.get(group_id):
                uncertain_rows.append(existing_target_audits[group_id])
            budget_accounting["target_cache_hits"] += 1
            continue
        pending_targets.append((group, base, bundle, prompt, route, cache_key, status))

    target_responses = execute_jobs(pending_targets, "classification")

    for (group, base, bundle, prompt, route, cache_key, status), response in zip(pending_targets, target_responses):
        raw = str(response.get("_raw_response") or "")
        response_hash = _hash(raw) if raw else None
        parsed, parse_error = _parse_json_object(raw)
        decision = str((parsed or {}).get("decision") or (parsed or {}).get("target") or "UNRESOLVED").upper()
        reason = str((parsed or {}).get("reason") or "")
        if response.get("_status") == "skipped":
            reason = response.get("_error") or "LLM target classification not configured"
        elif response.get("_status") == "error":
            reason = response.get("_error") or "LLM target classification failed"
        elif parse_error:
            reason = f"invalid target classification JSON: {parse_error}"
        applied = apply_target_decision(
            base, decision, reason, model=model_name,
            prompt_version=PROMPT_VERSION, response_hash=response_hash,
            resolution_evidence=(parsed or {}).get("resolution_evidence"),
        )
        if status == "CONFIRMED_FEATURE":
            admission = normalize_confirmed_classifier_result(group, base, applied, decision, reason)
        else:
            admission = applied
        if status=='CONFIRMED_FEATURE' and (response.get('_status') in {'skipped','error'} or parse_error):
            # Runtime unavailability is not evidence against an independently
            # proved identity. A real NON_TARGET result is never overridden.
            from agents.contracts.identity_credibility import evaluate_identity_credibility
            from agents.contracts.publication_integrity import evaluate_publication_integrity
            from agents.contracts.source_binding import projected_owners
            credibility=evaluate_identity_credibility(group,owners=owners,documents=documents,bundle=bundle,
                owner_index=projected_owners(group,bundle,owners_by_id))
            proposal={**base,'admission_status':'ADMITTED','semantic_ready':True,
                'target_resolution':'SOURCE_PROVEN_RUNTIME_UNAVAILABLE','target_classification':'TARGET'}
            proof=evaluate_publication_integrity(group,bundle,type_decisions.get(group['identity_unit_id']),proposal,
                typed_evidence=typed_by_group.get(group['identity_unit_id'],[]),
                source_identity_valid=bool(credibility.get('identity_credible')))
            if credibility.get('identity_credible') and proof['state']=='CONFIRMED':
                admission=proposal
                admission['reason']='Independently source-proven identity; classifier runtime unavailable'
                admission['target_runtime_state']=response.get('_status') or 'PARSE_FAILED'
        if applied.get("resolution_evidence"):
            admission["resolution_evidence"] = applied["resolution_evidence"]
        admission.update({
            "risk_stratum": (risk_by_group.get(str(group.get("identity_unit_id") or "")) or {}).get("risk_stratum"),
            "risk_reason_codes": list((risk_by_group.get(str(group.get("identity_unit_id") or "")) or {}).get("risk_reason_codes") or []),
            "route": route,
            "cache_key": cache_key,
        })
        uncertain_rows.append({
            "identity_group_id": admission["identity_group_id"],
            "identity": admission["identity"],
            "input_evidence_refs": admission["input_evidence_refs"],
            "previous_type": admission["previous_type"],
            "ai_decision": admission.get("ai_decision", "UNRESOLVED"),
            "reason": admission.get("reason"),
            "route": route,
            "risk_stratum": admission.get("risk_stratum"),
            "cache_key": cache_key,
            "model": model_name,
            "prompt_version": PROMPT_VERSION,
            "prompt": prompt,
            "raw_response": raw or None,
            "raw_response_hash": response_hash,
            "response_status": response.get("_status", "not_run"),
            "parse_error": parse_error,
            "timestamp": _now(),
        })
        admission_by_group[admission["identity_group_id"]] = admission

    for group in sorted(groups, key=lambda x: str(x.get("identity_unit_id") or "")):
        group_id = str(group.get("identity_unit_id") or "")
        admission = admission_by_group[group_id]
        # Preserve publication uncertainty separately from Semantic extractability.
        # A classifier repeating previous_type is not a second independent vote.
        from agents.contracts.publication_integrity import evaluate_publication_integrity
        publication_bundle = reusable_bundles.get(group_id)
        if publication_bundle is None:
            publication_bundle = source_bundle(group)
        integrity = evaluate_publication_integrity(
            group, publication_bundle, type_decisions.get(group_id), admission,
            typed_evidence=typed_by_group.get(group_id, []),
        )
        admission["publication_integrity"] = integrity
        admission["publication_target_state"] = integrity["state"]
        if admission.get("semantic_ready"):
            admitted_groups.append((group, admission))
        else:
            # Preserve the full admission record, including NON_FEATURE_ONLY.
            admission["source_group_status"] = group_status(group)
        admission_rows.append(admission)

    budget_accounting["confirmed_direct_semantic"] = sum(
        1 for row in admission_rows
        if str(row.get("group_status") or "") == "CONFIRMED_FEATURE"
        and bool(row.get("semantic_ready"))
    )

    _write_jsonl(output_root / "target" / "admission.jsonl", admission_rows)
    _write_jsonl(output_root / "target" / "uncertain_target_review.jsonl", uncertain_rows)
    _write_jsonl(output_root / "target" / "target_classification.jsonl", uncertain_rows)
    _write_jsonl(output_root / "routing" / "routing_decisions.jsonl", route_decisions)

    bundle_path = output_root / "evidence" / "bundles.jsonl"
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_stream = bundle_path.open("w", encoding="utf-8")

    existing_features = {
        str(row.get("identity_group_id") or ""): row
        for row in (_read_jsonl(output_root / "semantic" / "semantic_features.jsonl") if resume and (output_root / "semantic" / "semantic_features.jsonl").is_file() else [])
    }
    existing_raw = {
        str(row.get("identity_group_id") or ""): row
        for row in (_read_jsonl(output_root / "semantic" / "semantic_raw_responses.jsonl") if resume and (output_root / "semantic" / "semantic_raw_responses.jsonl").is_file() else [])
    }
    existing_validation = {
        str(row.get("identity_group_id") or ""): row
        for row in (_read_jsonl(output_root / "semantic" / "semantic_validation.jsonl") if resume and (output_root / "semantic" / "semantic_validation.jsonl").is_file() else [])
    }
    cached_features: list[dict[str, Any]] = []
    cached_raw: list[dict[str, Any]] = []
    cached_validation: list[dict[str, Any]] = []
    features: list[dict[str, Any]] = list(cached_features)
    raw_responses: list[dict[str, Any]] = list(cached_raw)
    validation_rows: list[dict[str, Any]] = list(cached_validation)
    semantic_jobs: list[tuple[Mapping[str, Any], dict[str, Any], dict[str, Any], str, str]] = []
    semantic_attempted_total = 0

    def process_semantic_batch(batch: list[tuple[Mapping[str, Any], dict[str, Any], dict[str, Any], str, str]]) -> None:
        nonlocal semantic_attempted_total
        if not batch:
            return
        if offline_evidence_fallback and (not run_llm or not llm_state.get("ready")):
            responses = [
                _offline_evidence_response(str(group.get("identity_group_id") or ""), bundle)
                for group, _admission, bundle, _prompt, _cache_key in batch
            ]
        else:
            responses = execute_jobs(batch, "semantic_extraction")
        semantic_attempted_total += sum(
            1 for response in responses
            if response.get("_status") in {"ok", "error", "offline_evidence"}
        )
        for (group, admission, bundle, prompt, cache_key), response in zip(batch, responses):
            feature, raw_row, validation_row = _materialize_semantic_response(
                group, admission, bundle, prompt, cache_key, response,
                model=model_name,
                config={"provider": provider, "timeout": timeout, "max_retries": max_retries},
                workers=workers,
            )
            features.append(feature)
            raw_responses.append(raw_row)
            validation_rows.append(validation_row)

    for group, admission in admitted_groups:
        group_id = admission["identity_group_id"]
        group_for_bundle = dict(group)
        group_for_bundle["feature_type"] = admission["feature_type"]
        bundle = reusable_bundles.get(group_id)
        if bundle is not None:
            # Keep the previously aggregated source evidence intact while
            # refreshing only the current admission metadata.
            bundle = dict(bundle)
            evidence_reuse["reused"] += 1
        else:
            bundle = source_bundle(group_for_bundle)
            evidence_reuse["rebuilt"] += 1
        bundle["feature_type"] = admission["feature_type"]
        bundle["group_status"] = admission["group_status"]
        bundle["admission"] = {
            "admission_status": admission["admission_status"],
            "target_resolution": admission["target_resolution"],
            "semantic_ready": admission["semantic_ready"],
            "route": admission.get("route"),
            "risk_stratum": admission.get("risk_stratum"),
            "risk_reason_codes": list(admission.get("risk_reason_codes") or []),
        }
        seal_bundle(bundle,expected_reuse[group_id])
        bundle_stream.write(json.dumps(bundle, ensure_ascii=False, sort_keys=True) + "\n")
        prompt = build_semantic_prompt(bundle, admission)
        cache_key = _cache_key(
            "semantic_extraction", group_id, admission.get("feature_type"), bundle,
            PROMPT_VERSION, model_name,
        )
        old_raw = existing_raw.get(group_id) if resume else None
        old_feature = existing_features.get(group_id) if resume else None
        reusable = bool(
            old_raw and old_feature and old_raw.get("cache_key") == cache_key
            and str(old_raw.get("response_status") or "") == "ok"
            and str(old_feature.get("execution_status") or "") in {
                "COMPLETE", "PARTIAL", "STRUCTURAL_ONLY", "VALIDATION_FAILED",
            }
        )
        if reusable and not retry_failed:
            cached_features.append(old_feature)
            cached_raw.append(old_raw)
            if existing_validation.get(group_id):
                cached_validation.append(existing_validation[group_id])
            budget_accounting["semantic_cache_hits"] += 1
            continue
        semantic_jobs.append((group, admission, bundle, prompt, cache_key))
        if len(semantic_jobs) >= SEMANTIC_PROVIDER_BATCH_SIZE:
            process_semantic_batch(semantic_jobs)
            semantic_jobs = []
    process_semantic_batch(semantic_jobs)
    bundle_stream.close()
    # Reports and the existing semantic-stage contract still consume the
    # authoritative JSONL artifact.  Reloading here keeps the hot provider
    # loop bounded instead of retaining all bundles alongside all prompts.
    bundles = _read_jsonl(bundle_path)

    # Cached rows are appended after active rows above only through the same
    # lists; preserve deterministic identity order for downstream artifacts.
    features.sort(key=lambda row: str(row.get("identity_group_id") or ""))
    raw_responses.sort(key=lambda row: str(row.get("identity_group_id") or ""))
    validation_rows.sort(key=lambda row: str(row.get("identity_group_id") or ""))
    budget_accounting["features_semantic_attempted"] = semantic_attempted_total
    budget_accounting["features_semantic_completed"] = sum(
        1 for row in validation_rows
        if row.get("execution_status") in {"COMPLETE", "PARTIAL", "STRUCTURAL_ONLY"}
    )

    _write_jsonl(output_root / "semantic" / "semantic_features.jsonl", features)
    _write_jsonl(output_root / "semantic" / "semantic_raw_responses.jsonl", raw_responses)
    _write_jsonl(output_root / "semantic" / "profile_field_consumption.jsonl", [
        {'identity_group_id':r.get('identity_group_id'),**item}
        for r in raw_responses for item in
        (((r.get('parsed') or {}).get('semantic_extensions') or {}).get('profile_field_consumption',[]))])
    _write_jsonl(output_root / "semantic" / "verified_field_consumption.jsonl", [
        {'identity_group_id':r.get('identity_group_id'),**item}
        for r in raw_responses for item in
        (((r.get('parsed') or {}).get('semantic_extensions') or {}).get('verified_field_consumption',[]))])
    _write_jsonl(output_root / "semantic" / "semantic_validation.jsonl", validation_rows)
    _write_jsonl(output_root / "evaluation" / "content_completeness.jsonl", [
        {'feature_id':f['feature_id'],'identity_group_id':f['identity_group_id'],
         'name':f.get('name'),**f.get('content_completeness',{'status':'NOT_EVALUATED'})} for f in features])
    _write_jsonl(output_root / "semantic" / "semantic_results.jsonl", features)

    summary = _build_summary(
        groups=groups,
        admission_rows=admission_rows,
        uncertain_rows=uncertain_rows,
        bundles=bundles,
        features=features,
        validation_rows=validation_rows,
        llm_state=llm_state,
        model=model_name,
        identity_root=identity_root,
        evidence_reuse=evidence_reuse,
        workers=workers,
        semantic_budget=semantic_budget,
        budget_accounting=budget_accounting,
        offline_evidence_fallback=offline_evidence_fallback,
    )
    summary['provider_mode'] = 'LOCAL_DETERMINISTIC_EXTRACTIVE_ADAPTER' if provider == 'deterministic' else provider
    summary['actual_model_calls'] = 0 if provider == 'deterministic' else budget_accounting.get('provider_calls_total',0)
    summary['local_adapter_calls'] = budget_accounting.get('provider_calls_total',0) if provider == 'deterministic' else 0
    _write_json(output_root / "evaluation" / "semantic_summary.json", summary)
    review_rows = _build_review_pack(features, bundles, validation_rows, uncertain_rows)
    _write_jsonl(output_root / "evaluation" / "semantic_review_pack.jsonl", review_rows)
    _write_review_markdown(output_root / "evaluation" / "semantic_review_pack.md", review_rows)
    _write_report(output_root / "evaluation" / "semantic_report.md", summary, bundles, features, validation_rows,
                  uncertain_rows, identity_root)
    _write_json(output_root / "runtime" / "run_manifest.json", {
        "schema_version": "semantic-stage-run/v1",
        "pipeline_id": PIPELINE_ID,
        "identity_root": str(identity_root),
        "output_root": str(output_root),
        "input_group_count": len(groups),
        "llm_requested": run_llm,
        "llm_state": llm_state,
        "evidence_reuse": evidence_reuse,
        "workers": workers,
        "resume": bool(resume),
        "retry_failed": bool(retry_failed),
        "offline_evidence_fallback": bool(offline_evidence_fallback),
        "type_aware_target_gate": True,
        "cache_contract": "stage/group/type/evidence/prompt/model/v1",
        "budget_accounting": budget_accounting,
        "canonical_run": False,
        "frontend_run": False,
        "discovery_run": False,
        "summary_path": "evaluation/semantic_summary.json",
    })
    return {"status": "accepted", "summary": summary, "output_root": str(output_root)}


def finalize_semantic_stage(
    identity_root: str | Path,
    output_root: str | Path,
    *,
    evidence_root: str | Path | None = None,
    workers: int = 8,
) -> dict[str, Any]:
    """Write the evaluation/report artifacts from already completed outputs.

    This is intentionally non-LLM and non-Discovery.  It is useful when a
    completed remote run was interrupted only after its JSONL artifacts were
    written, as happened during a process-level report finalization failure.
    """
    identity_root = Path(identity_root).resolve()
    output_root = Path(output_root).resolve()
    groups = _read_jsonl(identity_root / "identity" / "groups.jsonl")
    admission_rows = _read_jsonl(output_root / "target" / "admission.jsonl")
    uncertain_rows = _read_jsonl(output_root / "target" / "uncertain_target_review.jsonl")
    bundles = _read_jsonl(output_root / "evidence" / "bundles.jsonl")
    features = _read_jsonl(output_root / "semantic" / "semantic_features.jsonl")
    validation_rows = _read_jsonl(output_root / "semantic" / "semantic_validation.jsonl")
    raw_responses = _read_jsonl(output_root / "semantic" / "semantic_raw_responses.jsonl")
    model = next((str(x.get("model") or "NOT_CONFIGURED") for x in raw_responses), "NOT_CONFIGURED")
    completed = sum(1 for x in raw_responses if x.get("response_status") == "ok")
    llm_state = {
        "requested": True,
        "ready": completed > 0,
        "reason": None if completed > 0 else "NO_COMPLETED_LLM_RESPONSES",
    }
    evidence_reuse = {
        "requested": bool(evidence_root),
        "root": str(Path(evidence_root).resolve()) if evidence_root else None,
        "loaded": len(bundles),
        "reused": len(bundles) if evidence_root else 0,
        "rebuilt": 0,
    }
    summary = _build_summary(
        groups=groups,
        admission_rows=admission_rows,
        uncertain_rows=uncertain_rows,
        bundles=bundles,
        features=features,
        validation_rows=validation_rows,
        llm_state=llm_state,
        model=model,
        identity_root=identity_root,
        evidence_reuse=evidence_reuse,
        workers=workers,
    )
    _write_json(output_root / "evaluation" / "semantic_summary.json", summary)
    review_rows = _build_review_pack(features, bundles, validation_rows, uncertain_rows)
    _write_jsonl(output_root / "evaluation" / "semantic_review_pack.jsonl", review_rows)
    _write_review_markdown(output_root / "evaluation" / "semantic_review_pack.md", review_rows)
    _write_report(output_root / "evaluation" / "semantic_report.md", summary, bundles, features,
                  validation_rows, uncertain_rows, identity_root)
    _write_json(output_root / "runtime" / "run_manifest.json", {
        "schema_version": "semantic-stage-run/v1",
        "pipeline_id": PIPELINE_ID,
        "identity_root": str(identity_root),
        "output_root": str(output_root),
        "input_group_count": len(groups),
        "llm_requested": True,
        "llm_state": llm_state,
        "evidence_reuse": evidence_reuse,
        "workers": workers,
        "canonical_run": False,
        "frontend_run": False,
        "discovery_run": False,
        "finalized_from_existing_artifacts": True,
        "summary_path": "evaluation/semantic_summary.json",
    })
    return {"status": "accepted", "summary": summary, "output_root": str(output_root)}


def build_semantic_prompt(bundle: Mapping[str, Any], admission: Mapping[str, Any]) -> str:
    blocks = []
    by_ref: dict[str, dict[str, Any]] = {}
    physical_sources={str(b.get('block_id') or b.get('evidence_ref')):b for b in bundle.get('source_blocks',[])}

    def add_evidence(item: Mapping[str, Any], *, role: str | None = None) -> None:
        # Ranking rows are projections, not replacement physical evidence.
        # Recover source metadata before the first insertion so a lightweight
        # main row cannot silently strip heading level or slot provenance.
        physical=physical_sources.get(str(item.get('block_id') or item.get('evidence_ref')))
        if physical is not None:item={**item,**physical}
        if item.get('evidence_usage')=='DISCOVERY_ONLY':return
        """Project owner-local structural evidence without dropping its role.

        ``main_evidence`` is an intentionally compact ranking of the owner
        blocks.  Parameter and return evidence are separate contract buckets,
        so relying on ``main_evidence`` alone silently made those optional
        fields unavailable to the semantic provider.  This projection keeps
        the buckets in the same prompt and carries their structural role even
        when the block was already selected into ``main_evidence``.
        """
        ref = str(item.get("evidence_ref") or item.get("block_id") or "")
        if not ref:
            return
        marker = str(role or item.get("semantic_role") or item.get("role") or "").upper()
        existing = by_ref.get(ref)
        if existing is not None:
            if item.get('shared_definition_context'):
                existing['shared_definition_context']=True
            if item.get('structure_field_roles'):
                existing['structure_field_roles']=list(item['structure_field_roles'])
            if marker:
                existing_roles = list(existing.get("structural_roles") or [])
                if marker not in existing_roles:
                    existing_roles.append(marker)
                existing["structural_roles"] = sorted(set(existing_roles))
                existing_occurrence_roles = list(existing.get("occurrence_roles") or [])
                prompt_marker = {
                    "PARAMETER": "PARAMETER_INFO",
                    "RETURN_TYPE": "TYPE_OR_RETURN_INFO",
                }.get(marker, marker)
                if prompt_marker not in existing_occurrence_roles:
                    existing_occurrence_roles.append(prompt_marker)
                existing["occurrence_roles"] = sorted(set(existing_occurrence_roles))
                existing["evidence_role"] = marker
            return
        prompt_marker = {
            "PARAMETER": "PARAMETER_INFO",
            "RETURN_TYPE": "TYPE_OR_RETURN_INFO",
        }.get(marker, marker)
        projected = {
            "evidence_ref": ref,
            "source_url": item.get("source_url"),
            "block_kind": item.get("block_kind"),
            "heading_ancestry": item.get("heading_ancestry", []),
            "table_headers": item.get("table_headers", []),
            "section_contexts": item.get("section_contexts", []),
            "owner_ids": item.get("owner_ids", []),
            "source_order": item.get("source_order", 0),
            "structural_roles": list(item.get("structural_roles") or []),
            "occurrence_roles": list(item.get("occurrence_roles") or []),
            "semantic_role": item.get("semantic_role"),
            "text": item.get("text") or item.get("raw_text") or "",
        }
        # Additive input contract only. Preserve existing inferred roles and
        # output schema; observed containers/cells are not semantic claims.
        for key in ("block_id", "identity_unit_id", "page_id", "dom_path", "section_id", "heading_level",
                    "table_metadata", "inline_nodes", "observed_context", "table_context",
                    "source_structure_status", "source_hash", "contributing_owner_ids",
                    "literal_text", "source_text_origin",
                    "owner_local_binding", "content_applicability",
                    "definition_scope_status", "definition_section_proofs",
                    "definition_unit_ids", "definition_source_roles", "shared_definition_context",
                    "compound_definition_entry", "definition_entry_member", "structure_field_roles"):
            if key in item:
                projected[key] = item[key]
        if marker:
            projected["evidence_role"] = marker
            projected["structural_roles"] = sorted(set(projected["structural_roles"]) | {marker})
            projected["occurrence_roles"] = sorted(set(projected["occurrence_roles"]) | {prompt_marker})
        by_ref[ref] = projected
        blocks.append(projected)

    for item in (bundle.get("main_evidence") or [])[:80]:
        if isinstance(item, Mapping):
            add_evidence(item)
    # The large source unit is the retention boundary; member refs remain the
    # only claimable fine-grained evidence. Never flatten or import new text.
    definition_units=[]
    source_by_ref={str(b.get('block_id') or b.get('evidence_ref')):b for b in bundle.get('source_blocks',[])}
    missing_unit_refs=[]
    rejected_source_units=[]
    from agents.contracts.identity_key import normalize_identity
    subject_name=normalize_identity(admission.get('normalized_identity') or bundle.get('normalized_identity'))
    for unit in bundle.get('definition_units',[]):
        if (unit.get('status')!='VERIFIED_STRUCTURE' or subject_name not in unit.get('identity_values',[])
                or unit.get('identity_unit_id')!= (admission.get('identity_group_id') or bundle.get('identity_unit_id'))):
            rejected_source_units.append({'definition_unit_id':unit.get('definition_unit_id'),'reason':'SUBJECT_UNIT_OR_STRUCTURE_MISMATCH'})
            continue
        if unit.get('binding_status')!='COMPLETE':
            missing_unit_refs.extend(unit.get('missing_evidence_refs') or [])
            rejected_source_units.append({'definition_unit_id':unit.get('definition_unit_id'),'reason':'INCOMPLETE_DEFINITION_UNIT'})
            continue
        refs=unit.get('member_evidence_refs') or []
        # Revalidate transport at the actual Semantic input boundary.
        bad=[ref for ref in refs if ref not in source_by_ref or source_by_ref[ref].get('evidence_usage')=='DISCOVERY_ONLY'
             or source_by_ref[ref].get('noise_excluded_from_main')
             or source_by_ref[ref].get('source_url')!=unit.get('source_url')
             or source_by_ref[ref].get('source_hash')!=unit.get('source_hash')]
        if bad:
            missing_unit_refs.extend(bad);continue
        definition_units.append({k:unit.get(k) for k in (
            'definition_unit_id','source_role','identity_values','type_context','source_url','source_hash',
            'member_evidence_refs','structural_members','boundary','execution_plan','binding_status',
            'structure_execution','declarations')})
        definition_units[-1]['primary_identity']=(unit.get('source_proof') or {}).get('primary_identity')
        definition_units[-1]['definition_association']=(unit.get('source_proof') or {}).get('association')
    # Ranking budgets may truncate supplemental references, never an observed
    # definition of the current subject. In particular, a second overload can
    # rank below many catalog/usage references despite having a complete proof.
    required_definition_refs=[]
    required_catalog_refs=[]
    subject=str(admission.get('normalized_identity') or bundle.get('normalized_identity') or '').upper()
    unit_id=admission.get('identity_group_id') or bundle.get('identity_unit_id')
    for item in bundle.get('source_blocks') or []:
        bid=item.get('block_id') or item.get('evidence_ref')
        if item.get('noise_excluded_from_main') or item.get('evidence_usage')=='DISCOVERY_ONLY':continue
        if item.get('compound_definition_entry') or (item.get('definition_entry_member') or {}).get('subject')==subject:
            add_evidence(item)
            required_definition_refs.append(bid)
        if (item.get('content_applicability') or {}).get('identity')==subject:
            add_evidence(item)
            required_definition_refs.append(bid)
        if any(p.get('scope_status')=='COMPLETE' and subject in p.get('identity_values',[])
               and p.get('identity_unit_id') in (None,unit_id) and bid in p.get('block_ids',[])
               for p in item.get('definition_section_proofs') or []):
            add_evidence(item)
            required_definition_refs.append(bid)
        from agents.contracts.catalog_membership import catalog_membership
        if (item.get('owner_local_binding') is True and item.get('identity_unit_id') in (None,unit_id)
            and any(p['feature_type']==str(admission.get('feature_type') or bundle.get('feature_type') or '').upper()
                    for p in catalog_membership(item,subject))):
            add_evidence(item)
            required_catalog_refs.append(bid)
    for unit in definition_units:
        for ref in unit['member_evidence_refs']:add_evidence(source_by_ref[ref])
    # These are not inferred fields: the EvidenceBundle already established
    # the owner-local structural role.  Add them after ranked main evidence so
    # the provider can consume parameter/return evidence without changing any
    # admission or publication rule.
    for key, role in (("parameter_evidence", "PARAMETER"), ("return_type_evidence", "RETURN_TYPE")):
        for item in (bundle.get(key) or [])[:80]:
            if isinstance(item, Mapping):
                add_evidence(item, role=role)
    # Mandatory physical witnesses must not disappear under supplemental
    # ranking. Reconstruct roles here; stale/provider-supplied annotations do
    # not authorize transport or change the role contract.
    from agents.contracts.field_evidence import build_field_evidence,field_subject_bound
    required_field_refs=[]
    transport_fragments=build_field_evidence(bundle)
    for fragment in transport_fragments:
        item=source_by_ref.get(fragment['block_id'])
        if (fragment['proof_level']=='VERIFIED' and fragment.get('field') and item
                and not item.get('noise_excluded_from_main')
                and item.get('evidence_usage')!='DISCOVERY_ONLY'
                and field_subject_bound(item,bundle)):
            add_evidence(item)
            required_field_refs.append(fragment['block_id'])
    allowed_refs = [str(x.get("evidence_ref")) for x in blocks if x.get("evidence_ref")]
    payload = {
        "identity_group_id": admission.get("identity_group_id"),
        "normalized_identity": admission.get("normalized_identity"),
        "feature_type": admission.get("feature_type"),
        "group_status": admission.get("group_status"),
        "target_resolution": admission.get("target_resolution"),
        "variants": admission.get("variants", []),
        "defining_variants": bundle.get("defining_variants", []),
        "other_variants": bundle.get("other_variants", []),
        "allowed_evidence_refs": allowed_refs,
        "evidence": blocks,
        "field_evidence": [f for f in (bundle.get('field_evidence') if bundle.get('field_evidence') is not None else transport_fragments)
                           if f['block_id'] in allowed_refs],
        "definition_units": definition_units,
        "definition_source_associations": [edge for edge in bundle.get('definition_source_associations',[])
            if {edge['from_definition_unit_id'],edge['to_definition_unit_id']} <=
               {u['definition_unit_id'] for u in definition_units}],
        "evidence_completeness": bundle.get("evidence_completeness", {}),
        "structure_contract": {
            "version": bundle.get("structure_contract_version"),
            "observations_are_semantic_claims": False,
            "existing_role_fields": "LEGACY_INFERRED_NOT_SEMANTIC_TRUTH",
        },
        "input_selection": {
            "budgets": {"main_evidence": 80, "parameter_evidence": 80, "return_type_evidence": 80},
            "included_evidence_refs": allowed_refs,
            "required_definition_evidence_refs": sorted(set(required_definition_refs)),
            "required_definition_unit_refs": sorted({ref for u in definition_units for ref in u['member_evidence_refs']}),
            "definition_unit_transport_gaps": sorted(set(missing_unit_refs)),
            "excluded_definition_units": rejected_source_units,
            "required_catalog_evidence_refs": sorted(set(required_catalog_refs)),
            "required_field_evidence_refs": sorted(set(required_field_refs)),
            "definition_retention_policy": "PROVEN_CURRENT_SUBJECT_BLOCKS_NOT_TRUNCATED_BY_SUPPLEMENTAL_RANKING",
            "omitted_evidence": [
                {"evidence_ref": item.get("block_id") or item.get("evidence_ref"),
                 "page_id": item.get("page_id"), "dom_path": item.get("dom_path"),
                 "owner_ids": item.get("contributing_owner_ids", item.get("owner_ids", [])),
                 "reason": "NOISE_FILTER" if item.get("noise_excluded_from_main") else "BUDGET_OR_EXISTING_RANKING",
                 "recover_from": "EvidenceBundle.source_blocks"}
                for item in bundle.get("source_blocks", [])
                if (item.get("block_id") or item.get("evidence_ref")) not in by_ref
            ],
        },
    }
    schema = {
        "identity_group_id": admission.get("identity_group_id"),
        "description": {"text": "...", "evidence_refs": ["..."]},
        "formal_syntax": [{"text": "...", "evidence_refs": ["..."]}],
        "parameters": [{"name": "...", "description": "...", "evidence_refs": ["..."]}],
        "return_type": {"text": "...", "evidence_refs": ["..."]},
        "notes": [{"text": "...", "evidence_refs": ["..."]}],
        "official_examples": [{"sql": "...", "evidence_refs": ["..."], "source_url": "..."}],
        "generated_examples": [{
            "sql": "...", "origin": "AI_GENERATED",
            "generation_basis": ["formal_syntax"],
            "generation_basis_refs": ["..."],
            "verification_status": "UNVERIFIED",
        }],
    }
    return (
        "Extract semantic fields for exactly this Identity Group from official "
        "documentation evidence. Use no outside knowledge. Do not change the "
        "identity, type, variants, provenance, or add a feature. Every official "
        "claim must use one or more allowed evidence refs and its text must be "
        "extractive: preserve SQL operators, quoted identifiers and string literals. "
        "Use field_evidence fragment refs and source locations when available; "
        "a referenced block is not permission to change a field's role. "
        "Return type may be a claim or an array of independently evidenced claims; "
        "do not overwrite overload-specific values. Use null/[] when unavailable. "
        "Official examples must be explicit examples, not incidental mentions. "
        "Generated examples are optional and must be separate, origin exactly "
        "AI_GENERATED, verification_status UNVERIFIED, and never official. "
        "Return ONLY JSON.\n\nINPUT:\n"
        + _json(payload)
        + "\n\nOUTPUT SCHEMA:\n"
        + _json(schema)
    )


def _make_llm_client(*, run_llm: bool, provider: str, model: str | None, timeout: int,
                     max_retries: int):
    # Direct offline replays may call this stage without either CLI wrapper.
    # Resolve the same project-root environment before reading the model or
    # constructing the provider client.
    load_project_env()
    model_name = model or os.environ.get("FEATURE_CRAWLER_LLM_MODEL", "") or "NOT_CONFIGURED"
    if not run_llm:
        return None, {"requested": False, "ready": False, "reason": "LLM_NOT_REQUESTED"}, model_name
    if provider == "deterministic":
        from agents.semantic_stage.local_provider import DeterministicEvidenceClient
        return DeterministicEvidenceClient(), {"requested": True, "ready": True, "reason": None}, "LOCAL_DETERMINISTIC_EXTRACTIVE_ADAPTER"
    if provider != "openai_compatible":
        return None, {"requested": True, "ready": False, "reason": "DETERMINISTIC_PROVIDER_FORBIDDEN"}, model_name
    config = {
        "provider": provider,
        "model": model or "",
        "temperature": 0.0,
        "timeout": timeout,
        "max_retries": max_retries,
    }
    client = LLMClientFactory.create(config)
    check = getattr(client, "_check_ready", None)
    reason = check() if callable(check) else None
    if reason:
        return None, {"requested": True, "ready": False, "reason": reason}, model_name
    return client, {"requested": True, "ready": True, "reason": None}, model_name


def _call_llm(client: Any, prompt: str, feature_id: str, llm_state: Mapping[str, Any]) -> dict[str, Any]:
    if client is None or not llm_state.get("ready"):
        return {"_status": "skipped", "_raw_response": "", "_error": llm_state.get("reason")}
    try:
        return dict(client.call(prompt, feature_id))
    except Exception as exc:  # The raw client normally captures this; retain a diagnostic if a custom client does not.
        return {"_status": "error", "_raw_response": "", "_error": f"{type(exc).__name__}: {exc}"}


def _semantic_feature(group: Mapping[str, Any], admission: Mapping[str, Any], bundle: Mapping[str, Any],
                      fields: Mapping[str, Any], status: str, diagnostics: list[str], *,
                      response_hash: str | None, execution_status: str) -> dict[str, Any]:
    group_id = str(admission.get("identity_group_id") or "")
    feature_id = "feature:" + _hash(group_id)[:24]
    return {
        "schema_version": "semantic-feature/v1",
        "feature_id": feature_id,
        "identity_group_id": group_id,
        "dbms": str((group.get("provenance") or {}).get("dbms") or "unknown"),
        "name": admission.get("identity"),
        "normalized_identity": admission.get("normalized_identity"),
        "feature_type": admission.get("feature_type"),
        "group_status": admission.get("group_status"),
        "target_resolution": admission.get("target_resolution"),
        "variants": admission.get("variants", []),
        "description": fields.get("description"),
        "formal_syntax": fields.get("formal_syntax", []),
        "parameters": fields.get("parameters", []),
        "return_type": fields.get("return_type"),
        "notes": fields.get("notes", []),
        "official_examples": fields.get("official_examples", []),
        "generated_examples": fields.get("generated_examples", []),
        "owners": bundle.get("owners", []),
        "sources": bundle.get("source_urls", []),
        "evidence_refs": [str(x.get("evidence_ref")) for x in bundle.get("source_blocks", []) if x.get("evidence_ref")],
        "provenance": bundle.get("provenance", {}),
        "semantic_status": status,
        "execution_status": execution_status,
        "semantic_diagnostics": sorted(set(diagnostics)),
        "semantic_response_hash": response_hash,
    }


def _build_summary(*, groups, admission_rows, uncertain_rows, bundles, features, validation_rows,
                   llm_state, model, identity_root, evidence_reuse, workers=8,
                   semantic_budget=None, provider_calls=None, budget_accounting=None,
                   offline_evidence_fallback=False) -> dict[str, Any]:
    statuses = Counter(str(x.get("semantic_status") or "UNKNOWN") for x in features)
    admissions = Counter(str(x.get("admission_status") or "UNKNOWN") for x in admission_rows)
    types = Counter(str(x.get("feature_type") or "UNCERTAIN") for x in admission_rows if x.get("semantic_ready"))
    fields = {
        "description": sum(bool(x.get("description")) for x in features),
        "formal_syntax": sum(bool(x.get("formal_syntax")) for x in features),
        "parameters": sum(bool(x.get("parameters")) for x in features),
        "return_type": sum(bool(x.get("return_type")) for x in features),
        "notes": sum(bool(x.get("notes")) for x in features),
        "official_examples": sum(bool(x.get("official_examples")) for x in features),
        "generated_examples": sum(bool(x.get("generated_examples")) for x in features),
    }
    validation = {
        "accepted_claims": sum(int(x.get("accepted_claims", 0)) for x in validation_rows),
        "rejected_claims": sum(int(x.get("rejected_claims", 0)) for x in validation_rows),
        "invalid_evidence_refs": sum(int(x.get("invalid_evidence_refs", 0)) for x in validation_rows),
        "ungrounded_claims": sum(int(x.get("ungrounded_claims", 0)) for x in validation_rows),
    }
    uncertain_decisions = Counter(str(x.get("ai_decision") or "UNRESOLVED") for x in uncertain_rows)
    target_admitted = sum(1 for x in admission_rows if x.get("semantic_ready"))
    semantic_attempted = sum(1 for x in features if x.get("semantic_status") != "NOT_RUN")
    execution_statuses = Counter(str(x.get("execution_status") or "UNKNOWN") for x in features)
    offline_reused = sum(
        1 for x in features
        if "OFFLINE_EVIDENCE_REUSE" in (x.get("semantic_diagnostics") or [])
    )
    risk_strata = Counter(str(x.get("risk_stratum") or "NONE") for x in admission_rows)
    routes = Counter(str(x.get("route") or "UNKNOWN") for x in admission_rows)
    budget = dict(budget_accounting or {})
    if provider_calls is not None and "provider_calls_total" not in budget:
        budget["provider_calls_total"] = provider_calls
    return {
        "schema_version": "semantic-stage-summary/v1",
        "pipeline_id": PIPELINE_ID,
        "identity_root": str(identity_root),
        "identity_groups_input": len(groups),
        "target_admitted": target_admitted,
        "target_rejected": admissions.get("EXCLUDED", 0),
        "target_review_required": admissions.get("REVIEW_REQUIRED", 0),
        "target_pending_classification": admissions.get("PENDING_TARGET_CLASSIFICATION", 0),
        "confirmed_admitted": sum(1 for x in admission_rows if x.get("group_status") == "CONFIRMED_FEATURE" and x.get("semantic_ready")),
        "probable_admitted": sum(1 for x in admission_rows if x.get("group_status") == "PROBABLE_FEATURE" and x.get("semantic_ready")),
        "non_feature_excluded": sum(1 for x in admission_rows if x.get("group_status") == "NON_FEATURE_ONLY"),
        "uncertain_total": len(uncertain_rows),
        "uncertain_decisions": dict(sorted(uncertain_decisions.items())),
        "target_classification": {
            "confirmed_direct_semantic": budget.get("confirmed_direct_semantic", 0),
            "probable_total": sum(1 for x in groups if str(x.get("group_status") or "") == "PROBABLE_FEATURE"),
            "probable_classification_calls": budget.get("probable_classification_calls", 0),
            "probable_target": sum(1 for x in admission_rows if str(x.get("group_status") or "") == "PROBABLE_FEATURE" and x.get("admission_status") == "ADMITTED"),
            "probable_non_target": sum(1 for x in admission_rows if str(x.get("group_status") or "") == "PROBABLE_FEATURE" and x.get("admission_status") == "EXCLUDED"),
            "probable_review": sum(1 for x in admission_rows if str(x.get("group_status") or "") == "PROBABLE_FEATURE" and x.get("admission_status") in {"REVIEW_REQUIRED", "PENDING_TARGET_CLASSIFICATION"}),
            "uncertain_total": sum(1 for x in groups if str(x.get("group_status") or "") == "UNCERTAIN_FEATURE"),
            "uncertain_classification_calls": budget.get("uncertain_classification_calls", 0),
            "uncertain_target": sum(1 for x in admission_rows if str(x.get("group_status") or "") == "UNCERTAIN_FEATURE" and x.get("admission_status") == "ADMITTED"),
            "uncertain_non_target": sum(1 for x in admission_rows if str(x.get("group_status") or "") == "UNCERTAIN_FEATURE" and x.get("admission_status") == "EXCLUDED"),
            "uncertain_review": sum(1 for x in admission_rows if str(x.get("group_status") or "") == "UNCERTAIN_FEATURE" and x.get("admission_status") in {"REVIEW_REQUIRED", "PENDING_TARGET_CLASSIFICATION"}),
            "classification_calls_total": budget.get("classification_calls_total", 0),
        },
        "type_aware_gate": {
            "risk_strata": dict(sorted(risk_strata.items())),
            "routes": dict(sorted(routes.items())),
            "target_cache_hits": budget.get("target_cache_hits", 0),
            "semantic_cache_hits": budget.get("semantic_cache_hits", 0),
        },
        "target_by_type": {kind: types.get(kind, 0) for kind in TARGET_TYPES},
        "evidence_bundles": len(bundles),
        "evidence_reuse": evidence_reuse,
        "bundles_with_defining_evidence": sum(bool(x.get("defining_evidence")) for x in bundles),
        "bundles_with_example_evidence": sum(bool(x.get("example_evidence")) for x in bundles),
        "owners_per_group": round(sum(len(x.get("owners", [])) for x in bundles) / len(bundles), 3) if bundles else 0.0,
        "source_pages_per_group": round(sum(len(x.get("source_urls", [])) for x in bundles) / len(bundles), 3) if bundles else 0.0,
        "semantic_features": len(features),
        "semantic_attempted": semantic_attempted,
        "semantic_success": statuses.get("COMPLETE", 0),
        "semantic_complete": statuses.get("COMPLETE", 0),
        "semantic_partial": statuses.get("PARTIAL", 0),
        "semantic_structural_only": statuses.get("STRUCTURAL_ONLY", 0),
        "semantic_validation_failed": statuses.get("VALIDATION_FAILED", 0),
        "semantic_review_required": statuses.get("REVIEW_REQUIRED", 0),
        "semantic_not_run": statuses.get("NOT_RUN", 0),
        "semantic_execution_status": dict(sorted(execution_statuses.items())),
        "semantic_not_requested": execution_statuses.get("NOT_REQUESTED", 0),
        "semantic_not_configured": execution_statuses.get("NOT_CONFIGURED", 0),
        "semantic_call_failed": execution_statuses.get("CALL_FAILED", 0),
        "semantic_parse_failed": execution_statuses.get("PARSE_FAILED", 0),
        "semantic_budget": semantic_budget,
        "semantic_provider_calls": budget.get("semantic_extraction_calls", provider_calls),
        "semantic_mode": (
            "DETERMINISTIC_OWNER_LOCAL_EVIDENCE_REUSE" if offline_reused else
            "LLM" if semantic_attempted else
            "NOT_RUN_PROVIDER_UNAVAILABLE"
        ),
        "offline_evidence_reused": offline_reused,
        "offline_evidence_fallback_requested": bool(offline_evidence_fallback),
        "budget_accounting": budget,
        "field_counts": fields,
        "field_coverage": {key: (value / len(features) if features else 0.0) for key, value in fields.items()},
        "content_completeness": content_summary(features),
        "grounding": validation,
        "llm": {"state": dict(llm_state), "model": model, "prompt_version": PROMPT_VERSION, "workers": workers},
        "integrity": {
            "legacy_profile_used": False,
            "legacy_formal_used": False,
            "legacy_adapter_used": False,
            "db_specific_repair_used": False,
            "feature_inventory_used": False,
            "identity_modified_by_llm": False,
            "canonical_run": False,
            "frontend_run": False,
            "discovery_run": False,
        },
        "failure_buckets": _failure_buckets(features, uncertain_rows, bundles),
        "generated_at": _now(),
    }


def _failure_buckets(features, uncertain_rows, bundles) -> dict[str, int]:
    result = Counter({
        "TARGET_CLASSIFICATION_FAILURE": 0,
        "EVIDENCE_INSUFFICIENCY": 0,
        "LLM_EXTRACTION_FAILURE": 0,
        "GROUNDING_VALIDATION_FAILURE": 0,
        "MISSING_OFFICIAL_SEMANTIC_EVIDENCE": 0,
        "AI_NOT_RUN": 0,
    })
    for row in uncertain_rows:
        decision = str(row.get("ai_decision") or "UNRESOLVED")
        if decision == "UNRESOLVED":
            result["TARGET_CLASSIFICATION_FAILURE"] += 1
    for feature in features:
        for diagnostic in feature.get("semantic_diagnostics") or []:
            if diagnostic in {"LLM_NOT_CONFIGURED", "LLM_NOT_REQUESTED", "LLM_BUDGET_EXHAUSTED"}:
                result["AI_NOT_RUN"] += 1
            elif "LLM" in diagnostic or "PARSE" in diagnostic:
                result["LLM_EXTRACTION_FAILURE"] += 1
            elif "UNGROUNDED" in diagnostic or "EVIDENCE_REF" in diagnostic:
                result["GROUNDING_VALIDATION_FAILURE"] += 1
    result["EVIDENCE_INSUFFICIENCY"] = sum(1 for x in bundles if not x.get("defining_evidence"))
    result["MISSING_OFFICIAL_SEMANTIC_EVIDENCE"] = sum(1 for x in bundles if not x.get("source_blocks"))
    return dict(sorted(result.items()))


def _build_review_pack(features, bundles, validation_rows, uncertain_rows):
    by_type = {kind: [] for kind in TARGET_TYPES}
    for feature in features:
        if feature.get("feature_type") in by_type:
            by_type[feature["feature_type"]].append(feature)
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    limits = {"FUNCTION": 30, "OPERATOR": 15, "DATATYPE": 15}
    bundle_map = {x["identity_group_id"]: x for x in bundles}
    validation_map = {x["identity_group_id"]: x for x in validation_rows}
    for kind, limit in limits.items():
        for feature in by_type[kind][:limit]:
            selected_ids.add(feature["feature_id"])
            selected.append(_review_row(feature, bundle_map, validation_map, "type_sample"))
    for feature in features:
        if feature.get("semantic_status") in {"VALIDATION_FAILED", "REVIEW_REQUIRED"} and feature["feature_id"] not in selected_ids:
            selected_ids.add(feature["feature_id"])
            selected.append(_review_row(feature, bundle_map, validation_map, "failure"))
    for feature in features:
        if feature.get("semantic_status") == "PARTIAL" and feature["feature_id"] not in selected_ids:
            selected_ids.add(feature["feature_id"])
            selected.append(_review_row(feature, bundle_map, validation_map, "partial"))
    selected.extend({
        "review_kind": "uncertain_target",
        "target_review": row,
    } for row in uncertain_rows if row.get("ai_decision") not in TARGET_TYPES)
    return selected


def _review_row(feature, bundle_map, validation_map, kind):
    bundle = bundle_map.get(feature.get("identity_group_id"), {})
    return {
        "review_kind": kind,
        "feature": feature,
        "evidence_bundle_excerpt": {
            "identity_group_id": bundle.get("identity_group_id"),
            "defining_evidence": bundle.get("defining_evidence", [])[:10],
            "formal_variant_evidence": bundle.get("formal_variant_evidence", [])[:10],
            "example_evidence": bundle.get("example_evidence", [])[:10],
            "main_evidence": bundle.get("main_evidence", [])[:20],
            "owners": bundle.get("owners", []),
            "source_urls": bundle.get("source_urls", []),
        },
        "validation": validation_map.get(feature.get("identity_group_id"), {}),
    }


def _write_report(path: Path, summary, bundles, features, validation_rows, uncertain_rows, identity_root):
    bundle_map = {x.get("normalized_identity"): x for x in bundles}
    feature_map = {x.get("normalized_identity"): x for x in features}
    validation_map = {x.get("identity_group_id"): x for x in validation_rows}
    chosen = []
    for wanted, kind in (("ABS", "FUNCTION"),):
        item = next((x for x in bundles if x.get("normalized_identity") == wanted), None)
        if item:
            chosen.append((kind, item))
    for kind in ("OPERATOR", "DATATYPE"):
        item = next((x for x in bundles if x.get("feature_type") == kind), None)
        if item:
            chosen.append((kind, item))
    lines = [
        "# Identity Semantic Stage Report", "", f"- Pipeline: `{PIPELINE_ID}`",
        f"- Identity input: `{identity_root}`", f"- Input groups: `{summary['identity_groups_input']}`",
        "", "## 1. Target Admission", "",
        f"- Confirmed admitted: `{summary['confirmed_admitted']}`",
        f"- Probable admitted: `{summary['probable_admitted']}`",
        f"- Uncertain target decisions: `{summary['uncertain_decisions']}`",
        f"- Non-feature excluded: `{summary['non_feature_excluded']}`",
        f"- Review required: `{summary['target_review_required']}`",
        f"- Pending target classification: `{summary.get('target_pending_classification', 0)}`",
        f"- Target classification accounting: `{summary.get('target_classification', {})}`",
        "", "## 2. Evidence Aggregation", "",
        f"- Bundles: `{summary['evidence_bundles']}`",
        f"- Bundles with defining evidence: `{summary['bundles_with_defining_evidence']}`",
        f"- Bundles with example evidence: `{summary['bundles_with_example_evidence']}`",
        f"- Evidence reuse: `{summary['evidence_reuse']}`",
        f"- Average owners per group: `{summary['owners_per_group']}`",
        f"- Average source pages per group: `{summary['source_pages_per_group']}`",
        "", "### Representative bundles", "",
    ]
    for kind, bundle in chosen:
        lines.extend([f"#### {bundle.get('normalized_identity')} ({kind})", "", "```json",
                      json.dumps(_bundle_excerpt(bundle), ensure_ascii=False, indent=2), "```", ""])
    lines.extend([
        "## 3. AI Semantic Extraction", "",
        f"- Attempted: `{summary['semantic_attempted']}`",
        f"- Complete: `{summary['semantic_complete']}`",
        f"- Partial: `{summary['semantic_partial']}`",
        f"- Structural-only: `{summary['semantic_structural_only']}`",
        f"- Validation failed: `{summary['semantic_validation_failed']}`",
        f"- Not run: `{summary['semantic_not_run']}`",
        f"- LLM state: `{summary['llm']['state']}`",
        "- If LLM is not configured, this stage emits prompt payloads and NOT_RUN features; it does not use a deterministic semantic fallback.",
        f"- Field counts: `{summary['field_counts']}`",
        "", "## 4. Grounding Validation", "",
        f"- Accepted claims: `{summary['grounding']['accepted_claims']}`",
        f"- Rejected claims: `{summary['grounding']['rejected_claims']}`",
        f"- Invalid evidence refs: `{summary['grounding']['invalid_evidence_refs']}`",
        f"- Ungrounded claims: `{summary['grounding']['ungrounded_claims']}`",
        "- No AI claims were evaluated in this replay because the configured LLM was unavailable; acceptance/rejection examples are covered by the deterministic unit tests.",
        "", "## 5. Generated vs Official Examples", "",
        f"- Official example-bearing features: `{summary['field_counts']['official_examples']}`",
        f"- Generated-example-bearing features: `{summary['field_counts']['generated_examples']}`",
        "Official and AI-generated examples are separate fields; no generated example is promoted to official evidence.",
        "", "## 6. Semantic Feature Samples", "",
    ])
    sample_features = []
    for kind in TARGET_TYPES:
        item = next((x for x in features if x.get("feature_type") == kind), None)
        if item:
            sample_features.append(item)
    partial = next((x for x in features if x.get("semantic_status") == "PARTIAL"), None)
    if partial:
        sample_features.append(partial)
    for feature in features:
        if len(sample_features) >= 4:
            break
        if feature not in sample_features:
            sample_features.append(feature)
    for feature in sample_features[:4]:
        lines.extend([f"### {feature.get('normalized_identity')} ({feature.get('feature_type')})", "", "```json",
                      json.dumps({k: feature.get(k) for k in (
                          "name", "feature_type", "group_status", "semantic_status", "description",
                          "formal_syntax", "official_examples", "generated_examples", "semantic_diagnostics",
                      )}, ensure_ascii=False, indent=2), "```", ""])
    lines.extend([
        "## 7. Integrity", "", "```yaml",
        "legacy_profile_used: false", "legacy_formal_used: false", "legacy_adapter_used: false",
        "db_specific_repair_used: false", "feature_inventory_used: false", "identity_modified_by_llm: false",
        "canonical_run: false", "frontend_run: false", "discovery_run: false", "```", "",
        "## 8. Tests", "",
        "Unit tests for admission, bundle aggregation, grounding, missing-field retention, and example separation are in `tests/test_semantic_stage.py`.",
        "", "## 9. Main Failure Buckets", "",
        f"```yaml\n{json.dumps(summary['failure_buckets'], ensure_ascii=False, indent=2)}\n```", "",
        "## 10. Next Decision", "",
        "Semantic output remains isolated at the Semantic Feature boundary. Canonical KB and Frontend were not run.", "",
    ])
    _write_text(path, "\n".join(lines))


def _bundle_excerpt(bundle):
    return {
        "identity_group_id": bundle.get("identity_group_id"),
        "normalized_identity": bundle.get("normalized_identity"),
        "feature_type": bundle.get("feature_type"),
        "group_status": bundle.get("group_status"),
        "defining_variants": bundle.get("defining_variants", []),
        "other_variants": bundle.get("other_variants", []),
        "defining_evidence": bundle.get("defining_evidence", [])[:8],
        "formal_variant_evidence": bundle.get("formal_variant_evidence", [])[:8],
        "example_evidence": bundle.get("example_evidence", [])[:8],
        "supporting_evidence": bundle.get("supporting_evidence", [])[:8],
        "owners": bundle.get("owners", [])[:8],
        "source_urls": bundle.get("source_urls", []),
        "source_blocks": bundle.get("source_blocks", [])[:12],
    }


def _write_review_markdown(path: Path, rows):
    lines = ["# Semantic Review Pack", "", f"- Records: `{len(rows)}`", ""]
    for index, row in enumerate(rows, 1):
        feature = row.get("feature") or {}
        target = row.get("target_review")
        if target:
            lines.extend([f"## {index}. Uncertain target: {target.get('identity')}", "", "```json",
                          json.dumps(target, ensure_ascii=False, indent=2), "```", ""])
            continue
        lines.extend([f"## {index}. {feature.get('name')} ({feature.get('feature_type')})", "",
                      f"- Review kind: `{row.get('review_kind')}`",
                      f"- Semantic status: `{feature.get('semantic_status')}`", "", "### Feature", "", "```json",
                      json.dumps(feature, ensure_ascii=False, indent=2), "```", "", "### Evidence excerpt", "", "```json",
                      json.dumps(row.get("evidence_bundle_excerpt", {}), ensure_ascii=False, indent=2), "```", ""])
    _write_text(path, "\n".join(lines))


def _parse_json_object(raw: str) -> tuple[dict[str, Any], str | None]:
    if not raw or not raw.strip():
        return {}, "empty response"
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        end = next((i for i, line in enumerate(lines) if line.strip().startswith("```")), None)
        text = "\n".join(lines[:end] if end is not None else lines).strip()
    start = text.find("{")
    if start >= 0:
        text = text[start:]
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        return {}, str(exc)
    if not isinstance(value, dict):
        return {}, "response JSON is not an object"
    return value, None


def _empty_fields() -> dict[str, Any]:
    return {
        "description": None,
        "formal_syntax": [],
        "parameters": [],
        "return_type": None,
        "notes": [],
        "official_examples": [],
        "generated_examples": [],
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"Required artifact not found: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _load_reusable_bundles(evidence_root: str | Path | None,
                           groups: list[Mapping[str, Any]], *,expected=None,audit=None) -> dict[str, dict[str, Any]]:
    if not evidence_root:
        return {}
    root = Path(evidence_root).resolve()
    bundle_path = root if root.is_file() else root / "evidence" / "bundles.jsonl"
    if not bundle_path.is_file():
        raise FileNotFoundError(f"Evidence bundle artifact not found: {bundle_path}")
    known_groups = {str(x.get("identity_unit_id") or "") for x in groups}
    result: dict[str, dict[str, Any]] = {}
    seen=set()
    for bundle in _read_jsonl(bundle_path):
        group_id = str(bundle.get("identity_group_id") or "")
        if not group_id or group_id not in known_groups:
            raise ValueError(f"Evidence bundle does not belong to current Identity Quality input: {group_id}")
        if group_id in seen:
            raise ValueError(f"Duplicate evidence bundle for identity group: {group_id}")
        seen.add(group_id)
        from agents.contracts.evidence_cache import reusable
        if not reusable(bundle,(expected or {}).get(group_id)):
            if audit is not None:audit.append({'identity_unit_id':group_id,'state':'CACHE_STALE','action':'REBUILD_FROM_FROZEN_INPUTS'})
            continue
        if audit is not None:audit.append({'identity_unit_id':group_id,'state':'REUSED'})
        result[group_id] = bundle
    return result


def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def _hash(value: Any) -> str:
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _cache_key(stage: str, group_id: str, feature_type: Any, bundle: Mapping[str, Any],
               prompt_version: str, model: str) -> str:
    """Stable stage cache identity for resumable provider execution."""
    evidence_hash = _hash({
        "identity_group_id": group_id,
        "feature_type": str(feature_type or "").upper(),
        "source_blocks": bundle.get("source_blocks") or [],
        "main_evidence": bundle.get("main_evidence") or [],
    })
    return _hash({
        "stage": stage,
        "identity_group_id": group_id,
        "feature_type": str(feature_type or "").upper(),
        "evidence_hash": evidence_hash,
        "prompt_version": prompt_version,
        "model": model or "NOT_CONFIGURED",
    })


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
