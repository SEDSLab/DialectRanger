import json
from bs4 import BeautifulSoup
from agents.document_structure_v2.definition_observations import observe_definition
from agents.document_structure_v2.topology import build_page_topology
from agents.contracts.document_ir import from_v2_page
from agents.profile_bootstrap.cold_start import build_cold_start_profile
from agents.pipeline.generic_unified import run_generic_downstream


def test_plural_header_footnote_and_multiple_members():
    soup=BeautifulSoup('<table><tr><th>Operators<sup>2</sup></th></tr>'
        '<tr><td>|| &nbsp;&nbsp; -&gt; &nbsp;&nbsp; -&gt;&gt;</td></tr>'
        '<tr><td><small>[expr]</small> ISNULL &nbsp;&nbsp; <small>[expr]</small> NOT NULL</td></tr></table>','lxml')
    assert [m['identity'] for m in observe_definition(soup.find_all('tr')[1])['members']]==['||','->','->>']
    assert [m['identity'] for m in observe_definition(soup.find_all('tr')[2])['members']]==['ISNULL','NOT NULL']


def test_catalog_does_not_tokenize_description_or_example():
    soup=BeautifulSoup('<table><tr><th>Operator</th><th>Description</th></tr>'
        '<tr><td>+</td><td>EXAMPLE WORDS</td></tr></table>','lxml')
    assert [m['identity'] for m in observe_definition(soup.find_all('tr')[1])['members']]==['+']
    assert observe_definition(BeautifulSoup('<table><tr><th>Example</th></tr><tr><td>A + B</td></tr></table>','lxml').find_all('tr')[1])=={}


def test_pointer_return_type_is_not_sql_identity():
    term=BeautifulSoup('<dl><dt>int (*callback)(Context*)</dt><dd>Returns a value.</dd></dl>','lxml').dt
    assert observe_definition(term)['role']=='DEVELOPER_API_DECLARATION'


def test_compound_expression_connector_is_not_separate_catalog_member():
    soup=BeautifulSoup('<table><tr><th>Operators</th></tr><tr><td>'
        '<small>[expr]</small> BETWEEN <small>[expr]</small> AND <small>[expr]</small>'
        '</td></tr></table>','lxml')
    assert [m['identity'] for m in observe_definition(soup.find_all('tr')[1])['members']]==['BETWEEN']


def test_discovery_citation_preserved_but_not_in_semantic_prompt():
    from agents.semantic_stage.evidence_bundle import build_indexes,build_evidence_bundle
    from agents.semantic_stage.pipeline import build_semantic_prompt
    blocks=[{'block_id':'b'+str(i),'order':i,'block_kind':'TEXT','text':'Context '+str(i),
             'heading_ancestry':[],'dom_path':'body > p'+str(i)} for i in range(40)]
    doc={'page_id':'p','url':'https://official.test/ref','blocks':blocks}
    owner={'owner_id':'o','page_id':'p','member_block_ids':[b['block_id'] for b in blocks],
           'owner_scope_locality':'BROAD','owner_scope_anchor_block_ids':['b0'],'owner_kind':'SectionOwner'}
    unit={'identity_unit_id':'u','canonical_identity':'DEMO','owner_ids':['o'],'occurrence_ids':[],
          'adaptation_provenance':[{'owner_id':'o','block_id':'b39'}]}
    bundle=build_evidence_bundle(unit,indexes=build_indexes([unit],[],[owner],[doc]))
    last=next(b for b in bundle['source_blocks'] if b['block_id']=='b39')
    assert last['evidence_usage']=='DISCOVERY_ONLY'
    payload=json.loads(build_semantic_prompt(bundle,{}).split('INPUT:\n')[1].split('\n\nOUTPUT SCHEMA:')[0])
    assert 'b39' not in payload['allowed_evidence_refs']


def test_datatype_body_retained_and_no_adjacent_definition_leak(tmp_path):
    path=tmp_path/'types.html'
    path.write_text('<html><body><h1>Data types</h1><h2>The FLEX datatype</h2>'
        '<p>A FLEX column preserves its input value.</p><h2>The OTHER datatype</h2>'
        '<p>An OTHER column has a different value.</p></body></html>',encoding='utf-8')
    doc=from_v2_page(build_page_topology({'url':'https://example.test/types','html_path':str(path)},{}),dbms='fixture',html_path=path)
    profile=build_cold_start_profile(dbms='fixture',documents=[doc],run_id='test')
    root=tmp_path/'run'
    run_generic_downstream(dbms='fixture',documents=[doc],profile=profile,output_root=root,semantic=True,semantic_provider='deterministic')
    bundles=[json.loads(x) for x in (root/'evidence/bundles.jsonl').read_text(encoding='utf-8').splitlines()]
    flex=next(b for b in bundles if b['normalized_identity']=='FLEX')
    texts=[b['text'] for b in flex['source_blocks']]
    assert 'A FLEX column preserves its input value.' in texts
    assert not any('An OTHER' in t for t in texts)


def test_structural_admission_requires_source_proof_independent_of_runtime():
    from agents.contracts.publication_integrity import evaluate_publication_integrity
    from agents.contracts.admission import decide_admission
    unit={'identity_unit_id':'u','canonical_identity':'FLEX','feature_type':'DATATYPE','owner_ids':['o']}
    block={'block_id':'b','owner_ids':['o'],'text':'FLEX','source_structure_status':'RESOLVED',
        'observed_context':{'raw_node_kind':'LI','local_definition':{'role':'DATATYPE_DECLARATION_LIST','item_text':'FLEX'}}}
    integrity=evaluate_publication_integrity(unit,{'source_blocks':[block]},
        {'primary_type':'DATATYPE','status':'CONFIRMED'},{'admission_status':'ADMITTED'})
    args=dict(dbms='fixture',page_id='p',source_url='https://official.test',page_hash='hash',html_path='source.html',
        identity_unit_id='u',identity_credible=True,official_source=True,owner_evidence_correct=True,lineage_complete=True)
    assert decide_admission(**args,semantic_status='STRUCTURAL_ONLY',publication_integrity=integrity)['decision']=='ADMITTED_PARTIAL'
    assert decide_admission(**args,semantic_status='STRUCTURAL_ONLY')['decision']=='NEEDS_REVIEW'
    result=decide_admission(**args,semantic_status='UNAVAILABLE',publication_integrity=integrity)
    assert result['decision']=='ADMITTED_PARTIAL'
    assert result['semantic_status']=='UNAVAILABLE'
    assert 'VERIFIED_IDENTITY_SEMANTIC_UNAVAILABLE' in result['reason_codes']
    args['owner_evidence_correct']=False
    assert decide_admission(**args,semantic_status='STRUCTURAL_ONLY',publication_integrity=integrity)['decision']=='REJECTED'


def test_catalog_only_final_keeps_empty_fields_honestly(tmp_path):
    path=tmp_path/'operators.html'
    path.write_text('<html><body><h1>SQL Operators</h1><table><tr><th>Operators<sup>1</sup></th></tr>'
        '<tr><td>|| &nbsp;&nbsp; -&gt;</td></tr></table></body></html>',encoding='utf-8')
    doc=from_v2_page(build_page_topology({'url':'https://example.test/operators','html_path':str(path)},{}),dbms='fixture',html_path=path)
    profile=build_cold_start_profile(dbms='fixture',documents=[doc],run_id='test')
    root=tmp_path/'run'
    run_generic_downstream(dbms='fixture',documents=[doc],profile=profile,output_root=root,semantic=True,semantic_provider='deterministic')
    final=json.loads((root/'canonical/promotion_ready_kb.json').read_text(encoding='utf-8'))
    assert {r['name'] for r in final}=={'||','->'}
    assert all(r['semantic_status']=='STRUCTURAL_ONLY' for r in final)
    assert all(not r['semantics']['syntax'] and not r['semantics']['description'] for r in final)


def test_embedded_language_catalog_is_not_sql_publication_witness():
    from agents.contracts.publication_integrity import evaluate_publication_integrity
    block={'block_id':'b','owner_ids':['o'],'text':'AND','source_structure_status':'RESOLVED',
        'observed_context':{'feature_domain':'EMBEDDED_QUERY_LANGUAGE','local_definition':{
            'role':'OPERATOR_CATALOG','domain':'EMBEDDED_QUERY_LANGUAGE','members':[{'identity':'AND'}]}},
        'table_context':{'cells':[{'text':'AND','headers':[{'text':'Operator'}]}]}}
    result=evaluate_publication_integrity({'identity_unit_id':'u','canonical_identity':'AND',
        'feature_type':'OPERATOR','owner_ids':['o']},{'source_blocks':[block]},
        {'primary_type':'OPERATOR','status':'CONFIRMED'})
    assert result['state']=='REVIEW'


def test_bold_storage_class_definition_reaches_final_without_syntax_invention(tmp_path):
    path=tmp_path/'types.html'
    path.write_text('<html><body><h1>Storage Classes and Datatypes</h1>'
        '<p><b>FLEX</b>. The value preserves its input.</p></body></html>',encoding='utf-8')
    doc=from_v2_page(build_page_topology({'url':'https://example.test/types','html_path':str(path)},{}),dbms='fixture',html_path=path)
    profile=build_cold_start_profile(dbms='fixture',documents=[doc],run_id='test')
    root=tmp_path/'run'
    run_generic_downstream(dbms='fixture',documents=[doc],profile=profile,output_root=root,semantic=True,semantic_provider='deterministic')
    final=json.loads((root/'canonical/promotion_ready_kb.json').read_text(encoding='utf-8'))
    flex=next(r for r in final if r['name']=='FLEX')
    assert flex['feature_type']=='datatype'
    assert [x['value'] for x in flex['semantics']['description']]==['FLEX . The value preserves its input.']
    assert not flex['semantics']['syntax']


def test_explicitly_negated_native_type_remains_review():
    from agents.contracts.publication_integrity import evaluate_publication_integrity
    unit={'identity_unit_id':'u','canonical_identity':'FLEX','feature_type':'DATATYPE','owner_ids':['o']}
    proof={'heading_block_id':'h','identity_values':['FLEX'],'primary_identity':'FLEX','block_ids':['h','p']}
    blocks=[{'block_id':'h','block_kind':'HEADING','text':'The FLEX datatype','owner_ids':['o'],
        'heading_ancestry':['Data types'],'definition_section_proofs':[proof]},
        {'block_id':'p','text':'This engine does not have a separate FLEX storage class. Values use another representation.',
         'owner_ids':['o'],'definition_section_proofs':[proof]}]
    result=evaluate_publication_integrity(unit,{'source_blocks':blocks},{'primary_type':'DATATYPE','status':'CONFIRMED'})
    assert result['state']=='REVIEW'
    assert any(x['reason']=='NATIVE_TYPE_EXPLICITLY_NEGATED' for x in result['subject_conflicts'])
