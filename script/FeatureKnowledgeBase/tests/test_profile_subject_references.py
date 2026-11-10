"""Explicit links need the same verified subject and actual frozen anchors."""
import json
from pathlib import Path
import pytest
from agents.contracts.document_ir import from_v2_page
from agents.document_structure_v2.topology import build_page_topology
from agents.owner.generic import generate_owner_hypotheses
from agents.contracts.definition_unit import build_definition_units
from agents.semantic_stage.source_structure import SourceStructureIndex
from tests.test_five_structure_executors import profile_for


@pytest.mark.parametrize('target_name,linked,expected',[
    ('WHOLE',True,True),('OTHER',True,False),('WHOLE',False,False)])
def test_page_link_resolves_only_same_verified_datatype(tmp_path,target_name,linked,expected):
    def doc(slug,body):
        path=tmp_path/(slug+'.html');path.write_text('<main><h1>Data Types</h1>'+body+'</main>',encoding='utf8')
        return from_v2_page(build_page_topology({'url':'https://official.test/'+slug,'html_path':str(path)},{}),
                            dbms='sample',html_path=str(path))
    label='<a href="numeric">WHOLE</a>' if linked else 'WHOLE'
    a=doc('overview','<h2 id="types">Types</h2><table><tr><th>Type</th><th>Description</th></tr>'
        '<tr><td>'+label+'</td><td>Stores whole numbers.</td></tr></table>')
    b=doc('numeric','<h2 id="integer-types">Integer Types</h2><table><tr><th>Type</th><th>Description</th></tr>'
        '<tr><td>'+target_name+'</td><td>Stores a range of integers.</td></tr></table>')
    p=profile_for(a,'D')
    p['hint_contract']['definition_structure_patterns'][0]['execution']={
        'version':1,'identity':{'alternatives':[{'kind':'COLUMN_HEADER','headers':['Type']}],
                                  'cardinality':'ONE'},
        'fields':[{'role':'DESCRIPTION','locator':{'kind':'COLUMN_HEADER','headers':['Description']}}]}
    units=build_definition_units([a,b],generate_owner_hypotheses([a,b],p),p,source_structure=SourceStructureIndex())
    source=[u for u in units if u['source_url']==a['url'] and 'WHOLE' in u['identity_values']]
    refs=[r for u in source for r in u.get('subject_reference_proofs',[])]
    assert bool(refs)==expected
    if refs:
        assert refs[0]['target_url']=='https://official.test/numeric#integer-types'
        assert refs[0]['target_page_hash']==b['page_hash']


@pytest.mark.parametrize('explicit',[True,False])
def test_cross_page_prose_link_requires_explicit_subject_reference(tmp_path,explicit):
    def doc(slug,prose):
        path=tmp_path/(slug+'.html');path.write_text('<main><h1>Functions</h1><ul><li>'
            '<a id="function_turn"></a><p><code>TURN(x)</code></p><p>'+prose+'</p></li></ul></main>',encoding='utf8')
        return from_v2_page(build_page_topology({'url':'https://official.test/'+slug,'html_path':str(path)},{}),
                            dbms='sample',html_path=str(path))
    a=doc('overview',"See this function's description in " if explicit else 'An unrelated link: ')
    path=Path(a['html_path']);path.write_text(path.read_text().replace('</p></li>',
        '<a href="detail">the detailed chapter</a>.</p></li>'),encoding='utf8')
    a=from_v2_page(build_page_topology({'url':a['url'],'html_path':str(path)},{}),dbms='sample',html_path=str(path))
    b=doc('detail','Returns a complete rotation.')
    p=profile_for(a,'C');p['hint_contract']['definition_structure_patterns'][0]['execution']={
        'version':1,'identity':{'alternatives':[{'kind':'FIRST_PARAGRAPH_CODE'},{'kind':'VERIFIED_ANCHOR'}],
        'cardinality':'ONE_OR_MORE'},'fields':[{'role':'DESCRIPTION','locator':{'kind':'BODY_PROSE'}}]}
    units=build_definition_units([a,b],generate_owner_hypotheses([a,b],p),p,source_structure=SourceStructureIndex())
    refs=[r for u in units if u['source_url']==a['url'] for r in u.get('subject_reference_proofs',[])]
    assert bool(refs)==explicit

