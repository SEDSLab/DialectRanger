"""Explicit frozen syntax links. No retrieval, name lists or source-text rewriting."""
import copy
import re
from urllib.parse import urljoin, urldefrag, urlsplit
from agents.contracts.base import stable_id


def patterns(profile):
    values=(profile or {}).get('hint_contract',{}).get('linked_syntax_patterns',[])
    if not isinstance(values,list) or len(values)>8:raise ValueError('bounded linked syntax patterns required')
    for p in values:
        if not isinstance(p,dict) or set(p)!={'section_labels','link_text_prefixes'}:
            raise ValueError('linked syntax requires section labels and link text prefixes')
        for labels in p.values():
            if (not isinstance(labels,list) or not 1<=len(labels)<=12
                    or any(not isinstance(s,str) or not s.strip() or len(s)>120 for s in labels)):
                raise ValueError('invalid linked syntax label')
    return values


def norm(value):return ' '.join(str(value or '').split()).strip().casefold()


def citation(document,block):
    return {'page_id':document['page_id'],'source_url':document['url'],'source_hash':document['page_hash'],
            'html_path':document.get('html_path') or (block.get('provenance') or {}).get('html_path'),
            'block_id':block['block_id'],'dom_path':block.get('dom_path'),'text':block.get('text')}


def frozen_node(c):
    # Bounded cache, keyed by content expectation AND current file metadata.
    from pathlib import Path
    from collections import OrderedDict
    from agents.semantic_stage.source_structure import SourceStructureIndex
    global _pages
    if '_pages' not in globals():_pages=OrderedDict()
    try:
        path=Path(c['html_path']);stat=path.stat()
        key=(str(path),c['source_hash'],stat.st_mtime_ns,stat.st_size)
        if key not in _pages:
            index=SourceStructureIndex()
            _pages[key]=index._page({'html_path':str(path),'page_hash':c['source_hash']})
            if len(_pages)>8:_pages.popitem(last=False)
        page=_pages[key]
        if page.get('status')!='RESOLVED':return None
        nodes=page['nodes'].get(c.get('dom_path'),[])
        if len(nodes)!=1 or norm(nodes[0].get_text(' ',strip=True))!=norm(c.get('text')):return None
        return nodes[0]
    except (OSError,KeyError,TypeError):return None


def declarations(text):
    from agents.owner.declaration_binding import syntax_declarations,callable_members
    return syntax_declarations(text) or callable_members(text)


def verified(proof,block,name):
    witness=proof.get('linked_syntax_proof') or {}
    if name not in proof.get('identity_values',[]) or block['block_id']!=proof.get('heading_block_id'):return None
    cfg=witness.get('pattern') or {}
    try:patterns({'hint_contract':{'linked_syntax_patterns':[cfg]}})
    except ValueError:return None
    source=witness.get('source') or {};target=witness.get('target') or {}
    own=target if proof.get('linked_syntax_endpoint')=='TARGET' else source
    provenance=block.get('provenance') or {}
    if (block.get('text')!=own.get('text') or block.get('dom_path')!=own.get('dom_path')
            or block['block_id']!=own.get('block_id')
            or provenance.get('page_hash',block.get('source_hash'))!=own.get('source_hash')
            or provenance.get('source_url',block.get('source_url'))!=own.get('source_url')):return None
    head=frozen_node(source);code=frozen_node(target);link_block=frozen_node(witness.get('link_block') or {})
    if head is None or code is None or link_block is None:return None
    if head.name not in {'h1','h2','h3','h4','h5','h6'} or norm(head.get_text())!=name.casefold():return None
    if code.name not in {'pre','code'}:return None
    if urlsplit(source['source_url']).netloc!=urlsplit(target['source_url']).netloc:return None
    label=link_block.find_previous(['h1','h2','h3','h4','h5','h6'])
    if label is None or norm(label.get_text(' ',strip=True)) not in {norm(s) for s in cfg['section_labels']}:return None
    # Walk the hierarchy: a sibling feature's Syntax cannot lend its link.
    ancestor=label
    while ancestor is not None and ancestor is not head and int(ancestor.name[1])>int(head.name[1]):
        ancestor=ancestor.find_previous(['h1','h2','h3','h4','h5','h6'])
    if ancestor is not head:return None
    links=[a for a in link_block.find_all('a',href=True)
           if any(norm(a.get_text(' ',strip=True)).startswith(norm(s)) for s in cfg['link_text_prefixes'])
           and urljoin(source['source_url'],a['href'])==witness.get('target_url')]
    if len(links)!=1:return None
    base,fragment=urldefrag(witness['target_url'])
    if base!=urldefrag(target['source_url'])[0]:return None
    if fragment and not any(n.get('id')==fragment or n.get('name')==fragment for n in [code,*code.parents]):return None
    ds=declarations(target['text'])
    if not ds or {d['identity'] for d in ds}!={name}:return None
    return {'feature_type':'FUNCTION','identity_values':[name],
            'formal_value':target['text'] if proof.get('linked_syntax_endpoint')=='TARGET' else None}


def prepare(documents,profile):
    configs=patterns(profile)
    if not configs:return documents
    from agents.owner.definition_scope import navigation_block,heading_container
    from agents.owner.source_relations import scope
    urls={urldefrag(d['url'])[0]:d for d in documents};proofs={};diagnostics={}
    for document in documents:
        blocks=sorted(document.get('blocks',[]),key=lambda b:b.get('order',0))
        heads=[b for b in blocks if b.get('block_kind')=='HEADING' and not navigation_block(b)]
        if not heads:continue
        head=heads[0];name=head.get('text','').strip()
        if not re.fullmatch(r'[A-Za-z_][\w$]*',name):continue
        name=name.upper();start=blocks.index(head);level=head.get('heading_level') or 1
        end=next((i for i in range(start+1,len(blocks)) if blocks[i].get('block_kind')=='HEADING'
                 and (blocks[i].get('heading_level') or 1)<=level),len(blocks))
        parent=heading_container(head)
        local=[b for b in blocks[start:end] if not parent or b.get('dom_path','').startswith(parent+' > ')]
        active=None;matches=[]
        for block in local:
            if block.get('block_kind')=='HEADING':active=norm(block.get('text'));continue
            selected=[p for p in configs if active in {norm(s) for s in p['section_labels']}]
            if not selected or block.get('block_kind') not in {'TEXT','DEFINITION'}:continue
            node=frozen_node(citation(document,block))
            if node is None:continue
            for cfg in selected:
                for a in node.find_all('a',href=True):
                    if not any(norm(a.get_text(' ',strip=True)).startswith(norm(s)) for s in cfg['link_text_prefixes']):continue
                    target_url=urljoin(document['url'],a['href']);target=urls.get(urldefrag(target_url)[0])
                    if not target:
                        diagnostics.setdefault(document['page_id'],[]).append({'status':'TARGET_NOT_IN_CORPUS','target_url':target_url})
                        continue
                    for code in target.get('blocks',[]):
                        if code.get('block_kind')!='CODE':continue
                        witness={'pattern':copy.deepcopy(cfg),'source':citation(document,head),'target':citation(target,code),
                                 'link_block':citation(document,block),'target_url':target_url}
                        proof={'heading_block_id':head['block_id'],'identity_values':[name],'linked_syntax_proof':witness}
                        if verified(proof,head,name):matches.append((target,code,witness))
        unique={(w['target']['page_id'],w['target']['block_id']):(t,c,w) for t,c,w in matches}
        if len(unique)!=1:continue
        target,code,witness=next(iter(unique.values()))
        for doc,anchor,members,endpoint in [(document,head,local,'SOURCE'),(target,code,[code],'TARGET')]:
            rel={'schema_version':'DefinitionSourceRelation/v1','relation':'LINKED_SYNTAX_TEXT',
                 'relation_id':stable_id('linked_syntax',[witness['source']['block_id'],code['block_id']]),
                 'identities':[name],'feature_type':'FUNCTION','status':'VERIFIED_STRUCTURE',
                 'evidence':[witness['source'],witness['link_block'],witness['target']]}
            p=scope(doc,anchor,name,members,[rel],role='DEFINITION')
            p.update(feature_type='FUNCTION',linked_syntax_proof=witness,linked_syntax_endpoint=endpoint,
                     configured_structure='A' if endpoint=='SOURCE' else 'C',
                     scope_execution_plan={'relation':'PAGE_SUBJECT_RELATION'},
                     association='EXPLICIT_LINKED_SYNTAX',formal_signature_values={code['block_id']:[code['text']]} if endpoint=='TARGET' else {})
            proofs.setdefault(doc['page_id'],[]).append(p)
    return [dict(d,definition_source_relation_scopes=list(d.get('definition_source_relation_scopes',[]))+proofs.get(d['page_id'],[]),
                 linked_syntax_diagnostics=diagnostics.get(d['page_id'],[])) for d in documents]
