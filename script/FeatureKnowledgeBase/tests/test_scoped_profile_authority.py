import copy
import pytest
from tests.test_profile_effective_type_source import data
from agents.pipeline.generic_unified import _effective_profile_types, _explicit_profile_mode
from agents.profile_bootstrap.cold_start import validate_cold_start_profile


def test_scoped_verified_type_does_not_require_unconfigured_units(tmp_path):
    from agents.contracts.type_decision import make_type_decision
    unit,evidence,page=data(tmp_path,kind='DATATYPE')
    inference=make_type_decision(dbms='sample',identity_unit_id=unit['identity_unit_id'],primary_type='OPERATOR',
        source_url=page['url'],page_id=page['page_id'],page_hash=page['page_hash'],html_path=page['html_path'],
        confidence=0.8,status='CONFIRMED',parent_ids=[])
    unit.pop('profile_definition_required',None)
    other=copy.deepcopy(unit)
    other.update(identity_unit_id='unconfigured',canonical_identity='UNCONFIGURED',owner_ids=[])
    units,decisions=_effective_profile_types([unit,other],[inference],evidence['owners'],[page],
        authority_required=True,configured_only=False)
    assert units[0]['profile_definition_required'] is True
    assert decisions[0]['source']=='EXPLICIT_PROFILE'
    assert 'profile_definition_required' not in units[1]
    assert units[1]['feature_type']==other['feature_type']


def test_scoped_mode_validates_and_preserves_compatibility():
    import json
    from pathlib import Path
    p=json.loads(Path('inputs/cockroachdb/profile.json').read_text(encoding='utf-8'))
    validate_cold_start_profile(p)
    assert not _explicit_profile_mode(p)
    p['hint_contract']['authority_scope']='TYPO'
    with pytest.raises(ValueError):validate_cold_start_profile(p)
