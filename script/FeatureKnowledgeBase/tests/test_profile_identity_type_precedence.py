import json
import pytest
from tests.test_authoritative_profile_fields import configured
from tests.test_profile_definition_execution import document, run


def plans(d, entry_type='FUNCTION'):
    p=configured(d)
    from agents.owner.definition_patterns import default_patterns
    p['hint_contract']['definition_patterns']=default_patterns()
    fields=[{'role':'DESCRIPTION','locator':{'kind':'BODY_PROSE'},'block_kinds':['TEXT']}]
    p['hint_contract']['definition_structure_patterns']=[
        {'plan_id':'catalog','structure':'D','identity_slot':'VERIFIED_IDENTITY_CELL','boundary':'CURRENT_ROW',
         'execution':{'version':1,'authority':'EXPLICIT_DEFINITION','declaration_kind':'FUNCTION',
            'identity':{'alternatives':[{'kind':'COLUMN_HEADER','headers':['Name'],
                'text_pattern':r'([A-Z]+)\(\)','capture_group':1}],'cardinality':'ONE'},
            'fields':[{'role':'DESCRIPTION','locator':{'kind':'COLUMN_HEADER','headers':['Description']}}]}},
        {'plan_id':'definition','structure':'C','identity_slot':'LEADING_TERM','boundary':'CURRENT_ENTRY',
         'execution':{'version':1,'authority':'EXPLICIT_DEFINITION','declaration_kind':entry_type,
            'identity':{'alternatives':[{'kind':'FIRST_PARAGRAPH_CODE',
                'text_pattern':r'([A-Z]+)(?:\(.*\))?','capture_group':1}],'cardinality':'ONE'},
            'fields':fields}}]
    for plan in p['hint_contract']['definition_structure_patterns']:
        plan['execution']['when']={'page_path_prefixes':['/reference']}
    return p


def units(tmp_path):
    return [json.loads(line) for line in (tmp_path/'run/identity/groups.jsonl').open(encoding='utf8')]


def test_mixed_heading_catalog_and_entry_form_one_identity(tmp_path):
    d=document(tmp_path,'Numeric Functions and Operators',
        '<table><tr><th>Name</th><th>Description</th></tr>'
        '<tr><td>MAGNITUDE()</td><td>Short summary.</td></tr></table>'
        '<ul><li><p><code>MAGNITUDE(X)</code></p><p>Detailed explanation.</p></li></ul>')
    rows=run(tmp_path,d,plans(d))
    matches=[u for u in units(tmp_path)if u['canonical_identity']=='MAGNITUDE']
    assert len(matches)==1
    assert matches[0]['feature_type']=='FUNCTION'
    feature=next(r for r in rows if r['name']=='MAGNITUDE')
    assert len([r for r in rows if r['name']=='MAGNITUDE'])==1
    text=str(feature['semantics']['description'])
    assert 'Short summary.'in text and 'Detailed explanation.'in text


def test_verified_same_name_different_types_remain_separate(tmp_path):
    d=document(tmp_path,'Functions and Operators',
        '<table><tr><th>Name</th><th>Description</th></tr>'
        '<tr><td>SCALAR()</td><td>Converts a value.</td></tr></table>'
        '<ul><li><p><code>SCALAR</code></p><p>A scalar storage declaration.</p></li></ul>')
    rows=run(tmp_path,d,plans(d,'DATATYPE'))
    assert {r['feature_type']for r in rows if r['name']=='SCALAR'}=={'function','datatype'}
    assert {u['feature_type']for u in units(tmp_path)if u['canonical_identity']=='SCALAR'}=={'FUNCTION','DATATYPE'}


def test_stale_profile_source_cannot_gain_type_authority(tmp_path):
    d=document(tmp_path,'Numeric Functions and Operators',
        '<table><tr><th>Name</th><th>Description</th></tr>'
        '<tr><td>MAGNITUDE()</td><td>Short summary.</td></tr></table>')
    p=plans(d)
    # A changed frozen source must not be trusted using a stale DocumentIR.
    from pathlib import Path
    Path(d['html_path']).write_text('<main><p>Replaced content.</p></main>',encoding='utf8')
    with pytest.raises(ValueError,match='No identity groups found'):
        run(tmp_path,d,p)
    assert not units(tmp_path)


def test_cross_page_catalog_and_definition_reconcile_before_type_stage(tmp_path):
    from agents.document_structure_v2.topology import build_page_topology
    from agents.contracts.document_ir import from_v2_page
    from agents.pipeline.generic_unified import run_generic_downstream
    docs=[]
    for name,html in [
        ('catalog','<h1>Numeric Functions and Operators</h1><table><tr><th>Name</th><th>Description</th></tr>'
         '<tr><td>MAGNITUDE()</td><td>Short summary.</td></tr></table>'),
        ('definition','<h1>Mathematical Functions</h1><ul><li><p><code>MAGNITUDE(X)</code></p>'
         '<p>Detailed explanation.</p></li></ul>')]:
        path=tmp_path/(name+'.html');path.write_text('<main>'+html+'</main>',encoding='utf8')
        d=from_v2_page(build_page_topology({'url':'https://official.test/'+name,'html_path':str(path)},{}),
            dbms='sample',html_path=str(path));d['document_id']=d['page_id'];docs.append(d)
    p=plans(docs[0])
    p['hint_contract']['definition_structure_patterns'][0]['execution']['when']['page_path_prefixes']=['/catalog']
    p['hint_contract']['definition_structure_patterns'][1]['execution']['when']['page_path_prefixes']=['/definition']
    run_generic_downstream(dbms='sample',documents=docs,profile=p,output_root=tmp_path/'run',
        semantic=True,semantic_provider='deterministic')
    matched=[u for u in units(tmp_path)if u['canonical_identity']=='MAGNITUDE']
    assert len(matched)==1 and matched[0]['feature_type']=='FUNCTION'
    assert len(matched[0]['source_urls'])==2
    rows=json.loads((tmp_path/'run/canonical/promotion_ready_kb.json').read_text(encoding='utf8'))
    feature=next(r for r in rows if r['name']=='MAGNITUDE')
    assert len([r for r in rows if r['name']=='MAGNITUDE'])==1
    assert 'Short summary.'in str(feature['semantics']['description'])
    assert 'Detailed explanation.'in str(feature['semantics']['description'])
