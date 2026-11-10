"""One public dataset per DB; original records and review decisions are preserved."""
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote

ROOT=Path(__file__).resolve().parents[2]
def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.replace(str(tmp),str(path))
def label(db):
    if not re.fullmatch(r'[a-zA-Z0-9_-]+',db):raise ValueError('invalid database identifier')
    return db.lower()
def publish(db,run_root,destination=None,project_root=ROOT):
    project=Path(project_root).resolve();db=label(db);source=Path(run_root).resolve()
    dest=Path(destination).resolve()if destination else project/'output'/db
    if dest==source or source in dest.parents:raise ValueError('public output must be separate from run evidence')
    if dest.exists() and any(p.name not in {'final.json','review.json','summary.json'}for p in dest.iterdir()):
        raise ValueError('public output contains unrelated files; select a dedicated dataset directory')
    final_path=source/'kb/final_feature_kb.json'
    if not final_path.exists():final_path=source/'unified_pipeline/canonical/promotion_ready_kb.json'
    final=read(final_path)
    if not isinstance(final,list):raise ValueError('Final KB must be a list')
    ids=[r['feature_id']for r in final]
    if len(ids)!=len(set(ids)):raise ValueError('duplicate Final feature IDs')
    if any(str(r.get('dbms') or r.get('provenance',{}).get('dbms','')).lower()!=db for r in final):
        raise ValueError('Final KB database assignment mismatch')
    review_path=source/'unified_pipeline/canonical/review_features.jsonl'
    if not review_path.exists():review_path=source/'canonical/review_features.jsonl'
    if not review_path.exists():raise FileNotFoundError('Review artifact missing: '+str(review_path))
    review=[json.loads(l)for l in review_path.read_text(encoding='utf-8').splitlines()if l.strip()]
    quality=read(source/'quality_report.json')if (source/'quality_report.json').exists()else {}
    summary={'dbms':db,'updated_at':datetime.now(timezone.utc).isoformat(),
        'final_count':len(final),'review_count':len(review),
        'by_type':dict(Counter(r['feature_type']for r in final)),
        'with_fields':{f:sum(bool(r.get('semantics',{}).get(f))for r in final)for f in ['description','syntax','examples']},
        'quality_status':quality.get('quality_status','NOT_EVALUATED'),
        'actual_model_calls':quality.get('actual_model_calls','NOT_RECORDED'),
        'official_recall':quality.get('OFFICIAL_RECALL','NOT_EVALUATED'),
        'source_run':str(source),'source_final':str(final_path),'source_review':str(review_path),
        'projection_loss':0,'duplicate_ids':0}
    write(dest/'final.json',final);write(dest/'review.json',review);write(dest/'summary.json',summary)
    registry=project/'frontend/data/catalog.json'
    catalog=read(registry)if registry.exists()else {'databases':[]}
    if not isinstance(catalog,dict)or 'databases'not in catalog:catalog={'databases':[]}
    entry={'dbms':db,'directory':str(dest),**{k:summary[k]for k in ['final_count','review_count','updated_at']}}
    catalog['databases']=sorted([e for e in catalog['databases']if e['dbms']!=db]+[entry],key=lambda e:e['dbms'])
    write(registry,catalog)
    return summary

def handler(project_root=ROOT):
    project=Path(project_root).resolve()
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self,*a,**kw):super().__init__(*a,directory=str(project/'frontend'),**kw)
        def do_GET(self):
            path=unquote(urlsplit(self.path).path)
            if path.startswith('/api/'):
                try:
                    catalog=read(project/'frontend/data/catalog.json')
                    if path=='/api/catalog':value={'databases':[{k:v for k,v in e.items()if k!='directory'}for e in catalog['databases']]}
                    else:
                        parts=path.strip('/').split('/')
                        if len(parts)!=4 or parts[1]!='db' or parts[3]not in {'final','review','summary'}:raise KeyError(path)
                        entry=next(e for e in catalog['databases']if e['dbms']==parts[2])
                        value=read(Path(entry['directory'])/(parts[3]+'.json'))
                    body=json.dumps(value,ensure_ascii=False).encode('utf-8');status=200
                except (KeyError,StopIteration,FileNotFoundError):body=b'{"error":"Dataset not found"}';status=404
                except Exception:body=b'{"error":"Invalid dataset; check server logs"}';status=500
                self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
                self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
                return
            # Only serve viewer assets; dataset access is exclusively registered API.
            if path not in {'/','/index.html','/app.js','/style.css','/favicon.ico'}:
                self.send_error(404);return
            super().do_GET()
    return Handler
def serve(host='127.0.0.1',port=8766):
    print('FeatureCrawler: http://{}:{}/'.format(host,port),flush=True)
    ThreadingHTTPServer((host,port),handler()).serve_forever()
