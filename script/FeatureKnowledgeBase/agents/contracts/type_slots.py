"""Source-local type-slot roles shared by Type and publication verification."""
import re
from agents.contracts.definition_entry import declaration_names, identifier_key


def datatype_usage_slot(block, subject):
    """A column/argument's type names a reference, not a datatype definition.

    Keep the occurrence for audit. This is positive structural counterevidence,
    not a page blacklist or a negative vote against the real type elsewhere.
    """
    metadata=block.get('table_metadata') or {}
    headers=metadata.get('headers') or block.get('table_headers') or []
    cells=metadata.get('cells') or block.get('cells') or []
    name_slots=[i for i,h in enumerate(headers) if re.fullmatch(
        r'\s*(?:column|field|parameter|argument|attribute|result)(?:\s+name)?\s*',str(h),re.I)]
    if not name_slots:return None
    for i,h in enumerate(headers):
        if i in name_slots or i>=len(cells):continue
        if (re.fullmatch(r'\s*(?:data\s*)?type(?:\s+name)?\s*',str(h),re.I)
                and identifier_key(subject) in declaration_names(cells[i])):
            return {'rule':'DECLARED_MEMBER_TYPE_IS_USAGE/v1','block_id':block.get('block_id'),
                    'type_cell_index':i,'type_cell_text':cells[i],
                    'member_name_slots':[{'cell_index':n,'text':cells[n]} for n in name_slots if n<len(cells)],
                    'headers':headers}
    return None
