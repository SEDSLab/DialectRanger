import copy
import json
import pytest
from tests.test_profile_definition_execution import document,run
from tests.test_five_structure_executors import profile_for


def configured(d,kind='FUNCTION'):
    p=profile_for(d,'A')
    p.update(generated=False,generation_mode='MANUAL_PREPARED',manual_edit=False,
        profile_provenance={'author':'test','authored_at':'2026-11-08','revision':'1'})
    p['hint_contract']['selection_authority']='EXPLICIT_PROFILE_DEFINITIONS'
    p['hint_contract']['definition_structure_patterns']=[{
        'structure':'A','identity_slot':'PAGE_SUBJECT','boundary':'SUBJECT_PAGE',
        'execution':{'version':1,'authority':'EXPLICIT_DEFINITION','declaration_kind':kind,
            'when':{'page_path_prefixes':['/reference']},
            'identity':{'alternatives':[{'kind':'HEADING','heading_level':1,
                'text_pattern':r'([@A-Za-z_][@A-Za-z0-9_]*) \(Query-Dialect\)','capture_group':1}],
                'cardinality':'ONE'},
            'boundary':{'stop_headings':['Feedback']},
            'fields':[{'role':'DESCRIPTION','locator':{'kind':'BODY_PROSE'},'block_kinds':['TEXT']},
                {'role':'FORMAL_SYNTAX','locator':{'kind':'SECTION_LABEL','labels':['Syntax']},'block_kinds':['CODE']},
                {'role':'EXAMPLE','locator':{'kind':'SECTION_LABEL','labels':['Examples']},'block_kinds':['CODE']}]}}]
    return p


@pytest.mark.parametrize('kind',['FUNCTION','DATATYPE','UNCERTAIN'])
def test_explicit_page_identity_does_not_need_syntax_parser_approval(tmp_path,kind):
    d=document(tmp_path,'@@TOKEN (Query-Dialect)','<p>Official subject description.</p>'
        '<h2>Syntax</h2><pre>-- Official grammar\n@@TOKEN [ { FIRST | SECOND } value ] [ EXTRA ( clause ) ]</pre>'
        '<h2>Examples</h2><h3>Case one</h3><pre>SELECT 42;</pre>'
        '<h2>Feedback</h2><p>Was this page helpful?</p>')
    result=run(tmp_path,d,configured(d,kind))
    r=next(r for r in result if r['name']=='@@TOKEN')
    assert r['feature_type']==kind.lower()
    assert [v['value'] for v in r['semantics']['description']]==['Official subject description.']
    assert r['semantics']['syntax'][0]['value'].startswith('-- Official grammar')
    assert [v['value'] for v in r['semantics']['examples']]==['SELECT 42;']
    assert 'Was this page helpful?' not in json.dumps(r)


def test_distinct_authoritative_source_records_survive_frontend_without_false_overloads():
    from tests.test_frontend_projection_v2 import _record
    from agents.semantic_extractor.frontend_projection_v2 import project_database
    a=_record();b=_record('abs()')
    b['canonical_identity']=copy.deepcopy(a['canonical_identity'])
    for i,r in enumerate([a,b]):
        r['provenance']={'identity_unit_id':'unit'+str(i)}
        r['representation_status']='UNRESOLVED_SEPARATE_AUTHORITATIVE_RECORDS'
    result=project_database([a,b])
    assert len(result['features'])==2
    assert all(r['overload_count']==1 for r in result['features'])
    b.pop('representation_status')
    with pytest.raises(ValueError):project_database([a,b])
    b['representation_status']=a['representation_status'];b['feature_id']=a['feature_id']
    with pytest.raises(ValueError):project_database([a,b])
