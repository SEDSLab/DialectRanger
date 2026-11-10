from pathlib import Path

from agents.document_structure_v2.topology import build_page_topology
from agents.contracts.document_ir import from_v2_page
from agents.document_structure_v2.preservation_audit import audit_document
from agents.identity.production import _surface_values


def document(tmp_path, markup):
    path = tmp_path/'source.html'
    path.write_bytes(markup.encode('utf8'))
    page = build_page_topology({'url':'https://example.test/docs','html_path':str(path)}, {})
    return from_v2_page(page,dbms='synthetic',html_path=path), page


def test_code_source_preserves_newlines_spaces_quotes_and_crlf(tmp_path):
    raw = "SELECT 'a  b';\r\n  SELECT '&lt;';\r\n"
    doc, _ = document(tmp_path, '<main><h1>Reference</h1><pre>'+raw+'</pre></main>')
    block = next(b for b in doc['blocks'] if b['block_kind']=='CODE')
    assert block['source_text'] == "SELECT 'a  b';\r\n  SELECT '<';\r\n"
    assert block['normalized_text'] == block['text']
    assert block['source_text'] != block['text']
    assert audit_document(doc)['code_fidelity_mismatches'] == []


def test_uncovered_inline_text_is_retained_once_without_identity_authority(tmp_path):
    doc, _ = document(tmp_path, '<main><h1>Reference</h1><div>Loose explanation <span>OTHER_FN(x)</span>'
        '<p>Normal paragraph.</p>After the paragraph.</div></main>')
    recovered=[b for b in doc['blocks'] if b.get('preservation_only')]
    assert any('Loose explanation' in b['source_text'] for b in recovered)
    assert any('OTHER_FN(x)' in b['source_text'] for b in recovered)
    assert any('After the paragraph.' in b['source_text'] for b in recovered)
    assert all(b['block_kind']=='OTHER' and _surface_values(b)==[] for b in recovered)
    assert audit_document(doc)['uncovered_text_nodes']==0


def test_noise_and_explicit_hidden_text_not_added_as_unclassified_prose(tmp_path):
    doc, _ = document(tmp_path, '<body><nav><span>NAV_FN(x)</span></nav><div hidden>HIDDEN</div>'
        '<div aria-hidden="true">ARIA</div><div style="display:none">CSS</div>'
        '<script>window.X=1;</script><h1>Reference</h1><div>Visible note.</div></body>')
    recovered=' '.join(b['source_text'] for b in doc['blocks'] if b.get('preservation_only'))
    assert 'Visible note.' in recovered
    assert not any(v in recovered for v in ['NAV_FN','HIDDEN','ARIA','CSS','window'])
    audit=audit_document(doc)
    assert audit['uncovered_text_nodes']==0 and audit['excluded_text_nodes']


def test_dom_parent_is_actual_container_not_preceding_heading(tmp_path):
    doc, _ = document(tmp_path, '<main><h2>Reference</h2><p>Standalone.</p>'
        '<table><tr><td><p>Contained.</p></td></tr></table></main>')
    free=next(b for b in doc['blocks'] if b['text']=='Standalone.')
    child=next(b for b in doc['blocks'] if b['text']=='Contained.' and b['block_kind']=='TEXT')
    parent=next(b for b in doc['blocks'] if b['block_id']==child['parent_block_id'])
    assert free['parent_block_id'] is None
    assert parent['block_kind']=='TABLE_ROW'
    assert child['dom_path'].startswith(parent['dom_path']+' > ')
    assert child['previous_block_id'] is not None


def test_grid_preserves_multilevel_headers_and_spans(tmp_path):
    doc, _ = document(tmp_path, '<main><table><thead><tr><th rowspan="2">Name</th>'
        '<th colspan="2">Details</th></tr><tr><th>Description</th><th>Example</th></tr></thead>'
        '<tbody><tr><td>X</td><td>Details of X</td><td>X(1)</td></tr></tbody></table></main>')
    row=next(b for b in doc['blocks'] if b['block_kind']=='TABLE_ROW' and b['text'].startswith('X '))
    grid=row['table_metadata']['source_cell_grid']
    assert grid['structure_status']=='OBSERVED'
    assert [c['column_start'] for c in grid['cells']]==[0,1,2]
    assert len(grid['cells'][1]['header_locators'])==2
    assert all(c['header_status']=='EXPLICIT' for c in grid['cells'])


def test_invalid_or_blank_table_headers_are_not_invented(tmp_path):
    doc, _ = document(tmp_path,'<table><tr><th colspan="invalid"></th></tr><tr><td>X</td></tr></table>')
    rows=[b for b in doc['blocks'] if b['block_kind']=='TABLE_ROW']
    assert all(b['table_metadata']['source_cell_grid']['structure_status']=='UNKNOWN' for b in rows)


def test_legacy_ids_and_matching_text_stay_stable_with_additive_witnesses(tmp_path):
    markup='<main><h1>Reference</h1><p>Before.</p><div>Loose text.</div><pre>F(x)</pre></main>'
    doc, page=document(tmp_path,markup)
    old=[b for b in page['blocks'] if not b.get('preservation_only')]
    assert all(next(x for x in doc['blocks'] if x['block_id']==b['block_id'])['text']==b['text'] for b in old)
    # Adding witnesses does not change how the original topology ID is built.
    second=build_page_topology({'url':'https://example.test/docs','html_path':doc['html_path']},{})
    assert [b['block_id'] for b in second['blocks']]==[b['block_id'] for b in page['blocks']]


def test_outside_selected_root_is_reported_separately(tmp_path):
    doc, _=document(tmp_path,'<body><main><h1>Reference</h1><p>Content.</p></main><div>Outside note.</div></body>')
    audit=audit_document(doc)
    assert audit['uncovered_text_nodes']==0
    assert any('Outside note.' in x['source_text'] for x in audit['outside_selected_root'])
