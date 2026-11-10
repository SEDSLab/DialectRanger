"""Account for verified source slots and accepted claims through publication."""
from agents.contracts.field_evidence import build_field_evidence,comparable
from agents.contracts.field_validation import FIELDS,claim_value


def audit_field_dispositions(bundles,semantic,final_records):
    si={s['identity_group_id']:s for s in semantic};out=[]
    for b in bundles:
        uid=b['identity_group_id'];s=si.get(uid,{})
        final=[r for r in final_records if uid==r['provenance']['identity_unit_id'] or uid in
            {v['identity_unit_id'] for v in r.get('structural',{}).get('attributes',{}).get('canonical_representation_lineage',[])}]
        def claims(row,canonical=False):
            for destination,field in FIELDS.items():
                values=row.get(destination if canonical else field) or []
                for c in values if isinstance(values,list) else [values]:
                    if isinstance(c,dict):yield field,c
        accepted=list(claims(s));retained=[(f,c) for r in final for f,c in claims(r.get('semantics',{}),True)]
        for fragment in build_field_evidence(b):
            fid=fragment['fragment_id'];matches=[c for f,c in accepted if fid in c.get('fragment_refs',[])]
            kept=[c for f,c in retained if fid in c.get('fragment_refs',[])]
            disposition=('UNRESOLVED_ROLE' if fragment['proof_level']!='VERIFIED' else
                'PUBLISHED' if kept else 'FEATURE_REVIEW' if matches and not final else
                'PROJECTION_GAP' if matches else 'NOT_EXECUTED' if not s or s.get('semantic_status')=='NOT_RUN' else 'FIELD_REVIEW')
            rejections=[r for r in s.get('rejected_fields',[]) if fragment['block_id'] in r.get('evidence_refs',[])]
            reviews=[r for r in s.get('field_reviews',[]) if fragment['block_id'] in r.get('evidence_refs',[])]
            reasons=sorted({str(r.get('reason') or r.get('status')) for r in rejections+reviews})
            if not matches:
                reasons=sorted(set(reasons)|{r['reason'] for r in s.get('field_consumption',[])
                    if r.get('fragment_id')==fid and r.get('reason')})
            if not reasons:
                reasons=[{'PUBLISHED':'ACCEPTED_FIELD_RETAINED','FEATURE_REVIEW':'SUBJECT_NOT_PUBLISHED',
                    'PROJECTION_GAP':'ACCEPTED_FIELD_NOT_PROJECTED','NOT_EXECUTED':'SEMANTIC_NOT_EXECUTED',
                    'UNRESOLVED_ROLE':'NO_VERIFIED_FIELD_ROLE','FIELD_REVIEW':'VERIFIED_FIELD_NOT_ACCEPTED'}.get(disposition,disposition)]
            out.append({'identity_unit_id':uid,'fragment_id':fid,'block_id':fragment['block_id'],
                'source_url':fragment['source_url'],'field':fragment['field'],'raw_text':fragment['raw_text'],
                'proof_level':fragment['proof_level'],'disposition':disposition,'reason_codes':reasons,
                'full_fragment_retained':any(comparable(fragment['raw_text']) in comparable(claim_value(c)) for c in kept),
                'published_feature_ids':[r['feature_id'] for r in final]})
        # Every accepted claim is accounted separately; candidate source roles
        # do not hide a real projection loss.
        for field,c in accepted:
            key=(field,claim_value(c),c.get('fragment_refs',[]))
            present=any((f,claim_value(v),v.get('fragment_refs',[]))==key for f,v in retained)
            out.append({'identity_unit_id':uid,'field':field,'value':claim_value(c),
                'fragment_refs':c.get('fragment_refs',[]),'record_kind':'ACCEPTED_CLAIM',
                'disposition':'PUBLISHED' if present else 'FEATURE_REVIEW' if not final else 'PROJECTION_GAP'})
    return out
