import json
import pytest
import integration

def record(fid='sample.function.alpha'):
    return {'feature_id':fid,'dbms':'sample','name':'ALPHA','feature_type':'function',
        'semantic_status':'PARTIAL','semantics':{'description':[
            {'value':'First paragraph.','evidence':{'url':'https://official.test/alpha','section_heading':'Alpha'}},
            {'value':'Second paragraph.','evidence':{'url':'https://official.test/alpha#details'}}],
            'syntax':[{'value':'ALPHA(X)','evidence':{'url':'https://official.test/alpha'}}],
            'examples':[{'value':'SELECT ALPHA(2);\n2','evidence':{'url':'https://official.test/alpha'}}]},
        'structural':{'signatures':['ALPHA()'],'aliases':['BETA'],
            'primary_evidence':{'url':'https://official.test/catalog'}},'provenance':{'identity_unit_id':'u1'}}

def test_projection_preserves_all_paragraphs_and_official_fields():
    r=record();v=integration.adapt(r)
    assert v['descriptions']==['First paragraph.','Second paragraph.']
    assert v['syntax']==['ALPHA(X)'] and v['signature']=='ALPHA(X)'
    assert v['identity_variants']==['ALPHA()']
    assert v['examples']==['SELECT ALPHA(2);\n2']
    assert v['source_anchors'][0]=='https://official.test/alpha'

def test_variants_do_not_become_syntax():
    r=record();r['semantics']['syntax']=[]
    v=integration.adapt(r)
    assert v['syntax']==[] and v['signature']=='' and v['identity_variants']==['ALPHA()']

def test_publish_exports_only_registered_databases_preserves_files(tmp_path,monkeypatch):
    data=tmp_path/'data';asset=tmp_path/'web/js/data.js'
    monkeypatch.setattr(integration,'DATA_ROOT',data);monkeypatch.setattr(integration,'WEB_DATA',asset)
    asset.parent.mkdir(parents=True)
    (asset.parent.parent/'index.html').write_text('<script src="js/data.js"></script>',encoding='utf8')
    asset.write_text('window.FEATURE_DATA = '+json.dumps([
        {'dbms':'legacy','feature_id':'legacy.one','feature_type':'function'},
        {'dbms':'sample','feature_id':'old.sample','feature_type':'function'}])+';\n'
        'window.REVIEW_DATA = [];\n',encoding='utf8')
    old=data/'sample/function/old_snapshot.json';old.parent.mkdir(parents=True);old.write_text('preserve')
    public=tmp_path/'public';public.mkdir()
    integration.write(public/'final.json',[record()])
    review={'record':record('sample.review.one'),'admission':{'decision':'REJECTED','reason_codes':['SUBJECT_CONFLICT']}}
    integration.write(public/'review.json',[review])
    integration.write(public/'summary.json',{'dbms':'sample','final_count':1,'review_count':1,'source_run':'C:/local/cache'})
    report=integration.publish_dataset('sample',public)
    assert report['managed_datasets']['sample']['projection_loss']==0
    result=integration.load_web_variables(asset)
    assert len(result['FEATURE_DATA'])==1 and len(result['REVIEW_DATA'])==1
    assert result['DBMS_LIST']==['sample']
    assert not any(r['feature_id']=='old.sample'for r in result['FEATURE_DATA'])
    assert result['STATS']['total']==1
    assert result['REVIEW_DATA'][0]['review_reason']==['SUBJECT_CONFLICT']
    assert old.read_text()=='preserve'
    assert 'js/data.js?v='in (asset.parent.parent/'index.html').read_text(encoding='utf8')
    assert integration.read(data/'sample/final.json')==[record()]
    assert 'source_run'not in integration.read(data/'sample/summary.json')
    # Re-export is idempotent and does not add the managed data twice.
    assert integration.export_web()==report
    assert integration.load_web_variables(asset)==result

def test_catalog_cannot_read_outside_data(tmp_path,monkeypatch):
    monkeypatch.setattr(integration,'DATA_ROOT',tmp_path/'data')
    with pytest.raises(ValueError,match='outside'):integration.dataset_path('../secret.json')

def test_script_delimiters_are_escaped(tmp_path,monkeypatch):
    data=tmp_path/'data';asset=tmp_path/'data.js'
    monkeypatch.setattr(integration,'DATA_ROOT',data);monkeypatch.setattr(integration,'WEB_DATA',asset)
    r=record();r['name']='</script>'
    integration.write(data/'catalog.json',{'databases':[{'dbms':'sample','final':'sample/final.json','review':'sample/review.json'}]})
    integration.write(data/'sample/final.json',[r])
    integration.write(data/'sample/review.json',[])
    integration.export_web()
    assert '</script>'not in asset.read_text(encoding='utf8')
    assert integration.load_web_variables(asset)['FEATURE_DATA'][0]['name']=='</script>'

def test_empty_catalog_removes_stale_web_rows(tmp_path,monkeypatch):
    data=tmp_path/'data';asset=tmp_path/'data.js'
    monkeypatch.setattr(integration,'DATA_ROOT',data);monkeypatch.setattr(integration,'WEB_DATA',asset)
    integration.write(data/'catalog.json',{'databases':[]})
    asset.write_text('window.FEATURE_DATA = [{"dbms":"old"}];\nwindow.REVIEW_DATA = [{"dbms":"old"}];\n',encoding='utf8')
    integration.export_web()
    result=integration.load_web_variables(asset)
    assert result['FEATURE_DATA']==[] and result['REVIEW_DATA']==[] and result['DBMS_LIST']==[]
