"""Portable datatype consumer/serializer and provenance negative tests."""
import copy
import hashlib
import json
import pytest

from agents.contracts.canonical import deserialize_canonical_record, serialize_canonical_record
from agents.contracts.datatype_knowledge import build_datatype_extensions, response_extension_payload, validate_datatype_extensions, relation_kind
from agents.document_structure_v2.topology import build_page_topology
from agents.contracts.document_ir import from_v2_page
from agents.semantic_stage.source_structure import SourceStructureIndex


@pytest.fixture
def frozen(tmp_path):
    path = tmp_path / 'datatype.html'
    path.write_text('<html><body><main><table><tr><th>SQL type</th><th>Druid runtime type</th><th>Notes</th></tr>'
                    '<tr><td>DECIMAL</td><td>DOUBLE</td><td>DECIMAL uses floating point, not fixed point math</td></tr>'
                    '</table></main></body></html>', encoding='utf-8')
    url = 'https://official.invalid/types'
    page = from_v2_page(build_page_topology({'url': url, 'html_path': str(path)}, {}), dbms='fixture', html_path=str(path))
    block = next(b for b in page['blocks'] if b.get('block_kind') == 'TABLE_ROW' and 'DECIMAL' in b['text'])
    observed = SourceStructureIndex().observe(page, block)
    cells = observed['table_context']['cells']
    uid, oid, bid = 'unit', 'owner', block['block_id']
    def claim(index):
        cell = cells[index]
        return {'text': cell['text'], 'evidence_refs': [bid], 'source_url': url,
                'source_locations': [{'block_id': bid, 'cell_index': index, 'dom_locator': cell['dom_locator'],
                                      'header_locators': [h['dom_locator'] for h in cell['headers']]}]}
    return {
        'unit': {'identity_unit_id': uid, 'canonical_identity': 'DECIMAL', 'feature_type': 'DATATYPE', 'owner_ids': [oid]},
        'bundle': {'source_blocks': [{**block, 'source_url': url, 'owner_ids': [oid], 'source_hash': observed['source_hash']}]},
        'documents': [page],
        'owners': [{'owner_id': oid, 'page_id': page['page_id'], 'member_block_ids': [bid]}],
        'envelopes': [{'identity_unit_id': uid, 'owner_id': oid, 'source_block_ids': [bid], 'evidence_id': 'evidence'}],
        'payload': {'identity_group_id': uid, 'semantic_extensions': {'datatype_name': [claim(0)],
                    'datatype_mapping': [claim(1)], 'uncertain_fields': ['Default applicability unknown']}, 'notes': [claim(2)]},
    }


def consume(frozen):return build_datatype_extensions(**frozen)

def test_real_decimal_relation_and_notes(frozen):
    out=consume(frozen);dt=out['datatype'];assert not out['diagnostics']
    assert dt['definition_names'][0]['text']=='DECIMAL'
    assert dt['mappings'][0]['source_type']=='DECIMAL'
    assert dt['mappings'][0]['target_type']=='DOUBLE'
    assert dt['mappings'][0]['relation_kind']=='SQL_TO_RUNTIME_TYPE'
    assert dt['notes'][0]['text']=='DECIMAL uses floating point, not fixed point math'
    p=dt['mappings'][0]['provenance'][0]
    assert p['source_cell']['cell_index']==0 and p['value_cell']['cell_index']==1
    assert p['value_cell']['headers'][0]['text']=='Druid runtime type'
    assert p['owner_ids'] and p['evidence_ids'] and p['source_hash']

@pytest.mark.parametrize('change', ['wrong_value','wrong_block','wrong_column','wrong_header','wrong_owner','wrong_name','wrong_url'])
def test_conflicting_claims_are_not_knowledge(frozen,change):
    x=copy.deepcopy(frozen);claim=x['payload']['semantic_extensions']['datatype_mapping'][0]
    if change=='wrong_value':claim['text']='VARCHAR'
    elif change=='wrong_block':claim['evidence_refs']=['other-row'];claim['source_locations'][0]['block_id']='other-row'
    elif change=='wrong_column':claim['source_locations'][0]['cell_index']=2
    elif change=='wrong_header':claim['source_locations'][0]['header_locators']=['wrong-header']
    elif change=='wrong_owner':x['unit']['owner_ids']=['other-owner']
    elif change=='wrong_name':x['unit']['canonical_identity']='ANOTHER_TYPE'
    elif change=='wrong_url':claim['source_url']='https://different.invalid/type'
    out=consume(x);assert out['datatype']['mappings']==[];assert out['diagnostics']

def test_default_is_not_sql_default(frozen):
    out=consume(frozen);dt=out['datatype']
    assert 'sql_default_value' not in dt
    assert dt['uncertain_observations']
    assert all(x['status']=='UNCERTAIN' and x['knowledge_asserted'] is False for x in dt['uncertain_observations'])

def test_mapping_absent_is_legal(frozen):
    x=copy.deepcopy(frozen);x['payload']['semantic_extensions']['datatype_mapping']=[]
    assert consume(x)['datatype']['mappings']==[]

def test_other_feature_type_not_enriched(frozen):
    x=copy.deepcopy(frozen);x['unit']['feature_type']='FUNCTION';assert consume(x) is None

def test_missing_extensions_do_not_invent_mapping(frozen):
    x=copy.deepcopy(frozen);x['payload']={};assert consume(x) is None

@pytest.mark.parametrize('kind',['target','relation','default','certainty'])
def test_invalid_serialized_knowledge_rejected(frozen,kind):
    ext=consume(frozen)
    if kind=='target':ext['datatype']['mappings'][0]['target_type']='VARCHAR'
    elif kind=='relation':ext['datatype']['mappings'][0]['relation_kind']='RETURN_TYPE'
    elif kind=='default':ext['datatype']['sql_default_value']='0.0'
    else:ext['datatype']['uncertain_observations'][0]['knowledge_asserted']=True
    with pytest.raises(ValueError):validate_datatype_extensions(ext)

def test_roundtrip_projection_preserves_every_field(frozen):
    ext=consume(frozen);r={'canonical_identity':{'key':'unchanged'},'feature_type':'datatype','semantic_extensions':ext,'provenance':{'retained':'unchanged'}}
    assert deserialize_canonical_record(serialize_canonical_record(r))==r

def test_old_record_roundtrip_no_extension():
    r={'canonical_entry_id':'old','feature_type':'FUNCTION','description':'preserve','formal_syntax':['F(x)'],'publication_status':'PUBLISHABLE','lineage':{'owner_ids':['o']}}
    assert deserialize_canonical_record(serialize_canonical_record(r))==r

def test_provider_response_binding(frozen):
    raw=json.dumps(frozen['payload'],ensure_ascii=False,sort_keys=True);h=hashlib.sha256(raw.encode()).hexdigest();uid=frozen['unit']['identity_unit_id']
    feature={'identity_group_id':uid,'semantic_response_hash':h}
    response={'identity_group_id':uid,'raw_response':raw,'parsed':frozen['payload'],'response_hash':h}
    assert response_extension_payload(feature,response)[0]==frozen['payload']
    response['parsed']={};assert response_extension_payload(feature,response)[0] is None

@pytest.mark.parametrize('header,expected',[('Runtime type','SQL_TO_RUNTIME_TYPE'),('Java class','SQL_TO_HOST_LANGUAGE_TYPE'),('JDBC type','SQL_TO_HOST_LANGUAGE_TYPE'),('Internal type','OTHER_EXPLICIT_MAPPING'),('Target','UNKNOWN_RELATION')])
def test_generic_relation_vocabulary(header,expected):
    assert relation_kind([{'text':'SQL type'}],[{'text':header}])==expected

def test_reversed_direction_not_inferred_from_value():
    assert relation_kind([{'text':'Runtime type'}],[{'text':'SQL type'}])=='UNKNOWN_RELATION'

def test_synthetic_multiple_relations_serialize(frozen):
    ext=consume(frozen);second=copy.deepcopy(ext['datatype']['mappings'][0]);second['relation_kind']='SQL_TO_HOST_LANGUAGE_TYPE'
    second['target_type']=second['text']='java.lang.Number'
    second['provenance'][0]['value_cell']['text']='java.lang.Number'
    second['provenance'][0]['value_cell']['headers'][0]['text']='Java class'
    ext['datatype']['mappings'].append(second)
    record={'canonical_identity':{'key':'synthetic-only'},'feature_type':'DATATYPE','semantic_extensions':ext}
    assert len(deserialize_canonical_record(serialize_canonical_record(record))['semantic_extensions']['datatype']['mappings'])==2

def test_datatype_knowledge_cannot_pollute_function_schema(frozen):
    record={'canonical_identity':{'key':'function'},'feature_type':'FUNCTION','semantic_extensions':consume(frozen)}
    with pytest.raises(ValueError):serialize_canonical_record(record)
