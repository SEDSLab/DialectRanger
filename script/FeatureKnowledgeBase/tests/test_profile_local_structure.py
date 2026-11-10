"""Profile guidance is local, multi-label and independently source verified."""
import copy
import pytest
from agents.profile_bootstrap.cold_start import build_cold_start_profile, validate_cold_start_profile
from agents.profile_bootstrap.local_structure import local_context, owner_local_hint, observe_regions, build_local_patterns
from agents.owner.generic import generate_owner_hypotheses
from agents.type.evidence import build_typed_feature_evidence
from agents.type.decision import decide_type
from tests.test_profile_definition_execution import document


def test_sampling_covers_type_x_structure_without_discovery_type_tags():
    pages = []
    for i in range(15):
        label = 'Data Types' if i == 14 else 'Functions'
        pages.append({'page_id': str(i), 'url': 'https://official.test/' + str(i), 'page_hash': str(i),
            'blocks': [{'block_id': 'b' + str(i), 'block_kind': 'TABLE_ROW', 'text': 'Sample',
                        'heading_ancestry': [label]}]})
    profile = build_cold_start_profile(dbms='unseen', documents=pages, run_id='sample')
    assert '14' in {p['page_id'] for p in profile['source_pages']}
    assert not profile['bootstrap']['sampling']['uncovered_strata']
    assert {r['context_type'] for r in profile['hint_contract']['local_type_patterns']} == {'FUNCTION', 'DATATYPE'}
    assert all(s['type_hint'] == 'UNCERTAIN' for s in profile['surfaces'])


def test_mixed_parent_uses_nearest_section_and_negative_child_barrier():
    assert local_context({'heading_ancestry': ['Functions and Operators']})['types'] == ['FUNCTION', 'OPERATOR']
    assert local_context({'heading_ancestry': ['Functions and Operators', 'Data Types', 'Examples']})['types'] == []
    assert local_context({'heading_ancestry': ['Functions and Operators', 'Data Types', 'WHOLE']})['types'] == ['DATATYPE']


def test_profile_local_hint_cannot_leak_to_sibling_or_unknown_structure(tmp_path):
    d = document(tmp_path, 'Reference', '<h2>Functions</h2><dl><dt>TURN(x)</dt><dd>Returns a rotation.</dd></dl>'
        '<h2>Data Types</h2><ul><li><p><code>WHOLE(p)</code></p><p>A numerical declaration.</p></li></ul>'
        '<h2>Discussion</h2><p>Ordinary prose.</p>')
    profile = build_cold_start_profile(dbms='sample', documents=[d], run_id='mixed')
    owners = generate_owner_hypotheses([d], profile)
    hints = [h for o in owners for h in o['profile_hint'].get('local_type_patterns', [])]
    assert {h['context_type'] for h in hints} >= {'FUNCTION', 'DATATYPE'}
    by_id = {b['block_id']: b for b in d['blocks']}
    for h in hints:
        assert h['context_type'] in local_context(by_id[h['block_id']])['types']
        assert h['page_hash'] == d['page_hash']
    # Context must not become a new Identity grouping prior.
    assert all(o['profile_hint'].get('type_hint', 'UNCERTAIN') == 'UNCERTAIN' for o in owners)
    unknown = next(b for b in d['blocks'] if b.get('text') == 'Ordinary prose.')
    assert not owner_local_hint(d, [unknown], profile, {}).get('local_type_patterns')


def test_profile_hint_alone_never_creates_confirmed_feature():
    rows = build_typed_feature_evidence({'canonical_subject': 'TOKEN', 'owner_id': 'o',
        'profile_hint': {'type_hint': 'DATATYPE'}, 'blocks': [{'block_id': 'b', 'text': 'Ordinary prose.', 'block_type': 'text'}]})
    assert decide_type({'identity_unit_id': 'u'}, rows)['type_status'] != 'CONFIRMED'


def test_local_patterns_disable_without_mutating_profile_and_old_profiles_still_load(tmp_path):
    d = document(tmp_path, 'Data Types', '<p>Reference.</p>')
    profile = build_cold_start_profile(dbms='sample', documents=[d], run_id='contract')
    profile['hint_contract']['local_type_patterns'] = []
    old = copy.deepcopy(profile)
    assert not owner_local_hint(d, d['blocks'], profile, {}).get('local_type_patterns')
    assert profile == old
    del profile['hint_contract']['local_type_patterns']
    assert validate_cold_start_profile(profile)


def test_invalid_recipe_is_rejected_not_executed(tmp_path):
    d = document(tmp_path, 'Data Types', '<h2>Data Types</h2>')
    profile = build_cold_start_profile(dbms='sample', documents=[d], run_id='invalid')
    profile['hint_contract']['local_type_patterns'][0]['boundary'] = 'ARBITRARY_SELECTOR'
    with pytest.raises(ValueError):
        validate_cold_start_profile(profile)


def test_local_context_does_not_split_identity_units(tmp_path):
    from agents.pipeline.production_identity import execute_production_identity_path
    d = document(tmp_path, 'Functions', '<h2>Scalar Functions</h2><dl>'
        '<dt>TURN(x)</dt><dd>Returns a rotation.</dd></dl><h2>TURN</h2>'
        '<p><code>TURN(x)</code></p><p>Returns a rotation.</p>')
    profile = build_cold_start_profile(dbms='sample', documents=[d], run_id='identity-conservation')
    legacy = copy.deepcopy(profile)
    del legacy['hint_contract']['local_type_patterns']
    first = execute_production_identity_path([d], output_dir=tmp_path/'with-local', profile=profile)
    second = execute_production_identity_path([d], output_dir=tmp_path/'without-local', profile=legacy)
    assert {u['identity_unit_id'] for u in first['units']} == {u['identity_unit_id'] for u in second['units']}
