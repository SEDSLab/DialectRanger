"""Shared lexical and compound-cell observations, never a feature inventory.

Input must come from the source reader. Multiple declarations in a compound
cell retain separate members; only the current subject's syntax/examples may
be emitted. Shared prose retains explicit co-definition membership.
"""
import re

SQL_IDENTIFIER = r'(?:[A-Za-z_][\w$]*|"(?:[^"]|"")+")'
QUALIFIED_IDENTIFIER = SQL_IDENTIFIER + r'(?:\s*\.\s*' + SQL_IDENTIFIER + r'){0,2}'
CALLABLE_HEAD = re.compile(r'^\s*(?P<odbc>\{fn\s+)?(?P<name>' + QUALIFIED_IDENTIFIER + r')\s*\(', re.I)


def declaration_identity(value):
    """Normalize unquoted identifier parts; retain quoted identifier case."""
    parts = re.findall(SQL_IDENTIFIER, str(value))
    return '.'.join(p if p.startswith('"') else p.upper() for p in parts)


def identifier_key(value):
    value = ' '.join(str(value or '').split())
    return value if '"' in value else value.upper()


def declaration_names(value):
    """Unquoted SQL type spellings; retain original values elsewhere.

    Strip balanced parameter slots, not arbitrary prose. Optional words remain
    unresolved rather than being expanded into invented declarations.
    """
    result = []
    # Bracketed optional grammar slots are not part of the base identity.
    # This parser only consumes a proven type-name slot, never prose.
    value = str(value).strip()
    previous = None
    while value != previous:
        previous = value
        value = re.sub(r'\[[^\[\]]*\]', '', value)
    for part in re.split(r',\s*(?![^()]*\))|\s+or\s+', value):
        part = re.sub(r'\([^()]*\)', '', part).strip()
        if re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*(?:\s+[A-Za-z_][A-Za-z_0-9]*){0,5}', part):
            result.append(identifier_key(part))
    return list(dict.fromkeys(result))


def header_text(cell):
    return ' '.join(str(h.get('text') or '').strip().casefold() for h in cell.get('headers') or [])


def compound_header_role(header, feature_type):
    """Explicit composite columns may declare both functions and operators.

    The header grants a possible slot, not its type; a local signature or
    expression must still establish the actual subject below.
    """
    match=re.fullmatch(r'(function|operator)(?:\s*(?:/|and|or|&)\s*(function|operator))?\s+'
                      r'description(?:\s+examples?\s*\(?s?\)?)?',str(header).strip(),re.I)
    return bool(match and str(feature_type).casefold() in {v.casefold() for v in match.groups() if v})


def callable_signature(text):
    """A complete leading declaration, not a call mentioned inside prose."""
    match = CALLABLE_HEAD.match(text)
    if not match:
        return None
    depth = 1
    pos = match.end()
    while pos < len(text) and depth:
        if text[pos] == '(':
            depth += 1
        elif text[pos] == ')':
            depth -= 1
        pos += 1
    if depth:
        return None
    tail = text[pos:].strip()
    if match.group('odbc'):
        if tail != '}':return None
        tail = ''
    return_type=''
    end=pos
    if tail:
        suffix,arrow,return_type=tail.rpartition('→')
        if not arrow:
            suffix,return_type=tail,''
        if ((arrow and not re.fullmatch(r'\s*[A-Za-z_][\w\s.\[\](),]*',return_type))
                or (suffix.strip() and not re.fullmatch(
                    r'\s*(?:(?:WITHIN\s+GROUP|OVER|FILTER)\s*\([^;]*\)\s*)+',suffix,re.I))):return None
        end=len(text)-len(return_type)-1 if arrow else len(text)
    # Literal invocation/result pairs are examples, not formal declarations.
    if re.search(r"'|\b\d+\b", re.sub(r'"(?:[^"]|"")*"','Q',text[:end])):
        return None
    return declaration_identity(match.group('name')), (text.strip() if match.group('odbc') else text[:end].strip()), return_type.strip()


def observed_callable_declaration(text, inline_nodes=(), *, formal_marker=False):
    """Use observable declaration markup for literal SQL grammar alternatives.

    A quoted grammar terminal is not necessarily an example argument. Only
    explicitly marked formal syntax, or a nominal function token together with
    replaceable metavariables, can take this path. Plain call mentions cannot.
    """
    # A native signature term can contain a return annotation and permalink.
    # Its structural markup, never a name list, identifies the callable span.
    signature_markup=any(set(n.get('semantic_classes',[])) & {'sig-name','sig-param','sig-paren'} for n in inline_nodes)
    if signature_markup:
        value=text.split('→',1)[0].rstrip().rstrip('#¶').rstrip()
        parsed=callable_signature(value)
        if parsed:return parsed
    parsed=callable_signature(text)
    if parsed:return parsed
    nominal=any(n.get('element_name')=='code' and 'function' in n.get('semantic_classes',[])
                and re.match(r'^\s*'+re.escape(str(n.get('text') or ''))+r'\s*\(',text,re.I)
                for n in inline_nodes if n.get('text'))
    replaceable=any('replaceable' in n.get('semantic_classes',[]) for n in inline_nodes)
    if not (formal_marker or (nominal and replaceable)):return None
    from agents.owner.surface_patterns import formal_signatures
    value=text;returns=''
    if '→' in text:
        value,returns=text.rsplit('→',1)
        if not re.fullmatch(r'\s*[A-Za-z_][\w\s.\[\](),]*',returns):return None
    values=formal_signatures(value.strip())
    return (values[0][0],values[0][1],returns.strip()) if len(values)==1 else None


def compound_entry(block, identity, feature_type):
    """Return source fragments of one typed compound definition cell."""
    if (block.get('block_kind') != 'TABLE_ROW'
            or block.get('source_structure_status') != 'RESOLVED'
            or not block.get('source_url') or not block.get('source_hash')
            or (block.get('observed_context') or {}).get('feature_domain') in {'EMBEDDED_QUERY_LANGUAGE','DEVELOPER_API'}):
        return None
    cells = (block.get('table_context') or {}).get('cells') or []
    subject = identifier_key(identity)
    for cell in cells:
        header = header_text(cell)
        if cell.get('header_role') == 'COLUMN_HEADER' or cell.get('nested_table'):
            continue
        if not compound_header_role(header,feature_type):
            continue
        fragments = cell.get('fragments') or []
        has_signature_markup=any('func_signature' in f.get('source_classes',[]) for f in fragments)
        signatures = []
        for f in fragments:
            if not f.get('leading_code') or f.get('nested_section'):
                continue
            if has_signature_markup and 'func_signature' not in f.get('source_classes',[]):
                continue
            text = f['text']
            if feature_type == 'FUNCTION':
                parsed = observed_callable_declaration(text,formal_marker=(
                    'func_signature' in f.get('source_classes',[])))
            else:
                # Operands are source metavariables, never names from a dictionary.
                m = re.fullmatch(r'(?:[a-z][\w]*(?:\([^)]*\))?\s+)?([!<>=~%&|^+*/-]+)\s*'
                                 r'(?:[a-z][\w]*(?:\([^)]*\))?)?\s*(?:→\s*[a-z][\w\s\[\]]*)?', text)
                parsed = (m.group(1), text.split('→')[0].strip(), '') if m else None
            if parsed:
                signatures.append((f, parsed))
        if not signatures or subject not in {p[0] for _, p in signatures}:
            continue
        if len({p[0] for _,p in signatures})>1:
            # Co-declarations must form a leading declaration group. A new
            # unrelated head introduced after prose is another local section,
            # not permission to share that earlier subject's description.
            last=max(fragments.index(f) for f,_ in signatures)
            if any(f.get('node_kind')=='P' and not any(f is sf for sf,_ in signatures)
                   for f in fragments[:last]):
                continue
        signature_paths = {f['dom_locator'] for f, p in signatures if p[0]==subject}
        last_signature = max(fragments.index(f) for f, _ in signatures)
        roles = []
        for i, f in enumerate(fragments):
            parsed = next((p for sf, p in signatures if sf['dom_locator'] == f['dom_locator']), None)
            role = 'UNRESOLVED'
            value = f['text']
            if parsed:
                role, value = ('FORMAL_SYNTAX' if parsed[0]==subject else 'IDENTITY_VARIANT'), parsed[1]
            elif f.get('leading_code') and '→' in value and i>last_signature:
                expression, output = value.rsplit('→', 1)
                operator_expression=re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"",' ',expression)
                own = (re.match(r'^\s*' + re.escape(identity) + r'\s*\(', expression, re.I)
                       if feature_type == 'FUNCTION' else re.search(
                           r'(?<![!<>=~%&|^+*/@#?\-])'+re.escape(subject)+
                           r'(?![!<>=~%&|^+*/@#?\-])',operator_expression))
                literal_argument = bool(re.search(r"'(?:[^']|'')*'|\b\d+\b|\b(?:true|false|NULL)\b",expression,re.I))
                no_arguments=bool(re.fullmatch(r'\s*'+re.escape(identity)+r'\s*\(\s*\)\s*',expression,re.I))
                if own and output.strip() and (literal_argument or no_arguments):
                    role = 'EXAMPLE'
            elif (f.get('node_kind') == 'PRE' and i > last_signature and not f.get('nested_section')
                  and (re.search(r'\bfor example\s*[,.:]?\s*$', fragments[i-1]['text'], re.I)
                       or re.fullmatch(r'\s*For example\b[^;]*:\s*',fragments[i-1]['text'],re.I))
                  and re.match(r'^\s*(?:SELECT|VALUES|WITH|CALL)\b', value, re.I)):
                sql = re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|--[^\n]*|/\*.*?\*/",' ',value,flags=re.S)
                if (re.search(r'(?<![\w$])'+re.escape(identity)+r'\s*\(',sql,re.I)
                        if feature_type == 'FUNCTION' else re.search(re.escape(identity),sql)):
                    role = 'EXAMPLE'
            elif (i > last_signature and f.get('node_kind') == 'P'
                  and (not f.get('leading_code') or (f.get('inline_code') and '→' not in value
                       and ' '.join(c['text'] for c in f['inline_code']).strip()!=value.strip()))
                  and not f.get('nested_section')
                  and not re.match(r'^(?:Note|Caution|Warning|Examples?)\s*:', value, re.I)):
                role = 'DESCRIPTION'
            roles.append({**f, 'role': role, 'value': value,
                          'applies_to':[subject] if role in {'FORMAL_SYNTAX','EXAMPLE'} else sorted({p[0] for _,p in signatures}),
                          'return_type': parsed[2] if parsed and parsed[0]==subject else ''})
        # An explicit result arrow may introduce a separate PRE result block.
        # Adjacency alone is insufficient: both parts must be in this verified
        # cell, after its declarations, with an exact current-subject call.
        for i, f in enumerate(roles[:-1]):
            following=roles[i+1]
            if (i<=last_signature or f['role']!='UNRESOLVED' or not f.get('leading_code')
                    or not f['text'].rstrip().endswith('→') or f.get('nested_section')
                    or following.get('nested_section') or following.get('node_kind')!='PRE'
                    or not following.get('text','').strip()
                    or re.match(r'^\s*(?:SELECT|WITH|VALUES|CALL|INSERT|UPDATE|DELETE|CREATE|ALTER)\b',following['text'],re.I)):
                continue
            expression=f['text'].rsplit('→',1)[0]
            sql=re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|--[^\n]*|/\*.*?\*/",' ',expression,flags=re.S)
            own=(re.search(r'(?<![\w$])'+re.escape(identity)+r'\s*\(',sql,re.I)
                 if feature_type=='FUNCTION' else re.search(
                     r'(?<![!<>=~%&|^+*/@#?\-])'+re.escape(subject)+r'(?![!<>=~%&|^+*/@#?\-])',sql))
            if not own:continue
            f.update(role='EXAMPLE',applies_to=[subject],
                     result_locator=following['dom_locator'])
            following.update(role='EXAMPLE_RESULT',applies_to=[subject],
                             invocation_locator=f['dom_locator'])
        return {'version': 'compound-definition-entry/v1', 'subject': subject,
                'feature_type': feature_type, 'cell_locator': cell['dom_locator'],
                'parent_block_id': block.get('block_id'), 'fragments': roles,
                'signature_locators': sorted(signature_paths)}
    return None


def verified_entry_child(unit, block, owner, document, reader):
    """Revalidate a projected child without mutating the original Owner.

    Parent membership, child DOM occurrence, current source bytes and exact
    subject all have to agree. A same-page text match is insufficient.
    """
    member = block.get('definition_entry_member') or {}
    parent_id = member.get('parent_block_id')
    if not parent_id or parent_id not in owner.get('member_block_ids', []):
        return False
    if (owner.get('page_id') != document.get('page_id')
            or block.get('source_hash') != document.get('page_hash')
            or block.get('source_url') != document.get('url')):
        return False
    byid = {b['block_id']: b for b in document.get('blocks', [])}
    raw = byid.get(block.get('block_id'))
    parent = byid.get(parent_id)
    if not raw or not parent or raw.get('text') != block.get('text') or raw.get('dom_path') != block.get('dom_path'):
        return False
    observed = reader.observe(document, parent)
    entry = compound_entry({**parent, **observed, 'source_url': document.get('url')},
                           unit.get('canonical_identity'), str(unit.get('feature_type') or '').upper())
    return bool(entry and any(f['dom_locator'] == raw.get('dom_path') and f['text'] == raw.get('text')
                              for f in entry['fragments']))
