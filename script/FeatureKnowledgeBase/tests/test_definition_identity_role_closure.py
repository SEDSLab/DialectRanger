import json
import pytest
from tests.test_paragraph_label_definition import doc
from agents.profile_bootstrap.cold_start import build_cold_start_profile
from agents.pipeline.generic_unified import run_generic_downstream


def execute(tmp_path,body,heading):
    d=doc(tmp_path,body,heading)
    profile=build_cold_start_profile(dbms='sample',documents=[d],run_id='definition_contract')
    run_generic_downstream(dbms='sample',documents=[d],profile=profile,output_root=tmp_path/'run',
                           semantic=True,semantic_provider='deterministic')
    return json.loads((tmp_path/'run/canonical/promotion_ready_kb.json').read_text(encoding='utf-8'))


def test_parenthesis_free_official_definition_is_publishable(tmp_path):
    fs=execute(tmp_path,'<p>The CLOCK_NOW function returns the current clock value.</p>'
        '<h2>Syntax</h2><pre>CLOCK_NOW</pre>'
        '<h2>Example</h2><pre>VALUES CLOCK_NOW</pre>','CLOCK_NOW function')
    f=next(f for f in fs if f['name']=='CLOCK_NOW' and f['feature_type']=='function')
    assert [c['value'] for c in f['semantics']['syntax']]==['CLOCK_NOW']
    assert f['semantics']['description'] and f['semantics']['examples']


def test_explicit_co_definition_proves_both_subjects_without_cross_signature(tmp_path):
    fs=execute(tmp_path,'<p>The ALPHA or BETA function returns an absolute value.</p>'
        '<h2>Syntax</h2><pre>ALPHA(x)</pre><pre>BETA(x)</pre>'
        '<h2>Example</h2><pre>VALUES ALPHA(-1)</pre>','ALPHA or BETA function')
    for name in ['ALPHA','BETA']:
        f=next(f for f in fs if f['name']==name and f['feature_type']=='function')
        assert [c['value'] for c in f['semantics']['syntax']]==[name+'(x)']
        assert f['semantics']['description']
        if name=='BETA':assert not f['semantics']['examples']


def test_parameter_prose_under_syntax_is_not_formal_grammar(tmp_path):
    fs=execute(tmp_path,'<p>The ALPHA function returns the location.</p>'
        '<h2>Syntax</h2><pre>ALPHA(x)</pre>'
        '<p>The argument specifies the value to search for.</p>'
        '<p>The result is an integer position.</p>'
        '<h2>Example</h2><pre>VALUES ALPHA(1)</pre>','ALPHA function')
    f=next(f for f in fs if f['name']=='ALPHA')
    assert [c['value'] for c in f['semantics']['syntax']]==['ALPHA(x)']
    assert any('argument specifies' in c['value'] for c in f['semantics']['description'])
    assert any('result is' in c['value'] for c in f['semantics']['description'])


def test_bare_and_optional_catalog_declarations_do_not_require_description(tmp_path):
    fs=execute(tmp_path,'<p><strong>CLOCK_NOW</strong></p><p><code>CLOCK_NOW</code></p>'
        '<p><strong>CLOCK_TIME</strong></p><p><code>CLOCK_TIME [ ( &lt;precision&gt; ) ]</code></p>'
        '<p>These functions report shared session attributes.</p><p>OTHER_CONTEXT is a separate property.</p>',
        'Scalar Functions')
    for name in ['CLOCK_NOW','CLOCK_TIME']:
        f=next(f for f in fs if f['name']==name and f['feature_type']=='function')
        assert not f['semantics']['description'] and f['semantics']['syntax']


def test_existing_paragraph_proof_has_priority_and_unparsed_label_is_boundary(tmp_path):
    from agents.owner.definition_scope import definition_scopes
    d=doc(tmp_path,'<p><strong>ALPHA</strong></p><p><code>ALPHA(x)</code></p>'
        '<p>The arguments are character strings. Returns a joined value.</p>'
        '<p><strong>CLOCK_NOW</strong></p><p><code>CLOCK_NOW</code></p>'
        '<p><strong>UNSUPPORTED / SHAPE</strong></p><p>Foreign text.</p>')
    p=build_cold_start_profile(dbms='sample',documents=[d],run_id='priority')
    scopes=definition_scopes(d,p)
    alpha=[s for s in scopes if s.get('primary_identity')=='ALPHA']
    assert len(alpha)==1 and alpha[0]['definition_pattern']=='PARAGRAPH_LABEL_SIGNATURE_SECTION'
    clock=next(s for s in scopes if s.get('primary_identity')=='CLOCK_NOW')
    assert all('Foreign text' not in b['text'] for b in d['blocks'] if b['block_id'] in clock['block_ids'])


@pytest.mark.parametrize('title,body',[
    ('Function Examples','<p><strong>CLOCK_NOW</strong></p><pre>CLOCK_NOW</pre><p>Returns a value.</p>'),
    ('API Functions','<p><strong>ALPHA</strong></p><code>ALPHA(x)</code><p>Returns a client object.</p>'),
    ('ALPHA or BETA function','<p>These functions have independent uses.</p><h2>Syntax</h2><pre>ALPHA(x)</pre>'),
])
def test_unproven_context_does_not_get_explicit_function_contract(tmp_path,title,body):
    from agents.owner.definition_scope import definition_scopes
    d=doc(tmp_path,body,title)
    p=build_cold_start_profile(dbms='sample',documents=[d],run_id='negative')
    assert not any(s.get('definition_pattern')=='FUNCTION_DECLARATION_SECTION' for s in definition_scopes(d,p))


def test_definition_contract_disabled_and_foreign_declaration_not_absorbed(tmp_path):
    from agents.owner.definition_scope import definition_scopes
    d=doc(tmp_path,'<p>The ALPHA function returns a value.</p><h2>Syntax</h2>'
        '<pre>ALPHA(x)</pre><pre>FOREIGN(x)</pre>','ALPHA function')
    p=build_cold_start_profile(dbms='sample',documents=[d],run_id='foreign')
    assert not any(s.get('definition_pattern')=='FUNCTION_DECLARATION_SECTION' for s in definition_scopes(d,p))
    d=doc(tmp_path,'<p>The CLOCK_NOW function returns a value.</p><h2>Syntax</h2><pre>CLOCK_NOW</pre>','CLOCK_NOW function')
    p['hint_contract']['definition_scope_patterns']=[]
    assert not definition_scopes(d,p)


def test_untyped_reference_is_retained_but_cannot_pollute_definition_type(tmp_path):
    from agents.identity.production import reconcile_identity_groups
    def candidate(cid,kind,role,section=None):
        evidence=[{'type':kind,'strength':'STRONG','reason':'proven_local_definition_anchor','source':'definition_section'}] if kind else []
        return {'candidate_id':cid,'owner_id':cid+'_owner','normalized_identity':'BOX','page_id':cid+'_page',
            'provenance':{'dbms':'sample','page_hash':'hash','source_url':'https://official.test/'+cid,'html_path':str(tmp_path/'x.html')},
            'occurrence_records':[{'occurrence_id':cid+'_occurrence','raw_identity':'BOX','source_block_id':cid+'_block',
                'surface_role':role,'defining':True,'occurrence_role':'DEFINING_IDENTITY','definition_section_id':section,
                'type_evidence':evidence}], 'validation_status':'VALIDATED'}
    defining=candidate('definition','FUNCTION','FEATURE_DEFINITION','function_definition:anchor')
    reference=candidate('matrix',None,'SUPPORTING_CONTEXT')
    groups,_=reconcile_identity_groups([defining,reference])
    function=next(u for u in groups if u['feature_type']=='FUNCTION')
    assert function['owner_ids']==['definition_owner']
    unresolved=next(u for u in groups if u['feature_type']=='UNCERTAIN')
    assert unresolved['candidate_ids']==['matrix'] and unresolved['group_status']=='NON_FEATURE_ONLY'
    datatype=candidate('type','DATATYPE','FEATURE_DEFINITION','typed_definition:anchor')
    groups,_=reconcile_identity_groups([defining,datatype,reference])
    assert {u['feature_type'] for u in groups}=={'FUNCTION','DATATYPE','UNCERTAIN'}
