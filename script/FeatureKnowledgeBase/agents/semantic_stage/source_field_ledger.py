"""Physical field accounting independent of the semantic extraction algorithm.

Only explicit Profile slots and observed field labels create expectations.
UNKNOWN remains unknown; this does not certify official inventory coverage.
"""
from agents.owner.profile_execution import field_candidates
import re
from agents.contracts.field_validation import claim_value

ROLES={'DESCRIPTION':'description','FORMAL_SYNTAX':'formal_syntax','EXAMPLE':'official_examples',
       'PARAMETER':'parameters','RETURN_TYPE':'return_type'}
LABELS={'description':'description','syntax':'formal_syntax','signature':'formal_syntax',
        'example':'official_examples','examples':'official_examples','parameters':'parameters',
        'arguments':'parameters','return type':'return_type'}


def normalize_for_match(value):
    # Audit oracle: literal characters and case remain significant. Do not
    # reuse the extractor/grounding's equivalence policy to certify itself.
    return re.sub(r'\s+',' ',str(value or '')).strip()


def is_label_only(text):
    return str(text or '').strip().rstrip(':').strip().casefold() in LABELS


def field_values(value):
    values=value if isinstance(value,list) else [value] if value else []
    return [claim_value(v)+' '+str(v.get('description') or '') for v in values if isinstance(v,dict)]


def audit_source_fields(bundle,semantic,final_records):
    blocks={b['block_id']:b for b in bundle.get('source_blocks',[])}
    expected=[];controlled=set()
    for u in bundle.get('definition_units',[]):
        if (u.get('structure_execution') or {}).get('schema_version')!='StructureExecution/v2':continue
        candidates,_=field_candidates(u,blocks,bundle.get('normalized_identity',''))
        for c in candidates:
            if c['role'] in ROLES:
                expected.append({'block_id':c['block_id'],'field':ROLES[c['role']],'raw_text':c['value'],
                                 'basis':'PROFILE_PHYSICAL_SLOT','source_locator':c.get('source_locator'),
                                 'review_only':c.get('sharing') not in {'SUBJECT','DECLARED_MEMBERS'}})
                controlled.add(c['block_id'])
    for b in blocks.values():
        if b['block_id'] in controlled or b.get('block_kind')=='HEADING':continue
        labels=(b.get('observed_context') or {}).get('labels',[])
        fields={LABELS[str(l.get('text','')).strip().rstrip(':').casefold()] for l in labels
                if str(l.get('text','')).strip().rstrip(':').casefold() in LABELS}
        if is_label_only(b.get('text')):
            expected.append({'block_id':b['block_id'],'field':None,'raw_text':b['text'],
                             'basis':'FIELD_LABEL_ONLY','source_locator':b.get('dom_path')})
        elif len(fields)==1 and b.get('source_structure_status')=='RESOLVED':
            expected.append({'block_id':b['block_id'],'field':next(iter(fields)),'raw_text':b['text'],
                             'basis':'OBSERVED_FIELD_LABEL','source_locator':b.get('dom_path')})
        else:
            expected.append({'block_id':b['block_id'],'field':None,'raw_text':b['text'],
                             'basis':'UNKNOWN_SOURCE_ROLE','source_locator':b.get('dom_path')})
    uid=bundle['identity_group_id'];retained=[r for r in final_records if r['provenance']['identity_unit_id']==uid
        or uid in {v['identity_unit_id'] for v in r.get('structural',{}).get('attributes',{}).get('canonical_representation_lineage',[])}]
    mapping={'description':'description','formal_syntax':'syntax','official_examples':'examples',
             'parameters':'parameters','return_type':'return_type'}
    result=[]
    for row in expected:
        field=row['field'];value=normalize_for_match(row['raw_text'])
        captured=bool(field and value and any(value in normalize_for_match(v) for v in field_values(semantic.get(field))))
        final=bool(field and value and any(value in normalize_for_match(v) for r in retained
            for v in field_values(r.get('semantics',{}).get(mapping[field]))))
        state=('UNKNOWN' if not field else 'FIELD_REVIEW' if row.get('review_only') else
               'NOT_PUBLISHED' if not retained else 'MISSING_FROM_SEMANTIC' if not captured else
               'CAPTURED' if final else 'MISSING_FROM_FINAL')
        result.append({**row,'identity_unit_id':uid,'source_url':bundle.get('source_urls',[]),
                       'semantic_captured':captured,'final_captured':final,'disposition':state})
    return result
