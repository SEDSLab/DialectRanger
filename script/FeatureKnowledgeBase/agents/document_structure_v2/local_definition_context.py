"""Observable local container purposes; no DBMS or Feature vocabulary."""
import re
from bs4 import Tag
from agents.document_structure_v2.definition_observations import observe_definition,paragraph


def observe_page_function_definition(node, title):
    """A title mention alone is insufficient: require an explicit self-definition."""
    if not paragraph(node):
        return {}
    names = set(re.findall(r'\b([A-Za-z_]\w*)\s*\(\s*\)', title))
    if len(names) != 1:
        return {}
    name = next(iter(names))
    text = node.get_text(' ', strip=True)
    if not re.match(r'^(?:The\s+)?'+re.escape(name)+r'\s*\(\s*\)\s+function\s+is\s+(?:an?\s+)?'
                    r'(?:(?:SQL|scalar|aggregate|window|table-valued)\s+)+function\b', text, re.I):
        return {}
    return {'role': 'PAGE_FUNCTION_DEFINITION', 'identity': name, 'source_title': title, 'definition_text': text}


def observe_implementation_relation(node):
    if node.name not in {'p', 'dd'}:
        return {}
    text = node.get_text(' ', strip=True)
    match = re.match(r'^(?:The\s+)?([A-Za-z_]\w*)\s*\([^)]*\)\s+function\b', text, re.I)
    if match and re.search(r'\b(?:implement\w*|equivalent)\b', text, re.I) and re.search(r'\b(?:expression|operator)\b', text, re.I):
        return {'role': 'FUNCTION_IMPLEMENTATION_RELATION', 'identity': match[1], 'relation_text': text}
    match = re.match(r'^(?:The\s+)?(?:infix\s+)?([A-Za-z_]\w*)\s+operator\b', text, re.I)
    if match and re.search(r'\b(?:implement\w*|calling)\b', text, re.I) and re.search(r'\bfunction\b', text, re.I):
        return {'role': 'OPERATOR_IMPLEMENTATION_RELATION', 'identity': match[1], 'relation_text': text}
    return {}


def observe_local_definition_context(node):
    observation=observe_definition(node)
    if observation:return observation
    if node.name == 'tr':
        table = node.find_parent('table')
        headers = [h.get_text(' ', strip=True) for h in table.find_all('th')] if table else []
        if (headers and re.search(r'\boperand\b', headers[0], re.I)
                and any(re.search(r'\bresult\b', h, re.I) for h in headers[1:])):
            return {'role': 'BEHAVIOR_TRUTH_TABLE', 'headers': headers, 'container_kind': 'table'}
        return {}
    if node.name != 'li':
        return {}
    container = node.parent
    if not isinstance(container, Tag) or container.name not in {'ul', 'ol'}:
        return {}
    fragments = []
    for sibling in container.previous_siblings:
        if isinstance(sibling, Tag) and sibling.name in {'ul', 'ol', 'table', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
            break
        text = sibling.get_text(' ', strip=True) if isinstance(sibling, Tag) else str(sibling).strip()
        if text:
            fragments.insert(0, text)
        if len(fragments) >= 2:
            break
    intro = ' '.join(fragments)[-1200:]
    value = node.get_text(' ', strip=True)
    if re.search(r'\b(?:examples?|tutorial|API|JDBC|Java|Python)\b|C/C\+\+', intro, re.I):
        return {}
    role = None
    if (re.search(r'\b(?:data\s*type|datatype|type|type\s+name)s?\b', intro, re.I)
            and re.search(r'\b(?:must|allowed|permitted|supported)\b', intro, re.I)
            and re.search(r'\b(?:following|one\s+of)\b', intro, re.I)
            and re.fullmatch(r'[A-Za-z_]\w*', value)):
        role = 'DATATYPE_DECLARATION_LIST'
    elif (re.search(r'\bfunctions?\b', intro, re.I)
          and re.search(r'\b(?:supports?|following|as\s+follows)\b', intro, re.I)
          and re.fullmatch(r'[A-Za-z_]\w*\s*\([^;]*\)', value)):
        role = 'FUNCTION_DECLARATION_LIST'
    if not role:
        return {}
    return {'role': role, 'intro_text': intro, 'item_text': value, 'container_kind': container.name}
