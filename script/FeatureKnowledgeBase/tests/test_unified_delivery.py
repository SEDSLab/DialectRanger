import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen
from urllib.error import HTTPError
import pytest
from agents.pipeline.delivery import publish,handler

def fixture(tmp_path):
    run=tmp_path/'run';(run/'kb').mkdir(parents=True)
    (run/'unified_pipeline/canonical').mkdir(parents=True)
    row={'feature_id':'id','dbms':'testdb','name':'ABS','feature_type':'function',
         'semantics':{'description':[{'value':'First paragraph'},{'value':'Second paragraph'}],
                      'syntax':[],'examples':[]}}
    (run/'kb/final_feature_kb.json').write_text(json.dumps([row]),encoding='utf-8')
    (run/'unified_pipeline/canonical/review_features.jsonl').write_text(json.dumps({'record':dict(row,feature_id='review'),'publication':{'published':False}})+'\n',encoding='utf-8')
    return run,row

def test_delivery_preserves_records_and_replaces_one_database(tmp_path):
    run,row=fixture(tmp_path)
    publish('testdb',run,project_root=tmp_path)
    dest=tmp_path/'output/testdb'
    assert {f.name for f in dest.iterdir()}=={'final.json','review.json','summary.json'}
    assert json.loads((dest/'final.json').read_text(encoding='utf-8'))==[row]
    publish('testdb',run,project_root=tmp_path)
    assert len(json.loads((tmp_path/'frontend/data/catalog.json').read_text())['databases'])==1
    assert json.loads((dest/'summary.json').read_text())['review_count']==1

def test_delivery_rejects_duplicates_and_missing_reviews(tmp_path):
    run,row=fixture(tmp_path)
    (run/'kb/final_feature_kb.json').write_text(json.dumps([row,row]))
    with pytest.raises(ValueError):publish('testdb',run,project_root=tmp_path)
    (run/'kb/final_feature_kb.json').write_text(json.dumps([row]))
    (run/'unified_pipeline/canonical/review_features.jsonl').unlink()
    with pytest.raises(FileNotFoundError):publish('testdb',run,project_root=tmp_path)
    assert not (tmp_path/'frontend/data/catalog.json').exists()

def test_http_api_always_returns_json_and_limits_paths(tmp_path):
    run,row=fixture(tmp_path);publish('testdb',run,project_root=tmp_path)
    import io
    def get(path):
        h=object.__new__(handler(tmp_path));h.path=path;h.command='GET'
        h.request_version='HTTP/1.1';h.requestline='GET '+path+' HTTP/1.1'
        h.client_address=('127.0.0.1',0);h.wfile=io.BytesIO()
        h.do_GET();return h.wfile.getvalue()
    response=get('/api/db/testdb/final')
    assert b'application/json' in response.split(b'\r\n\r\n')[0]
    assert json.loads(response.split(b'\r\n\r\n')[1])==[row]
    missing=get('/api/db/missing/final')
    assert b'404' in missing.split(b'\r\n')[0]
    assert json.loads(missing.split(b'\r\n\r\n')[1])['error']=='Dataset not found'
    assert b'404' in get('/data/catalog.json').split(b'\r\n')[0]
