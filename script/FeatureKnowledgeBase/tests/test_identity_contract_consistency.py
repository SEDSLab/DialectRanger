import pytest
from agents.contracts.identity_key import normalize_identity
from agents.identity.production import (build_identity_occurrences,build_candidate_ledger,
    validate_candidate_ledger,reconcile_identity_groups)
from agents.identity.quality import explicit_usage_role
from agents.owner.profile_execution import identity_authorized
from tests.test_identity_quality_gate import _candidate,_record


def occurrence_fixture(example='SELECT TURN(2);',controlled=False):
    head={'block_id':'head','block_kind':'TEXT','text':'TURN(x)','heading_ancestry':['Functions']}
    call={'block_id':'example','block_kind':'CODE','text':example,'heading_ancestry':['Functions']}
    proof={'section_id':'list_definition:turn','definition_pattern':'SIGNATURE_LED_LIST_ITEM',
           'identity_values':['TURN'],'definition_anchor_block_ids':['head'],'block_ids':['head','example'],
           'formal_signature_values':{'head':['TURN(x)']}}
    owner={'owner_id':'o','page_id':'p','member_block_ids':['head','example'],'owner_kind':'SignatureOwner',
           'definition_section_proofs':[proof],'profile_hint':{}}
    if controlled:owner['profile_controlled_block_ids']=['example']
    page={'page_id':'p','dbms':'sample','url':'https://official.test/functions','page_hash':'hash',
          'html_path':'not-read','title':'Functions','blocks':[head,call]}
    return page,owner


@pytest.mark.parametrize('controlled',[False,True])
def test_body_example_retained_without_definition_vote(controlled):
    page,owner=occurrence_fixture(controlled=controlled)
    occurrences=build_identity_occurrences([page],[owner])
    examples=[o for o in occurrences if o['source_block_id']=='example']
    assert examples and all(o['occurrence_role']=='EXAMPLE_TOKEN' and not o['defining'] and not o['type_evidence'] for o in examples)
    candidates=build_candidate_ledger([page],[owner],occurrences)
    accepted,_=validate_candidate_ledger(candidates,[owner],retain_quality_evidence=True)
    units,_=reconcile_identity_groups(accepted)
    unit=next(u for u in units if u['canonical_identity']=='TURN')
    assert unit['variants']==['TURN(x)'] and unit['defining_variants']==['TURN(x)']
    assert 'TURN(2)' in unit['observed_variants']
    assert any(o['raw_evidence']=='SELECT TURN(2);' for o in unit['occurrence_records'])
    assert page['blocks'][1]['text']=='SELECT TURN(2);'


def test_foreign_use_never_becomes_owner_candidate():
    page,owner=occurrence_fixture('SELECT OTHER(2);')
    occurrences=build_identity_occurrences([page],[owner])
    foreign=[o for o in occurrences if o['normalized_identity']=='OTHER']
    assert foreign and all(not o['owner_local_binding'] and not o['defining'] for o in foreign)
    assert all(c['normalized_identity']!='OTHER' for c in build_candidate_ledger([page],[owner],occurrences))


def test_unproven_body_declaration_kept_as_reference_not_silently_deleted():
    page,owner=occurrence_fixture('TURN(y)')
    occurrences=build_identity_occurrences([page],[owner])
    body=[o for o in occurrences if o['source_block_id']=='example']
    assert body and all(o['occurrence_role']=='CROSS_REFERENCE' and not o['defining'] for o in body)


def test_example_only_owner_cannot_establish_feature():
    page,owner=occurrence_fixture()
    page['blocks']=page['blocks'][1:];owner['member_block_ids']=['example'];owner['definition_section_proofs']=[]
    occurrences=build_identity_occurrences([page],[owner])
    accepted,_=validate_candidate_ledger(build_candidate_ledger([page],[owner],occurrences),[owner],retain_quality_evidence=True)
    units,_=reconcile_identity_groups(accepted)
    assert occurrences and all(not o['defining'] for o in occurrences)
    assert not accepted and not units


@pytest.mark.parametrize('value,key',[
    ('turn(x)','TURN'),('space.turn(x)','SPACE.TURN'),('space."MiXeD"(x)','SPACE."MiXeD"'),
    ('space . "A  B"(x)','SPACE."A  B"'),('"a""b"()','"a""b"'),
    ('interval day to second','INTERVAL DAY TO SECOND'),('||','||'),('is not null','IS NOT NULL')])
def test_shared_keys_are_idempotent(value,key):
    assert normalize_identity(value)==key
    assert normalize_identity(key)==key


def test_quoted_case_separates_groups_unquoted_case_merges():
    records=[]
    for i,raw in enumerate(['space."MiXeD"(x)','space."MIXED"(x)','space.turn(x)','SPACE.TURN(y)']):
        rec=_record('o'+str(i),raw,'DEFINING_IDENTITY',[
            {'type':'FUNCTION','strength':'STRONG','reason':'proven_local_definition_anchor'}])
        records.append(_candidate(normalize_identity(raw),'c'+str(i),[rec],'FUNCTION'))
    units,_=reconcile_identity_groups(records)
    assert {u['canonical_identity'] for u in units}=={'SPACE."MiXeD"','SPACE."MIXED"','SPACE.TURN'}
    assert len(next(u for u in units if u['canonical_identity']=='SPACE.TURN')['candidate_ids'])==2


def test_profile_authority_respects_quoted_identity():
    owner={'profile_controlled_block_ids':['b'],'definition_section_proofs':[{
        'structure_execution':{'schema_version':'StructureExecution/v2'},
        'definition_anchor_block_ids':['b'],'identity_values':['SPACE."MiXeD"']}]}
    assert identity_authorized(owner,'b','space."MiXeD"')
    assert not identity_authorized(owner,'b','SPACE."MIXED"')


@pytest.mark.parametrize('text,kind,ancestry,example',[
    ('EXTRACT(field FROM value)','CODE',['Syntax'],False),
    ('TURN(x, size = 8192)','CODE',['Syntax'],False),
    ('TURN(2)','CODE',[],True),
    ('VALUES TURN(2)','CODE',[],True),
    ('client> SELECT TURN(2);','CODE',[],True),
    ('TURN(x)','CODE',['TURN Examples'],True),
    ('TURN(x) | Returns a value | SELECT TURN(2)','TABLE_ROW',[],False),
    ('DECIMAL(10,2)','CODE',['Data types'],False),
])
def test_use_site_guard_does_not_relabel_catalog_or_formal_grammar(text,kind,ancestry,example):
    assert bool(explicit_usage_role({'text':text,'block_kind':kind,'heading_ancestry':ancestry}))==example


@pytest.mark.parametrize('prose,context,defines',[
    (False,'Functions and Operators',True),(True,'Functions and Operators',False),
    (False,'API Functions',False),(False,'Configuration',False)])
def test_complete_marked_declaration_is_not_reparsed_as_prose(prose,context,defines):
    raw='TURN( value1 , value2 ,...)'
    text='This paragraph mentions '+raw if prose else raw
    block={'block_id':'b','block_kind':'TEXT','text':text,'heading_ancestry':[context],
        'inline_nodes':[{'element_name':'code','text':raw,'dom_path':'p > code','semantic_classes':[]},
                        {'element_name':'em','text':'value1','semantic_classes':['replaceable']}]}
    page={'page_id':'p','dbms':'sample','url':'https://official.test/reference','page_hash':'hash',
          'html_path':'not-read','title':context,'blocks':[block]}
    owner={'owner_id':'o','page_id':'p','member_block_ids':['b'],'owner_kind':'SignatureOwner'}
    occurrences=build_identity_occurrences([page],[owner])
    proved=[o for o in occurrences if o.get('identity_role_reason')=='OBSERVED_COMPLETE_FORMAL_DECLARATION']
    assert bool(proved)==defines
    if defines:
        assert proved[0]['defining'] and proved[0]['declaration_locator']=='p > code'
        assert proved[0]['raw_identity']==raw


def test_variadic_recovery_does_not_promote_parenthesized_predicate():
    from agents.identity.production import _surface_records
    block={'block_id':'b','block_kind':'TEXT','text':'PRESENT(subquery)',
        'heading_ancestry':['Functions and Operators'],'inline_nodes':[
            {'element_name':'code','text':'PRESENT(subquery)'},
            {'element_name':'em','text':'subquery','semantic_classes':['replaceable']}]}
    records=_surface_records(block,page={},owner={},blocks=[block])
    assert not any(r.get('identity_role_reason')=='OBSERVED_COMPLETE_FORMAL_DECLARATION' for r in records)
