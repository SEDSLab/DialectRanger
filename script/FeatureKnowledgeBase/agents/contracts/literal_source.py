"""One reader for the source loader's additive literal-node witness.

The producer verifies page bytes and DOM before attaching these facts. A model
claim or a standalone literal_text annotation cannot grant this authority.
"""


def verified_literal_text(block):
    key={'CODE':'literal_code_text','TABLE_ROW':'literal_row_text'}.get(str(block.get('block_kind') or '').upper())
    value=(block.get('observed_context') or {}).get(key) if key else None
    if (block.get('source_text_origin')=='HASH_VERIFIED_LITERAL_NODE'
            and block.get('source_structure_status')=='RESOLVED' and block.get('source_hash')
            and block.get('dom_path') and isinstance(value,str)
            and block.get('literal_text')==value.strip()):
        return value.strip()
    return None
