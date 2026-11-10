import copy
import pytest
from tests.test_profile_definition_execution import document
from tests.test_five_structure_executors import profile_for
from agents.owner.structure_execution import execute_structures
from agents.owner.profile_execution import compile_execution, explicit_declarations, revalidate_explicit_authority, field_candidates


def configured(d, family='A', locator=None):
    p=profile_for(d,family)
    p['hint_contract']['definition_structure_patterns'][0]['execution']={
        'version':1,'authority':'EXPLICIT_DEFINITION','declaration_kind':'FUNCTION',
        'identity':{'alternatives':[locator or {'kind':'HEADING','heading_level':1,
            'text_pattern':r'^([@A-Za-z_][@A-Za-z0-9_$]*)\s+\(Reference\)$','capture_group':1}], 'cardinality':'ONE'},
        'when':{'page_path_prefixes':['/reference']},
        'boundary':{'stop_headings':['Related content','Feedback','Additional resources'],'exclude_text_prefixes':['Applies to:']},
        'fields':[{'role':'FORMAL_SYNTAX','locator':{'kind':'SECTION_LABEL','labels':['Syntax']},'block_kinds':['CODE']},
                  {'role':'DESCRIPTION','locator':{'kind':'BODY_PROSE'}}]}
    return p


def test_authoritative_h1_needs_no_grammar_and_ignores_parameter_headings(tmp_path):
    d=document(tmp_path,'opaque (Reference)','<p>Applies to: Anything</p><p>Behavior.</p><h2>Syntax</h2><pre>opaque grammar ???</pre>'
        '<h2>OVER</h2><p>Details.</p><h2>Related content</h2><p>Foreign links.</p>')
    result=execute_structures(d,configured(d))
    proof=next(p for p in result['proofs'] if p['identity_values']==['OPAQUE'])
    members=[b['text'] for b in d['blocks'] if b['block_id'] in proof['block_ids']]
    assert 'Details.' in members and 'Foreign links.' not in members
    anchor=next(b for b in d['blocks'] if b['block_id']==proof['heading_block_id'])
    assert revalidate_explicit_authority(proof,anchor,'OPAQUE')
    bad=copy.deepcopy(anchor);bad['text']='forged'
    assert revalidate_explicit_authority(proof,bad,'OPAQUE') is None
    path=anchor['provenance']['html_path']
    with open(path,'a') as f:f.write('changed')
    assert revalidate_explicit_authority(proof,anchor,'OPAQUE') is None


@pytest.mark.parametrize('title',['Category Functions','opaque (Reference) extra'])
def test_identity_full_match_excludes_categories(tmp_path,title):
    d=document(tmp_path,title,'<p>Behavior.</p>')
    assert not execute_structures(d,configured(d))['proofs']


def test_split_normalization_and_empty_rejection():
    cfg={'declaration_kind':'DATATYPE'}
    loc={'split_pattern':r'(?:\s*,\s*(?:and\s+)?|\s+and\s+)','name_pattern':r'[a-z]+'}
    assert [d['identity'] for d in explicit_declarations('one, two, and three',{},loc,cfg)]==['ONE','TWO','THREE']
    assert explicit_declarations('one,,two',{},loc,cfg)==[]
    assert explicit_declarations('"Quoted"',{}, {},cfg)[0]['identity']=='"Quoted"'


@pytest.mark.parametrize('family,slot,body,name',[
    ('D',{'kind':'COLUMN_HEADER','headers':['Name']},'<table><tr><th>Name</th><th>Meaning</th></tr><tr><td>opaque</td><td>Behavior.</td></tr></table>','OPAQUE'),
    ('C',{'kind':'FIRST_PARAGRAPH_CODE','name_pattern':r'opaque'},'<ul><li><p><code>opaque</code></p><p>Behavior.</p></li></ul>','OPAQUE')])
def test_opaque_configured_slots(tmp_path,family,slot,body,name):
    d=document(tmp_path,'Catalog',body)
    result=execute_structures(d,configured(d,family,slot))
    proof=next(p for p in result['proofs'] if p['identity_values']==[name])
    anchor=next(b for b in d['blocks'] if b['block_id']==proof['heading_block_id'])
    assert revalidate_explicit_authority(proof,anchor,name)


def test_authority_validation(tmp_path):
    d=document(tmp_path,'opaque (Reference)','<p>Behavior.</p>')
    p=configured(d);plan=p['hint_contract']['definition_structure_patterns'][0]
    for mutate in [lambda x:x.update(authority='OTHER'),lambda x:x['identity']['alternatives'][0].update(capture_group=7),lambda x:x.update(boundary={'unknown':[]})]:
        bad=copy.deepcopy(plan);mutate(bad['execution'])
        with pytest.raises(ValueError):compile_execution(bad)
    bad=copy.deepcopy(plan);bad['structure']='E'
    with pytest.raises(ValueError,match='UNSUPPORTED_STRUCTURE_E'):compile_execution(bad)

def test_filters_and_opaque_syntax_are_literal_field_bindings(tmp_path):
    from agents.semantic_stage.source_structure import SourceStructureIndex
    d=document(tmp_path,'opaque (Reference)','<p>Applies to: Anywhere</p><p>Behavior.</p><h2>Syntax</h2><p>Introductory prose.</p><pre>??? arbitrary notation</pre><h2>Feedback</h2><p>Tail.</p>')
    proof=next(p for p in execute_structures(d,configured(d))['proofs'] if p['identity_values']==['OPAQUE'])
    reader=SourceStructureIndex()
    evidence={b['block_id']:{**b,**reader.observe(d,b),'source_url':d['url'],'source_order':b['order'], 'owner_local_binding':True}
              for b in d['blocks'] if b['block_id'] in proof['block_ids']}
    unit={'definition_unit_id':'unit','source_hash':d['page_hash'],'source_url':d['url'],'identity_values':['OPAQUE'],
          'binding_status':'COMPLETE','member_evidence_refs':list(evidence),'structure_execution':proof['structure_execution']}
    fields,diags=field_candidates(unit,evidence,'OPAQUE')
    assert [f['value'] for f in fields if f['role']=='FORMAL_SYNTAX']==['??? arbitrary notation']
    assert not any(f['value'].startswith('Applies to:') or f['value']=='Tail.' for f in fields)
    anchor=next(b for b in d['blocks'] if b['block_id']==proof['heading_block_id'])
    assert revalidate_explicit_authority({'structure_execution':proof['structure_execution']},anchor,'OPAQUE')
    bad=copy.deepcopy(proof);bad['block_ids']=bad['block_ids'][:-1]
    assert revalidate_explicit_authority(bad,anchor,'OPAQUE') is None
    bad=copy.deepcopy(proof);bad['structure_execution']['declarations'][0]['identity']='OTHER'
    assert revalidate_explicit_authority(bad,anchor,'OPAQUE') is None


def test_transport_is_compact_and_verification_cached(tmp_path,monkeypatch):
    import json
    import agents.owner.profile_execution as module
    d=document(tmp_path,'opaque (Reference)',''.join('<p>Body %d.</p>'%i for i in range(250)))
    proof=next(p for p in execute_structures(d,configured(d))['proofs'] if p['identity_values']==['OPAQUE'])
    contract=proof['structure_execution']
    assert 'authority_document' not in contract
    assert set(contract['authority_source'])=={'url','html_path','page_hash','page_id','dbms'}
    assert len(contract['authority_proof']['source_citations'])==1
    assert len(json.dumps(contract['authority_proof']))<len(json.dumps(d))/8
    anchor=next(b for b in d['blocks'] if b['block_id']==proof['heading_block_id'])
    module._authority_results.clear()
    assert module.revalidate_explicit_authority(proof,anchor,'OPAQUE')
    monkeypatch.setattr(module,'_revalidate_explicit_authority',lambda *a:pytest.fail('cache missed'))
    assert module.revalidate_explicit_authority(proof,anchor,'OPAQUE')


def test_body_prose_respects_configured_other_role_sections(tmp_path):
    from agents.semantic_stage.source_structure import SourceStructureIndex
    d=document(tmp_path,'opaque (Reference)','<p>Behavior.</p><h2>Trials</h2><p>Example explanation.</p><pre>???</pre><h3>Details</h3><p>More example prose.</p><h2>Discussion</h2><p>Independent prose.</p>')
    profile=configured(d)
    cfg=profile['hint_contract']['definition_structure_patterns'][0]['execution']
    cfg['fields'].append({'role':'EXAMPLE','locator':{'kind':'SECTION_LABEL','labels':['Trials']},'block_kinds':['CODE']})
    proof=next(p for p in execute_structures(d,profile)['proofs'] if p['identity_values']==['OPAQUE'])
    reader=SourceStructureIndex()
    evidence={b['block_id']:{**b,**reader.observe(d,b),'source_url':d['url'],'source_order':b['order'],'owner_local_binding':True} for b in d['blocks'] if b['block_id'] in proof['block_ids']}
    unit={'definition_unit_id':'u','source_hash':d['page_hash'],'source_url':d['url'],'identity_values':['OPAQUE'],'binding_status':'COMPLETE','member_evidence_refs':list(evidence),'structure_execution':proof['structure_execution']}
    fields,_=field_candidates(unit,evidence,'OPAQUE')
    descriptions=[f['value'] for f in fields if f['role']=='DESCRIPTION']
    assert descriptions==['Behavior.','Independent prose.']


@pytest.mark.parametrize('authority,block_kinds,expected',[
    (True,['CODE'],['SELECT 1;','SELECT 2;','SELECT 3;']),
    (True,['CODE','HEADING'],['Case A','SELECT 1;','Nested case','SELECT 2;','Case B','SELECT 3;']),
    (True,None,['SELECT 1;','SELECT 2;','SELECT 3;']),
    (False,['CODE'],[]),
])
def test_section_label_nested_unconfigured_headings(tmp_path,authority,block_kinds,expected):
    from agents.semantic_stage.source_structure import SourceStructureIndex
    d=document(tmp_path,'opaque (Reference)',
        '<h2>Examples</h2><h3>Case A</h3><pre>SELECT 1;</pre>'
        '<h4>Nested case</h4><pre>SELECT 2;</pre>'
        '<h3>Case B</h3><pre>SELECT 3;</pre>'
        '<h4>Inputs</h4><pre>SELECT 4;</pre>'
        '<h2>Examples</h2><h2>Other topic</h2><h3>Case C</h3><pre>SELECT 5;</pre>')
    plan=configured(d)['hint_contract']['definition_structure_patterns'][0]
    cfg=plan['execution']
    cfg['fields']=[{'role':'EXAMPLE','locator':{'kind':'SECTION_LABEL','labels':['Examples']}},
                   {'role':'PARAMETER','locator':{'kind':'SECTION_LABEL','labels':['Inputs']},'block_kinds':['CODE']}]
    if block_kinds is not None:cfg['fields'][0]['block_kinds']=block_kinds
    if not authority:
        cfg.pop('authority');cfg.pop('declaration_kind')
    reader=SourceStructureIndex()
    evidence={b['block_id']:{**b,**reader.observe(d,b),'source_url':d['url'],'source_order':b['order'],'owner_local_binding':True} for b in d['blocks']}
    unit={'definition_unit_id':'u','source_hash':d['page_hash'],'source_url':d['url'],
          'identity_values':['OPAQUE'],'binding_status':'COMPLETE','member_evidence_refs':list(evidence),
          'structure_execution':{'schema_version':'StructureExecution/v2','plan':plan,
              'profile_execution_digest':compile_execution(plan)['digest'],'field_bindings':cfg['fields'],'declarations':[]}}
    fields,_=field_candidates(unit,evidence,'OPAQUE')
    assert [f['value'] for f in fields if f['role']=='EXAMPLE']==expected
    assert [f['value'] for f in fields if f['role']=='PARAMETER']==['SELECT 4;']


@pytest.mark.parametrize('native_parent',[False,True])
def test_linked_operator_column_capture_revalidates_row_boundary(tmp_path,native_parent):
    from agents.document_structure_v2.topology import _dom_path
    from bs4 import BeautifulSoup
    d=document(tmp_path,'Arithmetic Operators',
        '<table><thead><tr><th>Operator</th><th>Meaning</th></tr></thead><tbody>'
        '<tr><td><a href="plus.html">+ (Add)</a></td><td>Addition</td></tr>'
        '<tr><td><a href="minus.html">- (Subtract)</a></td><td>Subtraction</td></tr></tbody></table>')
    locator={'kind':'COLUMN_HEADER','headers':['Operator'],
             'text_pattern':r'([!<>=~%&|^+*/:@?-]+)(?:\s+\([^)]*\))?','capture_group':1}
    profile=configured(d,'D',locator)
    profile['hint_contract']['definition_structure_patterns'][0]['execution']['declaration_kind']='OPERATOR'
    proof=next(p for p in execute_structures(d,profile)['proofs'] if p['identity_values']==['+'])
    anchor=next(b for b in d['blocks'] if b['block_id']==proof['heading_block_id'])
    soup=BeautifulSoup(open(d['html_path'],encoding='utf8').read(),'lxml')
    row=next(n for n in soup.find_all('tr') if _dom_path(n)==anchor['dom_path'])
    if native_parent:proof['dom_parent']=_dom_path(row.parent)
    assert revalidate_explicit_authority(proof,anchor,'+')
    forged=copy.deepcopy(proof);forged['dom_parent']=_dom_path(row.find_parent('table'))
    assert revalidate_explicit_authority(forged,anchor,'+') is None
    forged=copy.deepcopy(proof);forged['structure_execution']['identity_bindings'][0]['cell_index']=1
    assert revalidate_explicit_authority(forged,anchor,'+') is None
