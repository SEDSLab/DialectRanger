from agents.semantic_stage.local_provider import _semantic_from_payload, _is_example, _is_syntax
from agents.semantic_stage.extractive_fragments import validate_extractive_fragments


def block(ref, text, role, order=0, kind='TEXT'):
    return dict(evidence_ref=ref, text=text, block_kind=kind, source_order=order,
                section_contexts=[{'section_role':role}], structural_roles=[], source_url='official')


def test_all_paragraphs_order_dedupe_and_individual_grounding():
    a=block('a','Returns the absolute value.', 'DESCRIPTION',1)
    b=block('b','With minimum integer input an overflow occurs.', 'DESCRIPTION',2)
    parsed=_semantic_from_payload({'evidence':[b,a,a]})
    assert [p['evidence_refs'] for p in parsed['description']]==[['a'],['b']]
    valid=validate_extractive_fragments(parsed,{'source_blocks':[a,b]})
    assert valid['accepted_claims']==2 and valid['rejected_claims']==0
    parsed['description'].append({'text':'invented','evidence_refs':['foreign']})
    valid=validate_extractive_fragments(parsed,{'source_blocks':[a,b]})
    assert valid['invalid_evidence_refs']==1 and len(valid['fields']['description'])==2


def test_roles_not_keywords_and_expressions_are_examples():
    syntax=block('s','UNNEST(x) [WITH ORDINALITY]','SYNTAX',kind='CODE')
    prose=block('d','With SELECT users can query values.', 'DESCRIPTION')
    assert not _is_example(syntax) and _is_syntax(syntax,'UNNEST')
    assert not _is_example(prose) and not _is_syntax(prose,'')
    for text in ['ABS(I)', "CONCAT(NAME, '!')", 'BIGINT', "ENUM('a','b')"]:
        b=block('e',text,'EXAMPLE')
        assert _is_example(b) and not _is_syntax(b,'')
    assert not _is_example(dict(text='SELECT foo()', block_kind='TEXT'))


def test_no_silent_example_truncation():
    evidence=[block(str(i),'ABS(%s)'%i,'EXAMPLE') for i in range(45)]
    assert len(_semantic_from_payload({'evidence':evidence})['official_examples'])==45


def test_explicit_formal_table_signature_survives_coarse_definition_label():
    row=dict(text='ABS(X)', block_kind='TABLE_ROW', structural_roles=['DEFINITION','FORMAL_SYNTAX'])
    assert _is_syntax(row,'ABS') and not _is_example(row)
