from agents.semantic_stage.evidence_bundle import build_indexes, build_evidence_bundle
from agents.semantic_stage.grounding import validate_semantic_output

def bundle(name='ABS', metadata=False, broad=False):
    blocks=[{'block_id':bid,'block_kind':kind,'text':text,'order':i,'heading_level':3 if kind=='HEADING' else None,
             'heading_ancestry':[],'dom_path':'main > '+bid}
            for i,(bid,kind,text) in enumerate([
            ('h','HEADING','ABS'),('s','CODE','ABS(x) | ALIAS(x)'),('d','TEXT','Returns a value.'),
            ('d2','TEXT','Handles negative values.'),('label','TEXT','Example:'),('e','CODE','ABS(1)\nABS(2)'),
            ('next','HEADING','ACOS'),('n','CODE','ACOS(x)'),('nd','TEXT','Neighbor description.')])]
    doc={'page_id':'p','url':'https://docs.example/reference','blocks':blocks}
    owner={'owner_id':'o','page_id':'p','member_block_ids':['h','s'],'owner_kind':'SignatureOwner'}
    if broad:owner.update({'member_block_ids':[b['block_id'] for b in blocks],'owner_scope_locality':'BROAD'})
    if metadata:owner['profile_hint']={'feature_bearing':False,'surface_role':'SUPPORTING_CONTEXT'};blocks[1]['text']='Author: X Publisher: Y ABS(2009)'
    group={'identity_unit_id':'u','canonical_identity':name,'owner_ids':['o'],'occurrence_ids':['occ']}
    occurrence={'occurrence_id':'occ','owner_id':'o','page_id':'p','source_block_id':'s','occurrence_role':'FORMAL_VARIANT'}
    result=build_evidence_bundle(group,indexes=build_indexes([group],[occurrence],[owner],[doc]))
    assert owner['member_block_ids']==([b['block_id'] for b in blocks] if broad else ['h','s'])
    return result

def test_definition_prose_and_examples_bound_without_neighbor():
    result=bundle()
    assert {'d','d2','e'} <= {b['block_id'] for b in result['source_blocks']}
    assert not {'next','n','nd'} & {b['block_id'] for b in result['source_blocks']}
    assert len(result['definition_evidence'])==2
    assert result['official_example_evidence'][0]['raw_text']=='ABS(1)\nABS(2)'
    assert {b['block_id'] for b in result['formal_syntax_evidence']}=={'s'}

def test_explicit_alias_shares_only_current_section():
    assert {b['block_id'] for b in bundle('ALIAS')['source_blocks']}=={'h','s','d','d2','label','e'}

def test_incidental_identity_does_not_expand_section():
    result=bundle('OTHER')
    # A proven ABS/ALIAS owner cannot supply even its discovery anchors to an
    # unrelated subject. Keep the exclusion visible rather than leaking h/s.
    assert result['source_blocks']==[]
    assert result['excluded_owners'][0]['reason']=='EXCLUDED_FOREIGN_SUBJECT'

def test_book_metadata_does_not_expand_section():
    assert {b['block_id'] for b in bundle(metadata=True)['source_blocks']}=={'h','s'}


def test_broad_page_owner_does_not_become_full_page_projection():
    assert not {'next','n','nd'} & {b['block_id'] for b in bundle(broad=True)['source_blocks']}


def test_neighbor_claim_is_rejected_by_unchanged_grounding():
    result=validate_semantic_output({'description':{'text':'Neighbor description.','evidence_refs':['nd']}},bundle())
    assert result['invalid_evidence_refs']==1
    assert result['fields']['description'] is None
