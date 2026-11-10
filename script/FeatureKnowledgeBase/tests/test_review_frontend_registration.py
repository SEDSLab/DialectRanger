import importlib.util
import json
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location('review_builder', Path(__file__).resolve().parents[1] / 'scripts/build_review_frontend.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def fixture_pipeline(tmp_path):
    pipeline = tmp_path / 'pipeline'
    (pipeline / 'canonical').mkdir(parents=True)
    record = {'feature_id': 'canonical:one', 'dbms': 'sample', 'name': 'DEMO',
        'feature_type': 'function', 'semantic_status': 'PARTIAL',
        'semantics': {'description': [{'value': 'First paragraph.'}, {'value': 'Second paragraph.'}],
            'syntax': [{'value': 'DEMO(x)', 'evidence': {'evidence_role': 'FORMAL_SYNTAX'}}], 'examples': []},
        'structural': {'signatures': ['DEMO(value)']}, 'provenance': {'identity_unit_id': 'unit:one'}}
    review = {'record': dict(record, feature_id='canonical:two', name='OTHER'),
        'admission': {'decision': 'NEEDS_REVIEW', 'reason_codes': ['SUBJECT_CONFLICT']}}
    (pipeline / 'canonical/promotion_ready_kb.json').write_text(json.dumps([record]), encoding='utf-8')
    (pipeline / 'canonical/review_features.jsonl').write_text(json.dumps(review) + '\n', encoding='utf-8')
    return pipeline, record, review


def test_registration_preserves_content_and_separates_review(tmp_path):
    p, record, review = fixture_pipeline(tmp_path)
    before = builder.digest(p / 'canonical/promotion_ready_kb.json')
    freeze, viewer = tmp_path / 'freeze', tmp_path / 'viewer'
    manifest = builder.build('sample', 'Sample', p, freeze, viewer)
    payload = builder.read(viewer / 'data/sample.json')
    assert payload['final'] == [record]
    assert payload['review'] == [review]
    assert manifest['counts']['final'] == manifest['counts']['review'] == 1
    assert manifest['integrity']['projection_loss'] == 0
    assert builder.digest(freeze / 'final_feature_kb.json') == before
    assert builder.digest(p / 'canonical/promotion_ready_kb.json') == before
    assert builder.read(viewer / 'data/catalog.json')['databases'][0]['id'] == 'sample'
    with pytest.raises(FileExistsError):
        builder.build('sample', 'Sample', p, freeze, viewer)
    with pytest.raises(ValueError, match='already registered'):
        builder.build('sample', 'Sample', p, tmp_path / 'another', viewer)


def test_registration_rejects_foreign_db_and_duplicate_ids(tmp_path):
    p, record, review = fixture_pipeline(tmp_path)
    with pytest.raises(ValueError, match='foreign DB'):
        builder.build('other', 'Other', p, tmp_path / 'freeze', tmp_path / 'viewer')
    review['record']['feature_id'] = record['feature_id']
    (p / 'canonical/review_features.jsonl').write_text(json.dumps(review), encoding='utf-8')
    with pytest.raises(ValueError, match='Duplicate'):
        builder.build('sample', 'Sample', p, tmp_path / 'freeze', tmp_path / 'viewer')


def test_catalog_supports_second_db_without_replacing_first(tmp_path):
    p, _, _ = fixture_pipeline(tmp_path)
    viewer = tmp_path / 'viewer'
    builder.build('sample', 'Sample', p, tmp_path / 'freeze1', viewer)
    first_hash = builder.digest(viewer / 'data/sample.json')
    final = builder.read(p / 'canonical/promotion_ready_kb.json')
    final[0]['dbms'] = 'other'
    (p / 'canonical/promotion_ready_kb.json').write_text(json.dumps(final), encoding='utf-8')
    (p / 'canonical/review_features.jsonl').write_text('', encoding='utf-8')
    builder.build('other', 'Other', p, tmp_path / 'freeze2', viewer)
    assert [d['id'] for d in builder.read(viewer / 'data/catalog.json')['databases']] == ['sample', 'other']
    assert builder.digest(viewer / 'data/sample.json') == first_hash
