"""DialectRanger build/export/serve entry; the crawler engine stays unchanged."""
import argparse
import json
import subprocess
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from integration import TOOL_ROOT,REPO_ROOT,publish_dataset,export_web,write

class PreviewHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control','no-store')
        super().end_headers()

def main():
    p=argparse.ArgumentParser(description='Build official Feature KB and update DialectRanger web')
    sub=p.add_subparsers(dest='command',required=True)
    build=sub.add_parser('build');build.add_argument('--dbms',required=True)
    build.add_argument('--docs-manifest');build.add_argument('--profile')
    build.add_argument('--cache-only',action='store_true');build.add_argument('--cache-dir')
    build.add_argument('--semantic',action='store_true',help='Semantic is always enabled')
    build.add_argument('--semantic-provider',choices=['deterministic'],default='deterministic')
    publish=sub.add_parser('publish');publish.add_argument('--dbms',required=True);publish.add_argument('--public-root',required=True)
    sub.add_parser('export-web')
    serve=sub.add_parser('serve');serve.add_argument('--port',type=int,default=8771)
    args=p.parse_args()
    if args.command=='serve':
        handler=partial(PreviewHandler,directory=str(REPO_ROOT/'web'))
        server=ThreadingHTTPServer(('127.0.0.1',args.port),handler)
        print('DialectRanger: http://127.0.0.1:'+str(args.port)+'/',flush=True)
        server.serve_forever();return 0
    if args.command=='export-web':result=export_web()
    else:
        import re
        db=args.dbms.lower()
        if not re.fullmatch(r'[a-z][a-z0-9_-]*',db):p.error('Invalid DBMS identifier')
        if args.command=='build':
            public=TOOL_ROOT/'.runtime/public'/db
            # Resolve user paths before changing the child process cwd.
            docs=Path(args.docs_manifest).resolve()if args.docs_manifest else TOOL_ROOT/'inputs'/db/'urls.json'
            profile=Path(args.profile).resolve()if args.profile else TOOL_ROOT/'inputs'/db/'profile.json'
            command=[sys.executable,str(TOOL_ROOT/'crawler_cli.py'),'build','--dbms',db,
                '--docs-manifest',str(docs),'--profile',str(profile),'--output-root',str(public),
                '--semantic','--semantic-provider','deterministic']
            if args.cache_only:command.append('--cache-only')
            if args.cache_dir:command.extend(['--cache-dir',str(Path(args.cache_dir).resolve())])
            completed=subprocess.run(command,cwd=str(TOOL_ROOT))
            if completed.returncode:return completed.returncode
        else:public=Path(args.public_root).resolve()
        result=publish_dataset(db,public)
    write(TOOL_ROOT/'.runtime/integration/latest_export.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    print('Web directory: '+str(REPO_ROOT/'web'))
    return 0

if __name__=='__main__':sys.exit(main())
