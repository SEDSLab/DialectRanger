"""Read-only source-text coverage checks, independent of Feature decisions."""
from collections import Counter
from pathlib import Path
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from .topology import _dom_path, _main
from .source_visibility import exclusion


def audit_document(document):
    soup = BeautifulSoup(Path(document['html_path']).read_bytes(), 'lxml')
    root = _main(soup, ['main', 'article', 'body'])
    paths = {_dom_path(t): t for t in soup.find_all(True)}
    refs = Counter()
    mismatches = []
    unresolved = []
    for block in document['blocks']:
        node = paths.get(block.get('dom_path'))
        if node is None:
            unresolved.append(block['block_id'])
            continue
        if block.get('source_container_witness'):
            continue  # A whole-table copy must not hide a missing leaf surface.
        indexes = block.get('source_text_node_indexes')
        strings = [s for s in node.descendants if isinstance(s, NavigableString) and not isinstance(s, Comment)]
        if indexes is not None:
            strings = [strings[i] for i in indexes if i < len(strings)]
        for s in strings:
            if str(s).strip() and not exclusion(s):
                refs[id(s)] += 1
        if block.get('block_kind') == 'CODE':
            expected = node.get_text('', strip=False)
            actual = block.get('source_text', block.get('text', ''))
            if actual != expected:
                mismatches.append({'block_id': block['block_id'], 'dom_locator': block['dom_path'],
                                   'source_text': expected, 'projected_text': actual})
    missing, excluded, outside = [], Counter(), []
    visible_count = 0
    for s in root.descendants:
        if not isinstance(s, NavigableString) or isinstance(s, Comment) or not str(s).strip():
            continue
        why = exclusion(s)
        if why:
            excluded[why] += 1
            continue
        visible_count += 1
        if not refs[id(s)]:
            missing.append({'dom_locator': _dom_path(s.parent), 'source_text': str(s)})
    body = soup.body or soup
    for s in body.descendants:
        if (isinstance(s, NavigableString) and not isinstance(s, Comment) and str(s).strip()
                and root is not body and not any(p is root for p in s.parents)):
            outside.append({'dom_locator': _dom_path(s.parent), 'source_text': str(s),
                            'excluded_reason': exclusion(s)})
    return {'url': document['url'], 'page_id': document['page_id'],
            'source_root_locator': _dom_path(root), 'visible_source_text_nodes': visible_count,
            'uncovered_text_nodes': len(missing), 'uncovered': missing,
            'overlapping_text_nodes': sum(v > 1 for v in refs.values()),
            'overlap_is_not_automatically_an_error': True,
            'code_fidelity_mismatches': mismatches, 'unresolved_block_ids': unresolved,
            'excluded_text_nodes': dict(excluded), 'outside_selected_root': outside,
            'scope': 'Selected-root source text coverage, not CSS rendering or Feature recall'}
