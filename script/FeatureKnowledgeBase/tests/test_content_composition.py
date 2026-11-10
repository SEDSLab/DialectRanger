import json
import pytest

from tests.test_profile_definition_execution import document,run
from tests.test_five_structure_executors import profile_for
from tests.test_compound_definition_entry import source
from agents.contracts.definition_entry import compound_entry
from agents.semantic_stage.content_completeness import compare_fields,audit_final_content,audit_semantic_content


def cell(body):
    return '<table><tr><th>Function Description Example(s)</th></tr><tr><td>' \
        '<p class="func_signature"><code>coil(value)</code> → text</p>' \
        '<p>Returns each value.</p>'+body+'</td></tr></table>'


def test_split_example_survives_final_with_both_parts(tmp_path):
    d=document(tmp_path,'Functions',cell('<p><code>select * from coil(\'abc\')</code> →</p>'
        '<pre>value --- abc</pre>'))
    records=run(tmp_path,d,profile_for(d,'D'))
    r=next(r for r in records if r['name']=='COIL')
    values=[v['value'] for v in r['semantics']['examples']]
    assert len(values)==1 and "select * from coil('abc')" in values[0] and 'value --- abc' in values[0]
    from agents.semantic_stage.grounding import normalize_for_match
    witness=r['semantics']['examples'][0]['evidence']
    assert witness['block_type']=='TABLE_ROW'
    assert normalize_for_match(values[0]) in normalize_for_match(witness['evidence_span']['text'])
    audit=[json.loads(l) for l in (tmp_path/'run/semantic_stage/evaluation/content_completeness.jsonl').open(encoding='utf8')]
    a=next(a for a in audit if a['name']=='COIL')
    assert a['status']=='OBSERVED_CONTENT_ACCOUNTED'
    assert len(a['compositions'])==1
    assert len(a['compositions'][0]['parts'])==2
    assert a['compositions'][0]['parts'][0]['evidence_ref']!=a['compositions'][0]['parts'][1]['evidence_ref']
    final=[json.loads(l) for l in (tmp_path/'run/evaluation/final_content_completeness.jsonl').open(encoding='utf8')]
    assert next(a for a in final if a['name']=='COIL')['status']=='ACCEPTED_CONTENT_RETAINED'


@pytest.mark.parametrize('body',[
    '<p><code>select * from coil(1)</code> →</p>',
    '<p><code>select * from other(1)</code> →</p><pre>42</pre>',
    '<p><code>select \'coil(1)\'</code> →</p><pre>42</pre>',
    '<p><code>select * from coil(1)</code> →</p><pre>SELECT other(1);</pre>',
    '<p><code>select * from coil(1)</code> →</p><p>Another section.</p><pre>42</pre>',
    '<p><code>select * from coil(1)</code> →</p><section><h3>Other</h3><pre>42</pre></section>',
])
def test_no_adjacency_or_incidental_name_composition(body):
    _,_,row=source(cell(body))
    entry=compound_entry(row,'COIL','FUNCTION')
    assert not any(f.get('result_locator') for f in entry['fragments'])


def test_other_co_declared_subject_does_not_take_example():
    html=cell('<p><code>select * from coil(1)</code> →</p><pre>42</pre>')
    html=html.replace('<p>Returns each value.</p>',
        '<p class="func_signature"><code>other(value)</code> → text</p><p>Returns each value.</p>')
    _,_,row=source(html)
    assert any(f.get('result_locator') for f in compound_entry(row,'COIL','FUNCTION')['fragments'])
    assert not any(f.get('result_locator') for f in compound_entry(row,'OTHER','FUNCTION')['fragments'])


def test_output_in_another_cell_is_not_composed():
    html=cell('<p><code>select * from coil(1)</code> →</p>').replace('</td></tr></table>','</td><td><pre>42</pre></td></tr></table>')
    _,_,row=source(html)
    assert not any(f.get('result_locator') for f in compound_entry(row,'COIL','FUNCTION')['fragments'])


def test_missing_one_paragraph_is_detected_even_with_nonempty_description():
    expected={'description':[{'text':'First paragraph.','evidence_refs':['one']},
                             {'text':'Second paragraph.','evidence_refs':['two']}],
              'official_examples':[{'sql':'SELECT COIL(1);','evidence_refs':['three']}]}
    result=compare_fields(expected,{'description':[{'text':'First paragraph.'}]})
    assert result['description']['status']=='CONTENT_FIELD_GAP'
    assert result['description']['missing_claims'][0]['evidence_refs']==['two']
    assert result['official_examples']['status']=='CONTENT_FIELD_GAP'
    assert result['formal_syntax']['status']=='NO_PROVEN_EXPECTATION'


def test_final_audit_checks_field_content_not_just_nonempty_or_same_name():
    feature={'feature_id':'f','identity_group_id':'u','name':'COIL',
        'description':[{'text':'First.'},{'text':'Second.'}]}
    records=[{'name':'COIL','provenance':{'identity_unit_id':'u'},'semantics':{'description':[{'value':'First.'}]}},
             {'name':'COIL','provenance':{'identity_unit_id':'other'},'semantics':{'description':[{'value':'Second.'}]}}]
    result=audit_final_content([feature],records)[0]
    assert result['status']=='FINAL_FIELD_GAP'
    assert result['fields']['description']['missing_claims'][0]['text']=='Second.'
    assert audit_final_content([feature],[])[0]['status']=='NOT_PUBLISHED'


def test_absent_structured_input_is_not_falsely_certified():
    assert audit_semantic_content('ordinary prompt',{})['status']=='NOT_EVALUATED'


def test_multiple_paragraphs_from_one_parent_are_not_deduplicated_away():
    from agents.semantic_stage.extractive_fragments import validate_extractive_fragments
    result=validate_extractive_fragments({'description':[
        {'text':'First paragraph.','evidence_refs':['parent']},
        {'text':'Second paragraph.','evidence_refs':['parent']},
        {'text':'First paragraph.','evidence_refs':['parent']}]},
        {'source_blocks':[{'evidence_ref':'parent','text':'First paragraph. Second paragraph.'}]})
    assert [v['text'] for v in result['fields']['description']]==['First paragraph.','Second paragraph.']
