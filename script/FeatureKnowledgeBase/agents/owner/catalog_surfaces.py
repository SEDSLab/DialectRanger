"""Literal declaration slots shared by source observation and type consumers.

SQL expression grammar is not a DBMS feature inventory. Only a complete catalog
cell can instantiate these forms; prose, query examples and result cells cannot.
"""
import re
from agents.owner.surface_patterns import formal_signatures


def operator_form(value, *, bare=False):
    value = ' '.join(str(value or '').split()).strip()
    operand = r'[a-z][A-Za-z_0-9]*'
    symbol = r'[!<>=~%&|^+*/:@?-]{1,8}'
    if re.fullmatch(symbol, value):
        return value
    wrapped = re.fullmatch('(' + symbol + r')\s*\(\s*' + operand + r'\s*\)', value)
    if wrapped:
        return wrapped[1]
    if re.fullmatch(operand + r'\[[A-Za-z_0-9: ]*\]', value):
        return '[:]' if ':' in value else '[]'
    if re.fullmatch(operand + r'\.' + operand, value):
        return '.'
    if re.fullmatch(operand + r'\s*::\s*[A-Z][A-Z_0-9]*(?:\([^)]*\))?', value):
        return '::'
    match = re.fullmatch(operand + r'\s+(' + symbol + r')\s+' + operand, value)
    if match:
        return match[1]
    match=re.fullmatch(operand+r'\s+([A-Z][A-Z_]*(?:\s+[A-Z][A-Z_]*)*)\s+(?:\.\.\.|\([^;]+\))',value)
    if match:return match[1]
    # Uppercase SQL keywords occupy the operator position; lowercase tokens
    # are metavariables. The later AND is a ternary separator, not a new head.
    match = re.fullmatch(operand + r'\s+([A-Z][A-Z_]*(?:\s+[A-Z][A-Z_]*)*)'
                         r'(?:\s+' + operand + r'(?:\s+AND\s+' + operand + r')?)?', value)
    if match:
        return match[1]
    match = re.fullmatch(r'([A-Z][A-Z_]*(?:\s+[A-Z][A-Z_]*)*)\s+' + operand, value)
    if match:
        return match[1]
    if bare and re.fullmatch(r'[A-Z][A-Z_]*(?:\s+[A-Z][A-Z_]*){0,4}', value):
        return value
    return None


def catalog_surfaces(headers, cells, context=''):
    """Return only explicit identity-column declarations and their cell index."""
    if re.search(r'\b(?:examples?|results?|output|parameters?|configuration|API|SDK|JDBC|ODBC|mappings?|connectors?|navigation|tutorial)\b', context, re.I):
        return []
    if not headers or not cells or list(headers) == list(cells):
        return []
    # Explicit notation-equivalence tables have two identity forms rather than
    # a Description column. At least one complete symbolic spelling is required;
    # ordinary file/result tables and lowercase callable references do not pass.
    if (len(headers)==len(cells)==2 and all(re.search(r'(?:-\s*style|equivalent\s+expression)',h,re.I) for h in headers)
            and any(re.fullmatch(r'[!<>=~%&|^+*/:@?-]+',str(v).strip()) for v in cells)):
        return [{'identity':operator_form(v,bare=True),'feature_type':'OPERATOR','cell_index':i,'raw_entry':str(v).strip()}
                for i,v in enumerate(cells) if operator_form(v,bare=True)]
    if not any(re.fullmatch(r'\s*description\s*', h, re.I) for h in headers):
        return []
    result = []
    for index, header in enumerate(headers):
        if index >= len(cells):
            continue
        header = str(header).strip()
        explicit_op = bool(re.fullmatch(r'(?:SQL\s+)?(?:operators?|predicates?)', header, re.I))
        generic = index == 0 and bool(re.fullmatch(r'(?:functions?|routines?|signatures?|names?|expressions?)', header, re.I))
        if not (explicit_op or generic):
            continue
        value = str(cells[index]).strip()
        op = operator_form(value, bare=explicit_op)
        alternatives = re.split(r'\s+(?:or|and)\s+|\s*,\s*',value,flags=re.I)
        forms=[operator_form(v,bare=explicit_op) for v in alternatives]
        operators=[op] if op else list(dict.fromkeys(forms)) if len(forms)>1 and all(forms) else []
        if operators:
            result.extend({'identity': item, 'feature_type': 'OPERATOR', 'cell_index': index, 'raw_entry': value}
                          for item in operators)
        elif generic:
            signatures = formal_signatures(value)
            for name in dict.fromkeys(n for n, _ in signatures):
                result.append({'identity': name, 'feature_type': 'FUNCTION', 'cell_index': index, 'raw_entry': value})
    return result
