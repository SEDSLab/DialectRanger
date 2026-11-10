import copy
import pytest
from agents.semantic_stage.source_structure import example_introduction
from agents.semantic_stage.structured_semantics import extract_structured_semantics
from agents.owner.reference_definitions import reference_scopes
from agents.owner.scope_plan import compile_scope_plan
from tests.test_reference_definition_contract import block


@pytest.mark.parametrize('text',['An example is given below:','The examples are shown here.','For example,','The following queries illustrate this:'])
def test_example_introductions_are_explicit(text):
    assert example_introduction(text)


@pytest.mark.parametrize('text',['The argument is an example identifier.','No example is given below.','The example API method is unrelated.'])
def test_incidental_example_words_do_not_create_role(text):
    assert not example_introduction(text)


def test_reference_examples_are_internal_but_peer_definitions_are_boundary():
    a=block('Concatenation operator','HEADING',0)
    b=block('The Concatenation operator, ||, combines values.',order=1)
    e=block('Examples','HEADING',2);e['heading_level']=2
    c=block("VALUES 'a' || 'b'",'CODE',3)
    peer=block('BETA data type','HEADING',4)
    foreign=block('Unrelated definition',order=5)
    plans={p['relation']:p for p in compile_scope_plan()}
    document={'url':'https://official.invalid/ref','blocks':[a,b,e,c,peer,foreign]}
    original=reference_scopes(document,plans)
    assert c['block_id'] not in next(p for p in original if p['primary_identity']=='||')['block_ids']
    ps=reference_scopes(document,plans,include_semantic_subsections=True)
    own=next(p for p in ps if p['primary_identity']=='||')
    assert c['block_id'] in own['block_ids']
    assert peer['block_id'] not in own['block_ids']
    assert foreign['block_id'] not in own['block_ids']


def test_literal_example_result_is_not_formal_syntax():
    bid='sample';identity='ALPHA'
    b={'block_id':bid,'evidence_ref':bid,'block_kind':'CODE',
       'text':'ALPHA(1) 2 ALPHA(3) 4','source_url':'https://official.invalid/functions',
       'observed_context':{'labels':[{'text':'Example','origin':'ADJACENT_EXAMPLE_INTRODUCTION'}]},
       'definition_section_proofs':[{'primary_identity':identity}],
       'structural_roles':['FORMAL_SYNTAX']}
    before=copy.deepcopy(b)
    result=extract_structured_semantics({'normalized_identity':identity,'feature_type':'FUNCTION','evidence':[b]})
    assert not result['formal_syntax']
    assert result['official_examples'][0]['sql']==b['text']
    assert b==before


def test_group_description_never_assigns_individual_numeric_properties():
    a=block('ALPHA, BETA varied-width numbers respectively','TABLE_ROW')
    a['table_metadata']={'headers':['Type','Description'],'cells':['ALPHA, BETA','8 and 16 bit numbers respectively']}
    a['text']='ALPHA, BETA 8 and 16 bit numbers respectively'
    plans={p['relation']:p for p in compile_scope_plan()}
    proof=reference_scopes({'blocks':[a]},plans)[0]
    a.update(evidence_ref=a['block_id'],owner_local_binding=True,definition_section_proofs=[proof],
        table_context={'cells':[{'text':'ALPHA, BETA','header_role':'DATA_CELL','header_status':'EXPLICIT',
           'headers':[{'text':'Type'}],'cell_index':0},
           {'text':'8 and 16 bit numbers respectively','header_role':'DATA_CELL','header_status':'EXPLICIT',
           'headers':[{'text':'Description'}],'cell_index':1}]})
    for name in ['ALPHA','BETA']:
        result=extract_structured_semantics({'normalized_identity':name,'feature_type':'DATATYPE','evidence':[a]})
        assert result['description'][0]['text']==a['text']
        assert not result['formal_syntax']
        assert result['semantic_extensions']['shared_description_contexts'][0]['members']==['ALPHA','BETA']
    result=extract_structured_semantics({'normalized_identity':'FOREIGN','feature_type':'DATATYPE','evidence':[a]})
    assert not result['description']


def test_reference_operator_end_to_end_preserves_examples(tmp_path):
    from tests.test_definition_identity_role_closure import execute
    fs=execute(tmp_path,'<p>The Concatenation operator, ||, combines values.</p>'
        '<h2>Syntax</h2><pre>left || right</pre>'
        "<h2>Examples</h2><pre>VALUES 'a' || 'b'</pre>",'Concatenation operator')
    f=next(f for f in fs if f['name']=='||' and f['feature_type']=='operator')
    assert f['semantics']['examples']
    assert not any('VALUES' in c['value'] for c in f['semantics']['syntax'])


def test_source_example_intro_through_transparent_wrapper(tmp_path):
    import hashlib
    from pathlib import Path
    from tests.test_paragraph_label_definition import doc
    from agents.semantic_stage.source_structure import SourceStructureIndex
    d=doc(tmp_path,'<p>An example is given below:</p><div><pre>ALPHA(1)\n2</pre></div>','ALPHA function')
    d['page_hash']=hashlib.sha256(Path(d['html_path']).read_bytes()).hexdigest()
    code=next(b for b in d['blocks'] if b['block_kind']=='CODE')
    observed=SourceStructureIndex().observe(d,code)
    assert any(l['origin']=='ADJACENT_EXAMPLE_INTRODUCTION' for l in observed['observed_context']['labels'])


def test_comment_prefixed_official_example_not_comment_only_sql_mention():
    def extract(raw):
        b={'block_id':'example','text':' '.join(raw.split()),'block_kind':'CODE',
           'observed_context':{'labels':[{'text':'Examples','origin':'OWNER_LOCAL_VISIBLE_LABEL'}],'literal_code_text':raw},
           'definition_section_proofs':[{'primary_identity':'||'}]}
        return extract_structured_semantics({'normalized_identity':'||','feature_type':'OPERATOR','evidence':[b]})
    r=extract("-- returns 'ab'\nVALUES 'a' || 'b'")
    assert r['official_examples'] and not r['formal_syntax']
    assert not extract('-- SELECT mentions are just comments')['official_examples']


@pytest.mark.parametrize('raw,expected',[
    ("SELECT CAST(scores AS VARCHAR(6) ARRAY)",False),
    ("SELECT 'the || marker'",False),
    ("-- || is mentioned here\nVALUES 1",False),
    ("VALUES 'left' || 'right'",True),
])
def test_adjacent_example_in_grammar_scope_must_use_its_operator(raw,expected):
    b={'block_id':'example','text':' '.join(raw.split()),'block_kind':'CODE',
       'observed_context':{'labels':[{'text':'Example','origin':'ADJACENT_EXAMPLE_INTRODUCTION'}],
                           'literal_code_text':raw},
       'definition_section_proofs':[{'primary_identity':'||','definition_pattern':'REFERENCE_DECLARATION_SECTION',
                                    'reference_mode':'PARAGRAPH_SIGNATURE'}]}
    r=extract_structured_semantics({'normalized_identity':'||','feature_type':'OPERATOR','evidence':[b]})
    assert bool(r['official_examples'])==expected
