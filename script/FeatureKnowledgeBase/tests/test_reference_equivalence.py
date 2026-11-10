import copy
import hashlib
from bs4 import BeautifulSoup
from agents.document_structure_v2.topology import _dom_path
from agents.semantic_stage.source_structure import SourceStructureIndex
from agents.contracts.publication_integrity import evaluate_publication_integrity
from agents.contracts.canonical_integrity import resolve_canonical_representations


def parts():
    units=[];bundles=[];ads=[];valid=[];records=[]
    for number in range(2):
        uid='u'+str(number);bid='b'+str(number);owner='o'+str(number)
        u={'identity_unit_id':uid,'canonical_identity':'FROB','feature_type':'FUNCTION','owner_ids':[owner]}
        b={'block_id':bid,'text':'FROB','block_kind':'HEADING','owner_ids':[owner],
           'source_url':'https://official.invalid/ref'+str(number),'source_hash':'hash'+str(number),
           'dom_path':'h2','definition_targets':['https://official.invalid/definition#frob'],
           'heading_ancestry':['Functions'],'definition_section_proofs':[{'heading_block_id':bid,
             'identity_unit_id':uid,'identity_values':['FROB'],'primary_identity':'FROB'}]}
        bundle={'identity_group_id':uid,'identity_unit_id':uid,'source_blocks':[b]}
        integrity=evaluate_publication_integrity(u,bundle,{'primary_type':'FUNCTION','status':'CONFIRMED'})
        ads.append({'identity_unit_id':uid,'decision':'ADMITTED_PARTIAL','identity_credible':True,
            'official_source':True,'owner_evidence_correct':True,'lineage_complete':True,'publication_integrity':integrity})
        valid.append({'identity_group_id':uid,'invalid_evidence_refs':0,'ungrounded_claims':0,'semantic_status':'PARTIAL'})
        records.append({'feature_id':'f'+str(number),'name':'FROB','feature_type':'function',
            'canonical_identity':{'key':'demo|function|frob|default'},'semantic_status':'PARTIAL',
            'provenance':{'identity_unit_id':uid},'quality':{},'structural':{'attributes':{}},
            'semantics':{'description':[{'value':'Description '+str(number),'evidence':{'block_id':bid,
                'url':b['source_url'],'page_hash':b['source_hash']}}], 'syntax':[],'examples':[]}})
        units.append(u);bundles.append(bundle)
    return records,{'units':units,'bundles':bundles,'admissions':ads,'validations':valid,'lineage':{'status':'PASS'}}


def test_shared_observed_definition_target_merges_native_representations():
    records,kw=parts();out,audit=resolve_canonical_representations(records,**kw)
    assert len(out)==1 and len(out[0]['semantics']['description'])==2
    assert audit[0]['classification']=='PROVEN_REFERENCE_EQUIVALENCE'
    assert len(out[0]['structural']['attributes']['canonical_representation_lineage'])==2
    reverse,_=resolve_canonical_representations(list(reversed(records)),**kw)
    assert out==reverse


def test_same_name_without_shared_definition_target_does_not_merge():
    records,kw=parts()
    b=kw['bundles'][1]['source_blocks'][0];b['definition_targets']=['https://official.invalid/other#frob']
    kw['admissions'][1]['publication_integrity']=evaluate_publication_integrity(kw['units'][1],kw['bundles'][1],{'primary_type':'FUNCTION','status':'CONFIRMED'})
    out,audit=resolve_canonical_representations(records,**kw)
    assert len(out)==2 and not audit[0]['resolved']


def test_foreign_claim_cannot_be_imported_by_shared_target():
    records,kw=parts();records[1]['semantics']['description'][0]['evidence']['block_id']='foreign'
    out,audit=resolve_canonical_representations(records,**kw)
    assert len(out)==2 and not audit[0]['resolved']


def test_review_member_does_not_inherit_other_members_permission():
    records,kw=parts();kw['admissions'][1]['decision']='NEEDS_REVIEW'
    out,audit=resolve_canonical_representations(records,**kw)
    assert len(out)==2 and not audit[0]['resolved']


def test_source_anchors_and_table_links_are_observed_from_html(tmp_path):
    html='<html><body><h2>Functions</h2><table><tr><th>Name</th><th>Description</th></tr><tr><th scope="row"><a href="#frob">FROB()</a></th><td>Short.</td></tr></table><ul><li><a name="frob"></a><p>FROB(x)</p><p>Actual description.</p></li></ul></body></html>'
    path=tmp_path/'raw.html';path.write_text(html,encoding='utf-8')
    soup=BeautifulSoup(html,'lxml');p=soup.find('li').find('p');tr=soup.find_all('tr')[1]
    d={'page_id':'p','url':'https://official.invalid/ref','html_path':str(path),'page_hash':hashlib.sha256(path.read_bytes()).hexdigest()}
    idx=SourceStructureIndex()
    declaration=idx.observe(d,{'block_id':'b','dom_path':_dom_path(p),'text':p.get_text(' ',strip=True)})
    assert 'https://official.invalid/ref#frob' in declaration['definition_targets']
    body=idx.observe(d,{'block_id':'body','dom_path':_dom_path(p.find_next_sibling('p')),'text':'Actual description.'})
    assert not body['definition_targets']
    table=idx.observe(d,{'block_id':'r','dom_path':_dom_path(tr),'text':tr.get_text(' ',strip=True)})
    assert table['table_context']['cells'][0]['links'][0]['href']=='#frob'
