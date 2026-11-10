"""DialectRanger publication adapter; never extracts or invents KB content."""
import json
import hashlib
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

TOOL_ROOT=Path(__file__).resolve().parent
REPO_ROOT=TOOL_ROOT.parents[1]
DATA_ROOT=REPO_ROOT/'data/FeatureKnowledgeBase'
WEB_DATA=REPO_ROOT/'web/js/data.js'

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    temporary.replace(path)

def unique(items):
    return list(dict.fromkeys(x for x in items if isinstance(x,str) and x))

def claim_values(record,field):
    return unique(v.get('value') for v in record.get('semantics',{}).get(field,[]) if isinstance(v,dict))

def adapt(record,review=None):
    descriptions=claim_values(record,'description');syntax=claim_values(record,'syntax')
    evidence=[]
    for claims in record.get('semantics',{}).values():
        if isinstance(claims,list):
            for claim in claims:
                if isinstance(claim,dict):
                    evidence.append(claim.get('evidence')or{})
                    evidence.extend(x.get('evidence',{})for x in claim.get('additional_provenance',[])if isinstance(x,dict))
    structural=record.get('structural')or{}
    evidence.extend([structural.get('primary_evidence')or{},*structural.get('supporting_evidence',[])])
    urls=unique(url for ev in evidence if isinstance(ev,dict) for url in [ev.get('source_anchor'),ev.get('url')]
        if isinstance(url,str) and urlparse(url).scheme in {'http','https'})
    sections=unique(ev.get('section_heading') for ev in evidence if isinstance(ev,dict))
    row={'feature_id':record['feature_id'],'dbms':record['dbms'],'feature_type':record.get('feature_type','uncertain'),
        'name':record['name'],'description':descriptions[0]if descriptions else '',
        'descriptions':descriptions,'syntax':syntax,'signature':syntax[0]if syntax else '',
        'examples':claim_values(record,'examples'),'source_anchors':urls,
        'source_sections':sections,'source_sections_display':sections,
        'is_review':review is not None,'status':record.get('semantic_status')or'',
        'semantic_status':record.get('semantic_status'),'quality':record.get('quality'),
        'aliases':structural.get('aliases',[]),'identity_variants':structural.get('signatures',[]),
        'relations':record.get('relations',structural.get('relations',[])),
        'provenance':{k:v for k,v in record.get('provenance',{}).items()
            if k not in {'html_path','source_path','source_run'}}}
    if review is not None:
        row['review_reason']=unique(reason for part in ['admission','publication','validation']
            for reason in (review.get(part)or{}).get('reason_codes',[]))
        row['status']=(review.get('admission')or{}).get('decision')or row['status']
    return row

def load_web_variables(path):
    variables={}
    if Path(path).exists():
        with Path(path).open(encoding='utf8') as source:
            for line in source:
                match=re.match(r'^window\.(DBMS_LIST|FEATURE_DATA|REVIEW_DATA|STATS)\s*=\s*(.*);\s*$',line)
                if match:variables[match[1]]=json.loads(match[2])
    return variables

def dataset_path(path):
    result=(DATA_ROOT/path).resolve()
    if DATA_ROOT.resolve() not in result.parents:raise ValueError('Catalog path outside data root')
    return result

def export_web(data_root=None,web_data=None):
    # Paths are injectable for offline tests; the CLI uses the fixed repo roots.
    global DATA_ROOT,WEB_DATA
    if data_root is not None:DATA_ROOT=Path(data_root)
    if web_data is not None:WEB_DATA=Path(web_data)
    catalog=read(DATA_ROOT/'catalog.json')
    entries=catalog['databases']
    # The registry is the only authority. Never import historical web rows:
    # removing a DB from the registry must remove it from the next export.
    features=[]
    reviews=[]
    counts={}
    for entry in entries:
        final=read(dataset_path(entry['final']));review=read(dataset_path(entry['review']))
        if any(r['dbms']!=entry['dbms'] for r in final):raise ValueError('DB assignment mismatch')
        mapped=[adapt(r)for r in final]
        mapped_review=[adapt(r['record'],r)for r in review]
        if len({r['feature_id']for r in mapped})!=len(mapped):raise ValueError('Duplicate Final ids')
        features.extend(mapped);reviews.extend(mapped_review)
        counts[entry['dbms']]={'source_final':len(final),'web_final':len(mapped),
            'source_review':len(review),'web_review':len(mapped_review),'projection_loss':0}
    if len({r['feature_id']for r in features})!=len(features):raise ValueError('Cross-dataset Final id collision')
    dbms=sorted({r['dbms']for r in features+reviews})
    stats={'total':len(features),'dbms':{db:dict(Counter(r['feature_type']for r in features if r['dbms']==db))for db in dbms}}
    WEB_DATA.parent.mkdir(parents=True,exist_ok=True)
    temporary=WEB_DATA.with_name('data.js.tmp')
    with temporary.open('w',encoding='utf8')as f:
        f.write('// Generated from registered Final/Review datasets. Do not edit manually.\n')
        for name,value in [('DBMS_LIST',dbms),('FEATURE_DATA',features),('REVIEW_DATA',reviews),('STATS',stats)]:
            # Escape script delimiters without changing the JS string value.
            encoded=json.dumps(value,ensure_ascii=False).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
            f.write('window.'+name+' = '+encoded+';\n')
    temporary.replace(WEB_DATA)
    # GitHub Pages/browser caches must not keep a previous data.js after build.
    web_root=WEB_DATA.parent.parent
    index=web_root/'index.html'
    if index.is_file():
        markup=index.read_text(encoding='utf8')
        for asset in ['js/data.js','js/app.js','css/style.css']:
            file=web_root/asset
            if not file.is_file():continue
            revision=hashlib.sha256(file.read_bytes()).hexdigest()[:16]
            markup=re.sub(r'((?:src|href)=["\'])'+re.escape(asset)+r'(?:\?[^"\']*)?(["\'])',
                lambda m:m[1]+asset+'?v='+revision+m[2],markup)
        index.write_text(markup,encoding='utf8')
    report={'managed_datasets':counts,'legacy_passthrough_databases':[],'dataset_policy':'catalog_only','total_web_final':len(features),
        'total_web_review':len(reviews),'duplicate_final_ids':0}
    return report

def publish_dataset(db,public, *, export=True):
    public=Path(public);final=read(public/'final.json');review=read(public/'review.json');summary=read(public/'summary.json')
    if summary['dbms']!=db or len(final)!=summary['final_count'] or len(review)!=summary['review_count']:
        raise ValueError('Published dataset counts/DB mismatch')
    if any(r['dbms']!=db for r in final):raise ValueError('Foreign DB in Final')
    dest=DATA_ROOT/db
    for filename,value in [('final.json',final),('review.json',review)]:write(dest/filename,value)
    # Keep the public summary portable. Raw KB and official field provenance
    # remain unchanged; machine-specific runtime pointers are local-only.
    portable={k:v for k,v in summary.items()if k not in {'source_run','source_final','source_review'}}
    portable['runtime_artifacts_published']=False
    write(dest/'summary.json',portable)
    catalog=read(DATA_ROOT/'catalog.json')if (DATA_ROOT/'catalog.json').exists()else {'schema_version':'dialectranger-feature-catalog/v1','databases':[]}
    entry={'dbms':db,'final':db+'/final.json','review':db+'/review.json','summary':db+'/summary.json'}
    catalog['databases']=sorted([e for e in catalog['databases']if e['dbms']!=db]+[entry],key=lambda e:e['dbms'])
    write(DATA_ROOT/'catalog.json',catalog)
    return export_web() if export else {'dbms':db,'final_count':len(final),'review_count':len(review)}
