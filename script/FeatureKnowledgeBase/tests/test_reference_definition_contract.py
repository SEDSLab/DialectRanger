import copy
from agents.owner.reference_definitions import reference_scopes,verified_reference,bnf_names,catalog_names
from agents.owner.scope_plan import compile_scope_plan

def block(text,kind='TEXT',order=0):
    return {'block_id':'b'+str(order),'text':text,'block_kind':kind,'order':order,
        'dom_path':'html > body > '+('h1' if kind=='HEADING' else 'p:nth-of-type('+str(order+1)+')'),
        'heading_level':1 if kind=='HEADING' else None,'table_metadata':None,'inline_nodes':[],'heading_ancestry':[]}

def test_typed_reference_heading_and_prose_have_verifiable_subject():
    a=block('ALPHA VARYING data type','HEADING');b=block('ALPHA VARYING stores variable values.',order=1)
    d={'url':'https://official.invalid/types','blocks':[a,b]}
    plans={p['relation']:p for p in compile_scope_plan()}
    ps=reference_scopes(d,plans)
    assert len(ps)==1 and ps[0]['identity_values']==['ALPHA VARYING']
    assert verified_reference(ps[0],a,'ALPHA VARYING')['feature_type']=='DATATYPE'
    bad=dict(a,text='BETA data type')
    assert verified_reference(ps[0],bad,'ALPHA VARYING') is None
    assert reference_scopes(d,{})==[]

def test_bnf_terminal_definitions_not_function_calls_or_parameters():
    assert bnf_names(block('<boolean term> ::= <boolean factor> AND <boolean factor>'))==('OPERATOR',['AND'])
    assert bnf_names(block('<comp op> ::= <less than operator>'))==('OPERATOR',['<'])
    assert bnf_names(block('<comparison operator> ::= <less than operator>'))==('OPERATOR',['<'])
    assert bnf_names(block('<parameter> ::= NAME | COUNT')) is None
    assert bnf_names(block('SELECT COUNT(*) FROM data')) is None
    assert bnf_names(block('<distinct predicate> ::= <x> IS [ NOT ] DISTINCT FROM <y>'))==('OPERATOR',['IS DISTINCT FROM','IS NOT DISTINCT FROM'])

def test_typed_catalog_alternatives_keep_parameters_inside_declarations():
    a=block('ALPHA(P,S), BETA(P,S) or GAMMA(P,S)','TABLE_ROW')
    a['table_metadata']={'headers':['Type','Description'],'cells':[a['text'],'Fixed precision value']}
    snapshot=copy.deepcopy(a)
    assert catalog_names(a)==('DATATYPE',['ALPHA','BETA','GAMMA'])
    assert a==snapshot
    a['table_metadata']['headers']=['API method','Description']
    assert catalog_names(a) is None

def test_examples_and_embedded_languages_do_not_create_reference_proofs():
    a=block('<boolean term> ::= <factor> OR <term>');a['heading_ancestry']=['Examples']
    plans={p['relation']:p for p in compile_scope_plan()}
    assert reference_scopes({'blocks':[a]},plans)==[]
    a['heading_ancestry']=[];a['definition_context']={'domain':'EMBEDDED_QUERY_LANGUAGE'}
    assert reference_scopes({'blocks':[a]},plans)==[]

def test_explicit_manifest_can_use_user_provided_shared_project_namespace(tmp_path):
    import json
    from agents.pipeline.scoped_build import load_explicit_documents
    path=tmp_path/'urls.json'
    path.write_text(json.dumps({'dbms':'fixture','documents':[{'url':'https://shared.invalid/fixture/docs/types.html'}]}))
    assert load_explicit_documents(path,'fixture')[0]['url'].endswith('/fixture/docs/types.html')

def test_typed_function_definition_preserves_overload_subsections(tmp_path):
    from tests.test_definition_identity_role_closure import execute
    fs=execute(tmp_path,'<p>The ALPHA function takes a value and returns its representation.</p>'
        '<h2>Character mode syntax</h2><pre>ALPHA(text)</pre>'
        '<h2>Numeric mode syntax</h2><pre>ALPHA(number, width)</pre>'
        '<h2>Examples</h2><pre>VALUES ALPHA(12, 3)</pre>','ALPHA function')
    f=next(f for f in fs if f['name']=='ALPHA' and f['feature_type']=='function')
    assert {c['value'] for c in f['semantics']['syntax']}=={'ALPHA(text)','ALPHA(number, width)'}
    assert f['semantics']['description'] and f['semantics']['examples']

def test_named_sql_terminals_are_normalization_not_an_inventory():
    assert bnf_names(block('<comp op> ::= <unknown custom operator>')) is None
    assert bnf_names(block('The greater than operator is mentioned here.')) is None
    assert bnf_names(block('<factor> ::= <numeric primary>')) is None

def test_usage_signature_retains_evidence_but_not_another_type_vote():
    from agents.identity.production import reconcile_identity_groups
    def candidate(cid,owner,defined):
        obs={'occurrence_id':'occ-'+cid,'raw_identity':'ALPHA' if defined else 'ALPHA(8)',
            'source_block_id':'b-'+cid,
            'raw_evidence':'ALPHA data type' if defined else 'ALPHA(8)',
            'source_type':'heading' if defined else 'signature','defining':True,
            'occurrence_role':'DEFINING_IDENTITY','surface_role':'FEATURE_DEFINITION' if defined else 'SUPPORTING_CONTEXT',
            'structural_role':'FEATURE_NAME' if defined else 'SIGNATURE',
            'definition_section_id':'reference_definition:b' if defined else None,
            'type_evidence':[{'type':'DATATYPE','strength':'STRONG','reason':'proven_local_definition_anchor','source':'definition_section'}] if defined else []}
        return {'candidate_id':cid,'owner_id':owner,'page_id':'p','normalized_identity':'ALPHA',
            'raw_identity':obs['raw_identity'],'type_hint':'DATATYPE' if defined else 'UNCERTAIN',
            'occurrence_records':[obs],'occurrence_ids':[obs['occurrence_id']],
            'variants':[obs['raw_identity']],'source_block_ids':['b-'+cid],
            'provenance':{'dbms':'fixture','source_url':'https://fixture.invalid/types','page_hash':'hash','html_path':'fixture.html'}}
    units,_=reconcile_identity_groups([candidate('definition','definition-owner',True),candidate('usage','usage-owner',False)])
    u=next(u for u in units if u['feature_type']=='DATATYPE')
    assert u['semantic_reference_owner_ids']==['usage-owner']
    assert set(u['owner_ids'])=={'definition-owner','usage-owner'}
    assert u['strong_type_evidence']==['DATATYPE']
    assert u['occurrence_roles']['CROSS_REFERENCE']==1
