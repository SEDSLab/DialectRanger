"""Consume executable Profile fields without changing their physical scope."""
import re
from agents.owner.profile_execution import field_candidates


def named_parameter(block):
    cells=(block.get('table_context') or {}).get('cells',[])
    valid=[c for c in cells if c.get('header_status') in {'EXPLICIT','OBSERVED_GRID'} and not c.get('nested_table')]
    names=[c for c in valid if any(str(h.get('text') or '').strip().casefold() in
        {'parameter','argument','parameter name','argument name'} for h in c.get('headers',[]))]
    descriptions=[c for c in valid if any(str(h.get('text') or '').strip().casefold()=='description' for h in c.get('headers',[]))]
    if len(names)!=1 or len(descriptions)!=1:return None
    return {'name':names[0]['text'],'description':descriptions[0]['text'],
            'evidence_refs':[block.get('evidence_ref') or block['block_id']]}


def owner_sql_application(block,value):
    """Official context examples need not repeat the feature's name.

    This is callable only after a complete subject-local unit was verified.
    It recognizes actual SQL commands, not prose or arbitrary CLI output.
    """
    from agents.semantic_stage.structured_semantics import _sql_shape,_commented_example_shape
    if _sql_shape(value) or _commented_example_shape(block):return True
    if re.match(r'^\s*SET\s+(?:@+[A-Za-z_][\w$]*|[A-Za-z_][\w$]*)\s*(?:=|:=)',value,re.I):return True
    commands=[]
    for line in value.splitlines():
        m=re.match(r'^\s*[A-Za-z][\w.-]*>\s+(.+)$',line)
        if m:commands.append(m[1])
    return any(re.match(r'^\s*(?:SELECT|WITH|INSERT|UPDATE|DELETE|VALUES|CALL|EXPLAIN|CREATE|ALTER)\b',command,re.I)
               or re.match(r'^SET\s+[@\w]+\s*(?:=|:=)',command,re.I) for command in commands)


def formal_profile_value(value,identity):
    from agents.contracts.identity_key import normalize_identity
    from agents.semantic_stage.structured_semantics import _syntax_shape,_sql_shape
    from agents.contracts.definition_entry import observed_callable_declaration
    if _syntax_shape(value,identity):return True
    parsed=observed_callable_declaration(value,formal_marker=True)
    if parsed and normalize_identity(parsed[0])==normalize_identity(identity) and not _sql_shape(value):return True
    match=re.match(r'^([A-Z][A-Z0-9_]*(?:\s+[A-Z][A-Z0-9_]*)?)\s+('+re.escape(identity)+r'\s*\(.*)$',value,re.I|re.S)
    if not match or match[1]!=match[1].upper() or _sql_shape(value):return False
    parsed=observed_callable_declaration(match[2],formal_marker=True)
    return bool(parsed and normalize_identity(parsed[0])==normalize_identity(identity))


def consume(payload, evidence, identity, feature_type, emit, review, decision, extension):
    from agents.semantic_stage.structured_semantics import _syntax_shape, _cli_example_shape, _sql_shape
    controlled=set()
    audit=[]
    diagnostics=[]
    for unit in payload.get('definition_units',[]):
        if (unit.get('structure_execution') or {}).get('schema_version')!='StructureExecution/v2':continue
        refs=set(unit.get('member_evidence_refs',[]))
        controlled.update(refs)
        candidates,checks=field_candidates(unit,evidence,identity)
        diagnostics.extend(checks)
        from agents.contracts.profile_authority import verified_authority
        authoritative=verified_authority(unit,{'source_blocks':list(evidence.values())},identity)
        paired_results=set()
        for example in candidates:
            if example['role']!='EXAMPLE' or example.get('cell_index') is None or example['sharing']!='SUBJECT':continue
            outputs=[c for c in candidates if c['role']=='EXAMPLE_RESULT' and c['block_id']==example['block_id']
                     and c.get('cell_index')==example['cell_index']+1 and c['sharing']=='SUBJECT']
            if len(outputs)!=1:continue
            output=outputs[0];combined=example['value']+'\n'+output['value']
            if ' '.join(combined.split()) not in ' '.join(evidence[example['block_id']].get('text','').split()):continue
            example.update(value_parts=[example['value'],output['value']],value=combined,
                           result_cell_index=output['cell_index'],result_source_locator=output['source_locator'])
            paired_results.add((output['block_id'],output['binding_index']))
        for item in candidates:
            b=evidence[item['block_id']]
            role=item['role'];value=item['value'];status='REVIEW'
            reason=None
            cell=next((c for c in (b.get('table_context') or {}).get('cells',[])
                       if c['cell_index']==item.get('cell_index')),None)
            if item['sharing']!='SUBJECT':
                reason='PROFILE_FIELD_'+item['sharing']
            elif (not authoritative and role=='FORMAL_SYNTAX' and not formal_profile_value(value,identity)
                  and unit['structure_execution']['field_bindings'][item['binding_index']]['locator']['kind']!='DECLARATION_ANCHORS'):
                reason='PROFILE_SYNTAX_SHAPE_UNPROVEN'
            elif role in {'EXAMPLE','CONSTRUCTION_EXAMPLE','DECLARATION_EXAMPLE'}:
                # Explicit construction/declaration columns are official examples,
                # not automatically formal syntax. Multi-member rows still need
                # a local callable/operator occurrence for ordinary examples.
                ordinary=role=='EXAMPLE'
                if ordinary and not authoritative and feature_type=='OPERATOR':
                    expression=re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"",' ',value)
                    edge=r'[!<>=~%&|^+*/:@?-]' if re.fullmatch(r'[!<>=~%&|^+*/:@?-]+',identity) else r'[\w$]'
                    if not re.search(r'(?<!'+edge+r')'+re.escape(identity)+r'(?!'+edge+r')',expression,re.I):
                        reason='PROFILE_EXAMPLE_OTHER_SURFACE'
                if ordinary and not authoritative and len(unit.get('identity_values',[]))>1 and feature_type=='FUNCTION' and not re.search(
                        r'(?<![\w$])'+re.escape(identity)+r'\s*\(',value,re.I):
                    reason='PROFILE_EXAMPLE_OTHER_MEMBER'
                elif item['binding_index']>=0 and (unit['structure_execution']['field_bindings'][item['binding_index']]['locator']['kind']=='OWNER_SQL_APPLICATION'):
                    if not owner_sql_application(b,value):
                        reason='PROFILE_CODE_APPLICATION_UNPROVEN'
                    elif not re.search(r'(?<![\w$])'+re.escape(identity)+r'\s*\(',value,re.I):
                        item['application_relation']='OWNER_CONTEXT'
                if reason is None:
                    emit('official_examples',b,value,cell);status='CONSUMED'
            elif role=='EXAMPLE_RESULT':
                # Preserve separately in the audit; do not invent an invocation
                # or a combined witness that the source does not contain.
                if (item['block_id'],item['binding_index']) in paired_results:status='CONSUMED'
                else:reason='PROFILE_RESULT_REQUIRES_CONTIGUOUS_INVOCATION'
            elif role=='PARAMETER':
                parameter=named_parameter(b)
                if parameter:
                    extension.setdefault('parameter_claims',[]).append(parameter)
                    status='CONSUMED'
                else:reason='PROFILE_PARAMETER_REQUIRES_NAMED_SCHEMA'
            else:
                target={'DESCRIPTION':'description','FORMAL_SYNTAX':'formal_syntax',
                        'NOTE':'notes','CONSTRAINT':'notes','RETURN_TYPE':'return_type'}.get(role)
                if target:emit(target,b,value,cell);status='CONSUMED'
                else:reason='PROFILE_ROLE_UNSUPPORTED'
            if reason:review(b,role,reason)
            decision(b,role,'EXPLICIT_PROFILE_FIELD_'+status,cell)
            audit.append(dict(item,status=status,reason=reason,source_url=b.get('source_url'),source_hash=b.get('source_hash')))
        mapped={x['block_id'] for x in candidates}
        for bid in sorted(refs-mapped):
            if bid not in evidence:continue
            audit.append({'definition_unit_id':unit['definition_unit_id'],'block_id':bid,
                          'role':'UNKNOWN','status':'UNMAPPED','value':evidence[bid].get('text'),
                          'reason':'NO_CONFIGURED_FIELD_LOCATION'})
    extension['profile_field_consumption']=audit
    extension['profile_field_diagnostics']=diagnostics
    return controlled
