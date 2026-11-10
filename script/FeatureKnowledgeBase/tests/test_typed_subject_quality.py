import copy
import json
import tracemalloc
import pytest
from agents.contracts.artifact_io import write_json,build_report
from agents.contracts.document_ir import from_v2_page
from agents.document_structure_v2.topology import build_page_topology
from agents.document_structure_v2.definition_observations import observe_definition
from agents.owner.surface_patterns import datatype_definition_name
from agents.owner.structure_execution import execute_structures
from agents.owner.typed_page import datatype_metadata
from agents.pipeline.generic_unified import run_generic_downstream
from tests.test_subject_contract_repair import section_profile
from bs4 import BeautifulSoup


def page(tmp_path,html):
    p=tmp_path/'page.html';p.write_text(html,encoding='utf-8')
    d=from_v2_page(build_page_topology({'url':'https://official.test/reference/alloy','html_path':str(p)},{}),dbms='sample',html_path=str(p))
    d['document_id']=d['page_id']
    profile=section_profile(d)
    a=copy.deepcopy(profile['hint_contract']['definition_structure_patterns'][0])
    a.update(structure='A',identity_slot='PAGE_SUBJECT',boundary='SUBJECT_PAGE')
    profile['hint_contract']['definition_structure_patterns'].append(a)
    return d,profile


def test_explicit_page_type_survives_nested_header_and_camel_case(tmp_path):
    d,p=page(tmp_path,'<main><div><header><div><div><h1>Alloy(T)</h1></div></div>'
             '<p>Documentation for the Alloy data type in ExampleDB</p></header>'
             '<div><span data-as="p">An ordered collection of T values.</span>'
             '<h2>Examples</h2><pre>CREATE TABLE x (a Alloy(Int));</pre></div></div></main>')
    head=next(b for b in d['blocks'] if b['block_kind']=='HEADING')
    assert datatype_metadata(head)['identity']=='ALLOY'
    scopes=execute_structures(d,p)['proofs']
    proof=next(x for x in scopes if x.get('feature_type')=='DATATYPE')
    assert any(b['text']=='An ordered collection of T values.' for b in d['blocks'] if b['block_id'] in proof['block_ids'])
    run_generic_downstream(dbms='sample',documents=[d],profile=p,output_root=tmp_path/'run',semantic=True,semantic_provider='deterministic')
    fs=json.loads((tmp_path/'run/canonical/promotion_ready_kb.json').read_text(encoding='utf-8'))
    f=next(f for f in fs if f['name']=='ALLOY' and f['feature_type']=='datatype')
    assert 'An ordered collection of T values.' in [c['value'] for c in f['semantics']['description']]
    assert 'Alloy(T)' in [c['value'] for c in f['semantics']['syntax']]


def test_other_subject_metadata_does_not_retype_heading(tmp_path):
    d,p=page(tmp_path,'<main><h1>Alloy(T)</h1><p>Documentation for the Other data type</p></main>')
    assert datatype_metadata(d['blocks'][0]) is None


def test_partial_code_in_prose_heading_is_not_a_declaration(tmp_path):
    d,p=page(tmp_path,'<main><h1>Operators</h1><h2>Checking for <code>NULL</code></h2>'
             '<p>Explains null comparison behavior.</p><h2>Using the <code>ORDER BY</code> Clause</h2>'
             '<p>Explains ordering.</p></main>')
    names={n for x in execute_structures(d,p)['proofs'] for n in x['identity_values']}
    assert 'CHECKING FOR NULL' not in names and 'USING THE ORDER BY CLAUSE' not in names


@pytest.mark.parametrize('tag',['p','span data-as="p"','div role="paragraph"'])
def test_notation_mapping_is_paragraph_independent(tag):
    end=tag.split()[0]
    soup=BeautifulSoup('<main><h1>Operators</h1><'+tag+'><code>a &gt;= b</code> — '
                       '<code>atLeast(a,b)</code></'+end+'></main>','lxml')
    node=soup.find_all('code')[0].parent
    obs=observe_definition(node)
    assert obs['members'][0]['identity']=='>='
    assert obs['declaration_origin']=='OBSERVED_NOTATION_IMPLEMENTATION_MAPPING'


@pytest.mark.parametrize('body,expected',[
    ('Syntax <code>SELECT a AND b</code> — logical conjunction.',{'AND'}),
    ('<code>a[N]</code> — The <code>arrayElement(a,N)</code> function.',{'[]'}),
    ('<code>a NOT IN ...</code> — The <code>notIn(a,b)</code> function.',{'NOT IN'}),
    ('ExampleDB supports the <code>IS NULL</code> and <code>IS NOT NULL</code> operators.',{'IS NULL','IS NOT NULL'}),
])
def test_explicit_operator_templates_are_observed_not_invented(body,expected):
    s=BeautifulSoup('<main><h1>Operators</h1><span data-as="p">'+body+'</span></main>','lxml')
    obs=observe_definition(s.find('span'))
    assert {m['identity'] for m in obs['members']}==expected


def test_query_example_does_not_become_operator_definition():
    s=BeautifulSoup('<main><h1>Operators</h1><h2>Examples</h2><span data-as="p">'
                     'Example <code>SELECT a AND b</code></span></main>','lxml')
    assert observe_definition(s.find('span'))=={}


def test_prose_operator_catalog_reaches_final_once(tmp_path):
    d,p=page(tmp_path,'<main><h1>Operators</h1><h2>Checking for NULL</h2>'
             '<span data-as="p">ExampleDB supports the <code>IS NULL</code> and <code>IS NOT NULL</code> operators.</span>'
             '<h2>Logical conjunction</h2><span data-as="p">Syntax <code>SELECT a AND b</code> — conjunction.</span></main>')
    p['hint_contract']['definition_structure_patterns'].append({'structure':'E','identity_slot':'TYPED_MEMBER','boundary':'SHARED_PARENT'})
    p['hint_contract']['definition_scope_patterns'].append({'relation':'CATALOG_MEMBER_CONTEXT','identity_slot':'TYPED_LITERAL_MEMBER','boundary':'LIST_AND_SHARED_PARENT'})
    run_generic_downstream(dbms='sample',documents=[d],profile=p,output_root=tmp_path/'run',semantic=True,semantic_provider='deterministic')
    fs=json.loads((tmp_path/'run/canonical/promotion_ready_kb.json').read_text(encoding='utf-8'))
    names=[f['name'] for f in fs if f['feature_type']=='operator']
    assert set(names)=={'IS NULL','IS NOT NULL','AND'}
    assert len(names)==3


def test_article_is_not_a_datatype_identity():
    assert datatype_definition_name('A data type is a fixed set of properties.') is None
    assert datatype_definition_name('FOO type stores fixed values.')=='FOO'


def test_nested_optional_type_declaration_keeps_identity():
    from agents.contracts.definition_entry import declaration_names
    assert declaration_names('EXACT [ ( p [, s ]) ]')==['EXACT']


def test_same_name_type_and_local_constructor_stay_distinct(tmp_path):
    d,p=page(tmp_path,'<main><div><header><div><h1>Alloy(T)</h1></div>'
             '<p>Documentation for the Alloy data type in ExampleDB</p></header>'
             '<div><span data-as="p">A collection of T values.</span>'
             '<h2>Creating a value</h2><span data-as="p">You can use a function to create a collection:</span>'
             '<div><div><pre>alloy(T)</pre></div></div></div></div></main>')
    c=copy.deepcopy(p['hint_contract']['definition_structure_patterns'][0])
    c.update(structure='C',identity_slot='LEADING_TERM',boundary='CURRENT_ENTRY')
    c['execution']['identity']['alternatives']=[{'kind':'VERIFIED_ANCHOR'}]
    p['hint_contract']['definition_structure_patterns'].append(c)
    p['hint_contract']['definition_scope_patterns'].append({'relation':'PAGE_SUBJECT_RELATION','identity_slot':'TYPED_SUBJECT_HEADING','boundary':'DEFINITION_PREFIX'})
    run_generic_downstream(dbms='sample',documents=[d],profile=p,output_root=tmp_path/'run',semantic=True,semantic_provider='deterministic')
    fs=json.loads((tmp_path/'run/canonical/promotion_ready_kb.json').read_text(encoding='utf-8'))
    assert {f['feature_type'] for f in fs if f['name']=='ALLOY'}=={'function','datatype'}


def test_camel_case_type_requires_its_own_explicit_type_sentence(tmp_path):
    d,p=page(tmp_path,'<main><h1>Data Types</h1><h2>Structured Values</h2>'
             '<h3>TreeType</h3><p>This type stores trees.</p>'
             '<p>TreeType is a system-defined type, usable in table columns.</p></main>')
    head=next(b for b in d['blocks'] if b['text']=='TreeType')
    assert datatype_metadata(head)['identity']=='TREETYPE'
    proof=next(x for x in execute_structures(d,p)['proofs'] if x['identity_values']==['TREETYPE'])
    from agents.owner.definition_patterns import verified_pattern
    assert verified_pattern(proof,head,'TREETYPE')['feature_type']=='DATATYPE'


def test_explicit_named_datatype_heading_keeps_its_examples(tmp_path):
    d,p=page(tmp_path,'<main><h1>Data Types</h1><h2>Alloy Data Type</h2>'
             '<p>Stores collection values.</p><h3>Examples</h3><pre>CREATE TABLE t (a ALLOY);</pre></main>')
    run_generic_downstream(dbms='sample',documents=[d],profile=p,output_root=tmp_path/'run',semantic=True,semantic_provider='deterministic')
    fs=json.loads((tmp_path/'run/canonical/promotion_ready_kb.json').read_text(encoding='utf-8'))
    f=next(f for f in fs if f['name']=='ALLOY' and f['feature_type']=='datatype')
    assert 'CREATE TABLE t (a ALLOY);' in [c['value'] for c in f['semantics']['examples']]


def test_stream_json_preserves_destination_on_failure(tmp_path):
    p=tmp_path/'summary.json';p.write_text('{"old":true}',encoding='utf-8')
    with pytest.raises(TypeError):write_json(p,{'bad':object()})
    assert json.loads(p.read_text())=={'old':True}
    assert not list(tmp_path.glob('*.tmp'))


def test_stream_json_does_not_allocate_full_serialized_file(tmp_path):
    data={'values':['x'*1000]*10000}
    tracemalloc.start();write_json(tmp_path/'large.json',data)
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    assert peak<1024*1024


def test_build_summary_does_not_copy_evidence_graph(tmp_path):
    result={'status':'accepted','final_feature_count':12,'evidence':object(),'type':object(),
            'canonical':object(),'semantic':{'summary':{'actual_model_calls':0},'features':object()},
            'publication':{'final_count':12,'decisions':object()}}
    report=build_report(result,tmp_path)
    write_json(tmp_path/'summary.json',report)
    assert report['final_feature_count']==12
    assert report['publication']['final_count']==12
    assert report['stage_artifacts']['evidence'].endswith('bundles.jsonl')
