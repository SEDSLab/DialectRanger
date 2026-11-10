"""Bind publication to sanitized accepted claims, not aggregate error counts."""
import hashlib
import json

FIELDS={'description':'description','syntax':'formal_syntax','examples':'official_examples',
        'parameters':'parameters','return_type':'return_type','constraints':'notes'}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def claim_value(item):
    return str(item.get('text') or item.get('value') or item.get('sql') or item.get('name') or '').strip()


def claim_key(field,item,refs=None):
    return (field,claim_value(item),tuple(sorted(set(refs if refs is not None else item.get('evidence_refs',[])))),
            str(item.get('name') or '') if field=='parameters' else '',
            str(item.get('description') or '') if field=='parameters' else '',
            digest({k:item[k] for k in ('fragment_refs','source_locations','field_evidence') if k in item}))


def make_field_contract(uid,fields):
    accepted=[]
    for field in FIELDS.values():
        values=fields.get(field) or []
        if not isinstance(values,list):values=[values]
        for item in values:
            if isinstance(item,dict):accepted.append(list(claim_key(field,item)))
    result={'version':'field-isolation/v2','identity_unit_id':uid,'accepted_claims':accepted}
    result['fingerprint']=digest(result)
    return result


def verified_field_contract(value,uid):
    return (isinstance(value,dict) and value.get('version')=='field-isolation/v2'
            and value.get('identity_unit_id')==uid
            and value.get('fingerprint')==digest({k:v for k,v in value.items() if k!='fingerprint'}))


def record_fields_valid(record,validations,admissions):
    uid=record['provenance']['identity_unit_id']
    members={uid}
    lineage=(record.get('structural',{}).get('attributes',{}).get('canonical_representation_lineage') or [])
    if lineage and not record.get('structural',{}).get('attributes',{}).get('canonical_relation_proof'):return False
    for entry in lineage:
        other=entry.get('identity_unit_id')
        if admissions.get(other,{}).get('decision') not in {'ADMITTED','ADMITTED_PARTIAL'}:return False
        from agents.contracts.identity_key import normalize_identity
        source=admissions[other]
        if normalize_identity(source.get('identity_credibility',{}).get('identity'))!=normalize_identity(record.get('name')):return False
        if source.get('publication_integrity',{}).get('type_hypothesis','').casefold()!=record.get('feature_type','').casefold():return False
        members.add(other)
    allowed=set()
    for member in members:
        contract=validations.get(member,{}).get('field_isolation')
        if not verified_field_contract(contract,member):return False
        for c in contract['accepted_claims']:
            allowed.add((c[0],c[1],tuple(c[2]),*c[3:]))
    for field,claims in record.get('semantics',{}).items():
        if field not in FIELDS:continue
        for claim in claims:
            refs=claim.get('evidence_refs') or [claim.get('evidence',{}).get('block_id')]
            if None in refs or claim_key(FIELDS[field],claim,refs) not in allowed:return False
    return True
