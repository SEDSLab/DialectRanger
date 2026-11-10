"""One lexical identity key for declarations, ownership and grouping.

This is normalization, not evidence that a spelling defines a feature.
Quoted identifiers retain case and internal whitespace; unquoted parts fold
to the existing uppercase convention. No DBMS vocabulary is consulted.
"""
import re
from agents.contracts.definition_entry import CALLABLE_HEAD, QUALIFIED_IDENTIFIER, declaration_identity


def normalize_identity(value):
    text=re.sub(r'"(?:[^"]|"")*"|\s+',
                lambda m:m[0] if m[0].startswith('"') else ' ',str(value or '')).strip()
    declared=CALLABLE_HEAD.match(text)
    if declared:return declaration_identity(declared.group('name'))
    if re.fullmatch(QUALIFIED_IDENTIFIER,text):return declaration_identity(text)
    # Compound SQL spellings/operators are not qualified identifier paths.
    return ''.join(p if p.startswith('"') else p.upper()
                   for p in re.findall(r'"(?:[^"]|"")*"|[^"]+',text))


def identity_text(value):
    """Normalize comparison text without removing formal argument grammar."""
    text=re.sub(r'"(?:[^"]|"")*"|\s+',lambda m:m[0] if m[0].startswith('"') else ' ',str(value or '')).strip()
    return ''.join(p if p.startswith('"') else p.upper() for p in re.findall(r'"(?:[^"]|"")*"|[^"]+',text))


def canonical_name_key(value):
    # Keep historical lowercase keys for unquoted names; quoted parts retain
    # their exact identity. Display names remain the normalized spelling.
    return ''.join(p if p.startswith('"') else p.casefold()
                   for p in re.findall(r'"(?:[^"]|"")*"|[^"]+',normalize_identity(value)))


def canonical_identity_key(dbms,name,feature_type,sense='DEFAULT'):
    return '|'.join([str(dbms).casefold(),str(feature_type).casefold(),canonical_name_key(name),str(sense).casefold()])
