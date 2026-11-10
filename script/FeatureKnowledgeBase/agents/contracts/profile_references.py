"""Resolve explicit subject references to actual frozen definition anchors.

No spelling-only edges: a physical identity-cell link or an explicit "this
feature's description" reference must already point into the supplied corpus.
"""
import re
from urllib.parse import urldefrag,urljoin,urlparse
from agents.profile_bootstrap.cold_start import profile_digest


def _kind(unit):
    if unit.get('type_context'):return unit['type_context']
    kinds={d['feature_type'] for b in (unit.get('structure_execution') or {}).get('identity_bindings',[])
           for d in b['declarations']}
    return next(iter(kinds)) if len(kinds)==1 else None


def bind_subject_references(units,documents,profile,reader):
    from agents.owner.structure_execution import execute_structures
    from agents.owner.profile_execution import declarations
    from agents.owner.surface_patterns import formal_signatures
    from agents.contracts.definition_entry import identifier_key
    pages={d['page_id']:d for d in documents};urls={urldefrag(d['url'])[0]:d for d in documents}
    indexes={pid:{b['block_id']:b for b in d['blocks']} for pid,d in pages.items()}
    cache=getattr(reader,'_profile_reference_scope_cache',{})
    reader._profile_reference_scope_cache=cache
    def anchor_targets(document,block):
        own=reader.observe(document,block).get('definition_targets',[])
        if own:return own,None
        headings=[b for b in document['blocks'] if b.get('block_kind')=='HEADING'
                  and (b.get('order') or 0)<(block.get('order') or 0)]
        for heading in sorted(headings,key=lambda b:b.get('order') or 0,reverse=True):
            actual=reader.observe(document,heading)
            if actual.get('definition_targets'):return actual['definition_targets'],heading['block_id']
        return [],None
    for unit in units:
        execution=unit.get('structure_execution') or {}
        if execution.get('schema_version')!='StructureExecution/v2':continue
        source=pages[unit['page_id']];kind=_kind(unit)
        if kind not in {'FUNCTION','OPERATOR','DATATYPE'}:continue
        names=set(unit['identity_values']);links=[]
        source_anchor=indexes[unit['page_id']].get((unit.get('source_proof') or {}).get('heading_block_id'),{})
        self_targets,parent_heading=anchor_targets(source,source_anchor)
        if parent_heading and self_targets:
            unit.setdefault('definition_targets',[]).extend(t for t in self_targets if t not in unit.get('definition_targets',[]))
            unit['section_target_proof']={'heading_block_id':parent_heading,'declaration_block_id':source_anchor['block_id'],
                'identity_values':sorted(names),'feature_type':kind,'targets':self_targets,'source_hash':unit['source_hash']}
        if execution.get('structure')=='D':
            for binding in execution.get('identity_bindings',[]):
                b=indexes[unit['page_id']].get(binding['block_id'],{})
                observed=reader.observe(source,b)
                for cell in (observed.get('table_context') or {}).get('cells',[]):
                    if cell['cell_index']!=binding.get('cell_index'):continue
                    for link in cell.get('links',[]):
                        label=str(link.get('text') or '')
                        heads={identifier_key(label)}|{identifier_key(n)for n,_ in formal_signatures(label)}
                        if not heads.intersection(identifier_key(n)for n in names):continue
                        links.append({'url':urljoin(source['url'],link.get('href','')),
                            'source_block_id':b['block_id'],'source_locator':link.get('dom_locator'),
                            'purpose':'DECLARATION_CELL_REFERENCE'})
        else:
            for bid in unit['member_block_ids']:
                b=indexes[unit['page_id']][bid]
                if not re.search(r"\bthis\s+(?:function|operator|(?:data\s*)?type)(?:['’]s)?\s+(?:description|definition|documentation)\b",b.get('text',''),re.I):continue
                observed=reader.observe(source,b)
                for link in (observed.get('observed_context') or {}).get('outgoing_links',[]):
                    links.append({'url':link['target_url'],'source_block_id':bid,
                                  'source_locator':link.get('dom_locator'),'purpose':'EXPLICIT_SUBJECT_DESCRIPTION_REFERENCE'})
        for link in links:
            base,fragment=urldefrag(link['url']);target=urls.get(base)
            if not target or urlparse(base).hostname!=urlparse(source['url']).hostname:continue
            key=(target['page_hash'],profile_digest(profile))
            if key not in cache:cache[key]=execute_structures(target,profile)['proofs']
            matches=[]
            target_blocks=indexes[target['page_id']]
            for proof in cache[key]:
                if not names.intersection(proof.get('identity_values',[])):continue
                target_kinds={proof.get('feature_type')} if proof.get('feature_type') else {
                    d['feature_type'] for binding in (proof.get('structure_execution') or {}).get('identity_bindings',[]) for d in binding['declarations']}
                if not target_kinds:
                    target_kinds={d['feature_type']for bid in proof.get('definition_anchor_block_ids',[])
                                  for d in declarations(target_blocks.get(bid,{}).get('text'),target_blocks.get(bid,{}))}
                if target_kinds!={kind}:continue
                anchor=target_blocks.get(proof.get('heading_block_id'),{})
                targets,parent=anchor_targets(target,anchor)
                for uri in targets:
                    if not fragment or uri==link['url']:
                        matches.append({'target_url':uri,'target_page_hash':target['page_hash'],
                                        'target_anchor_block_id':anchor['block_id'],'target_section_id':proof['section_id'],
                                        'target_parent_heading_block_id':parent})
            unique={m['target_url']for m in matches}
            if len(unique)!=1:continue
            match=matches[0]
            if match['target_url'] not in unit.setdefault('definition_targets',[]):unit['definition_targets'].append(match['target_url'])
            unit.setdefault('subject_reference_proofs',[]).append(dict(link,**match,source_hash=unit['source_hash'],
                source_url=unit['source_url'],identity_values=sorted(names),feature_type=kind,
                proof='EXPLICIT_SUBJECT_REFERENCE_TO_UNIQUE_FROZEN_DEFINITION'))
