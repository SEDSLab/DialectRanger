import json
from pathlib import Path
import pytest
import crawler_cli as cli
from agents.pipeline import scoped_build


def test_cli_input_modes():
    parser = cli._product_parser()
    a = parser.parse_args(['build','--dbms','fixture','--docs-manifest','docs.json'])
    assert a.docs_url is None and a.semantic and a.semantic_provider == 'deterministic'
    with pytest.raises(SystemExit):
        parser.parse_args(['build','--dbms','fixture','--docs-manifest','docs.json','--docs-url','https://fixture.example'])


def test_manifest_validation(tmp_path):
    path=tmp_path/'input.json'
    def save(rows,db='fixture'):
        path.write_text(json.dumps({'dbms':db,'documents':rows}))
    save([{'url':'https://fixture.example/functions/#a'}, {'url':'https://fixture.example/functions/'}])
    assert len(scoped_build.load_explicit_documents(path,'fixture')) == 1
    save([{'url':'https://shared-host.example/product/reference'}])
    assert scoped_build.load_explicit_documents(path,'fixture')
    for rows,db in [([{'url':'file:///local/docs'}],'fixture'),
                    ([{'url':'https://user:password@fixture.example/docs'}],'fixture'),
                    ([{'url':'https://fixture.example','identity':'ABS'}],'fixture'),
                    ([{'url':'https://fixture.example'}],'other')]:
        save(rows,db)
        with pytest.raises(ValueError):scoped_build.load_explicit_documents(path,'fixture')


def test_no_resolver_and_no_type_hint_propagation(tmp_path,monkeypatch):
    path=tmp_path/'input.json'
    path.write_text(json.dumps({'dbms':'fixture','documents':[{'url':'https://fixture.example/functions','category_hint':'DATATYPE'}]}))
    monkeypatch.setattr(scoped_build,'resolve_feature_scope',lambda **kw:pytest.fail('resolver must not execute'))
    def crawl(**kw):
        assert kw['max_depth']==0
        target=kw['output_root']/'corpus/page_manifest.jsonl';target.parent.mkdir(parents=True)
        target.write_text(json.dumps({'url':'https://fixture.example/functions','fetch_status':'FETCHED',
            'feature_scopes':['DATATYPE'],'provenance':{'feature_scopes':['DATATYPE']}})+'\n')
        return {'summary':{'budget_exhausted':False,'depth_blocked_links':10}}
    monkeypatch.setattr(scoped_build,'run_scoped_discovery',crawl)
    def materialize(row,**kw):
        assert not row['feature_scopes'] and not row['provenance']['feature_scopes']
        return {'page_id':'test'}
    monkeypatch.setattr(scoped_build,'build_document_ir_from_record',materialize)
    docs,_,summary=scoped_build.scoped_documents('fixture',None,tmp_path/'output',{'docs_manifest':path})
    assert docs and summary['scope_discovery_bypassed'] and not summary['DISCOVERY_TRUNCATED']
