"""Atomic streaming JSON output; never build a whole-file Unicode copy."""
import json
import os
from pathlib import Path
import tempfile


def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    handle=tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',newline='\n',
                                      dir=str(path.parent),prefix=path.name+'.',suffix='.tmp',delete=False)
    temporary=Path(handle.name)
    try:
        with handle:
            for chunk in json.JSONEncoder(ensure_ascii=False,indent=2,sort_keys=True).iterencode(value):handle.write(chunk)
            handle.write('\n')
        os.replace(str(temporary),str(path))
    finally:
        if temporary.exists():temporary.unlink()


def write_jsonl(path,rows):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8',newline='\n') as stream:
        for row in rows:
            for chunk in json.JSONEncoder(ensure_ascii=False,sort_keys=True).iterencode(row):stream.write(chunk)
            stream.write('\n')


def build_report(result,root):
    """Public build summary plus references; detailed evidence stays in stage files."""
    heavy={'type','evidence','semantic','sense','canonical','conservation','identity','publication','execution_manifest'}
    report={k:v for k,v in result.items() if k not in heavy}
    report['report_schema_version']='unified-build-report/v2'
    for name in ('semantic','sense'):
        report[name]={'summary':(result.get(name) or {}).get('summary',{})}
    publication=result.get('publication') or {}
    report['publication']={k:v for k,v in publication.items() if k!='decisions'}
    report['artifact_root']=str(Path(root).resolve())
    report['stage_artifacts']={
        'type':'unified_pipeline/type/type_decisions.jsonl',
        'identity':'unified_pipeline/identity/groups.jsonl',
        'evidence':'unified_pipeline/evidence/bundles.jsonl',
        'semantic':'unified_pipeline/semantic_stage/semantic/semantic_features.jsonl',
        'sense':'unified_pipeline/sense/sense/semantic_features_sense_aware.jsonl',
        'canonical':'unified_pipeline/canonical/canonical_features.jsonl',
        'publication':'unified_pipeline/canonical/publication_decisions.jsonl',
        'final':'kb/final_feature_kb.json','frontend':'frontend/data/index.json',
        'execution_manifest':'unified_pipeline/execution_manifest.json'}
    return report
