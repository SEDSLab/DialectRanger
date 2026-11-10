"""Explicit source/profile/owner contracts for offline evidence reuse."""
from pathlib import Path
import hashlib
from agents.contracts.field_validation import digest

VERSION='evidence-reuse/v2'


def reuse_contracts(groups,owners,documents,profile):
    agents=Path(__file__).resolve().parents[1]
    producers=['contracts/definition_unit.py','owner/structure_execution.py','owner/profile_execution.py',
               'semantic_stage/evidence_bundle.py','semantic_stage/source_structure.py','contracts/identity_key.py',
               'contracts/source_binding.py','contracts/evidence_cache.py','contracts/field_evidence.py']
    implementation={p:hashlib.sha256((agents/p).read_bytes()).hexdigest() for p in producers}
    source={}
    for d in documents:
        p=Path(d.get('html_path') or '')
        source[d['page_id']]={'page_hash':d.get('page_hash'),'url':d.get('url'),
            'actual_hash':hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else 'SOURCE_UNAVAILABLE',
            'ir_hash':digest(d.get('blocks',[]))}
    owner={o['owner_id']:digest(o) for o in owners}
    page_by_owner={o['owner_id']:o.get('page_id') for o in owners}
    result={}
    for g in groups:
        ids=sorted(g.get('owner_ids',[]))
        context={'version':VERSION,'identity_unit_id':g['identity_unit_id'],
            'identity_hash':digest(g),'profile_hash':digest(profile),
            'implementation_hash':digest(implementation),
            'owners':{i:owner.get(i) for i in ids},
            'sources':source}
        context['fingerprint']=digest(context);result[g['identity_unit_id']]=context
    return result


def seal_bundle(bundle,context):
    from agents.contracts.field_evidence import build_field_evidence
    bundle['field_evidence']=build_field_evidence(bundle)
    bundle['reuse_contract']={'context':context,'content_hash':digest({k:v for k,v in bundle.items() if k!='reuse_contract'})}
    return bundle


def reusable(bundle,context):
    contract=bundle.get('reuse_contract') or {}
    return (contract.get('context')==context and context is not None
        and all(s and s.get('actual_hash')!='SOURCE_UNAVAILABLE' for s in context.get('sources',{}).values())
        and contract.get('content_hash')==digest({k:v for k,v in bundle.items() if k!='reuse_contract'}))
