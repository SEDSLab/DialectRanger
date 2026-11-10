"""Generic Unified production orchestration boundary.

The ``unified`` CLI path is intentionally independent from the historical
onboarding/Formal pipeline. It starts with the official seed URL, creates a
generic Discovery/DocumentIR/Profile package, and then hands IdentityUnit
records to the generic type, evidence, semantic, sense, canonical, and
frontend stages.

Historical onboarding and directory-discovery entry points are retired.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any, Iterable, Mapping

from agents.pipeline.generic_unified import run_generic_downstream
from agents.profile_bootstrap.cold_start import (
    build_cold_start_profile,
    profile_digest,
    write_cold_start_profile,
)


PIPELINE_ID = "generic-unified-feature-kb-pipeline/v1"
CONTRACT_PACKAGE_VERSION = "featurecrawler-generic-unified-contract/v1"
TARGET_TYPES = ("FUNCTION", "OPERATOR", "DATATYPE")


def _write_json(path: Path, value: Any) -> None:
    from agents.contracts.artifact_io import write_json,build_report
    if path.name=='onboarding_report.json':value=build_report(value,path.parent)
    write_json(path,value)


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    from agents.contracts.artifact_io import write_jsonl
    write_jsonl(path,rows)


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))








def _generic_config(*, dbms: str, docs_url: str, output_root: Path, options: Mapping[str, Any]) -> dict[str, Any]:
    """Record the scoped route's actual public inputs, without legacy config dependencies."""
    return {
        "dbms": dbms,
        "docs_root": docs_url,
        "seed_urls": [docs_url],
        "output_dir": str(output_root / "discovery"),
        "max_depth": options.get("discovery_max_depth", 8),
        "max_pages": options.get("max_pages"),
        "cache_only": bool(options.get("cache_only", False)),
        "cache_dir": options.get("cache_dir"),
        "legacy_profile_dsl": False,
    }


def _copy_tree(source: Path, destination: Path) -> None:
    if source.is_dir():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, dirs_exist_ok=True)


def _publish_projection_aliases(output_root: Path, contract_root: Path) -> dict[str, str]:
    """Expose the Generic projection at stable top-level paths.

    These are byte-for-byte copies of the Generic projection. No historical
    KB is read and no legacy record is transformed.
    """
    source_kb = contract_root / "canonical" / "promotion_ready_kb.json"
    top_kb = output_root / "kb" / "canonical_kb.json"
    if source_kb.is_file():
        top_kb.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_kb, top_kb)
        shutil.copyfile(source_kb,output_root/'kb'/'final_feature_kb.json')
    _copy_tree(contract_root/'canonical',output_root/'canonical')
    _copy_tree(contract_root / "frontend", output_root / "frontend")
    return {
        "canonical_kb": str(top_kb),
        "frontend_web": str(output_root / "frontend" / "web"),
        "frontend_data": str(output_root / "frontend" / "data"),
    }


def _quality_summary(downstream: Mapping[str, Any]) -> dict[str, Any]:
    canonical = list(downstream.get("contract_canonical") or [])
    semantic = list(downstream.get("semantic_records") or [])
    quality = Counter(str(row.get("quality_status") or "REVIEW_REQUIRED") for row in canonical)
    semantic_status = Counter(str(row.get("status") or "UNAVAILABLE") for row in semantic)
    return {
        "features": len(canonical),
        "quality_by_status": dict(sorted(quality.items())),
        "semantic_by_status": dict(sorted(semantic_status.items())),
        "unresolved_evidence": sum(not row.get("evidence_ids") for row in canonical),
        "candidate_detection_gaps": "NOT_EVALUATED",
    }


def _materialize_generic_contract(
    *,
    dbms: str,
    docs_url: str,
    output_root: Path,
    documents: list[dict[str, Any]],
    profile: Mapping[str, Any],
    discovery: Mapping[str, Any],
    exclusions: list[dict[str, Any]],
    options: Mapping[str, Any],
) -> dict[str, Any]:
    contract_root = output_root / "unified_pipeline"
    result = run_generic_downstream(
        dbms=dbms,
        documents=documents,
        profile=profile,
        output_root=contract_root,
        semantic=bool(options.get("semantic", False)),
        semantic_provider=str(options.get("semantic_provider") or "openai_compatible"),
        semantic_model=options.get("semantic_model"),
        semantic_budget=options.get("semantic_budget"),
    )
    generic_manifest = _read_json(contract_root / "execution_manifest.json", {})
    generic_manifest["dbms"] = dbms
    generic_manifest["docs_root"] = docs_url
    generic_manifest["discovery_source"] = "generic_discovery_document_ir"
    generic_manifest["profile_id"] = profile.get("profile_id")
    generic_manifest["profile_digest"] = profile_digest(profile)
    generic_manifest["discovery_counts"] = {
        "included_pages": discovery.get("included_pages", 0),
        "unique_included_pages": discovery.get("unique_included_pages", discovery.get("included_pages", 0)),
        "processed_documents": len(documents),
        "document_exclusions": len(exclusions),
    }
    # Keep the actual execution manifest in both locations. The same object is
    # used, so the legacy flags cannot drift.
    _write_json(contract_root / "execution_manifest.json", generic_manifest)
    _write_json(output_root / "runtime" / "execution_manifest.json", generic_manifest)
    _write_jsonl(output_root / "runtime" / "document_exclusions.jsonl", exclusions)

    lineage = result.get("lineage") or {}
    counts = generic_manifest.get("counts") or {}
    summary = {
        "schema_version": CONTRACT_PACKAGE_VERSION,
        "pipeline_id": PIPELINE_ID,
        "dbms": dbms,
        "docs_root": docs_url,
        "profile_source": "generic_structural_profile",
        "identity_source": "generic_identity_unit",
        "type_source": "generic_owner_local_typed_evidence",
        "admission_source": "generic_identity_and_evidence_contract",
        "evidence_source": "generic_owner_aligned_evidence_bundle",
        "semantic_source": "generic_evidence_grounded_semantic_stage",
        "grounding_source": "generic_semantic_validator",
        "sense_source": "generic_semantic_sense_audit",
        "canonical_source": "generic_canonical_projection",
        "frontend_source": "generic_frontend_projection_v2",
        "legacy_profile_dsl_used": False,
        "legacy_formal_candidate_used": False,
        "legacy_fallback_used": False,
        "legacy_adapter_used": False,
        "db_specific_rules_used": False,
        "feature_inventory_used": False,
        "counts": counts,
        "discovery": dict(discovery),
        "document_exclusions": len(exclusions),
        "lineage": lineage,
        "profile": {
            "profile_id": profile.get("profile_id"),
            "digest": profile_digest(profile),
            "generation_mode": profile.get("generation_mode"),
            "source_pages": len(profile.get("source_pages") or []),
        },
    }
    _write_json(contract_root / "summary.json", summary)
    _write_json(contract_root / "run_manifest.json", {
        "schema_version": "generic-unified-run/v1",
        "pipeline_id": PIPELINE_ID,
        "dbms": dbms,
        "docs_root": docs_url,
        "contract_root": str(contract_root),
        "execution_manifest": str(contract_root / "execution_manifest.json"),
        "discovery_run": True,
        "legacy_path_used": False,
    })
    return {"result": result, "summary": summary, "manifest": generic_manifest}


def _build_result(
    *,
    dbms: str,
    docs_url: str,
    output_root: Path,
    profile: Mapping[str, Any],
    documents: list[dict[str, Any]],
    discovery: Mapping[str, Any],
    package: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(package["result"])
    downstream = result.get("canonical") or {}
    typed_units = list((result.get("type") or {}).get("units") or [])
    quality = _quality_summary(downstream)
    aliases = _publish_projection_aliases(output_root, output_root / "unified_pipeline")
    active = sorted({
        str(row.get("feature_type") or "").lower()
        for row in typed_units
        if str(row.get("feature_type") or "").upper() in TARGET_TYPES
    })
    semantic_by_status = quality["semantic_by_status"]
    quality_status = "ready" if quality["features"] and not quality["quality_by_status"].get("REVIEW_REQUIRED") else "partial"
    result.update({
        "status": "accepted",
        "dbms": dbms,
        "docs_root": docs_url,
        "profile_status": "accepted",
        "active_feature_types": active,
        "skipped_feature_types": [kind.lower() for kind in TARGET_TYPES if kind.lower() not in active],
        "authoritative_pipeline": {
            "pipeline_id": PIPELINE_ID,
            "entrypoint": "run_feature_crawler.py build/update --pipeline unified",
            "orchestrator": "agents.pipeline.orchestrator",
            "legacy_fallback": False,
            "contract_package": package["summary"],
        },
        "unified_contract": package["summary"],
        "final_summary": {
            "pipeline_execution_status": "success",
            "knowledge_quality_status": quality_status,
            "canonical": {
                "features": quality["features"],
                "unresolved_evidence": quality["unresolved_evidence"],
                "candidate_detection_gaps": quality["candidate_detection_gaps"],
            },
            "semantics": {
                "semantic_enriched": semantic_by_status.get("COMPLETE", 0),
                "semantic_gaps": sum(value for key, value in semantic_by_status.items() if key != "COMPLETE"),
                "provider_calls": (result.get("semantic") or {}).get("summary", {}).get("budget_accounting", {}).get('provider_calls_total',0),
                "actual_external_model_calls": (result.get('semantic') or {}).get('summary',{}).get('actual_model_calls',0),
                "checkpoints_reused": (result.get("semantic") or {}).get("summary", {}).get("evidence_reuse", {}).get('reused',0),
            },
            "final_feature_snapshots": {
                "projected_records": (result.get('publication') or {}).get('final_count',0),
                "audit_canonical_records": quality['features'],
                "outputs": aliases,
            },
        },
    })
    _write_json(output_root / "onboarding_report.json", result)
    return result


def _run_build(dbms: str, docs_url: str, output_root: str | Path, **options: Any) -> dict[str, Any]:
    output = Path(output_root).resolve()
    resume = bool(options.get("resume", False))
    if resume:
        raise ValueError('Unified full-stage resume is not yet safe; use a fresh output directory with explicit frozen cache input')
    if output.exists() and any(output.iterdir()) and not resume:
        raise FileExistsError(
            f"unified output must be new and empty; refusing to reuse: {output}"
        )
    output.mkdir(parents=True, exist_ok=True)
    seed_url = str(docs_url or '').strip()
    if options.get('docs_manifest'):
        from agents.pipeline.scoped_build import load_explicit_documents
        seed_url = load_explicit_documents(options['docs_manifest'], dbms)[0]['url']
    if not seed_url:
        raise ValueError("unified pipeline requires a non-empty official seed URL")

    provided_profile = None
    if options.get('profile_path'):
        from agents.profile_bootstrap.cold_start import load_cold_start_profile
        provided_profile = load_cold_start_profile(options['profile_path'])
        if str(provided_profile['dbms']).casefold() != str(dbms).casefold():
            raise ValueError('External Profile DBMS mismatch')
    config = _generic_config(
        dbms=dbms, docs_url=seed_url, output_root=output, options=options,
    )
    run_id = f"unified_{str(dbms).lower()}_{hashlib.sha256(seed_url.encode()).hexdigest()[:16]}"
    _write_json(output / "runtime" / "generic_discovery_config.json", {
        **config,
        "run_id": run_id,
        "pipeline": "unified",
        "profile_source": "generic_structural_profile",
        "legacy_profile_dsl_used": False,
        "legacy_formal_candidate_used": False,
        "legacy_fallback_used": False,
    })

    from agents.pipeline.scoped_build import scoped_documents
    documents, document_exclusions, discovery = scoped_documents(dbms, seed_url, output, options)
    if not documents:
        raise RuntimeError("Generic Discovery produced no readable DocumentIR pages")
    _write_jsonl(output / "document_ir" / "documents.jsonl", documents)
    _write_jsonl(output / "document_ir" / "exclusions.jsonl", document_exclusions)

    profile = provided_profile if provided_profile is not None else build_cold_start_profile(dbms=dbms, documents=documents, run_id=run_id)
    write_cold_start_profile(output / "profile" / "generated_profile.json", profile)
    _write_json(output / "profile" / "profile_metadata.json", {
        "profile_id": profile["profile_id"],
        "run_id": run_id,
        "generation_mode": profile['generation_mode'],
        "manual_edit": profile['manual_edit'],
        "digest": profile_digest(profile),
        "load_mode": 'EXTERNAL_FILE' if options.get('profile_path') else 'GENERATED_THIS_RUN',
        "source_path": str(Path(options['profile_path']).resolve()) if options.get('profile_path') else None,
        "selection_authority": profile.get("hint_contract", {}).get("selection_authority"),
    })

    from agents.pipeline.preflight import evaluate_documents
    preflight = evaluate_documents(documents, profile, output/'preflight',
                                   sample_pages=options.get('preflight_sample_pages', 12))
    if options.get('require_preflight') and preflight['status'] != 'SUPPORTED':
        raise RuntimeError('PREFLIGHT_REVIEW_REQUIRED: inspect preflight/report.md before a strict build')

    package = _materialize_generic_contract(
        dbms=dbms, docs_url=seed_url, output_root=output,
        documents=documents, profile=profile, discovery=discovery,
        exclusions=document_exclusions, options=options,
    )
    result = _build_result(
        dbms=dbms, docs_url=seed_url, output_root=output, profile=profile,
        documents=documents, discovery=discovery, package=package,
    )
    return result


def definition_publication_health(output: Path) -> dict[str, Any]:
    """Report a category lost on missing subject proof, without changing admission."""
    stage=Path(output)/'unified_pipeline'
    def records(relative):
        path=stage/relative
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()] if path.is_file() else []
    decisions=records('type/type_decisions.jsonl')
    admissions={r['identity_unit_id']:r for r in records('canonical/admission_decisions.jsonl')}
    final=_read_json(Path(output)/'kb/final_feature_kb.json',[])
    counts=Counter(str(r.get('feature_type','')).upper() for r in final)
    by_type={};issues=[]
    for kind in TARGET_TYPES:
        typed=[r for r in decisions if r.get('primary_type')==kind]
        missing=sum('INDEPENDENT_SUBJECT_TYPE_PROOF_MISSING' in admissions.get(r['identity_unit_id'],{}).get('reason_codes',[]) for r in typed)
        by_type[kind]={'typed_candidates':len(typed),'final_count':counts[kind],'missing_subject_proof':missing}
        if typed and counts[kind]==0 and missing:
            issues.append({'code':'TYPE_PUBLICATION_COLLAPSE','feature_type':kind,'missing_subject_proof':missing})
    conflicts=[r['definition_unit_id'] for r in records('owners/definition_units.jsonl')
               if 'DEFINITION_UNIT_SOURCE_CONFLICT' in r.get('reason_codes',[])]
    if conflicts:issues.append({'code':'DEFINITION_UNIT_SOURCE_CONFLICT','count':len(conflicts)})
    return {'by_type':by_type,'issues':issues,'conflicting_definition_units':conflicts,
            'official_coverage':'NOT_EVALUATED'}


def run_build(dbms: str, docs_url: str, output_root: str | Path, **options: Any) -> dict[str, Any]:
    output=Path(output_root).resolve()
    existed=output.exists() and any(output.iterdir())
    options.setdefault('semantic',True)
    options.setdefault('semantic_provider','deterministic')
    try:
        if options['semantic']:
            from agents.semantic_stage.pipeline import _make_llm_client
            _,state,_=_make_llm_client(run_llm=True,provider=options['semantic_provider'],model=options.get('semantic_model'),timeout=60,max_retries=0)
            if not state.get('ready'):raise ValueError('SEMANTIC_PROVIDER_NOT_CONFIGURED: '+str(state.get('reason')))
        result=_run_build(dbms,docs_url,output,**options)
        publication=result.get('publication',{})
        semantic=result.get('semantic',{}).get('summary',{})
        scope=result.get('unified_contract',{}).get('discovery',{})
        unresolved=[k for k,v in scope.get('scope_manifest',{}).get('scopes',{}).items() if not v]
        losses=result.get('frontend',{}).get('canonical_records',0)-result.get('frontend',{}).get('frontend_records',0)
        contract=bool(publication.get('final_count')) and losses==0 and result.get('lineage',{}).get('status')=='PASS'
        expected_mode='LOCAL_DETERMINISTIC_EXTRACTIVE_ADAPTER' if options['semantic_provider']=='deterministic' else options['semantic_provider']
        provider_matches=semantic.get('provider_mode')==expected_mode
        issues={'Provider Failure':semantic.get('semantic_call_failed',0)+semantic.get('semantic_parse_failed',0),
                'Scope Gap':unresolved,'Semantic Incomplete':not options['semantic'] or semantic.get('semantic_not_configured',0) or semantic.get('semantic_not_run',0),
                'Grounding Failure':semantic.get('ungrounded_claims',0)+semantic.get('semantic_validation_failed',0),'Publication Risk':publication.get('review_count',0),
                'Provider Mismatch':not provider_matches}
        health=definition_publication_health(output)
        issues['Definition Publication Health']=health['issues']
        good=contract and options['semantic'] and provider_matches and not scope.get('DISCOVERY_TRUNCATED') and not unresolved and not issues['Provider Failure'] and not issues['Semantic Incomplete'] and not issues['Grounding Failure'] and not health['issues']
        report={'execution_status':'EXECUTION_COMPLETED','quality_status':'QUALITY_ACCEPTED' if good else 'QUALITY_REVIEW_REQUIRED' if options['semantic'] else 'QUALITY_NOT_EVALUATED',
                'RELEASE_CONTRACT_VALID':contract,'OFFICIAL_RECALL':'NOT_EVALUATED','OFFICIAL_PRECISION':'NOT_EVALUATED',
                'coverage_by_type':{k:'NOT_EVALUATED' if k in unresolved else 'SCOPE_DISCOVERED_NOT_INDEPENDENTLY_EVALUATED' for k in ('FUNCTION','OPERATOR','DATATYPE')},
                'DISCOVERY_TRUNCATED':scope.get('DISCOVERY_TRUNCATED',False),'issues':issues,'provider_mode':semantic.get('provider_mode'),
                'actual_model_calls':semantic.get('actual_model_calls',0),'final_feature_count':publication.get('final_count',0),'review_count':publication.get('review_count',0),
                'provider_matches_requested':provider_matches,
                'FRONTEND_PROJECTION_LOSS':losses,'definition_publication_health':health,
                'quality_scope':'verifiable release contract only; not proof of recall/precision'}
        from agents.pipeline.release_quality import describe_quality
        report.update(describe_quality(output, report, result, semantic_enabled=options['semantic']))
        if result.get('final_summary'):
            result['final_summary']['knowledge_quality_status'] = report['quality_status']
        _write_json(output/'quality_report.json',report)
        _write_json(output/'run_manifest.json',{'dbms':dbms,'docs_url':docs_url,'output_root':str(output),**report})
        result.update(report);_write_json(output/'onboarding_report.json',result)
        return result
    except Exception as exc:
        if not existed:
            output.mkdir(parents=True,exist_ok=True)
            import traceback
            (output/'failure_traceback.txt').write_text(traceback.format_exc(),encoding='utf-8')
            from agents.pipeline.release_quality import failed_dimensions
            failure={'execution_status':'EXECUTION_FAILED','quality_status':'QUALITY_NOT_EVALUATED','error_type':type(exc).__name__,'error':str(exc),
                     'quality_dimensions':failed_dimensions(_read_json(output/'preflight/summary.json')),
                     'release_contract_version':'feature-kb-release/v1'}
            _write_json(output/'quality_report.json',failure);_write_json(output/'run_manifest.json',failure)
        raise


def _generic_change_set(baseline_root: Path, output_root: Path, dbms: str) -> dict[str, Any]:
    baseline_path = baseline_root / "unified_pipeline" / "canonical" / "canonical_kb.json"
    current_path = output_root / "unified_pipeline" / "canonical" / "canonical_kb.json"
    baseline = _read_json(baseline_path, []) or []
    current = _read_json(current_path, []) or []
    baseline_ids = {str(row.get("feature_id") or row.get("canonical_identity", {}).get("key")) for row in baseline}
    current_ids = {str(row.get("feature_id") or row.get("canonical_identity", {}).get("key")) for row in current}
    change_set = {
        "schema_version": "generic-feature-crawler-change-set/v1",
        "dbms": dbms,
        "baseline": str(baseline_root),
        "output_root": str(output_root),
        "execution_strategy": "generic_full_canonical_rebuild",
        "profile_reused": False,
        "features": {
            "added": len(current_ids - baseline_ids),
            "removed": len(baseline_ids - current_ids),
            "retained": len(current_ids & baseline_ids),
        },
    }
    _write_json(output_root / "changes" / "change_set.json", change_set)
    _write_json(output_root / "changes" / "update_report.json", change_set)
    return change_set


def run_update(
    dbms: str,
    docs_url: str,
    baseline: str | Path,
    output_root: str | Path,
    **options: Any,
) -> dict[str, Any]:
    baseline_root = Path(baseline).resolve()
    output = Path(output_root).resolve()
    if baseline_root == output:
        raise ValueError("update output must differ from --baseline")
    baseline_manifest = _read_json(baseline_root / "unified_pipeline" / "execution_manifest.json")
    if not isinstance(baseline_manifest, Mapping):
        raise ValueError("baseline is not a Generic Unified run")
    if baseline_manifest.get("pipeline") != "unified":
        raise ValueError("baseline execution manifest is not unified")
    if str(baseline_manifest.get("dbms") or "") != dbms:
        raise ValueError("baseline DBMS does not match --dbms")
    result = run_build(dbms, docs_url, output, **options)
    result["update"] = _generic_change_set(baseline_root, output, dbms)
    _write_json(output / "onboarding_report.json", result)
    return result


__all__ = ["run_build", "run_update"]
