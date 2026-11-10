import copy
import json
import pytest

from tests.test_profile_definition_execution import document,run
from agents.owner.definition_scope import definition_scopes
from agents.owner.generic import generate_owner_hypotheses
from agents.owner.scope_plan import default_scope_patterns
from agents.profile_bootstrap.cold_start import build_cold_start_profile,validate_cold_start_profile
from agents.contracts.definition_unit import build_definition_units
from agents.semantic_stage.pipeline import build_semantic_prompt


def payload(bundle):
    prompt=build_semantic_prompt(bundle,{'identity_group_id':bundle['identity_unit_id'],
        'normalized_identity':bundle['normalized_identity'],'feature_type':bundle['feature_type']})
    return json.loads(prompt.split('INPUT:\n')[1].split('\n\nOUTPUT SCHEMA:')[0])


@pytest.mark.parametrize('relation,body,association',[
    ('TERM_BODY_PAIR','<dl><dt>TURN(x)</dt><dd>Returns a rotation.</dd>'
     '<dt>SHIFT(x)</dt><dd>Returns a displacement.</dd></dl>','NATIVE_DEFINITION_TERM_BODY'),
    ('HEADING_SECTION','<h2>TURN</h2><h3>Syntax</h3><pre>TURN(x)</pre><p>Returns a rotation.</p>'
     '<h2>SHIFT</h2><pre>SHIFT(x)</pre><p>Returns a displacement.</p>','EXACT_DEFINITION_HEADING'),
])
def test_profile_controls_native_relation_execution(tmp_path,relation,body,association):
    d=document(tmp_path,'Functions',body)
    p=build_cold_start_profile(dbms='sample',documents=[d],run_id='relation')
    p['hint_contract']['definition_patterns']=[]
    p['hint_contract']['definition_scope_patterns']=[v for v in default_scope_patterns() if v['relation']==relation]
    proofs=definition_scopes(d,p)
    matching=[v for v in proofs if v['association']==association]
    assert len(matching)==2 and all(v['scope_execution_plan']['relation']==relation for v in matching)
    for proof in matching:
        text=' '.join(b['text'] for b in d['blocks'] if b['block_id'] in proof['block_ids'])
        assert not ('rotation' in text and 'displacement' in text)
    p['hint_contract']['definition_scope_patterns']=[]
    assert definition_scopes(d,p)==[]


def test_unsupported_scope_recipe_rejected(tmp_path):
    d=document(tmp_path,'Functions','<p>Reference text.</p>')
    p=build_cold_start_profile(dbms='sample',documents=[d],run_id='invalid')
    p['hint_contract']['definition_scope_patterns']=[{'relation':'HEADING_SECTION','identity_slot':'ANY_WORD','boundary':'WHOLE_PAGE'}]
    with pytest.raises(ValueError):validate_cold_start_profile(p)


def fixture_bundle(tmp_path, *, link=True):
    target='#turn' if link else '#shift'
    d=document(tmp_path,'Functions','<table><tr><th>Name</th><th>Description</th></tr>'
        '<tr><td><a href="'+target+'">TURN()</a></td><td>Return a rotation</td></tr></table>'
        '<h2 id="turn">TURN</h2><h3>Syntax</h3><pre>TURN(x)</pre>'
        '<h3>Description</h3><p>Returns a rotation.</p><p>Null values stay null.</p>'
        '<h3>Examples</h3><pre>SELECT TURN(1);</pre>'
        '<h2 id="shift">SHIFT</h2><pre>SHIFT(x)</pre><p>Returns a displacement.</p>')
    run(tmp_path,d)
    bundles=[json.loads(l) for l in (tmp_path/'run/evidence/bundles.jsonl').read_text(encoding='utf-8').splitlines()]
    return next(b for b in bundles if b['normalized_identity']=='TURN' and
                {u['source_role'] for u in b['definition_units']}=={'CATALOG_ENTRY','DEFINITION'})


def test_catalog_and_definition_are_separate_large_sources_with_explicit_link(tmp_path):
    b=fixture_bundle(tmp_path)
    assert b['definition_unit_integrity']['status']=='COMPLETE'
    assert b['definition_source_associations']
    assert all(edge['production_identity_merge'] is False for edge in b['definition_source_associations'])
    data=payload(b)
    assert {u['source_role'] for u in data['definition_units']}=={'CATALOG_ENTRY','DEFINITION'}
    assert data['definition_source_associations']
    for unit in data['definition_units']:
        assert set(unit['member_evidence_refs'])<=set(data['allowed_evidence_refs'])
    assert all('displacement' not in x['text'] for x in data['evidence'])


def test_same_name_does_not_invent_a_definition_link(tmp_path):
    b=fixture_bundle(tmp_path,link=False)
    assert not b['definition_source_associations']


def test_empty_fragment_anchor_precedes_identity_inside_proven_container(tmp_path):
    d=document(tmp_path,'Functions','<table><tr><th>Name</th><th>Description</th></tr>'
        '<tr><td><a href="#turn">TURN()</a></td><td>Return a rotation</td></tr></table>'
        '<ul><li><a name="turn"></a><p><code>TURN(x)</code></p><p>Returns a rotation.</p></li>'
        '<li><a name="shift"></a><p><code>SHIFT(x)</code></p><p>Returns a displacement.</p></li></ul>')
    run(tmp_path,d)
    bundles=[json.loads(l) for l in (tmp_path/'run/evidence/bundles.jsonl').read_text(encoding='utf-8').splitlines()]
    b=next(b for b in bundles if b['normalized_identity']=='TURN')
    assert b['definition_source_associations']
    assert all('displacement' not in v['text'] for v in payload(b)['evidence'])


def test_source_unit_not_flat_ranking_controls_complete_semantic_input(tmp_path):
    b=fixture_bundle(tmp_path)
    b['main_evidence']=[]
    for block in b['source_blocks']:block['definition_section_proofs']=[]
    # Definition unit transport independently retains the full ordered source.
    data=payload(b)
    expected={r for u in b['definition_units'] for r in u['member_evidence_refs']}
    assert expected<=set(data['allowed_evidence_refs'])
    assert expected==set(data['input_selection']['required_definition_unit_refs'])


@pytest.mark.parametrize('damage',['missing','foreign_hash','discovery_only'])
def test_incomplete_source_unit_is_reported_not_silently_marked_complete(tmp_path,damage):
    b=fixture_bundle(tmp_path)
    unit=next(u for u in b['definition_units'] if u['source_role']=='DEFINITION')
    bid=unit['member_evidence_refs'][-1]
    if damage=='missing':b['source_blocks']=[v for v in b['source_blocks'] if v['block_id']!=bid]
    else:
        block=next(v for v in b['source_blocks'] if v['block_id']==bid)
        block['source_hash']='foreign' if damage=='foreign_hash' else block['source_hash']
        if damage=='discovery_only':block['evidence_usage']='DISCOVERY_ONLY'
    data=payload(b)
    assert bid in data['input_selection']['definition_unit_transport_gaps']
    assert unit['definition_unit_id'] not in {u['definition_unit_id'] for u in data['definition_units']}


def test_catalog_scope_disabled_without_mutating_profile(tmp_path):
    d=document(tmp_path,'Data Types','<table><tr><th>Name</th><th>Description</th></tr>'
        '<tr><td>WHOLE</td><td>A number.</td></tr></table>')
    p=build_cold_start_profile(dbms='sample',documents=[d],run_id='catalog')
    owners=generate_owner_hypotheses([d],p)
    assert any(u['source_role']=='CATALOG_ENTRY' for u in build_definition_units([d],owners,p))
    p['hint_contract']['definition_scope_patterns']=[]
    saved=copy.deepcopy(p)
    assert not any(u['source_role']=='CATALOG_ENTRY' for u in build_definition_units([d],owners,p))
    assert p==saved
