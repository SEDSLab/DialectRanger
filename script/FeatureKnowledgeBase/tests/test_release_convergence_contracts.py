"""Complete local declaration positions, not page keywords or feature lists."""
import json
import pytest
from bs4 import BeautifulSoup
from agents.owner.catalog_surfaces import operator_form, catalog_surfaces
from agents.document_structure_v2.definition_observations import observe_definition
from tests.test_profile_definition_execution import document, run
from tests.test_five_structure_executors import profile_for


@pytest.mark.parametrize('value,expected',[
    ('left_value @> right_value','@>'),('arg1 || arg2','||'),
    ('x BETWEEN lo AND hi','BETWEEN'),('x NOT BETWEEN lo AND hi','NOT BETWEEN'),
    ('x IS NOT NULL','IS NOT NULL'),('NOT x','NOT'),('list[begin:end]','[:]'),
    ('items[index]','[]'),('record.field','.'),('value::TYPE','::'),('!~~*','!~~*')])
def test_literal_operator_slots(value,expected):
    assert operator_form(value)==expected


@pytest.mark.parametrize('value',['TIME WITH TIME ZONE','SELECT x + y','sample(42)',
    'Returns a + b.','A simple example','CHARACTER SET'])
def test_non_operator_whole_cells(value):
    assert operator_form(value) is None


def test_mixed_catalog_slot_is_not_its_chapter_type():
    h=['Function','Description'];c=['left_value @> right_value','Alias for a containment function.']
    assert catalog_surfaces(h,c,'Functions and Operators')[0]['feature_type']=='OPERATOR'
    assert catalog_surfaces(['Name','Description'],['turn(x)','Returns a rotation.'])[0]['feature_type']=='FUNCTION'
    assert not catalog_surfaces(h,c,'Example query output')
    assert not catalog_surfaces(h,['turn(x)','Mention of left_value @> right_value.'])[0]['feature_type']=='OPERATOR'


def test_native_operator_column_is_independent_from_function_column():
    soup=BeautifulSoup('<h2>SQL Reference</h2><table><tr><th>Function</th><th>Operator</th><th>Description</th></tr>'
        '<tr><td>extract(value,path)</td><td>-&gt;</td><td>Extracts a value.</td></tr></table>','lxml')
    observed=observe_definition(soup.find_all('tr')[1])
    assert observed['role']=='DECLARATION_CATALOG'
    assert observed['members']==[{'identity':'->','feature_type':'OPERATOR','cell_index':1,'raw_entry':'->'}]


def test_numbered_fields_without_function_category_reach_final(tmp_path):
    d=document(tmp_path,'Reference','<h2>TURN(value)</h2><table>'
        '<tr><td>Description</td><td>Returns a rotation.</td></tr>'
        '<tr><td>Example 1</td><td>TURN(1)</td></tr><tr><td>Result 1</td><td>2</td></tr>'
        '<tr><td>Example 2</td><td>TURN(3)</td></tr><tr><td>Result 2</td><td>4</td></tr></table>')
    records=run(tmp_path,d,profile_for(d,'B'))
    row=next(r for r in records if r['name']=='TURN' and r['feature_type']=='function')
    assert [v['value'] for v in row['semantics']['description']]==['Returns a rotation.']
    assert [v['value'] for v in row['semantics']['syntax']]==['TURN(value)']
    examples=[v['value'] for v in row['semantics']['examples']]
    assert any('TURN(1)' in v and 'Result 1 2' in v for v in examples)
    assert any('TURN(3)' in v and 'Result 2 4' in v for v in examples)


def test_expression_catalog_is_published_as_operator(tmp_path):
    d=document(tmp_path,'Reference','<table><tr><th>Function</th><th>Description</th></tr>'
        '<tr><td>left_value @&gt; right_value</td><td>Tests containment.</td></tr></table>')
    records=run(tmp_path,d,profile_for(d,'D'))
    assert any(r['name']=='@>' and r['feature_type']=='operator' for r in records)
    assert not any(r['name']=='@>' and r['feature_type']=='function' for r in records)


def test_relation_matrix_and_output_schema_do_not_vote():
    from agents.type.definition_roles import interpret_type_evidence
    blocks=[{'block_id':'matrix','block_kind':'TABLE_ROW','heading_ancestry':['Casting Operations Matrix'],
             'table_metadata':{'cells':['SAMPLE','E','I'],'headers':['','FIRST','SECOND']}},
            {'block_id':'schema','block_kind':'TABLE_ROW','heading_ancestry':['Table Functions'],
             'table_metadata':{'cells':['sample','JSON','The input value'],'headers':['Name','Type','Description']}}]
    unit={'identity_unit_id':'u','canonical_identity':'SAMPLE'}
    votes=[{'identity_unit_id':'u','candidate_type':k,'evidence_kind':e,'block_id':b['block_id']}
           for b,k,e in zip(blocks,['OPERATOR','FUNCTION'],['OPERATOR_SLOT','FUNCTION_SLOT'])]
    interpreted=interpret_type_evidence([unit],votes,[{'page_id':'p','blocks':blocks}])
    assert all(r['definition_vote_eligible'] is False for r in interpreted)
    assert {r['role_proof']['rule'] for r in interpreted}=={
        'RELATION_MATRIX_NOT_FEATURE_DECLARATION/v1','OUTPUT_SCHEMA_COLUMN_NOT_CALLABLE/v1'}


def test_mixed_function_catalog_description_cannot_vote_operator(tmp_path):
    d=document(tmp_path,'Functions and Operators','<table><tr><th>Function</th><th>Description</th></tr>'
        '<tr><td>TURN(x)</td><td>Returns a rotation. See also operator @&gt;.</td></tr></table>')
    records=run(tmp_path,d)
    assert any(r['name']=='TURN' and r['feature_type']=='function' for r in records)
    assert not any(r['name']=='TURN' and r['feature_type']=='operator' for r in records)


def test_notation_alias_table_preserves_both_literal_forms():
    assert {m['identity'] for m in catalog_surfaces(['Keyword-style','Symbolic-style'],['MATCHES','~~*'])}=={'MATCHES','~~*'}
    assert not catalog_surfaces(['file','result'],['README','~~*'])
    assert not catalog_surfaces(['Keyword-style','Symbolic-style'],['MATCHES','~~*'],'Connector mappings')


def test_explicit_operator_enumeration_reaches_publication(tmp_path):
    d=document(tmp_path,'SQL Reference','<p>The following logical operators are available: '
        '<code>AND</code>, <code>OR</code> and <code>NOT</code>.</p>')
    records=run(tmp_path,d)
    assert {'AND','OR','NOT'}<={r['name'] for r in records if r['feature_type']=='operator'}


def test_operator_mention_is_not_an_enumerated_definition():
    soup=BeautifulSoup('<p>A query example contains <code>OR</code> and <code>NOT</code>.</p>','lxml')
    assert not observe_definition(soup.p)


def test_function_and_operator_columns_both_survive_to_final(tmp_path):
    d=document(tmp_path,'Functions','<table><tr><th>Function</th><th>Operator</th><th>Description</th></tr>'
        '<tr><td>extract(value,path)</td><td>-&gt;</td><td>Extracts a value.</td></tr></table>')
    records=run(tmp_path,d)
    assert {('EXTRACT','function'),('->','operator')}<={(r['name'],r['feature_type']) for r in records}


def test_operator_notation_columns_both_survive_to_final(tmp_path):
    d=document(tmp_path,'SQL Operators','<table><tr><th>Keyword-style</th><th>Symbolic-style</th></tr>'
        '<tr><td>MATCHES</td><td>~~*</td></tr></table>')
    records=run(tmp_path,d)
    assert {'MATCHES','~~*'}<={r['name'] for r in records if r['feature_type']=='operator'}


def test_self_definition_remains_description(tmp_path):
    d=document(tmp_path,'SQL Operators','<ul><li><code>!</code><p>Logical inversion.</p>'
        '<p>The ! operator is a nonstandard extension.</p></li></ul>')
    records=run(tmp_path,d)
    row=next(r for r in records if r['name']=='!' and r['feature_type']=='operator')
    assert any(c['value']=='The ! operator is a nonstandard extension.' for c in row['semantics']['description'])


def test_embedded_language_introduction_is_local_not_page_wide():
    from agents.document_structure_v2.definition_observations import feature_domain
    soup=BeautifulSoup('<h1>SQL Functions</h1><p>Descriptions of XPath expressions follow:</p>'
        '<div><ul><li><p>The * operator matches elements.</p></li></ul></div>'
        '<p id="native">The + operator adds values.</p>','lxml')
    embedded=soup.select_one('li p')
    assert feature_domain(embedded)=='EMBEDDED_QUERY_LANGUAGE'
    assert not observe_definition(embedded).get('declaration_origin')
    assert feature_domain(soup.select_one('#native'))=='DOCUMENT_FEATURE'
    assert observe_definition(soup.select_one('#native'))['members'][0]['identity']=='+'


def test_native_observation_coverage_needs_actual_occurrence_and_owner():
    from agents.owner.surface_patterns import native_definition_coverage
    block={'block_id':'b','definition_context':{'role':'OPERATOR_CATALOG','domain':'DOCUMENT_FEATURE',
        'members':[{'identity':'SAME','raw_entry':'The SAME operator tests equality.'}]}}
    owner={'owner_id':'o','page_id':'p','member_block_ids':['b']}
    unit={'identity_unit_id':'u','canonical_identity':'SAME','owner_ids':['o'],
        'occurrence_records':[{'source_block_id':'b','defining':True,'owner_local_binding':True}]}
    assert native_definition_coverage([unit],[owner],[{'blocks':[block]}])[('p','b','SAME','OPERATOR')]=='u'
    unit['occurrence_records'][0]['owner_local_binding']=False
    assert not native_definition_coverage([unit],[owner],[{'blocks':[block]}])


def test_enum_does_not_borrow_substring_from_later_prose():
    soup=BeautifulSoup('<p>The following logical operators are available: <code>XOR</code>. '
        'A later example uses <code>OR</code>.</p>','lxml')
    assert [m['identity'] for m in observe_definition(soup.p)['members']]==['XOR']


def test_adapted_subject_witness_requires_the_actual_declaration_citation():
    from agents.contracts.publication_integrity import subject_witnesses
    unit={'identity_unit_id':'u','canonical_identity':'SAME','feature_type':'OPERATOR','owner_ids':['o']}
    block={'block_id':'b','owner_ids':['o'],'page_id':'p','source_url':'https://official.test/ref','source_hash':'hash',
        'block_kind':'TEXT','text':'The SAME operator tests equality.','source_structure_status':'RESOLVED',
        'observed_context':{'local_definition':{'role':'OPERATOR_CATALOG','domain':'DOCUMENT_FEATURE',
            'members':[{'identity':'SAME','raw_entry':'The SAME operator tests equality.'}]}}}
    assert subject_witnesses(unit,{'source_blocks':[block]})[0]

    unit['adapted_identity_kind']='OPERATOR'
    assert not subject_witnesses(unit,{'source_blocks':[block]})[0]
    unit['adaptation_provenance']=[{'block_id':'b','page_id':'p','owner_id':'o',
        'source_url':'https://official.test/ref','corpus_hash':'hash'}]
    assert subject_witnesses(unit,{'source_blocks':[block]})[0]


def test_explicit_enumeration_reaches_closure_without_keyword_noise_filter():
    from agents.type.operator_closure import operator_surface_observations
    b={'block_id':'b','block_type':'text','text':'The following logical operators are available: AND, OR.',
       'definition_context':{'role':'OPERATOR_CATALOG','domain':'DOCUMENT_FEATURE',
          'declaration_origin':'EXPLICIT_OPERATOR_ENUMERATION',
          'members':[{'identity':name,'raw_entry':name} for name in ['AND','OR']]}}
    assert {r['surface'] for r in operator_surface_observations(b)}=={'AND','OR'}
    b['definition_context']['domain']='EMBEDDED_QUERY_LANGUAGE'
    assert not operator_surface_observations(b)


def test_host_sql_example_is_preserved_inside_embedded_language_scope(tmp_path):
    from agents.semantic_stage.source_structure import SourceStructureIndex
    d=document(tmp_path,'SQL Functions','<p>Descriptions of XPath expressions follow:</p>'
        '<div><ul><li><p>The * operator matches any element.</p>'
        '<pre>client&gt; SELECT PICK(xml, \'/a/*\');</pre><pre>/a/*</pre></li></ul></div>')
    reader=SourceStructureIndex()
    sql=next(b for b in d['blocks'] if b['block_kind']=='CODE' and 'SELECT PICK' in b['text'])
    expr=next(b for b in d['blocks'] if b['block_kind']=='CODE' and b['text']=='/a/*')
    host=reader.observe(d,sql)['observed_context'];nested=reader.observe(d,expr)['observed_context']
    assert host['feature_domain']=='DOCUMENT_FEATURE' and host['host_language']=='SQL'
    assert host['embedded_expression_domain']=='EMBEDDED_QUERY_LANGUAGE'
    assert nested['feature_domain']=='EMBEDDED_QUERY_LANGUAGE'


def test_wrapped_symbol_and_literal_alternatives_are_declarations():
    assert operator_form('@(value)')=='@'
    assert operator_form('|/(value)')=='|/'
    assert operator_form('fn(value)') is None
    assert operator_form('@(17)') is None
    assert {m['identity'] for m in catalog_surfaces(['Operator','Description'],['= or ==','Tests equality.'])}=={'=','=='}
    assert not catalog_surfaces(['Function','Description'],['turn(x) or shift(x)','Choose a function.'])


def test_wrapped_operator_and_alternatives_reach_final(tmp_path):
    d=document(tmp_path,'SQL Reference','<table><tr><th>Function</th><th>Description</th></tr>'
        '<tr><td>@(value)</td><td>Computes a magnitude.</td></tr></table>'
        '<table><tr><th>Operator</th><th>Description</th></tr>'
        '<tr><td>= or ==</td><td>Tests equality.</td></tr></table>')
    records=run(tmp_path,d)
    assert {'@','=','=='}<={r['name'] for r in records if r['feature_type']=='operator'}


def test_verified_operator_declaration_overrides_chapter_title(tmp_path):
    d=document(tmp_path,'Numeric Functions','<table><tr><th>Function</th><th>Description</th></tr>'
        '<tr><td>@(value)</td><td>Computes a magnitude.</td></tr></table>')
    records=run(tmp_path,d)
    row=next(r for r in records if r['name']=='@' and r['feature_type']=='operator')
    assert row['semantics']['description']
    decisions=[json.loads(l) for l in (tmp_path/'run/canonical/admission_decisions.jsonl').open(encoding='utf8')]
    admission=next(a for a in decisions if a['identity_unit_id']==row['provenance']['identity_unit_id'])
    assert not admission['publication_scope_review']['review_required']
    assert {s['type_context_source'] for s in admission['publication_scope_review']['definition_sections'].values()}=={'SOURCE_VERIFIED_DEFINITION'}


def test_named_operator_catalog_consumes_its_description(tmp_path):
    d=document(tmp_path,'Numeric Functions','<table><tr><th>Name</th><th>Description</th></tr>'
        '<tr><td>@(x)</td><td>Absolute value. Parentheses are optional.</td></tr></table>')
    records=run(tmp_path,d)
    row=next(r for r in records if r['name']=='@' and r['feature_type']=='operator')
    assert [v['value'] for v in row['semantics']['description']]==['Absolute value. Parentheses are optional.']
    assert not row['semantics']['syntax']


def test_unverified_type_assertion_cannot_override_real_section_context():
    from agents.contracts.publication_scope import definition_publication_review
    unit={'identity_unit_id':'u','canonical_identity':'VALUE','feature_type':'FUNCTION'}
    block={'block_id':'h','text':'VALUE','block_kind':'HEADING','heading_ancestry':['Data Types'],
           'source_structure_status':'RESOLVED'}
    owner={'definition_section_proofs':[{'section_id':'s','heading_block_id':'h',
        'definition_pattern':'UNSUPPORTED_ASSERTION','feature_type':'FUNCTION','identity_values':['VALUE']}]}
    result=definition_publication_review(unit,{'source_blocks':[block],'owners':[owner]})
    assert result['reason_codes']==['DEFINITION_SECTION_TYPE_MISMATCH']
