import copy,hashlib
import pytest
from bs4 import BeautifulSoup
from agents.document_structure_v2.topology import _dom_path
from agents.semantic_stage.source_structure import SourceStructureIndex
from agents.semantic_stage.structured_semantics import extract_structured_semantics,_cli_example_shape
from agents.contracts.publication_integrity import evaluate_publication_integrity
from agents.contracts.canonical_integrity import resolve_canonical_representations
from tests.test_reference_equivalence import parts


def linked_parts(tmp_path):
    html='<html><body><h1>Functions</h1><p>The following functions are supported:</p>'
    html+='<ol><li><a href="#frob">FROB</a>(x)</li></ol><a name="frob"></a>'
    html+='<h2 id="details">The FROB() functions</h2><table><tr><th>Function</th><th>Behavior</th></tr>'
    html+='<tr><td>FROB()</td><td>Yes</td></tr></table></body></html>'
    path=tmp_path/'raw.html';path.write_text(html,encoding='utf-8')
    s=BeautifulSoup(html,'lxml');digest=hashlib.sha256(path.read_bytes()).hexdigest()
    doc={'page_id':'p','url':'https://official.invalid/ref','html_path':str(path),'page_hash':digest}
    records,kw=parts();reader=SourceStructureIndex()
    for i,node in enumerate([s.find('li'),s.find_all('tr')[1]]):
        uid='u'+str(i);owner='o'+str(i)
        b={'block_id':'b'+str(i),'text':node.get_text(' ',strip=True),'block_kind':'LIST_ITEM' if i==0 else 'TABLE_ROW',
            'dom_path':_dom_path(node),'heading_ancestry':['Functions']+(['The FROB() functions'] if i else []),
            'owner_ids':[owner],'source_url':doc['url'],'page_id':'p','source_hash':digest}
        b.update(reader.observe(doc,b));kw['bundles'][i]['source_blocks']=[b]
        records[i]['semantics']['description'][0]={'value':b['text'],'evidence':{'block_id':b['block_id'],'url':doc['url'],'page_hash':digest}}
        kw['admissions'][i]['publication_integrity']=evaluate_publication_integrity(kw['units'][i],kw['bundles'][i],{'primary_type':'FUNCTION','status':'CONFIRMED'})
    return records,kw


def test_explicit_local_link_and_typed_section_coordinate_duplicate_records(tmp_path):
    records,kw=linked_parts(tmp_path)
    out,audit=resolve_canonical_representations(records,**kw)
    assert len(out)==1 and audit[0]['resolved']
    assert audit[0]['proofs'][0]['edges'][0]['relation']=='EXPLICIT_DECLARATION_LINK_TO_VERIFIED_MEMBER_SECTION'
    assert len(out[0]['structural']['attributes']['canonical_representation_lineage'])==2
    assert out==resolve_canonical_representations(list(reversed(records)),**kw)[0]


@pytest.mark.parametrize('tamper',['no_link','different_hash','foreign_label','review','foreign_claim','api_section'])
def test_linked_collision_never_borrows_identity_source_or_permission(tmp_path,tamper):
    records,kw=linked_parts(tmp_path)
    a=kw['bundles'][0]['source_blocks'][0];b=kw['bundles'][1]['source_blocks'][0]
    if tamper=='no_link':a['observed_context']['definition_references']=[]
    elif tamper=='different_hash':b['source_hash']='another-version'
    elif tamper=='foreign_label':a['observed_context']['definition_references'][0]['label']='OTHER'
    elif tamper=='review':kw['admissions'][1]['decision']='NEEDS_REVIEW'
    elif tamper=='foreign_claim':records[1]['semantics']['description'][0]['evidence']['block_id']='foreign'
    elif tamper=='api_section':b['observed_context']['definition_section']['heading_text']='API FROB() functions'
    out,audit=resolve_canonical_representations(records,**kw)
    assert len(out)==2 and not audit[0]['resolved']


@pytest.mark.parametrize('raw,expected',[
    ('dbshell> .mode pretty\ndbshell> SELECT ALPHA(1);\n2',True),
    ('tool> SELECT OTHER(1);\nALPHA(1)',False),
    ("tool> SELECT 'ALPHA(1)';",False),
    ('ALPHA(1) output only',False),
])
def test_generic_cli_examples_preserve_original_transcript(raw,expected):
    b={'block_id':'b','text':' '.join(raw.split()),'block_kind':'CODE',
       'observed_context':{'labels':[{'text':'Examples'}],'literal_code_text':raw},
       'definition_section_proofs':[{'primary_identity':'ALPHA'}]}
    result=extract_structured_semantics({'normalized_identity':'ALPHA','feature_type':'FUNCTION','evidence':[b]})
    assert _cli_example_shape(b,'ALPHA')==expected
    # Non-transcript code retains its separate established interpretation.
    if '>' in raw:assert bool(result['official_examples'])==expected
    if expected:assert result['official_examples'][0]['sql']==b['text']


@pytest.mark.parametrize('intro,pure,expected',[(True,True,True),(False,True,False),(True,False,False)])
def test_code_comparison_table_requires_real_intro_and_all_code_cells(tmp_path,intro,pure,expected):
    html='<html><body><h1>Types</h1><p>'+('For example:' if intro else 'The storage format:')+'</p>'
    html+='<center><table><tr><th>Mode A</th><th>Mode B</th></tr><tr><td><pre>CREATE TABLE t(a BOX);</pre></td>'
    html+='<td>'+('<pre>CREATE TABLE t(a BOX);</pre>' if pure else 'ordinary parameter value')+'</td></tr></table></center></body></html>'
    path=tmp_path/'raw.html';path.write_text(html,encoding='utf-8');s=BeautifulSoup(html,'lxml');node=s.find('pre')
    d={'page_id':'p','url':'https://official.invalid/types','html_path':str(path),'page_hash':hashlib.sha256(path.read_bytes()).hexdigest()}
    block={'block_id':'b','dom_path':_dom_path(node),'text':node.get_text(' ',strip=True)}
    observed=SourceStructureIndex().observe(d,block)['observed_context']
    assert bool([l for l in observed['labels'] if l.get('context_scope')=='CODE_ONLY_COMPARISON_TABLE'])==expected


@pytest.mark.parametrize('name,kind,sql,expected',[
    ('TOKEN','FUNCTION','CREATE TABLE t(c TOKEN(4));',False),
    ('TOKEN','FUNCTION','SELECT CAST(c AS TOKEN(4)) FROM t;',False),
    ('TOKEN','FUNCTION','SELECT TOKEN(c) FROM t;',True),
    ('TOKEN','FUNCTION','SELECT OTHER(c); -- TOKEN(c)',False),
    ('TOKEN','DATATYPE','INSERT INTO t TOKEN c=1;',False),
    ('SET','DATATYPE','INSERT INTO t SET c=1;',False),
    ('TOKEN','DATATYPE','CREATE TABLE t(c TOKEN(4));',True),
])
def test_cli_subject_requires_type_appropriate_application(name,kind,sql,expected):
    raw='dbshell> '+sql
    b={'block_id':'b','text':raw,'block_kind':'CODE',
       'observed_context':{'labels':[{'text':'Examples'}],'literal_code_text':raw},
       'definition_section_proofs':[{'primary_identity':name}]}
    assert _cli_example_shape(b,name,kind)==expected
    r=extract_structured_semantics({'normalized_identity':name,'feature_type':kind,'evidence':[b]})
    assert bool(r['official_examples'])==expected
