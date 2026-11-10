"""Whole local definition surfaces; no DBMS vocabulary or URL knowledge."""
import re


def formal_signatures(value):
    """Return literal (head, span) pairs, including balanced optional suffixes.

    Accept whole declarations only, not SQL statements or a call in prose.
    Commas/pipes may separate repeated declarations. Parentheses and optional
    groups are balanced so nested arguments never silently truncate syntax.
    """
    text=str(value or '').strip()
    if not text or ';' in text or '\n' in text:
        return []
    position=0;result=[]

    def group(start):
        stack=[];quote=None;i=start
        pairs={')':'(',']':'['}
        while i<len(text):
            ch=text[i]
            if quote:
                if ch==quote:
                    if i+1<len(text) and text[i+1]==quote:i+=1
                    else:quote=None
            elif ch in "'\"`":quote=ch
            elif ch in '([':stack.append(ch)
            elif ch in ')]':
                if not stack or stack.pop()!=pairs[ch]:return None
                if not stack:return i+1
            i+=1
        return None

    while position<len(text):
        match=re.match(r'([A-Za-z_][A-Za-z0-9_$]*)\s*\(',text[position:])
        if not match:return []
        start=position;head=match.group(1).upper()
        end=group(position+match.end()-1)
        if end is None:return []
        while True:
            following=end
            while following<len(text) and text[following].isspace():following+=1
            if following<len(text) and text[following]=='[':
                end=group(following)
                if end is None:return []
            else:break
        result.append((head,text[start:end]))
        position=following
        if position==len(text):break
        if text[position] not in ',|/':return []
        position+=1
        while position<len(text) and text[position].isspace():position+=1
        if position==len(text):return []
    return result


def operator_label(value):
    """A whole symbolic/uppercase label or metavariable operator expression."""
    text=str(value or '').strip()
    symbol=r'[+*/%<>=!|&^~?:-]{1,8}'
    word=r'[A-Z][A-Z_]*(?:\s+[A-Z][A-Z_]*){0,2}'
    if re.fullmatch(symbol,text):return text,False
    infix=re.fullmatch(r'[A-Za-z_][\w]*\s+('+symbol+'|'+word+r')\s+[A-Za-z_][\w]*',text)
    if infix:return infix.group(1),True
    if re.fullmatch(word,text):return text,False
    return None


def datatype_definition_name(value):
    """An explicit positive self-definition sentence, not a mere mention."""
    match=re.match(r'^(?:The\s+)?([A-Z][A-Z0-9_]*(?:\s+[A-Z][A-Z0-9_]*){0,2})\s+'
        r'(?:data\s+)?type\s+(?:is\s+(?!not\b)|stores\b|represents\b|holds\b)',str(value or '').strip())
    return match.group(1) if match and match.group(1) not in {'A','AN','THE'} else None


def native_definition_coverage(units,owners,documents=()):
    """Index exact native scope citations already materialized as identities."""
    owner_by_id={str(o.get('owner_id')):o for o in owners};covered={}
    blocks={b['block_id']:b for d in documents for b in d.get('blocks',[])}
    for unit in units:
        if documents and unit.get('adapted_identity_kind'):continue
        name=' '.join(str(unit.get('canonical_identity') or '').split()).upper()
        occurrences={r.get('source_block_id') for r in unit.get('occurrence_records') or []
            if r.get('defining') and r.get('owner_local_binding') is True}
        sections={r.get('definition_section_id') for r in unit.get('occurrence_records') or []
                  if r.get('owner_local_binding') is not False and r.get('definition_section_id')}
        for oid in unit.get('owner_ids') or []:
            owner=owner_by_id.get(str(oid),{})
            cited_members=set(owner.get('member_block_ids') or [])
            for bid in cited_members:
                block=blocks.get(bid,{})
                local=block.get('definition_context') or {}
                same_native_subject=bid in occurrences
                if (same_native_subject and local.get('role') in {'OPERATOR_CATALOG','DECLARATION_CATALOG'}
                        and local.get('domain')=='DOCUMENT_FEATURE'
                        and any(m.get('identity')==name and m.get('feature_type','OPERATOR')=='OPERATOR'
                                for m in local.get('members',[]))):
                    covered[(str(owner.get('page_id')),str(bid),name,'OPERATOR')]=unit['identity_unit_id']
            for proof in owner.get('definition_section_proofs') or []:
                kind=proof.get('feature_type')
                if (kind in {'OPERATOR','DATATYPE'} and proof.get('section_id') in sections
                        and name in proof.get('identity_values',[])):
                    for bid in proof['block_ids']:
                        covered[(str(owner.get('page_id')),str(bid),name,kind)]=unit['identity_unit_id']
    return covered
