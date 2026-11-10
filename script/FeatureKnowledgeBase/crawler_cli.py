"""Unified FeatureCrawler CLI. Historical experimental entry points are retired."""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from pathlib import Path
from uuid import uuid4
from agents.runtime_config import load_project_env

ROOT = str(Path(__file__).resolve().parent)


def _add_product_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dbms", required=True, help="DBMS identifier for canonical identities")
    docs_input = parser.add_mutually_exclusive_group(required=True)
    docs_input.add_argument("--docs-url", help="Official documentation entry URL")
    docs_input.add_argument("--docs-manifest", help="Explicit official document collection; bypass scope resolution")
    parser.add_argument("--output-root", help="Public dataset directory; default output/<dbms> (final/review/summary)")
    parser.add_argument('--profile', help='Existing validated generic cold-start Profile JSON; reused by all downstream stages')
    parser.add_argument(
        "--pipeline",
        choices=("unified",),
        default="unified",
        help="The single supported production pipeline.",
    )
    parser.add_argument("--semantic", action="store_true", help="Run semantic enrichment")
    parser.add_argument("--no-semantic", action="store_false", dest="semantic", help="Debug only: disable semantic extraction")
    parser.set_defaults(semantic=True)
    parser.add_argument('--discovery-max-depth',type=int,default=8,help='Scoped crawl safety depth; truncation is reported')
    parser.add_argument(
        "--semantic-budget", type=int, default=None,
        help="Maximum number of real semantic provider calls for this run",
    )
    parser.add_argument(
        "--semantic-provider", choices=("openai_compatible", "deterministic"),
        default="deterministic",
    )
    parser.add_argument("--semantic-model", help="OpenAI-compatible semantic model")
    parser.add_argument("--cache-only", action="store_true", help="Forbid network access and use cached HTML")
    parser.add_argument("--cache-dir", help="Existing Discovery HTML cache for cache-only replay")
    parser.add_argument("--resume", action="store_true", help="Reserved: full-stage resume is currently rejected")
    parser.add_argument('--preflight-sample-pages', type=_sample_count, default=12,
                        help='Bounded structural preflight sample (1-64 pages); not a recall estimate')
    parser.add_argument('--require-preflight', action='store_true',
                        help='Stop before full extraction unless every sampled page passes structural preflight')
    parser.add_argument(
        "--discovery-max-pages", type=int, default=None,
        help="Optional discovery page budget for benchmark/debug runs (default: no page cap)",
    )



def _add_discovery_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dbms", required=True, help="DBMS identifier recorded in the frozen corpus")
    parser.add_argument(
        "--root-url",
        help="Deprecated/manual mode: official documentation root URL. Omit for DBMS-only scope resolution.",
    )
    parser.add_argument(
        "--output", "--output-root", dest="output_root",
        help="New isolated Stage A output directory (default: output/<dbms>_feature_scope_corpus in DBMS-only mode)",
    )
    parser.add_argument("--max-depth", type=int, default=3, help="Maximum generic link depth (default: 3)")
    parser.add_argument("--max-pages", type=int, default=None, help="Optional page budget")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP request timeout in seconds")
    parser.add_argument("--polite-delay", type=float, default=0.0, help="Delay between requests in seconds")



def _add_scope_resolve_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dbms", required=True, help="DBMS name used to resolve official documentation scope")
    parser.add_argument(
        "--output", "--output-root", dest="output_root",
        help="Scope/corpus output directory (default: output/<dbms>_feature_scope_corpus)",
    )
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument(
        "--max-verifications", type=int, default=80,
        help="Bounded official candidate verifications (default: 80)",
    )
    parser.add_argument("--refresh", action="store_true", help="Ignore a cached FeatureScopeManifest")



def _add_scope_crawl_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dbms", required=True)
    parser.add_argument("--scope-manifest", required=True, help="Path to feature_scope_manifest.json")
    parser.add_argument(
        "--output", "--output-root", dest="output_root",
        help="Scoped corpus output directory (default: manifest parent)",
    )
    parser.add_argument("--max-depth", type=int, default=8, help="Safety depth limit; exhaustion is preferred")
    parser.add_argument("--max-pages", type=int, default=None, help="Safety page budget; omit for frontier exhaustion")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--polite-delay", type=float, default=0.0)



def _default_scope_output(dbms: str) -> str:
    return os.path.join(ROOT, "output", f"{dbms}_feature_scope_corpus")



def _run_scope_resolve_command(args: argparse.Namespace) -> bool:
    from agents.discovery.scope_resolver import resolve_feature_scope

    output_root = os.path.abspath(args.output_root or _default_scope_output(args.dbms))
    try:
        result = resolve_feature_scope(
            dbms=args.dbms,
            output_root=output_root,
            timeout=args.timeout,
            max_verifications=args.max_verifications,
            reuse_cache=not args.refresh,
            report_path=Path(ROOT) / "reports" / f"{args.dbms}_feature_scope_discovery.md",
        )
    except Exception as error:
        print(f"Feature scope resolution failed: {type(error).__name__}: {error}")
        return False
    manifest = result.get("manifest", {})
    print("Feature scope resolution completed")
    print(f"Status: {manifest.get('scope_completeness_status', 'UNKNOWN')}")
    for family in ("FUNCTION", "OPERATOR", "DATATYPE"):
        print(f"{family} roots: {len((manifest.get('scopes') or {}).get(family, []))}")
    print(f"Feature identity allowlist entries: {manifest.get('feature_identity_count_in_scope_manifest', 0)}")
    print(f"Manifest: {os.path.join(output_root, 'scope', 'feature_scope_manifest.json')}")
    print(f"Report: {result.get('report_path')}")
    return result.get("status") in {"accepted", "cached"}



def _run_scope_crawl_command(args: argparse.Namespace) -> bool:
    from agents.discovery.generic_stage import run_scoped_discovery

    manifest_path = os.path.abspath(args.scope_manifest)
    output_root = os.path.abspath(args.output_root or os.path.dirname(os.path.dirname(manifest_path)))
    report_name = f"{args.dbms}_feature_scope_crawl.md"
    try:
        result = run_scoped_discovery(
            dbms=args.dbms,
            scope_manifest=manifest_path,
            output_root=output_root,
            max_depth=args.max_depth,
            max_pages=args.max_pages,
            timeout=args.timeout,
            polite_delay=args.polite_delay,
            report_path=Path(ROOT) / "reports" / report_name,
        )
    except Exception as error:
        print(f"Scoped crawl failed: {type(error).__name__}: {error}")
        return False
    summary = result.get("summary", {})
    print("Scoped documentation crawl completed")
    print(f"Stop reason: {summary.get('frontier_stop_reason')}")
    print(f"Frozen corpus pages: {summary.get('fetched_pages', 0)}")
    print(f"Fetch failures: {summary.get('fetch_failures', 0)}")
    print(f"Pending scoped frontier: {summary.get('pending_discovered_urls', 0)}")
    print(f"Report: {result.get('report_path')}")
    return result.get("status") == "accepted"



def _run_discovery_command(args: argparse.Namespace) -> bool:
    from agents.discovery.generic_stage import run_generic_discovery, run_scoped_discovery

    if not args.root_url:
        output_root = os.path.abspath(args.output_root or _default_scope_output(args.dbms))
        from agents.discovery.scope_resolver import resolve_feature_scope
        try:
            resolved = resolve_feature_scope(
                dbms=args.dbms,
                output_root=output_root,
                timeout=args.timeout,
                report_path=Path(ROOT) / "reports" / f"{args.dbms}_feature_scope_discovery.md",
            )
            if resolved.get("status") not in {"accepted", "cached"}:
                print("Feature scope resolution did not produce a usable manifest")
                return False
            result = run_scoped_discovery(
                dbms=args.dbms,
                scope_manifest=os.path.join(output_root, "scope", "feature_scope_manifest.json"),
                output_root=output_root,
                max_depth=max(args.max_depth, 8),
                max_pages=args.max_pages,
                timeout=args.timeout,
                polite_delay=args.polite_delay,
                report_path=Path(ROOT) / "reports" / f"{args.dbms}_feature_scope_crawl.md",
            )
        except Exception as error:
            print(f"DBMS-only discovery failed: {type(error).__name__}: {error}")
            return False
        summary = result.get("summary", {})
        print("DBMS-only Feature Scope Discovery + Scoped Crawl completed")
        print(f"Stop reason: {summary.get('frontier_stop_reason')}")
        print(f"Frozen corpus pages: {summary.get('fetched_pages', 0)}")
        print(f"Fetch failures: {summary.get('fetch_failures', 0)}")
        print(f"Feature scope pages: {json.dumps(summary.get('feature_scope_counts', {}), ensure_ascii=False, sort_keys=True)}")
        print(f"Report: {result.get('report_path')}")
        return result.get("status") == "accepted"
    try:
        result = run_generic_discovery(
            dbms=args.dbms,
            root_url=args.root_url,
            output_root=args.output_root,
            max_depth=args.max_depth,
            max_pages=args.max_pages,
            timeout=args.timeout,
            polite_delay=args.polite_delay,
            report_path=Path(ROOT) / "reports" / f"{args.dbms}_generic_discovery_adaptation.md",
        )
    except Exception as error:
        print(f"Generic Discovery failed: {type(error).__name__}: {error}")
        return False
    summary = result.get("summary", {})
    print("Generic Discovery Stage A completed")
    print(f"Visited/attempted URLs: {summary.get('visited_urls', 0)}")
    print(f"Frozen corpus pages: {summary.get('fetched_pages', 0)}")
    print(f"Fetch failures: {summary.get('fetch_failures', 0)}")
    print(f"Formats: {json.dumps(summary.get('format_counts', {}), ensure_ascii=False, sort_keys=True)}")
    print(f"Index pages: {summary.get('document_role_counts', {}).get('DOCUMENT_INDEX', 0)}")
    print(f"Report: {result.get('report_path')}")
    print(f"Corpus manifest: {os.path.join(os.path.abspath(args.output_root), 'corpus', 'corpus_manifest.json')}")
    return result.get("status") == "accepted"



def _sample_count(value):
    from agents.pipeline.preflight import bounded_count
    return bounded_count(value)


def _run_preflight_command(args):
    from agents.pipeline.preflight import run_preflight, save
    if not args.output_root:
        label = re.sub(r'[^A-Za-z0-9_-]', '_', args.dbms)
        args.output_root = str(Path(ROOT)/'output'/'preflight'/(label+'_'+uuid4().hex))
    output = Path(args.output_root)
    existed = output.exists() and any(output.iterdir())
    try:
        result = run_preflight(args.dbms, args.docs_manifest, args.profile, output,
            sample_pages=args.sample_pages, sample_units=args.sample_units,
            cache_only=args.cache_only, cache_dir=args.cache_dir)
    except Exception as error:
        if not existed:
            save(output/'preflight_summary.json', {'status':'EXECUTION_ERROR', 'error_type':type(error).__name__,
                'error':str(error), 'scope':'Structural preflight only; no Semantic/provider execution',
                'semantic_extraction_executed':False, 'actual_external_llm_api_calls':0})
        print('Structural preflight failed: '+type(error).__name__+': '+str(error))
        return 1
    print('Structural Preflight: '+result['status'])
    print('Sampled Pages: '+str(result['sampled_pages']))
    print('Official Coverage: NOT_EVALUATED')
    print('External LLM API Calls: 0 (no semantic/provider path)')
    print('Report: '+str(output/'preflight/report.md'))
    return 0 if result['status']=='SUPPORTED' else 1 if result['status']=='EXECUTION_ERROR' else 2


def _product_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and safely update canonical DBMS feature knowledge bases",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    viewer=commands.add_parser('serve',help='Serve the single multi-database frontend')
    viewer.add_argument('--port',type=int,default=8766)
    publication=commands.add_parser('publish',help='Register an existing completed run without crawling')
    publication.add_argument('--dbms',required=True)
    publication.add_argument('--run-root',required=True)
    publication.add_argument('--output-root')
    build = commands.add_parser("build", help="Build from one official documentation URL")
    _add_product_options(build)
    update = commands.add_parser("update", help="Update from a previous successful build")
    _add_product_options(update)
    update.add_argument("--baseline", required=True, help="Previous successful output root")
    preflight = commands.add_parser('preflight', help='Inspect URL/Profile structural execution without any semantic provider')
    preflight.add_argument('--dbms', required=True)
    preflight.add_argument('--docs-manifest', required=True)
    preflight.add_argument('--profile', required=True)
    preflight.add_argument('--output-root')
    preflight.add_argument('--cache-only', action='store_true')
    preflight.add_argument('--cache-dir')
    preflight.add_argument('--sample-pages', type=_sample_count, default=12)
    preflight.add_argument('--sample-units', type=_sample_count, default=8)
    discover = commands.add_parser(
        "discover", help="DBMS-only scope resolution + scoped frozen documentation corpus (root-url is deprecated)",
    )
    _add_discovery_options(discover)
    resolve_scopes = commands.add_parser(
        "resolve-scopes", help="Stage 0: resolve official FUNCTION/OPERATOR/DATATYPE documentation roots",
    )
    _add_scope_resolve_options(resolve_scopes)
    crawl_scopes = commands.add_parser(
        "crawl-scopes", help="Stage A: crawl a cached FeatureScopeManifest into a frozen corpus",
    )
    _add_scope_crawl_options(crawl_scopes)
    return parser



def _run_product_command(args: argparse.Namespace) -> bool:
    from agents.pipeline.orchestrator import run_build, run_update

    if args.docs_manifest and args.command != "build":
        print("--docs-manifest is supported for build only")
        return False
    from agents.pipeline.delivery import label as db_label, publish
    public_output=Path(args.output_root or Path(ROOT)/'output'/db_label(args.dbms)).resolve()
    if args.resume:
        if not (public_output/'summary.json').exists():
            print('--resume requires an existing public summary.json');return False
        args.output_root=json.loads((public_output/'summary.json').read_text(encoding='utf-8'))['source_run']
    else:
        args.output_root=str(Path(ROOT)/'.runtime'/'runs'/(db_label(args.dbms)+'_'+uuid4().hex))
    options = {
        "project_root": ROOT,
        "docs_manifest": args.docs_manifest,
        "profile_path": args.profile,
        "discovery_max_depth": args.discovery_max_depth,
        "max_pages": args.discovery_max_pages,
        "cache_only": args.cache_only,
        "cache_dir": args.cache_dir,
        "semantic": args.semantic,
        "semantic_provider": args.semantic_provider,
        "semantic_model": args.semantic_model,
        "semantic_budget": args.semantic_budget,
        "export_frontend_data": True,
        "resume": args.resume,
        "preflight_sample_pages": args.preflight_sample_pages,
        "require_preflight": args.require_preflight,
    }
    try:
        if args.command == "build":
            result = run_build(args.dbms, args.docs_url, args.output_root, **options)
        else:
            if (Path(args.baseline)/'summary.json').exists():
                args.baseline=json.loads((Path(args.baseline)/'summary.json').read_text(encoding='utf-8'))['source_run']
            result = run_update(args.dbms, args.docs_url, args.baseline, args.output_root, **options)
        if result.get('status')=='accepted':
            publish(args.dbms,args.output_root,public_output,project_root=ROOT)
            # The shared viewer is the only retained UI, not one copy per run.
            for base in [Path(args.output_root)/'frontend',Path(args.output_root)/'unified_pipeline/frontend']:
                if base.exists():
                    for asset in base.rglob('*'):
                        if asset.is_file() and asset.suffix.lower() in {'.html','.js','.css'}:asset.unlink()
    except Exception as error:
        print(f"FeatureCrawler {args.command} failed: {type(error).__name__}: {error}")
        return False
    print(f"FeatureCrawler {args.command}: {result.get('status', 'failed')}")
    for label, value in (
        ("Execution Status", result.get("execution_status")),
        ("Quality Status", result.get("quality_status")),
        ("Final Feature Count", result.get("final_feature_count")),
        ("Review Count", result.get("review_count")),
        ("Provider Mode", args.semantic_provider),
        ("Output Directory", str(public_output)),
        ("Final KB Path", str(public_output / "final.json")),
        ("Review KB Path", str(public_output / "review.json")),
        ("Summary Path", str(public_output / "summary.json")),
        ("Frontend", "python run_feature_crawler.py serve"),
    ):
        print(f"{label}: {value}")
    for dimension, observation in (result.get('quality_dimensions') or {}).items():
        print(dimension.title()+' Status: '+str(observation['status']))
    return result.get("status") == "accepted"


def main():
    if len(sys.argv)>1 and sys.argv[1] in {'serve','publish'}:
        from agents.pipeline.delivery import serve,publish
        p=_product_parser()
        a=p.parse_args()
        if a.command=='serve':serve(port=a.port)
        else:
            if not a.dbms or not a.run_root:p.error('publish requires --dbms and --run-root')
            print(json.dumps(publish(a.dbms,a.run_root,a.output_root),ensure_ascii=False,indent=2))
        return 0
    load_project_env(ROOT, override=False)
    args = _product_parser().parse_args()
    if args.command == 'preflight':
        return _run_preflight_command(args)
    handlers = {
        "discover": _run_discovery_command,
        "resolve-scopes": _run_scope_resolve_command,
        "crawl-scopes": _run_scope_crawl_command,
    }
    return 0 if handlers.get(args.command, _run_product_command)(args) else 1


if __name__ == "__main__":
    sys.exit(main())
