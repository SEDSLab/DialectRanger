"""A literal signature immediately introduced as a function, within one owner."""
import re
from agents.owner.linked_syntax import citation,frozen_node
from agents.owner.source_relations import scope
from agents.contracts.base import stable_id


def introduction(text):
    return bool(re.search(r'\b(?:use|call|invoke)\s+(?:an?\s+)?function\b',text,re.I)
                and text.rstrip().endswith(':'))


def verified(proof,block,name):
    witness=proof.get('local_callable_proof') or {}
    sig=witness.get('signature') or {};intro=witness.get('introduction') or {}
    if block['block_id']!=sig.get('block_id') or name not in proof.get('identity_values',[]):return None
    a=frozen_node(intro);b=frozen_node(sig)
    if a is None or b is None or b.name!='pre' or not introduction(a.get_text(' ',strip=True)):return None
    wrapper=b
    from agents.document_structure_v2.semantic_regions import semantic_region_role
    while wrapper.parent is not None and wrapper.parent is not a.parent:
        wrapper=wrapper.parent
        pres=wrapper.find_all('pre')
        if len(pres)!=1 or pres[0] is not b:return None
        if wrapper.find(['h1','h2','h3','h4','h5','h6','p']) or any(
                semantic_region_role(n)[0]=='PARAGRAPH' for n in wrapper.find_all()):return None
    if wrapper.parent is not a.parent or wrapper.find_previous_sibling() is not a:return None
    from agents.owner.surface_patterns import formal_signatures
    values=formal_signatures(b.get_text(' ',strip=True))
    if not values or {n for n,_ in values}!={name}:return None
    provenance=block.get('provenance') or {}
    if (block.get('text')!=sig.get('text') or block.get('dom_path')!=sig.get('dom_path')
            or provenance.get('page_hash')!=sig.get('source_hash')
            or provenance.get('source_url')!=sig.get('source_url')):return None
    return {'feature_type':'FUNCTION','identity_values':[name],'formal_value':sig['text']}


def prepare(documents,profile):
    from agents.owner.structure_execution import enabled_structures
    if 'C' not in enabled_structures(profile):return documents
    from agents.owner.surface_patterns import formal_signatures
    output=[]
    for document in documents:
        blocks=sorted(document.get('blocks',[]),key=lambda b:b.get('order',0));proofs=[]
        for intro,code in zip(blocks,blocks[1:]):
            if intro.get('block_kind')!='TEXT' or code.get('block_kind')!='CODE' or not introduction(intro.get('text','')):continue
            if re.search(r'\b(?:examples?|results?|API|SDK|tutorial)\b',' / '.join(code.get('heading_ancestry',[])),re.I):continue
            parsed=formal_signatures(code['text'])
            if not parsed or len({n for n,_ in parsed})!=1:continue
            name=parsed[0][0];witness={'introduction':citation(document,intro),'signature':citation(document,code)}
            rel={'schema_version':'DefinitionSourceRelation/v1','relation_id':stable_id('local_callable',[code['block_id']]),
                 'relation':'EXPLICIT_FUNCTION_INTRODUCTION','identities':[name],'feature_type':'FUNCTION',
                 'status':'VERIFIED_STRUCTURE','evidence':[witness['introduction'],witness['signature']]}
            p=scope(document,code,name,[intro,code],[rel],role='DEFINITION')
            p.update(feature_type='FUNCTION',configured_structure='C',local_callable_proof=witness,
                     scope_execution_plan={'relation':'PAGE_SUBJECT_RELATION'},
                     subject_formal_values={name:{code['block_id']:[code['text']]}})
            if verified(p,code,name):proofs.append(p)
        output.append(dict(document,definition_source_relation_scopes=list(document.get('definition_source_relation_scopes',[]))+proofs))
    return output
