"""Field-level accounting for observable evidence, never official-site recall.

An empty field without a proven expectation is allowed. Unknown source roles
remain reviewable; this audit neither invents content nor grants admission.
"""
import json
from collections import Counter

from agents.semantic_stage.grounding import normalize_for_match

FIELDS=('description','formal_syntax','official_examples','parameters','return_type')


def items(value):
    return value if isinstance(value,list) else [value] if value else []


def text(item):
    return str(item.get('text') or item.get('sql') or item.get('value') or item.get('name') or '')


def compare_fields(expected,actual):
    result={}
    for field in FIELDS:
        wanted=items(expected.get(field));present=items(actual.get(field))
        missing=[]
        for claim in wanted:
            value=normalize_for_match(text(claim))
            if value and not any(value in normalize_for_match(text(c)) for c in present):
                missing.append({'text':text(claim),'evidence_refs':claim.get('evidence_refs',[])})
        result[field]={'expected_claims':len(wanted),'missing_claims':missing,
            'status':'CONTENT_FIELD_GAP' if missing else 'CAPTURED' if wanted else 'NO_PROVEN_EXPECTATION'}
    return result


def audit_semantic_content(prompt,accepted):
    if 'INPUT:\n' not in prompt:
        return {'status':'NOT_EVALUATED','reason':'NO_STRUCTURED_INPUT'}
    try:
        payload=json.loads(prompt.split('INPUT:\n',1)[1].split('\n\nOUTPUT SCHEMA:',1)[0])
    except (ValueError,TypeError):
        return {'status':'NOT_EVALUATED','reason':'INVALID_STRUCTURED_INPUT'}
    if not isinstance(payload,dict) or not isinstance(payload.get('evidence'),list):
        return {'status':'NOT_EVALUATED','reason':'NO_EVIDENCE_ARRAY'}
    from agents.semantic_stage.structured_semantics import extract_structured_semantics
    expected=extract_structured_semantics(payload)
    fields=compare_fields(expected,accepted)
    ext=expected['semantic_extensions']
    unresolved=[r for r in ext['role_decisions'] if r['role'] in {'UNKNOWN','FIELD_REVIEW','UNRESOLVED'}]
    return {'schema_version':'ContentCompleteness/v1',
        'status':'CONTENT_FIELD_GAP' if any(f['missing_claims'] for f in fields.values()) else 'OBSERVED_CONTENT_ACCOUNTED',
        'scope':'observable owner-local evidence only; unknown roles and undiscovered content are not certified',
        'fields':fields,'unresolved_roles':unresolved,
        'compositions':ext.get('content_compositions',[])}


def summary(features):
    audits=[f.get('content_completeness') or {'status':'NOT_EVALUATED'} for f in features]
    return {'scope':'observable owner-local evidence; not official completeness',
        'status_counts':dict(Counter(a['status'] for a in audits)),
        'missing_claims':sum(len(f['missing_claims']) for a in audits for f in a.get('fields',{}).values()),
        'unresolved_roles':sum(len(a.get('unresolved_roles',[])) for a in audits),
        'composed_examples':sum(len(a.get('compositions',[])) for a in audits)}


def audit_final_content(features,records,sense_features=None):
    """Check literal accepted core fields after sense/publication/projection.

    REVIEW is reported as not published, never treated as a missing field or
    silently promoted. Group by the actual lineage ID, not matching names.
    """
    rows=[]
    for feature in features:
        group=feature.get('identity_group_id')
        retained=[r for r in records if (r.get('provenance') or {}).get('identity_unit_id')==group]
        row={'feature_id':feature.get('feature_id'),'identity_group_id':group,'name':feature.get('name')}
        if not retained:
            rows.append(dict(row,status='NOT_PUBLISHED',fields={}));continue
        expected={k:feature.get(k) for k in FIELDS[:3]}
        actual={}
        for field,canonical in [('description','description'),('formal_syntax','syntax'),('official_examples','examples')]:
            actual[field]=[v for r in retained for v in (r.get('semantics') or {}).get(canonical,[])]
        fields={k:v for k,v in compare_fields(expected,actual).items() if k in FIELDS[:3]}
        # Account for explicit, source-resolvable non-native namesake review;
        # never treat a generic review flag as permission to hide field loss.
        from agents.semantic_sense.audit import explicit_application_namesake
        reviews=[q for f in (sense_features or []) if f.get('identity_group_id')==group
                 for q in f.get('field_reviews',[]) if q.get('reason')=='EXPLICIT_APPLICATION_DEFINED_NAMESAKE']
        for field,comparison in fields.items():
            isolated=[];missing=[]
            for claim in comparison['missing_claims']:
                if (feature.get('feature_type')=='FUNCTION'
                    and explicit_application_namesake(claim['text'],feature.get('normalized_identity') or feature.get('name') or '')
                    and any(q.get('field')==field and q.get('raw_text')==claim['text']
                            and q.get('evidence_refs')==claim['evidence_refs'] for q in reviews)):
                    isolated.append(dict(claim,reason='EXPLICIT_APPLICATION_DEFINED_NAMESAKE'))
                else:missing.append(claim)
            comparison['missing_claims']=missing
            if isolated:
                comparison['quarantined_claims']=isolated
                comparison['status']='CONTENT_FIELD_GAP' if missing else 'CAPTURED_WITH_QUARANTINE'
        rows.append(dict(row,status='FINAL_FIELD_GAP' if any(f['missing_claims'] for f in fields.values())
                         else 'ACCEPTED_CONTENT_RETAINED_WITH_QUARANTINE' if any(
                             f.get('quarantined_claims') for f in fields.values()) else 'ACCEPTED_CONTENT_RETAINED',fields=fields))
    return rows


def audit_profile_dispositions(consumption,units,records):
    """Account for explicit source bindings independently of re-extraction.

    Records review/non-publication separately; it never grants publication or
    treats absent official fields as defects. A consumed core field must have
    a visible final disposition, including source-preserving deduplication.
    """
    typed={u['identity_unit_id']:u for u in units}
    role_fields={'DESCRIPTION':'description','FORMAL_SYNTAX':'syntax','EXAMPLE':'examples',
                 'CONSTRUCTION_EXAMPLE':'examples','DECLARATION_EXAMPLE':'examples','EXAMPLE_RESULT':'examples'}
    def names(record):
        values=[record.get('name'),record.get('canonical_name')]
        for a in (record.get('structural') or {}).get('aliases',[]):
            values.append((a.get('name') or a.get('alias') or a.get('value')) if isinstance(a,dict) else a)
        return {str(v).strip().upper()for v in values if v}
    result=[]
    for row in consumption:
        uid=row.get('identity_group_id');unit=typed.get(uid,{})
        item={k:row.get(k)for k in ['identity_group_id','definition_unit_id','block_id','cell_index','role',
                                   'value','source_url','source_hash','reason']}
        if row['status']!='CONSUMED':
            result.append(dict(item,disposition='ISOLATED_REVIEW' if row['status']=='REVIEW' else 'UNMAPPED_SOURCE_RETAINED'));continue
        field=role_fields.get(row['role'])
        if not field:
            result.append(dict(item,disposition='NON_CORE_ROLE_RECORDED'));continue
        targets=[r for r in records if (r.get('provenance') or {}).get('identity_unit_id')==uid or
                 (str(r.get('feature_type')).upper()==str(unit.get('feature_type')).upper()
                  and str(unit.get('canonical_identity')).upper() in names(r))]
        if not targets:
            result.append(dict(item,disposition='NOT_PUBLISHED_REVIEW'));continue
        value=normalize_for_match(str(row.get('value') or ''))
        matches=[c for r in targets for c in r.get('semantics',{}).get(field,[])
                 if value and value in normalize_for_match(text(c))]
        if matches:
            cited=any((c.get('evidence') or {}).get('block_id')==row.get('block_id')for c in matches)
            disposition='FINAL_CAPTURED' if cited else 'DUPLICATE_VALUE_PRIMARY_CITATION_RETAINED'
        else:
            moved=[f for f in ['description','syntax','examples','notes']if f!=field
                   and any(value and value in normalize_for_match(text(c))for r in targets for c in r.get('semantics',{}).get(f,[]))]
            disposition='FINAL_ROLE_CHANGED_REVIEW' if moved else 'FINAL_FIELD_GAP'
        result.append(dict(item,disposition=disposition))
    return result
