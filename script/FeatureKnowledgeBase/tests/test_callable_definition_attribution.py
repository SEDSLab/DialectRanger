from agents.type.evidence import build_typed_feature_evidence, _callable_definition_span


def evidence(text, kind='table_row', headings=(), headers=(), cells=()):
    return build_typed_feature_evidence({'subject': 'FOO', 'owner_id': 'o', 'identity_unit_id': 'u',
        'owner_kind': 'TableRowOwner', 'raw_text': text,
        'profile_hint': {'feature_bearing': False, 'surface_role': 'SUPPORTING_CONTEXT'},
        'blocks': [{'block_id': 'b', 'block_type': kind, 'text': text,
                    'heading_ancestry': list(headings), 'table_headers': list(headers), 'cells': list(cells)}]})


def strong(rows):
    return [r for r in rows if r['candidate_type'] == 'FUNCTION' and r['strength'] == 'STRONG']


def test_prose_parenthetical_is_not_strong_callable():
    assert not strong(evidence('Author: A Publisher: B Public FOO (for more information visit a website) function reference'))


def test_book_title_year_is_not_callable():
    assert not strong(evidence('FOO(2009) Author: someone Publisher: B; function reference book'))


def test_supporting_context_without_definition_cannot_confirm_function():
    assert not strong(evidence('Author: A Publisher: B FOO(x) is mentioned in a function reference book'))


def test_supporting_context_alone_does_not_veto_real_paragraph_callable():
    assert strong(evidence('The FOO(x) function returns a result', 'paragraph', ['Functions']))


def test_real_definition_inside_metadata_catalog_is_preserved():
    assert strong(evidence('FOO(x) Author: A Publisher: B', headers=['Function', 'Description'], cells=['FOO(x)', 'Author: A Publisher: B']))


def test_real_heading_callable_is_preserved():
    assert strong(evidence('FOO(x)', 'heading', ['Mathematical Functions']))


def test_real_code_callable_is_preserved():
    assert strong(evidence('FOO(x)', 'code', ['Functions', 'Syntax']))


def test_real_table_defined_callable_is_preserved():
    rows = strong(evidence('FOO(x) Returns a value', headers=['Function', 'Description'], cells=['FOO(x)', 'Returns a value']))
    assert rows and rows[0]['callable_definition_span']['text'] == 'FOO(x)'


def test_h2_function_regression():
    block = {'block_type': 'signature', 'text': '{ LOCATE(x,y) } | { INSTR(y,x) }'}
    for name in ('LOCATE', 'INSTR'):
        assert _callable_definition_span(name, block, {'owner_kind': 'SignatureOwner'}, [], ['String Functions'])


def test_postgresql_leaf_function_regression():
    block = {'block_type': 'table_row', 'text': 'pg_sync_replication_slots() → void', 'cells': ['pg_sync_replication_slots() → void']}
    assert _callable_definition_span('PG_SYNC_REPLICATION_SLOTS', block, {}, ['Function'], ['Administration'])
