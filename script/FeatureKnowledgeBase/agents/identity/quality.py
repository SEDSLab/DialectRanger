"""Generic quality signals for the cold-start identity path.

This module deliberately works from local document structure.  It does not
know a database's feature inventory and it does not turn a weak mention into
a feature merely because the text looks like an identifier.
"""
from __future__ import annotations

from collections import Counter
import re
from typing import Any, Mapping


SURFACE_ROLES = (
    "FEATURE_DEFINITION",
    "FEATURE_CATALOG",
    "FEATURE_REFERENCE",
    "SUPPORTING_CONTEXT",
    "UNKNOWN",
)
SURFACE_CONFIDENCE = ("HIGH", "MEDIUM", "LOW")
OCCURRENCE_ROLES = (
    "DEFINING_IDENTITY",
    "FORMAL_VARIANT",
    "MENTION",
    "EXAMPLE_TOKEN",
    "PARAMETER_OR_FIELD",
    "OUTPUT_TEXT",
    "CROSS_REFERENCE",
    "UNKNOWN",
)
TYPE_NAMES = ("FUNCTION", "OPERATOR", "DATATYPE")
TYPE_STRENGTHS = ("STRONG", "MEDIUM", "WEAK")
DEFINING_ROLES = {"DEFINING_IDENTITY", "FORMAL_VARIANT"}

_CALLABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*\s*\([^\n]*\)$")
_CALLABLE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_$])([A-Za-z_][A-Za-z0-9_$]*)\s*\([^\n)]*\)")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_SYMBOL_RE = re.compile(r"^[+*/<>=!|&^~%?@-]{1,3}$")
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_EXAMPLE_COMMAND_RE = re.compile(
    r"(?:\bSELECT\b|\bINSERT\b|\bUPDATE\b|\bDELETE\b|\bCREATE\b|"
    r"\bALTER\b|\bDROP\b|\bSHOW\b|\bSET\b|\bFROM\b|\bWHERE\b|"
    r"\$\s|^[A-Za-z]:\\|^>\s)",
    re.I,
)
_DESCRIPTION_WORDS = {
    "DESCRIPTION", "DESCRIPTIONS", "MEANING", "DETAILS", "PURPOSE",
    "SUMMARY", "NOTES", "NOTE", "RETURNS", "RETURN", "RESULT",
}
_FEATURE_HEADER_WORDS = {
    "FUNCTION", "FUNCTIONS", "OPERATOR", "OPERATORS", "DATATYPE",
    "DATATYPES", "TYPE", "TYPES", "DATA", "NAME", "NAMES", "KEYWORD",
    "KEYWORDS", "SYNTAX", "SIGNATURE", "SIGNATURES", "EXPRESSION",
    "EXPRESSIONS", "ROUTINE", "ROUTINES", "COMMAND", "COMMANDS",
    "STATEMENT", "STATEMENTS", "PREDICATE", "PREDICATES",
}
_EXPLICIT_IDENTITY_HEADER_RE = re.compile(
    r"^(?:function|functions|routine|routines|operator|operators|predicate|predicates|"
    r"name|names|keyword|keywords|callback|callbacks|command|commands|statement|statements|"
    r"syntax|signature|signatures|expression|expressions|data\s+types?|"
    r"datatype|type\s+name|declared\s+type)(?:\s*\d+)?$",
    re.I,
)
_SUPPORT_HEADER_WORDS = {
    "VARIABLE", "VARIABLES", "OPTION", "OPTIONS", "DEFAULT", "VALUE",
    "VALUES", "SCOPE", "MEANING", "SETTING", "SETTINGS", "ATTRIBUTE",
    "ATTRIBUTES", "FIELD", "FIELDS", "PARAMETER", "PARAMETERS", "ARGUMENT",
    "ARGUMENTS", "COLUMN", "COLUMNS", "OUTPUT", "RESULT", "EXAMPLE",
    "EXAMPLES", "PLATFORM", "VERSION", "STATUS",
}
_TYPE_CONTEXT_RE = re.compile(
    r"\b(?:data\s*types?|datatype|type\s+name|type\s+syntax|"
    r"storage\s+class|type\s+affinity|affinity|declared\s+type)\b",
    re.I,
)
_C_API_CONTEXT_RE = re.compile(
    r"\b(?:c(?:/c\+\+)?|c-language|native|programming[- ]language)\b.*\b(?:api|interface|reference)\b|"
    r"\b(?:api|interface|reference)\b.*\b(?:c(?:/c\+\+)?|c-language|native|programming[- ]language)\b",
    re.I,
)
_VIRTUAL_METHOD_CONTEXT_RE = re.compile(
    r"\b(?:virtual\s+table|vtab|table\s+module)\b.*\b(?:method|methods|callback|callbacks)\b|"
    r"\b(?:method|methods|callback|callbacks)\b.*\b(?:virtual\s+table|vtab|table\s+module)\b",
    re.I,
)
_C_PROTOTYPE_RE = re.compile(
    r"(?:;\s*$|\(\s*\*\s*[A-Za-z_][A-Za-z0-9_$]*\s*\)|"
    r"\b(?:const|struct|enum|union|void|char|short|long|int|size_t|bool)\b[^\n;]*\*)"
)
_NON_SIGNATURE_PAREN_RE = re.compile(
    r"\b(?:a\.?k\.?a\.?|alias|synonym|formerly|also\s+known\s+as|"
    r"equivalent\s+to|same\s+as|read[- ]only\s+access|application[- ]defined|"
    r"gui|cli|access|version|circa|year)\b",
    re.I,
)
_NON_SIGNATURE_VALUE_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)?|[^\s()]+\.exe|[^\s()]+\.(?:dll|so|dylib))\s*$",
    re.I,
)
_API_PAGE_RE = re.compile(
    r"\b(?:c\s*/\s*c\+\+|c\b|c[- ]language|native|programming[- ]language)\b.*\b(?:api|interface)\b|"
    r"\b(?:api|interface)\b.*\b(?:c\s*/\s*c\+\+|c\b|c[- ]language|native|programming[- ]language)\b|"
    r"\bapi\b",
    re.I,
)
_NON_FEATURE_PAGE_RE = re.compile(
    r"\b(?:release\s+history|changelog|books?\s+about|documentation|site\s*map|sitemap|"
    r"keyword\s+index|bytecode\s+engine|virtual\s+machine|internal\s+implementation)\b",
    re.I,
)
_PAGE_CONTEXT_CACHE: dict[str, tuple[str, bool, bool]] = {}


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _words(value: Any) -> set[str]:
    return {word.upper() for word in _WORD_RE.findall(_text(value))}


def is_callable(value: Any) -> bool:
    text = _text(value)
    if not _CALLABLE_RE.fullmatch(text) or _NON_SIGNATURE_PAREN_RE.search(text):
        return False
    match = re.search(r"\((.*)\)$", text)
    return not bool(match and _NON_SIGNATURE_VALUE_RE.fullmatch(match.group(1) or ""))


def is_symbol(value: Any) -> bool:
    return bool(_SYMBOL_RE.fullmatch(_text(value)))


def is_atomic_identifier(value: Any) -> bool:
    text = _text(value)
    if not _IDENTIFIER_RE.fullmatch(text):
        return False
    return len(text) <= 96 and text.upper() not in {
        "A", "AN", "AND", "THE", "FOR", "FROM", "IN", "OF", "ON", "OR", "THIS",
        "TO", "WITH", "FUNCTION", "FUNCTIONS", "TYPE", "TYPES", "EXAMPLE",
        "DESCRIPTION", "RETURN", "RETURNS", "VALUE", "VALUES",
    }


def callable_tokens(value: Any) -> list[str]:
    """Return bounded callable-looking labels without scanning prose."""
    return [
        match.group(0).strip()
        for match in _CALLABLE_TOKEN_RE.finditer(_text(value))
        if is_callable(match.group(0).strip())
    ]


def is_type_context(block: Mapping[str, Any]) -> bool:
    headers, _ = _table_metadata(block)
    ancestry = " ".join(_text(value) for value in (block.get("heading_ancestry") or []))
    return bool(_TYPE_CONTEXT_RE.search(" ".join([*headers, ancestry])))


def is_programming_api_declaration(block: Mapping[str, Any], page: Mapping[str, Any] | None = None) -> bool:
    """Recognize native API declaration surfaces from local structure only."""
    text = _text(block.get("text"))
    ancestry = " ".join(_text(value) for value in (block.get("heading_ancestry") or []))
    page_title = _text((page or {}).get("title"))
    api_context = f"{ancestry} {page_title}"
    if not text:
        return False
    if str(block.get("block_kind") or "") in {"DEFINITION", "CODE"} and _C_PROTOTYPE_RE.search(text):
        return True
    if _VIRTUAL_METHOD_CONTEXT_RE.search(api_context) and str(block.get("block_kind") or "") in {"DEFINITION", "CODE", "HEADING", "LIST_ITEM"}:
        return True
    if _C_API_CONTEXT_RE.search(api_context) and str(block.get("block_kind") or "") in {"DEFINITION", "CODE", "HEADING", "LIST_ITEM"}:
        return bool(_C_PROTOTYPE_RE.search(text) or _CALLABLE_TOKEN_RE.search(text) or _IDENTIFIER_RE.fullmatch(text))
    return False


def is_programming_api_page(page: Mapping[str, Any] | None) -> bool:
    if not page:
        return False
    return _page_context(page)[1]


def is_non_feature_page(page: Mapping[str, Any] | None) -> bool:
    if not page:
        return False
    return _page_context(page)[2]


def _page_context(page: Mapping[str, Any]) -> tuple[str, bool, bool]:
    title = _text(page.get("title"))
    ancestry = " ".join(
        _text(value)
        for block in (page.get("blocks") or [])[:20]
        for value in (block.get("heading_ancestry") or [])[:2]
    )
    combined = f"{title} {ancestry}"
    # Temporary page dicts can be freed and their Python ids reused. Cache
    # the actual classification input, never process object identity.
    key=combined
    cached = _PAGE_CONTEXT_CACHE.get(key)
    if cached is not None:return cached
    if len(_PAGE_CONTEXT_CACHE)>=4096:_PAGE_CONTEXT_CACHE.clear()
    value = (combined, bool(_API_PAGE_RE.search(combined)), bool(_NON_FEATURE_PAGE_RE.search(combined)))
    _PAGE_CONTEXT_CACHE[key] = value
    return value


def _table_metadata(block: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    metadata = block.get("table_metadata") or {}
    headers = [_text(item) for item in (metadata.get("headers") or []) if _text(item)]
    cells = [_text(item) for item in (metadata.get("cells") or []) if _text(item)]
    return headers, cells


def _feature_context_text(block: Mapping[str, Any], page: Mapping[str, Any] | None) -> str:
    ancestry = " ".join(_text(value) for value in (block.get("heading_ancestry") or []))
    page_text = " ".join(_text(value) for value in (
        (page or {}).get("title"),
        *((page or {}).get("feature_context") or []),
    ))
    return f"{ancestry} {page_text}"


def _has_function_context(block: Mapping[str, Any], page: Mapping[str, Any] | None) -> bool:
    return bool(re.search(
        r"\b(?:function|functions|routine|routines|callback|callbacks|aggregate|scalar|window)\b",
        _feature_context_text(block, page),
        re.I,
    ))


def _has_operator_context(block: Mapping[str, Any], page: Mapping[str, Any] | None) -> bool:
    return bool(re.search(
        r"\b(?:operator|operators|predicate|predicates|expression|expressions|precedence)\b",
        _feature_context_text(block, page),
        re.I,
    ))


def typed_leading_label(block: Mapping[str, Any], text: str) -> str:
    """Return a type label only when the local text looks declarative.

    A broad datatype-page heading is not enough to turn sentence labels such
    as ``Update: ...`` into a datatype identity.  Uppercase/qualified labels
    and explicit local type wording remain valid generic declaration surfaces.
    """
    first = re.split(r"\s*[.]\s*|\s*[:;]\s*", text, maxsplit=1)[0].strip()
    if not is_atomic_identifier(first):
        return ""
    prefix = text[: max(0, text.find(first) + len(first) + 1)]
    explicit_local_type = bool(re.search(
        r"\b(?:data\s+type|datatype|declared\s+type|storage\s+class|type\s+affinity|affinity)\b",
        prefix,
        re.I,
    ))
    uppercase_label = first.upper() == first and len(first) > 1
    return first if uppercase_label or explicit_local_type else ""


def _has_explicit_identity_header(headers: list[str]) -> bool:
    """Return true only for an identity column, not a result/type column.

    Tables such as ``Internal Type / Requested Type / Conversion`` and
    ``Result of IN operator`` contain feature-looking words but describe
    values or outcomes.  Treating those columns as identity slots creates
    generic false positives (for example NULL) across unrelated DBMS docs.
    """
    return any(_EXPLICIT_IDENTITY_HEADER_RE.fullmatch(_text(header)) for header in headers)


def _table_identity_values(block: Mapping[str, Any]) -> list[str]:
    """Recover bounded inline labels from DocumentIR ``cell_items``."""
    headers, cells = _table_metadata(block)
    metadata = block.get("table_metadata") or {}
    groups = [
        [_text(item) for item in group if _text(item)]
        for group in (metadata.get("cell_items") or [])
        if isinstance(group, list) and any(_text(item) for item in group)
    ]
    header_words = _words(" ".join(headers))
    feature_header = _has_explicit_identity_header(headers)
    type_declaration_header = any(
        re.fullmatch(r"(?:type|type\s+name|data\s+types?|datatype|declared\s+type|storage\s+class|affinity)", _text(header), re.I)
        for header in headers
    )
    operator_header = any(
        re.fullmatch(r"(?:operator|operators|predicate|predicates|expression|expressions)(?:\s*\d+)?", _text(header), re.I)
        for header in headers
    )
    if groups and len(groups) == len(cells):
        if len(groups) == 1 and len(groups[0]) > 1 and (feature_header or operator_header):
            return groups[0]
        if not feature_header and not operator_header and not type_declaration_header:
            return []
        values = [" ".join(group) for group in groups]
        if values and len(values) == len(headers) and all(
            _text(values[index]).casefold() == _text(headers[index]).casefold()
            for index in range(min(len(values), len(headers)))
        ):
            return []
        return values
    if not feature_header and not operator_header and not type_declaration_header:
        return []
    return cells


def _header_profile(headers: list[str]) -> tuple[set[str], set[str], bool, bool]:
    words = set().union(*(_words(header) for header in headers)) if headers else set()
    has_feature = _has_explicit_identity_header(headers)
    has_support = bool(words & _SUPPORT_HEADER_WORDS)
    # A description column is useful evidence when it accompanies an
    # identity-like first column, but is not itself an identity slot.
    has_description = bool(words & _DESCRIPTION_WORDS)
    first_identity_header = bool(headers and (_words(headers[0]) & _FEATURE_HEADER_WORDS))
    return words, {"DESCRIPTION"} if has_description else set(), has_feature, has_support or bool(words & {"DESCRIPTION"}) and not first_identity_header


def _identity_shape(value: str) -> str | None:
    if is_callable(value):
        return "callable"
    if is_symbol(value):
        return "symbol"
    if is_atomic_identifier(value):
        return "identifier"
    return None


def _profile_hint(owner: Mapping[str, Any] | None, profile_hint: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if profile_hint:
        return profile_hint
    if owner:
        return owner.get("profile_hint") or {}
    return {}


def assess_surface(
    block: Mapping[str, Any],
    *,
    page: Mapping[str, Any] | None = None,
    owner: Mapping[str, Any] | None = None,
    profile_hint: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assess whether one structural block is feature-bearing.

    The result is intentionally explainable and can be copied onto both the
    generated profile and each identity occurrence.
    """
    kind = str(block.get("block_kind") or "OTHER")
    text = _text(block.get("text"))
    hint = _profile_hint(owner, profile_hint)
    evidence: list[str] = []
    feature_evidence: list[str] = []
    role = "UNKNOWN"
    confidence = 0.15

    if kind in {"TABLE", "TABLE_ROW"}:
        headers, cells = _table_metadata(block)
        identity_values = _table_identity_values(block)
        words, _, has_feature_header, has_support_header = _header_profile(headers)
        first = identity_values[0] if identity_values else ""
        first_shape = next((_identity_shape(value) for value in identity_values if _identity_shape(value)), None)
        operator_label_surface = bool(
            any(re.fullmatch(r"(?:operator|operators|predicate|predicates)(?:\s*\d+)?", _text(header), re.I) for header in headers)
        ) and bool(
            re.search(r"[+*/<>=!|&^~%?-]", " ".join(identity_values))
            or re.search(r"\b[A-Z][A-Z0-9_$]{1,}\b", " ".join(identity_values))
        )
        if has_feature_header and (first_shape or operator_label_surface):
            role = "FEATURE_CATALOG"
            confidence = 0.95
            feature_evidence.extend(["identity_header", f"first_cell_{first_shape or 'operator_label'}"])
            if len(identity_values) > 1 or len(cells) > 1:
                feature_evidence.append("adjacent_context_cell")
        elif first_shape in {"callable", "symbol"} and (len(identity_values) > 1 or len(cells) > 1) and not has_support_header:
            role = "FEATURE_CATALOG"
            confidence = 0.82
            feature_evidence.extend([f"first_cell_{first_shape}", "adjacent_context_cell"])
        elif has_support_header:
            role = "SUPPORTING_CONTEXT"
            confidence = 0.90
            evidence.append("supporting_table_headers")
        elif first_shape == "identifier" and page and str(page.get("feature_context") or ""):
            role = "FEATURE_CATALOG"
            confidence = 0.62
            feature_evidence.extend(["atomic_identity_cell", "typed_page_prior"])
        elif first_shape:
            role = "SUPPORTING_CONTEXT"
            confidence = 0.48
            evidence.append("identity_shaped_cell_without_definition_header")
        else:
            role = "SUPPORTING_CONTEXT"
            confidence = 0.30
            evidence.append("table_without_identity_slot")
        evidence.extend(f"header:{word.lower()}" for word in sorted(words & (_FEATURE_HEADER_WORDS | _SUPPORT_HEADER_WORDS)))
    elif kind == "CODE":
        if _EXAMPLE_COMMAND_RE.search(text) or "example" in " ".join(str(value).casefold() for value in (block.get("heading_ancestry") or [])):
            role = "SUPPORTING_CONTEXT"
            confidence = 0.18
            evidence.append("executable_or_example_context")
        elif is_callable(text) or is_symbol(text):
            role = "FEATURE_DEFINITION"
            confidence = 0.92
            feature_evidence.append("standalone_signature")
        elif is_atomic_identifier(text):
            role = "FEATURE_DEFINITION" if hint.get("surface_role") == "FEATURE_DEFINITION" else "SUPPORTING_CONTEXT"
            confidence = 0.65 if role == "FEATURE_DEFINITION" else 0.38
            (feature_evidence if role == "FEATURE_DEFINITION" else evidence).append("atomic_code_token")
        else:
            role = "SUPPORTING_CONTEXT"
            confidence = 0.25
            evidence.append("non_signature_code")
    elif kind == "DEFINITION":
        first = re.split(r"\s+(?:returns?|is|means|describes?)\s+|[:;—–-]", text, maxsplit=1, flags=re.I)[0].strip()
        if _identity_shape(first) and not is_programming_api_declaration(block):
            role = "FEATURE_DEFINITION"
            confidence = 0.90
            feature_evidence.append("definition_term_with_body")
        else:
            role = "SUPPORTING_CONTEXT"
            confidence = 0.28
            evidence.append("definition_without_identity_term")
    elif kind == "HEADING":
        heading_is_reference = bool(re.search(
            r"\b(?:call|calls|calling|interface|api|family|variant|method)\b",
            text,
            re.I,
        ))
        if (callable_tokens(text) and _has_function_context(block, page) and not heading_is_reference) or (
            is_symbol(text) and _has_operator_context(block, page)
        ):
            role = "FEATURE_DEFINITION"
            confidence = 0.92
            feature_evidence.append("identity_shaped_heading")
        elif is_atomic_identifier(text) and text.upper() == text:
            role = "FEATURE_REFERENCE"
            confidence = 0.58
            feature_evidence.append("atomic_heading")
        else:
            role = "SUPPORTING_CONTEXT"
            confidence = 0.22
            evidence.append("ordinary_heading")
    elif kind == "LIST_ITEM":
        literals = (block.get("code_metadata") or {}).get("inline_literals") or []
        callable_values = callable_tokens(text)
        residue = re.sub(r"[A-Za-z_][A-Za-z0-9_$]*\s*\([^\n)]*\)", "", text).strip(" \t,;|/")
        bounded_callable = bool(callable_values) and not residue and _has_function_context(block, page)
        typed_label = bool(is_type_context(block) and typed_leading_label(block, text))
        if (literals and any(_identity_shape(str(item)) for item in literals)) or bounded_callable or typed_label:
            role = "FEATURE_CATALOG"
            confidence = 0.88
            feature_evidence.append("inline_identity_literal" if literals else "bounded_identity_surface")
        else:
            role = "SUPPORTING_CONTEXT"
            confidence = 0.25
            evidence.append("list_item_without_bounded_identity")
    elif kind == "LINK":
        role = "SUPPORTING_CONTEXT"
        confidence = 0.18
        evidence.append("navigation_or_cross_reference")
    else:
        if is_type_context(block):
            first = typed_leading_label(block, text)
            if first:
                role = "FEATURE_DEFINITION"
                confidence = 0.86
                feature_evidence.append("typed_leading_label")
            else:
                role = "SUPPORTING_CONTEXT"
                confidence = 0.12
                evidence.append("prose_or_unclassified_block")
        else:
            role = "SUPPORTING_CONTEXT"
            confidence = 0.12
            evidence.append("prose_or_unclassified_block")

    if is_programming_api_page(page) or is_non_feature_page(page) or is_programming_api_declaration(block, page=page):
        role = "SUPPORTING_CONTEXT"
        confidence = min(confidence, 0.18)
        feature_evidence = []
        if is_programming_api_page(page):
            evidence.append("programming_api_page")
        if is_non_feature_page(page):
            evidence.append("non_feature_page_context")
        if is_programming_api_declaration(block, page=page):
            evidence.append("programming_api_declaration")

    hinted_role = str(hint.get("surface_role") or "").upper()
    if hinted_role in SURFACE_ROLES:
        evidence.append(f"profile_surface_role:{hinted_role}")
        # A supporting profile observation can demote a weak structural guess,
        # but never overrides a strong standalone signature/catalog signal.
        if hinted_role == "SUPPORTING_CONTEXT" and confidence < 0.85:
            role = "SUPPORTING_CONTEXT"
            confidence = min(confidence, 0.45)
        elif hinted_role == "FEATURE_REFERENCE" and role == "UNKNOWN":
            role = hinted_role
    if feature_evidence:
        evidence.extend(feature_evidence)
    if not evidence:
        evidence.append("no_decisive_surface_signal")
    label = "HIGH" if confidence >= 0.80 else "MEDIUM" if confidence >= 0.50 else "LOW"
    return {
        "surface_role": role,
        "surface_confidence": round(float(confidence), 4),
        "surface_confidence_label": label,
        "feature_evidence": sorted(set(feature_evidence)),
        "surface_evidence": sorted(set(evidence)),
        "block_kind": kind,
    }


def explicit_usage_role(block):
    """Use-site evidence, without treating SQL in a table cell as its owner."""
    if block.get('block_kind') not in {'CODE','TEXT','DEFINITION'}:return None
    text=_text(block.get('text'))
    from agents.contracts.definition_entry import CALLABLE_HEAD, observed_callable_declaration
    command=re.match(r'^\s*(?:[\w.:-]+>\s*|>\s*)?(?:SELECT|VALUES|INSERT|UPDATE|DELETE|'
                     r'CREATE\s+TABLE|DROP\s+TABLE|ALTER\s+TABLE)\b',text,re.I)
    if command and not CALLABLE_HEAD.match(text):return 'EXAMPLE_TOKEN'
    if block.get('block_kind')=='CODE':
        labels=[str(x) for x in block.get('heading_ancestry',[])]
        labels += [str(x.get('text','')) for x in (block.get('observed_context') or {}).get('labels',[]) if isinstance(x,Mapping)]
        if any(re.search(r'\bexamples?\b',label,re.I) for label in labels):return 'EXAMPLE_TOKEN'
        # A standalone literal call is use, unless local declaration markup
        # proves formal grammar (e.g. default values or quoted alternatives).
        marked=any('replaceable' in n.get('semantic_classes',[]) for n in block.get('inline_nodes',[]))
        syntax_label=any(re.fullmatch(r'\s*(?:syntax|signature|grammar)\s*:?',label,re.I) for label in labels)
        if (CALLABLE_HEAD.match(text) and not is_type_context(block)
                and re.search(r"'|\b\d+\b",text)
                and not observed_callable_declaration(text,block.get('inline_nodes',[]),formal_marker=marked or syntax_label)):
            return 'EXAMPLE_TOKEN'
    return None


def classify_occurrence_role(
    *,
    raw_identity: str,
    source_type: str,
    block: Mapping[str, Any],
    surface: Mapping[str, Any],
) -> str:
    """Classify an identity mention without deleting the occurrence."""
    source = str(source_type or "").casefold()
    role = str(surface.get("surface_role") or "UNKNOWN")
    evidence = _text(block.get("text"))
    ancestry = " ".join(_text(value) for value in (block.get("heading_ancestry") or []))
    context = f"{ancestry} {evidence}".casefold()

    if source in {"anchor", "link"}:
        return "CROSS_REFERENCE"
    usage=explicit_usage_role(block)
    if usage:return usage
    if any(token in context for token in ("output", "result", "returns", "return value")) and source in {"text", "inline_code"}:
        return "OUTPUT_TEXT"
    if role in {"FEATURE_DEFINITION", "FEATURE_CATALOG"}:
        if source in {"table_row", "definition_list", "heading", "list_item", "text"}:
            return "DEFINING_IDENTITY"
        if source == "signature":
            return "FORMAL_VARIANT"
        if source == "inline_code":
            return "FORMAL_VARIANT" if is_callable(raw_identity) or is_symbol(raw_identity) else "EXAMPLE_TOKEN"
    if source == "inline_code" or _EXAMPLE_COMMAND_RE.search(evidence):
        return "EXAMPLE_TOKEN"
    if source == "table_row":
        headers, _ = _table_metadata(block)
        header_words = set().union(*(_words(value) for value in headers)) if headers else set()
        if header_words & _SUPPORT_HEADER_WORDS:
            return "PARAMETER_OR_FIELD"
    if role == "FEATURE_REFERENCE":
        return "MENTION"
    if source in {"text", "heading", "definition_list", "list_item"}:
        return "MENTION"
    return "UNKNOWN"


def is_defining_role(role: str) -> bool:
    return str(role or "") in DEFINING_ROLES


def infer_type_evidence(
    *,
    raw_identity: str,
    occurrence_role: str,
    block: Mapping[str, Any],
    owner: Mapping[str, Any],
    page: Mapping[str, Any],
    surface: Mapping[str, Any],
    structural_role: str | None = None,
    surface_type_hint: str | None = None,
) -> list[dict[str, str]]:
    """Return explainable type evidence with explicit strength."""
    if not is_defining_role(occurrence_role):
        return []
    raw = _text(raw_identity)
    headers, _ = _table_metadata(block)
    header_text = " ".join(headers).casefold()
    ancestry = " ".join(_text(value) for value in (block.get("heading_ancestry") or [])).casefold()
    title = _text(page.get("title"))
    context_text = f"{header_text} {ancestry} {title.casefold()}"
    hint = owner.get("profile_hint") or {}
    page_context = {str(value).upper() for value in (page.get("feature_context") or [])}
    result: list[dict[str, str]] = []

    def add(feature_type: str, strength: str, reason: str, source: str) -> None:
        if feature_type in TYPE_NAMES and strength in TYPE_STRENGTHS:
            result.append({"type": feature_type, "strength": strength, "reason": reason, "source": source})

    callable_raw = is_callable(raw)
    symbol_raw = is_symbol(raw)
    type_context = re.search(
        r"\b(?:data\s*[- ]?types?|datatype|type\s*[- ]?name|type\s*[- ]?syntax|storage\s+class|type\s+affinity|declared\s+type|affinity)\b",
        context_text,
        re.I,
    )
    explanatory_parenthetical = bool(_NON_SIGNATURE_PAREN_RE.search(raw))
    operator_context = bool(re.search(
        r"\b(?:operator|operators|predicate|predicates|expression|expressions)\b",
        context_text,
        re.I,
    ))
    function_context = bool(re.search(
        r"\b(?:function|functions|routine|routines|callable|signature|signatures)\b",
        context_text,
        re.I,
    ))
    explicit_surface_type = str(surface_type_hint or "").upper()
    if explicit_surface_type in TYPE_NAMES:
        add(explicit_surface_type, "STRONG", "explicit_structural_column_role", "table_header")
    if symbol_raw:
        add("OPERATOR", "STRONG", "symbol_definition", "raw_identity")
    if operator_context and not type_context:
        # Operator-labelled tables may contain word operators or callable
        # notation such as COLLATE(name).  The local structural label wins;
        # do not manufacture a competing FUNCTION type from parentheses.
        add("OPERATOR", "STRONG", "explicit_operator_context", "heading_or_table_header")
    elif callable_raw and not (type_context and explanatory_parenthetical):
        add("FUNCTION", "STRONG", "callable_signature", "raw_identity")
    if type_context and not symbol_raw and structural_role in {"FEATURE_NAME", "SIGNATURE", "ALIAS", None}:
        add("DATATYPE", "STRONG", "type_declaration_context", "heading_or_table_header")
    elif callable_raw and function_context:
        add("FUNCTION", "STRONG", "explicit_function_context", "heading_or_table_header")
    elif not symbol_raw and not operator_context and function_context:
        add("FUNCTION", "STRONG", "explicit_function_context", "heading_or_table_header")
    elif not symbol_raw and not callable_raw and operator_context and not type_context:
        add("OPERATOR", "STRONG", "explicit_operator_context", "heading_or_table_header")

    hinted_type = str(hint.get("type_hint") or "").upper()
    if hinted_type in TYPE_NAMES:
        add(hinted_type, "MEDIUM", "profile_surface_prior", "profile")
    for context_type in page_context & set(TYPE_NAMES):
        add(
            context_type,
            "MEDIUM" if structural_role in {"FEATURE_NAME", "SIGNATURE", "ALIAS"} else "WEAK",
            "discovery_context_prior",
            "discovery",
        )
    return _dedupe_type_evidence(result)


def _dedupe_type_evidence(values: list[dict[str, str]]) -> list[dict[str, str]]:
    rank = {"STRONG": 3, "MEDIUM": 2, "WEAK": 1}
    best: dict[tuple[str, str], dict[str, str]] = {}
    for value in values:
        key = (value["type"], value["reason"])
        current = best.get(key)
        if current is None or rank[value["strength"]] > rank[current["strength"]]:
            best[key] = dict(value)
    return sorted(best.values(), key=lambda row: (-rank[row["strength"]], row["type"], row["reason"]))


def aggregate_occurrence_roles(occurrences: list[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(str(row.get("occurrence_role") or "UNKNOWN") for row in occurrences)
    return {role: int(counts.get(role, 0)) for role in OCCURRENCE_ROLES}


def aggregate_type_evidence(occurrences: list[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {name: {strength: 0 for strength in TYPE_STRENGTHS} for name in TYPE_NAMES}
    for occurrence in occurrences:
        for item in occurrence.get("type_evidence") or []:
            feature_type = str(item.get("type") or "")
            strength = str(item.get("strength") or "")
            if feature_type in result and strength in TYPE_STRENGTHS:
                result[feature_type][strength] += 1
    return result
