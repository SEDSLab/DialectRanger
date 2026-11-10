import copy
import hashlib
import json
import tracemalloc
import pytest
from agents.discovery.generic_stage import _jsonl,_EdgeJournal,run_scoped_discovery
from agents.contracts.document_ir import from_v2_page
from agents.document_structure_v2.topology import build_page_topology
from agents.owner.linked_syntax import prepare,verified
from agents.owner.structure_execution import execute_structures
from agents.pipeline.generic_unified import run_generic_downstream
from tests.test_subject_contract_repair import section_profile


def doc(tmp_path,name,body,url):
    path=tmp_path/name
    path.write_text('<main>'+body+'</main>',encoding='utf-8')
    d=from_v2_page(build_page_topology({'url':url,'html_path':str(path)},{}),dbms='sample',html_path=str(path))
    d['document_id']=d['page_id']
    return d


def fixture(tmp_path,signature='SPIN(x)',section='Syntax',target='https://official.test/diagrams/spin'):
    a=doc(tmp_path,'main.html','<h1>SPIN</h1><h2>'+section+'</h2><p><a href="'+target+'">'
          'Text version of diagram</a></p><h2>Purpose</h2><p>Returns a rotation.</p>'
          '<p>Null input returns null.</p><h2>Examples</h2><pre>SELECT SPIN(1);</pre>',
          'https://official.test/reference/spin')
    b=doc(tmp_path,'signature.html','<h1>Text version of diagram</h1><pre>'+signature+'</pre>',target)
    p=section_profile(a);h=p['hint_contract']
    h['definition_scope_patterns']+=[{'relation':'PAGE_SUBJECT_RELATION','identity_slot':'TYPED_SUBJECT_HEADING','boundary':'DEFINITION_PREFIX'}]
    base=h['definition_structure_patterns'][0]
    base['field_labels']={'DESCRIPTION':['Purpose'],'EXAMPLE':['Examples']}
    base['execution']['fields']=[{'role':'DESCRIPTION','locator':{'kind':'SECTION_LABEL','labels':['Purpose']}},
                               {'role':'EXAMPLE','locator':{'kind':'SECTION_LABEL','labels':['Examples']}},
                               {'role':'FORMAL_SYNTAX','locator':{'kind':'DECLARATION_ANCHORS'}}]
    page=copy.deepcopy(base);page.update(structure='A',identity_slot='PAGE_SUBJECT',boundary='SUBJECT_PAGE')
    entry=copy.deepcopy(base);entry.update(structure='C',identity_slot='LEADING_TERM',boundary='CURRENT_ENTRY')
    entry['execution']['identity']['alternatives']=[{'kind':'LEADING_CODE'}]
    h['definition_structure_patterns']=[page,base,entry]
    h['linked_syntax_patterns']=[{'section_labels':['Syntax'],'link_text_prefixes':['Text version of diagram']}]
    return a,b,p


def test_linked_signature_rechecks_both_frozen_endpoints(tmp_path):
    a,b,p=fixture(tmp_path);before=copy.deepcopy(a['blocks'])
    docs=prepare([a,b],p)
    proof=docs[0]['definition_source_relation_scopes'][0]
    assert verified(proof,a['blocks'][0],'SPIN')['feature_type']=='FUNCTION'
    assert a['blocks']==before
    assert any('SPIN' in x['identity_values'] for x in execute_structures(docs[0],p)['proofs'])
    broken=copy.deepcopy(proof);broken['linked_syntax_proof']['target']['source_hash']='wrong'
    assert verified(broken,a['blocks'][0],'SPIN') is None
    (tmp_path/'signature.html').write_text('<pre>OTHER(x)</pre>',encoding='utf-8')
    assert verified(proof,a['blocks'][0],'SPIN') is None


@pytest.mark.parametrize('signature,section,target',[
    ('OTHER(x)','Syntax','https://official.test/diagrams/spin'),
    ('SELECT SPIN(1);','Syntax','https://official.test/diagrams/spin'),
    ('SPIN(x)','Examples','https://official.test/diagrams/spin'),
    ('SPIN(x)','Syntax','https://external.test/diagrams/spin'),
])
def test_linked_signature_does_not_borrow_other_subject_or_example(tmp_path,signature,section,target):
    a,b,p=fixture(tmp_path,signature,section,target)
    assert not prepare([a,b],p)[0]['definition_source_relation_scopes']


def test_linked_signature_missing_target_is_explicit(tmp_path):
    a,b,p=fixture(tmp_path)
    d=prepare([a],p)[0]
    assert not d['definition_source_relation_scopes']
    assert d['linked_syntax_diagnostics'][0]['status']=='TARGET_NOT_IN_CORPUS'


def test_linked_signature_full_pipeline_preserves_separate_sources(tmp_path):
    a,b,p=fixture(tmp_path)
    run_generic_downstream(dbms='sample',documents=[a,b],profile=p,output_root=tmp_path/'run',semantic=True,semantic_provider='deterministic')
    rows=json.loads((tmp_path/'run/canonical/promotion_ready_kb.json').read_text(encoding='utf-8'))
    selected=[r for r in rows if r['name']=='SPIN' and r['feature_type']=='function']
    assert len(selected)==1
    semantics=selected[0]['semantics']
    assert {'Returns a rotation.','Null input returns null.'}<={x['value'] for x in semantics['description']}
    assert 'SPIN(x)' in {x['value'] for x in semantics['syntax']}
    assert 'SELECT SPIN(1);' in {x['value'] for x in semantics['examples']}
    assert a['url'] in json.dumps(selected[0]) and b['url'] in json.dumps(selected[0])


def test_linked_signature_optional_grammar_remains_formal(tmp_path):
    text='SPIN({ * | [ DISTINCT | ALL ] expr }) [ OVER { window_name | (analytic_clause) } ]'
    a,b,p=fixture(tmp_path,text)
    docs=prepare([a,b],p)
    assert any(x['identity_values']==['SPIN'] for x in execute_structures(docs[1],p)['proofs'])


def test_preflight_keeps_supplied_link_endpoint(tmp_path):
    from agents.pipeline.preflight import evaluate_documents
    a,b,p=fixture(tmp_path)
    evaluate_documents([a,b],p,tmp_path/'preflight',sample_pages=2)
    rows=json.loads((tmp_path/'preflight/summary.json').read_text(encoding='utf-8'))
    assert rows['status']=='SUPPORTED'


def test_stream_writer_hashes_actual_unicode_bytes(tmp_path):
    path=tmp_path/'rows.jsonl'
    digest=_jsonl(path,({'text':'内容 '+str(i)} for i in range(10)))
    assert digest==hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(path.read_text(encoding='utf-8').splitlines())==10


def test_edge_journal_memory_is_bounded(tmp_path):
    journal=_EdgeJournal(tmp_path/'edges.jsonl')
    tracemalloc.start()
    for i in range(3000):journal.append({'id':i,'text':'x'*1000})
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    assert peak<1024*1024
    assert len(journal)==3000
    assert sum(1 for _ in journal)==3000


def test_duplicate_roots_merge_families_and_edges_reference_manifest(tmp_path):
    url='https://official.test/ref';calls=[]
    def fetch(session,url,**kw):
        calls.append(url)
        return {'ok':True,'raw':b'<main><a href="/other">Other</a></main>',
                'final_url':url,'content_type':'text/html','http_status':200}
    manifest={'dbms':'sample','scopes':{k:[{'url':url}] for k in ['FUNCTION','OPERATOR','DATATYPE']}}
    result=run_scoped_discovery(dbms='sample',scope_manifest=manifest,output_root=tmp_path/'crawl',
                               max_depth=0,fetcher=fetch,report_path=tmp_path/'report.md')
    assert calls==[url]
    assert result['corpus']['scope_roots']==[url]
    edges=[json.loads(s) for s in (tmp_path/'crawl/discovery/discovery_edges.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(edges)==1
    assert 'scope_roots' not in edges[0]['provenance']
    reference=edges[0]['provenance']['scope_manifest_ref']
    assert (tmp_path/'crawl/discovery'/reference).resolve().is_file()
