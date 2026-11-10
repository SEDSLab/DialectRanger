import pytest
from agents.discovery.scope_resolver import _provided_project_source, _reference_chapter_evidence,ScopeResolver
from agents.discovery.scope_resolver import _reference_index_members
from agents.discovery.generic_stage import GenericDiscovery


def test_explicit_project_namespace_not_whole_shared_host():
    root='https://shared.test/projectx/manual/index.html'
    assert _provided_project_source('projectx','https://shared.test/projectx/manual/ch1.html',root)
    for url in ['https://shared.test/other/manual','https://evil.test/projectx/manual',
                'http://shared.test/projectx/manual','https://shared.test/projectx/../other/manual']:
        assert not _provided_project_source('projectx',url,root)
    assert not _provided_project_source('projectx','https://shared.test/projectx/manual','')
    assert not _provided_project_source('projectx','https://medium.com/projectx/manual','https://medium.com/projectx/')


def chapter():
    return '<html><title>Built-in functions</title><body><h1>Built-in functions</h1><ul>'+''.join(
        '<li><strong><a href="ref'+str(i)+'.html">'+name+' function</a></strong><br/>Returns an official value.</li>'
        for i,name in enumerate(['ALPHA','BETA','GAMMA']))+'</ul></body></html>'


def test_reference_link_description_index(tmp_path):
    text=chapter()
    assert _reference_chapter_evidence(text,'FUNCTION','Built-in functions')==3
    resolver=ScopeResolver(dbms='projectx',output_root=tmp_path)
    assert resolver._candidate_is_scope_root('FUNCTION','https://shared.test/projectx/ch1.html','Built-in functions',[],'',text)[0]


@pytest.mark.parametrize('title',['Function Examples','Programming API Functions','Function Tutorial'])
def test_reference_index_excludes_nonreference_context(title):
    assert _reference_chapter_evidence(chapter(),'FUNCTION',title)==0


def test_navigation_and_generic_words_are_not_reference_rows():
    assert _reference_chapter_evidence(chapter().replace('<ul>','<nav><ul>').replace('</ul>','</ul></nav>'),'FUNCTION','Functions')==0
    assert _reference_chapter_evidence(chapter().replace(' function</a>',' guide</a>'),'FUNCTION','Functions')==0


def test_explicit_reference_members_cross_file_root_not_entire_directory(tmp_path):
    root='https://shared.test/projectx/manual/ch1.html'
    members=_reference_index_members(chapter(),'FUNCTION',root)
    assert members==['https://shared.test/projectx/manual/ref'+str(i)+'.html' for i in range(3)]
    manifest={'dbms':'projectx','scopes':{'FUNCTION':[{'url':root,'scope_kind':'REFERENCE_CHAPTER','verified_member_urls':members}]}}
    crawl=GenericDiscovery(dbms='projectx',scope_manifest=manifest,output_root=tmp_path)
    assert crawl._scope_decision(members[0])[0]
    assert not crawl._scope_decision('https://shared.test/projectx/manual/unlisted.html')[0]
    assert not crawl._scope_decision('https://evil.test/projectx/manual/ref0.html')[0]


def test_reference_index_members_exclude_external_parent_and_chrome():
    root='https://shared.test/projectx/manual/ch1.html'
    for target in ['https://evil.test/ref.html','../outside.html']:
        assert not _reference_index_members(chapter().replace('ref0.html',target).replace('ref1.html',target).replace('ref2.html',target),'FUNCTION',root)
