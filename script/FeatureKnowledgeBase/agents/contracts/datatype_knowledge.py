"""Optional Canonical knowledge from existing Semantic extensions, not extraction.

Only literal claims already emitted by Semantic are consumed. Official frozen
structure and owner-local references must independently justify every cell.
No admission, identity, type or publication authority lives in this module.
"""
from __future__ import annotations
import copy
import hashlib
import json
import re
from typing import Any, Mapping

from agents.semantic_stage.source_structure import SourceStructureIndex

VERSION = 'canonical-semantic-extensions/v1'
RELATIONS = {'SQL_TO_RUNTIME_TYPE','SQL_TO_HOST_LANGUAGE_TYPE','OTHER_EXPLICIT_MAPPING','UNKNOWN_RELATION'}

def _norm(value):return ' '.join(str(value or '').replace('\u200b','').split()).casefold()

def relation_kind(source_headers, target_headers):
    source=' '.join(_norm(h.get('text')) for h in source_headers)
    target=' '.join(_norm(h.get('text')) for h in target_headers)
    if not re.fullmatch(r'sql (?:data\s*)?type',source):return 'UNKNOWN_RELATION'
    if re.search(r'\bruntime\s+(?:data\s*)?type\b',target):return 'SQL_TO_RUNTIME_TYPE'
    if re.search(r'\b(?:jdbc|java|python|host language|clr|\.net)\b',target) and re.search(r'\b(?:type|class)\b',target):return 'SQL_TO_HOST_LANGUAGE_TYPE'
    if re.search(r'\b(?:mapped|mapping|native|underlying|internal)\s+(?:data\s*)?type\b',target):return 'OTHER_EXPLICIT_MAPPING'
    return 'UNKNOWN_RELATION'

def response_extension_payload(feature, response):
    """Read the same provider response, without re-running or changing Semantic.

    Hash binding is mandatory: a different response for the same Unit cannot
    silently donate knowledge. Parsed JSON and raw response must agree.
    """
    if not response:return None, []
    try:
        raw=response.get('raw_response') or ''
        parsed=json.loads(raw)
        actual=hashlib.sha256(raw.encode('utf8')).hexdigest()
        if (response.get('identity_group_id')!=feature.get('identity_group_id')
                or parsed.get('identity_group_id')!=feature.get('identity_group_id')
                or response.get('parsed') not in (None,parsed)
                or actual!=feature.get('semantic_response_hash')
                or response.get('response_hash',actual)!=actual):
            raise ValueError('unbound response')
        return parsed, []
    except (ValueError,TypeError,AttributeError):
        return None, [{'reason':'SEMANTIC_EXTENSION_RESPONSE_BINDING_FAILURE'}]

def build_datatype_extensions(*, unit, bundle, documents, owners, envelopes, payload,
                              source_index=None, initial_diagnostics=()):
    """Validate existing datatype claims against actual frozen row/cell/owners."""
    if str(unit.get('feature_type') or '').upper()!='DATATYPE':return None
    payload=payload or {};extension=payload.get('semantic_extensions') or {}
    relevant=bool(extension.get('datatype_name') or extension.get('datatype_mapping') or extension.get('uncertain_fields'))
    if not relevant and not initial_diagnostics:return None
    uid=unit['identity_unit_id'];name=str(unit.get('canonical_identity') or '')
    result={'schema_version':VERSION,'datatype':{'definition_names':[],'mappings':[],'notes':[],'uncertain_observations':[]},
            'diagnostics':copy.deepcopy(list(initial_diagnostics))}
    knowledge=result['datatype'];diags=result['diagnostics']
    pages={p['page_id']:p for p in documents}
    block_index={b['block_id']:(p,b) for p in documents for b in p.get('blocks',[])}
    source_blocks={b['block_id']:b for b in bundle.get('source_blocks',[])}
    owner_index={o['owner_id']:o for o in owners}
    index=source_index or SourceStructureIndex()

    def reject(field,claim,reason):
        diags.append({'field':field,'reason':reason,'unverified_claim':copy.deepcopy(claim)})

    def proof(claim,field):
        refs=claim.get('evidence_refs') or []
        locations=claim.get('source_locations') or []
        if not refs or not locations:raise ValueError('MISSING_CELL_OR_BLOCK_LOCATOR')
        proofs=[]
        for loc in locations:
            bid=loc.get('block_id')
            if bid not in refs or bid not in block_index or bid not in source_blocks:raise ValueError('FOREIGN_OR_MISSING_SOURCE_BLOCK')
            page,block=block_index[bid];bound=source_blocks[bid]
            allowed=[]
            for oid in set(bound.get('contributing_owner_ids') or bound.get('owner_ids') or []) & set(unit.get('owner_ids') or []):
                owner=owner_index.get(oid) or {}
                scope=set(owner.get('owner_scope_block_ids') or owner.get('member_block_ids') or [])
                if bid in scope and owner.get('page_id')==page['page_id']:allowed.append(oid)
            env=[e for e in envelopes if e.get('identity_unit_id')==uid and bid in e.get('source_block_ids',[]) and e.get('owner_id') in allowed]
            if not allowed or not env:raise ValueError('NO_CURRENT_OWNER_ENVELOPE')
            if bound.get('source_url')!=page.get('url') or bound.get('text')!=block.get('text'):raise ValueError('SOURCE_CONTENT_OR_URL_MISMATCH')
            if claim.get('source_url') and claim['source_url']!=page.get('url'):raise ValueError('CLAIM_SOURCE_URL_MISMATCH')
            observed=index.observe(page,block)
            if observed.get('source_structure_status')!='RESOLVED' or bound.get('source_hash')!=observed.get('source_hash'):raise ValueError('FROZEN_SOURCE_UNVERIFIED')
            table=observed.get('table_context')
            if not table or table.get('structure_status')!='OBSERVED':raise ValueError('MISSING_TABLE_RELATION')
            cells=table['cells']
            names=[c for c in cells if c.get('header_status') in {'EXPLICIT','OBSERVED_GRID'} and re.fullmatch(r'(?:sql (?:data\s*)?type|data\s*type|type name|name|type)',' '.join(_norm(h['text']) for h in c.get('headers',[]))) and _norm(c['text'])==_norm(name)]
            if len(names)!=1:raise ValueError('ROW_SUBJECT_UNPROVEN')
            candidates=[c for c in cells if c.get('cell_index')==loc.get('cell_index') and c.get('dom_locator')==loc.get('dom_locator')]
            if len(candidates)!=1:raise ValueError('CELL_LOCATOR_MISMATCH')
            cell=candidates[0]
            if cell.get('header_status') not in {'EXPLICIT','OBSERVED_GRID'} or cell['text']!=claim.get('text'):raise ValueError('CELL_TEXT_OR_HEADER_MISMATCH')
            expected=[h['dom_locator'] for h in cell['headers']]
            if loc.get('header_locators')!=expected:raise ValueError('HEADER_LOCATOR_MISMATCH')
            if field=='datatype_name' and cell!=names[0]:raise ValueError('NAME_IS_NOT_SUBJECT_CELL')
            if field=='notes' and not re.fullmatch(r'(?:notes?|remarks?|limitations?)',' '.join(_norm(h['text']) for h in cell['headers'])):raise ValueError('NOT_A_NOTES_COLUMN')
            if field=='datatype_mapping' and cell==names[0]:raise ValueError('MAPPING_TARGET_IS_SOURCE_CELL')
            proofs.append({'identity_unit_id':uid,'owner_ids':sorted(allowed),'evidence_ids':sorted(e['evidence_id'] for e in env),
                'block_id':bid,'page_id':page['page_id'],'source_url':page['url'],'source_hash':observed['source_hash'],
                'document_id':page.get('document_id'),'html_path':page.get('html_path'),'row_locator':table['row_id'],
                'table_locator':table['table_locator'],'source_cell':copy.deepcopy(names[0]),'value_cell':copy.deepcopy(cell)})
        if set(refs)!=set(p['block_id'] for p in proofs):raise ValueError('UNVERIFIED_ADDITIONAL_REFERENCE')
        return proofs

    for field,target in (('datatype_name','definition_names'),('datatype_mapping','mappings'),('notes','notes')):
        claims=extension.get(field,[]) if field!='notes' else payload.get('notes',[])
        if not isinstance(claims,list):claims=[claims]
        for claim in claims:
            try:
                if not isinstance(claim,Mapping):raise ValueError('INVALID_EXTENSION_CLAIM')
                evidence=proof(claim,field)
                item={'text':claim['text'],'evidence_refs':copy.deepcopy(claim['evidence_refs']),
                      'source_locations':copy.deepcopy(claim['source_locations']),'provenance':evidence,'status':'VERIFIED'}
                if field=='datatype_mapping':
                    relations={relation_kind(p['source_cell']['headers'],p['value_cell']['headers']) for p in evidence}
                    relation=next(iter(relations)) if len(relations)==1 else 'UNKNOWN_RELATION'
                    if relation=='UNKNOWN_RELATION':
                        reject(field,claim,'UNKNOWN_MAPPING_DIRECTION');continue
                    item.update(relation_kind=relation,source_type=evidence[0]['source_cell']['text'],target_type=claim['text'])
                knowledge[target].append(item)
            except ValueError as exc:reject(field,claim,str(exc))
    # Unknown defaults and table footnote applicability stay observations, never SQL defaults.
    for value in extension.get('uncertain_fields') or []:
        knowledge['uncertain_observations'].append({'status':'UNCERTAIN','observation':copy.deepcopy(value),'knowledge_asserted':False})
    validate_datatype_extensions(result)
    return result

def validate_datatype_extensions(extension):
    if extension.get('schema_version')!=VERSION:raise ValueError('unsupported Canonical semantic extension version')
    dt=extension.get('datatype')
    if not isinstance(dt,Mapping):raise ValueError('datatype extension must be an object')
    for field in ('definition_names','mappings','notes'):
        if not isinstance(dt.get(field),list):raise ValueError('datatype '+field+' must be a list')
        for item in dt[field]:
            if item.get('status')!='VERIFIED' or not item.get('evidence_refs') or not item.get('provenance'):raise ValueError('unverified datatype knowledge')
            for p in item['provenance']:
                if (p.get('block_id') not in item['evidence_refs'] or not p.get('owner_ids') or not p.get('evidence_ids') or not p.get('source_hash') or not p.get('source_url')):raise ValueError('datatype provenance incomplete')
                if p['value_cell']['text']!=item['text']:raise ValueError('datatype value conflicts with cell')
                if field=='mappings' and (item.get('relation_kind') not in RELATIONS-{'UNKNOWN_RELATION'} or item['relation_kind']!=relation_kind(p['source_cell']['headers'],p['value_cell']['headers']) or item.get('target_type')!=p['value_cell']['text'] or item.get('source_type')!=p['source_cell']['text']):raise ValueError('invalid mapping direction or endpoints')
    if any(x.get('status')!='UNCERTAIN' or x.get('knowledge_asserted') is not False for x in dt.get('uncertain_observations',[])):raise ValueError('uncertain observation promoted to knowledge')
    if 'sql_default_value' in dt:raise ValueError('SQL default is not part of this contract')
