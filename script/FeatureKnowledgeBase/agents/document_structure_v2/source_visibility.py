"""Observable exclusions, without CSS execution or document-specific selectors."""
from bs4 import Tag

EXCLUDED_TAGS = {'script', 'style', 'noscript', 'template', 'nav', 'footer', 'aside'}


def exclusion(node):
    for parent in node.parents:
        if not isinstance(parent, Tag):
            continue
        if parent.name in EXCLUDED_TAGS:
            return parent.name
        if parent.has_attr('hidden') or str(parent.get('aria-hidden', '')).lower() == 'true':
            return 'explicit_hidden'
        style = ''.join(str(parent.get('style', '')).lower().split())
        if 'display:none' in style or 'visibility:hidden' in style:
            return 'inline_hidden'
    return None
