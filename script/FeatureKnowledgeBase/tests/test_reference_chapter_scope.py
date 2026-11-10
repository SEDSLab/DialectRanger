import pytest
from agents.discovery.scope_resolver import ScopeResolver,_reference_chapter_evidence


def chapter():
    return '<html><title>Built In Functions</title><body><div><h2>String Functions</h2>'+''.join(
        '<p><strong>'+n+'</strong></p><p><code>'+n+'(x)</code></p><p>Returns a value.</p>'
        for n in ['ALPHA','BETA','GAMMA'])+'</div></body></html>'


def test_reference_chapter_does_not_require_category_path(tmp_path):
    resolver=ScopeResolver(dbms='newdb',output_root=tmp_path)
    accepted,signals,kind=resolver._candidate_is_scope_root('FUNCTION','https://newdb.test/manual/ch04.html',
        'Built In Functions',[],'',chapter())
    assert accepted and kind=='REFERENCE_CHAPTER'
    assert 'observed_reference_chapter' in signals


@pytest.mark.parametrize('title',['Function Examples','Function Tutorial','Programming API Functions','Function Usage'])
def test_nonreference_context_does_not_create_chapter(title):
    assert _reference_chapter_evidence(chapter(),'FUNCTION',title)==0


def test_example_invocations_are_not_formal_reference_structures():
    text='<h2>Functions</h2>'+''.join('<pre>SELECT '+n+'(1);</pre><p>Result is 1.</p>' for n in ['A','B','C'])
    assert _reference_chapter_evidence(text,'FUNCTION','Functions')==0
