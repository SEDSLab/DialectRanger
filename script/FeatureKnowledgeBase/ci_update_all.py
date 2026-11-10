"""Catalog-driven CI orchestration. Each worker produces an isolated artifact."""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from integration import TOOL_ROOT, DATA_ROOT, read, write, publish_dataset, export_web


def registered():
    names=[e['dbms'] for e in read(DATA_ROOT/'catalog.json')['databases']]
    if not names or len(names)!=len(set(names)) or any(not re.fullmatch(r'[a-z][a-z0-9_-]*',n)for n in names):
        raise ValueError('Empty, duplicate or invalid catalog DBMS')
    return sorted(names)


def collect(root):
    results=[]
    for db in registered():
        folder=Path(root)/db
        status=read(folder/'result.json') if (folder/'result.json').exists() else {'status':'MISSING_ARTIFACT'}
        if status.get('dbms')!=db or status.get('status')!='PASS':
            results.append({'dbms':db,'status':status.get('status','FAILED'),'old_data_preserved':True})
            continue
        try:
            audit=read(folder/'validation.json');summary=read(folder/'summary.json')
            final=read(folder/'final.json');review=read(folder/'review.json')
            if audit.get('status')!='PASS' or audit.get('dbms')!=db or audit.get('network_fetch')is not True or audit.get('actual_external_llm_api_calls')!=0:
                raise ValueError('Artifact validation not PASS/online/zero model calls')
            if summary.get('actual_model_calls')!=0 or summary.get('projection_loss')!=0:
                raise ValueError('Invalid model/projection accounting')
            if audit.get('candidate_final')!=len(final)or audit.get('candidate_review')!=len(review):
                raise ValueError('Artifact count mismatch')
            if not final or len({r['feature_id'] for r in final})!=len(final):
                raise ValueError('Empty or duplicate Final')
            if any(r.get('dbms')!=db for r in final+[r.get('record',r)for r in review]):
                raise ValueError('Foreign database in artifact')
            baseline=read(DATA_ROOT/db/'final.json')
            if len(final)<0.9*len(baseline):
                raise ValueError('Count drop vs current default branch exceeds ten percent')
            # publish_dataset checks DB assignments and summary counts before writing.
            publish_dataset(db,folder,export=False)
            results.append({'dbms':db,'status':'UPDATED','final':len(final),'review':len(review)})
        except (ValueError,KeyError,TypeError,OSError) as exc:
            results.append({'dbms':db,'status':'INVALID_ARTIFACT','error_type':type(exc).__name__,'old_data_preserved':True})
    projection=export_web()
    summary={'results':results,'updated':sum(r['status']=='UPDATED'for r in results),
             'preserved':sum(r['status']!='UPDATED'for r in results),'projection':projection}
    write(TOOL_ROOT/'.runtime/ci/all_update_summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('plan')
    worker=sub.add_parser('worker');worker.add_argument('--dbms',required=True)
    merge=sub.add_parser('collect');merge.add_argument('--artifact-root',type=Path,required=True)
    a=p.parse_args()
    if a.command=='plan':
        names=registered()
        for db in names:
            for filename in ('urls.json','profile.json'):
                if not (TOOL_ROOT/'inputs'/db/filename).is_file():raise FileNotFoundError(db+'/'+filename)
        matrix=json.dumps(names)
        print(matrix)
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf8')as f:f.write('databases='+matrix+'\n')
    elif a.command=='worker':
        if a.dbms not in registered():p.error('DBMS is not registered')
        folder=TOOL_ROOT/'.runtime/ci/results'/a.dbms
        write(folder/'result.json',{'dbms':a.dbms,'status':'RUNNING'})
        code=subprocess.run([sys.executable,str(TOOL_ROOT/'ci_update.py'),'--dbms',a.dbms,
            '--candidate-only','--artifact-dir',str(folder)],cwd=str(TOOL_ROOT)).returncode
        write(folder/'result.json',{'dbms':a.dbms,'status':'PASS' if code==0 else 'FAILED','returncode':code})
        return code
    else:
        collect(a.artifact_root)
    return 0


if __name__=='__main__':sys.exit(main())
