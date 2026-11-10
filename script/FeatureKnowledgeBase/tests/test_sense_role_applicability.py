import pytest
from tests.test_semantic_sense import _context
from agents.semantic_sense.audit import detect_sense_candidate,_deterministic_decision,_project_group,explicit_application_namesake
from agents.semantic_stage.content_completeness import audit_final_content


@pytest.mark.parametrize('kind',['FUNCTION','DATATYPE','UNCERTAIN'])
def test_operator_role_words_do_not_split_other_types(kind):
    c=_context('SAMPLE',kind,[{'evidence_ref':'d','raw_text':'SAMPLE supports assignment and comparison.'}],
        description={'text':'Supports assignment and comparison.','evidence_refs':['d']})
    assert detect_sense_candidate(c) is None
    projected,_=_project_group(c,_deterministic_decision(c,None))
    assert len(projected)==1 and projected[0]['description']==c['features'][0]['description']
    assert projected[0]['semantic_sense']=='DEFAULT'


def test_one_mixed_operator_definition_is_not_two_senses():
    c=_context('=', 'OPERATOR',[{'evidence_ref':'one','raw_text':'Equal comparison and assignment are discussed together.'}])
    assert detect_sense_candidate(c) is None


def test_mixed_context_does_not_steal_real_operator_definition_refs():
    c=_context('=', 'OPERATOR',[{'evidence_ref':'cmp','raw_text':'Equal operator.'},
        {'evidence_ref':'asg','raw_text':'Assign a value.'},
        {'evidence_ref':'general','raw_text':'Comparison and assignment examples.'}])
    candidate=detect_sense_candidate(c)
    assert candidate['role_signal_refs']=={'ASSIGNMENT':['asg'],'COMPARISON':['cmp']}
    assert _deterministic_decision(c,candidate)['decision']=='MULTI_SENSE'


def test_multi_paragraph_description_is_filtered_per_claim_and_status_recomputed():
    c=_context('=', 'OPERATOR',[{'evidence_ref':'cmp','raw_text':'Equal operator.'},
                              {'evidence_ref':'asg','raw_text':'Assign a value.'}])
    f=c['features'][0]
    f.update(description=[{'text':'Equal operator.','evidence_refs':['cmp']},
                          {'text':'Assign a value.','evidence_refs':['asg']}],semantic_status='COMPLETE')
    rows,_=_project_group(c,_deterministic_decision(c,detect_sense_candidate(c)))
    by={r['semantic_sense']:r for r in rows}
    assert [x['text'] for x in by['COMPARISON']['description']]==['Equal operator.']
    assert [x['text'] for x in by['ASSIGNMENT']['description']]==['Assign a value.']
    assert all(r['semantic_status']=='PARTIAL' for r in rows)


def test_explicit_local_role_overrides_ancestor_heading():
    claim={'text':'In another context it is a comparison operator.','evidence_refs':['contrast']}
    c=_context('=', 'OPERATOR',[{'evidence_ref':'cmp','raw_text':'Equal operator.'},
                              {'evidence_ref':'asg','raw_text':'Assign a value.'}],description=claim)
    c['blocks']['contrast']['heading_ancestry']=['Assignment Operators']
    projected,_=_project_group(c,_deterministic_decision(c,detect_sense_candidate(c)))
    by={r['semantic_sense']:r for r in projected}
    assert by['COMPARISON']['description']==claim
    assert by['ASSIGNMENT']['description'] is None


def test_application_namesake_is_reviewed_without_losing_native_fields():
    bad='If the application supplies a user function called "sample", SELECT sample(x) may rank rows.'
    c=_context('SAMPLE','FUNCTION',[{'evidence_ref':'native','raw_text':'Returns the position of a row.'}],
        description={'text':'Returns the position of a row.','evidence_refs':['native']},
        examples=[{'sql':'SELECT sample() OVER ();','evidence_refs':['native-example']},
                  {'sql':bad,'evidence_refs':['custom-example']}])
    rows,_=_project_group(c,_deterministic_decision(c,None));r=rows[0]
    assert [q['sql'] for q in r['official_examples']]==['SELECT sample() OVER ();']
    assert r['field_reviews'][0]['raw_text']==bad
    final=[{'provenance':{'identity_unit_id':'group:sample'},'semantics':{
        'description':[{'value':'Returns the position of a row.'}],
        'examples':[{'value':'SELECT sample() OVER ();'}]}}]
    audited=audit_final_content(c['features'],final,rows)[0]
    assert audited['status']=='ACCEPTED_CONTENT_RETAINED_WITH_QUARANTINE'
    assert len(audited['fields']['official_examples']['quarantined_claims'])==1
    assert audit_final_content(c['features'],final)[0]['status']=='FINAL_FIELD_GAP'


@pytest.mark.parametrize('text',[
    'The function returns a user value and supports comparison.',
    'If the application supplies a user function called "another", SELECT sample() is supported.',
    'Use this function when an application supplies a value.',
])
def test_incidental_custom_or_assignment_words_do_not_quarantine(text):
    assert not explicit_application_namesake(text,'SAMPLE')


def test_missing_defining_evidence_still_requires_review_for_functions():
    c=_context('SAMPLE','FUNCTION',[])
    assert _deterministic_decision(c,detect_sense_candidate(c))['decision']=='REVIEW_REQUIRED'


def test_reference_proof_survives_sorted_json_and_dict_reordering():
    import json
    from agents.owner.reference_definitions import verified_reference
    heading={'block_id':'head','order':0,'text':'Joining operator','dom_path':'main > h1',
             'block_kind':'HEADING','table_metadata':None}
    citations={'head':heading,
        'z-first':{'block_id':'z-first','order':1,'text':'The joining operator, || , joins values.'},
        'a-last':{'block_id':'a-last','order':10,'text':'Later unrelated explanation.'}}
    proof={'definition_pattern':'REFERENCE_DECLARATION_SECTION','heading_block_id':'head',
        'source_citations':citations,'reference_mode':'HEADING_SECTION',
        'feature_type':'OPERATOR','identity_values':['||']}
    for saved in [proof,json.loads(json.dumps(proof,sort_keys=True)),
                  dict(proof,source_citations=dict(reversed(list(citations.items()))))]:
        assert verified_reference(saved,heading,'||')['identity_values']==['||']
    assert verified_reference(proof,dict(heading,text='A different heading'),'||') is None
