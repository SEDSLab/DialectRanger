"""Connect official-root scope discovery and frozen-corpus DocumentIR."""
import hashlib
import json
from pathlib import Path
from agents.discovery.scope_resolver import resolve_feature_scope
from agents.discovery.generic_stage import run_scoped_discovery
from agents.pipeline.presemantic import build_document_ir_from_record


def load_frozen_fetcher(root):
    """Strict local corpus, shared by explicit-document and seed replay."""
    root=Path(root or '')
    manifest=root/'page_manifest.jsonl'
    if not manifest.is_file():manifest=root/'corpus/page_manifest.jsonl'
    if not manifest.is_file():raise ValueError('cache-only build requires corpus/page_manifest.jsonl')
    cached={};types={}
    for row in map(json.loads,manifest.read_text(encoding='utf8').splitlines()):
        # Failed fetch rows are valid audit records with no local payload.
        # Leave them as cache misses; never fall back to network in cache-only.
        if row.get('fetch_status') not in {None,'FETCHED'}:continue
        raw_path=row.get('local_artifact_absolute_path') or row.get('local_artifact_path')
        if not raw_path:raise ValueError('FROZEN_HTML_PATH_RESOLUTION_FAILURE: '+str(row.get('url')))
        path=Path(raw_path)
        if not path.is_absolute():path=manifest.parent/path
        if not path.is_file():raise FileNotFoundError('FROZEN_HTML_MISSING: '+str(path))
        raw=path.read_bytes();expected=row.get('content_hash')
        if expected and hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('FROZEN_CONTENT_HASH_MISMATCH: '+row['url'])
        cached[row['url']]=raw;types[row['url']]=row.get('content_type') or 'text/html'
    def fetch(session,url,**kwargs):
        raw=cached.get(url)
        return {'ok':raw is not None,'raw':raw or b'','final_url':url,'raw_final_url':url,'http_status':200 if raw is not None else None,
            'content_type':types.get(url,'text/html'),'encoding':'utf-8','error_type':None if raw is not None else 'FrozenCacheMiss','attempts':[]}
    fetch.network_free=True
    return fetch,cached


def load_explicit_documents(path, dbms):
    """Validate a URL-only input, never a Feature inventory or type truth."""
    from urllib.parse import urlsplit
    from agents.discovery.generic_stage import _normalize_url
    from agents.discovery.scope_resolver import _official_host_match, _provided_project_source
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if value.get('dbms', '').casefold() != str(dbms).casefold():
        raise ValueError('Documentation manifest DBMS mismatch')
    result = {}
    for row in value.get('documents', []):
        if not isinstance(row, dict) or set(row) - {'url', 'category_hint'}:
            raise ValueError('Document entries support URL and optional category_hint only')
        raw_url=str(row.get('url') or '').strip()
        raw_parts=urlsplit(raw_url)
        if raw_parts.scheme not in {'https','http'} or not raw_parts.hostname or raw_parts.username or raw_parts.password:
            raise ValueError('Invalid explicit document URL')
        url = _normalize_url(raw_url)
        parsed = urlsplit(url)
        if parsed.scheme not in {'https', 'http'} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Invalid official document URL')
        # Explicit manifests are the user's source trust boundary. Product
        # branding need not occur in an official shared-host domain/path.
        hint = row.get('category_hint')
        if hint is not None and hint not in {'FUNCTION', 'OPERATOR', 'DATATYPE'}:
            raise ValueError('Unknown documentation category_hint')
        result.setdefault(url, {'url': url, 'category_hint': hint})
    if not result:
        raise ValueError('Documentation manifest contains no documents')
    return list(result.values())


def explicit_documents(dbms, output, options):
    """Reuse Scoped Crawl and materialization; hints are transport metadata only."""
    from agents.discovery.http_transport import fetch_with_negotiation
    from agents.discovery.scope_resolver import _official_host_match, _provided_project_source
    from bs4 import BeautifulSoup
    from urllib.parse import urlsplit
    rows = load_explicit_documents(options['docs_manifest'], dbms)
    frozen,_ = load_frozen_fetcher(options.get('cache_dir')) if options.get('cache_only') else (None,{})
    scope_root = output / 'scoped_discovery'
    scope = {'schema_version': 'feature-scope-manifest/v1', 'dbms': dbms,
             'input_mode': 'EXPLICIT_DOCUMENTATION_INPUT', 'scopes': {k: [] for k in ('FUNCTION', 'OPERATOR', 'DATATYPE')}}
    for row in rows:
        for family in ([row['category_hint']] if row['category_hint'] else list(scope['scopes'])):
            scope['scopes'][family].append({'url': row['url'], 'scope_kind': 'EXPLICIT_DOCUMENT',
                'provenance': {'source': 'user_document_manifest', 'category_hint_not_type_evidence': True}})
    def fetch(session, url, **kwargs):
        result = (frozen or fetch_with_negotiation)(session, url, **kwargs)
        if result.get('ok'):
            final = result.get('raw_final_url') or result.get('final_url') or url
            parsed=urlsplit(final)
            if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('Invalid explicit document response URL')
            result['official_verification'] = ['user_provided_document_manifest']
        return result
    fetch.network_free=bool(frozen)
    scope_root.mkdir(parents=True, exist_ok=True)
    (output / 'input_manifest.json').write_text(json.dumps({'dbms':dbms,'documents':rows,
        'input_mode':'EXPLICIT_DOCUMENTATION_INPUT'}, indent=2), encoding='utf8')
    crawl = run_scoped_discovery(dbms=dbms, scope_manifest=scope, output_root=scope_root,
        max_depth=0, max_pages=options.get('max_pages'), fetcher=fetch,
        report_path=scope_root / 'explicit_document_crawl.md')
    path = scope_root / 'corpus/page_manifest.jsonl'
    records = [json.loads(x) for x in path.read_text(encoding='utf8').splitlines() if x.strip()]
    documents = []
    for record in records:
        if record.get('fetch_status') != 'FETCHED':
            continue
        # Crawl schema groups roots by family. Do not pass user hints into any
        # downstream structural profile, identity or TypeEvidence consumer.
        neutral = dict(record, feature_scopes=[])
        neutral['provenance'] = dict(record.get('provenance') or {}, feature_scopes=[])
        documents.append(build_document_ir_from_record(neutral, page_manifest_path=path,
            corpus_root=scope_root / 'corpus', dbms=dbms, profile={}))
    summary = dict(crawl.get('summary') or {})
    summary.update({'input_mode':'EXPLICIT_DOCUMENTATION_INPUT','scope_discovery_bypassed':True,
        'category_hints_used_for_type':False,'explicit_document_count':len(rows),'scope_manifest':scope,
        'DISCOVERY_TRUNCATED':bool(summary.get('budget_exhausted')),
        'TRUNCATION_REASON':'page_budget' if summary.get('budget_exhausted') else None,
        'crawl_contract':'only supplied documents; depth zero is intentional, not whole-site coverage',
        'network_fetch':not bool(frozen),'cache_only':bool(frozen)})
    return documents, [], summary


def scoped_documents(dbms, docs_url, output, options):
    if options.get('docs_manifest'):
        return explicit_documents(dbms, output, options)
    frozen = None
    cached = {}
    content_types = {}
    if options.get('cache_only'):
        frozen,cached=load_frozen_fetcher(options.get('cache_dir'))
    scope_root=output/'scoped_discovery'
    resolved=resolve_feature_scope(dbms=dbms,docs_url=docs_url,output_root=scope_root,fetcher=frozen,cached_urls=tuple(cached))
    manifest=resolved['manifest']
    if not any(manifest.get('scopes',{}).values()):raise RuntimeError('SCOPE_UNRESOLVED: no verified feature roots')
    crawl=run_scoped_discovery(dbms=dbms,scope_manifest=manifest,output_root=scope_root,max_depth=options.get('discovery_max_depth',8),
                               max_pages=options.get('max_pages'),fetcher=frozen)
    path=scope_root/'corpus/page_manifest.jsonl'
    records=[json.loads(x) for x in path.read_text(encoding='utf8').splitlines() if x.strip()]
    documents=[build_document_ir_from_record(r,page_manifest_path=path,corpus_root=scope_root/'corpus',dbms=dbms,profile={}) for r in records if r.get('fetch_status')=='FETCHED']
    summary=dict(crawl.get('summary') or {})
    summary.update({'scope_manifest':manifest,'DISCOVERY_TRUNCATED':bool(summary.get('budget_exhausted') or summary.get('depth_blocked_links')),
                    'TRUNCATION_REASON':summary.get('frontier_stop_reason') if summary.get('budget_exhausted') else 'depth_budget' if summary.get('depth_blocked_links') else None,
                    'network_fetch':not bool(frozen),'LIVE_ROOT_DISCOVERY_VALID':'NOT_EVALUATED' if frozen else 'REQUIRES_INDEPENDENT_REVIEW'})
    return documents,[],summary
