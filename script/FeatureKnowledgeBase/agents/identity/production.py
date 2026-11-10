"""Recall-first IdentityCandidate/v2 and IdentityUnit/v1 production path."""
from __future__ import annotations

from collections import Counter, defaultdict
import re
from typing import Any, Iterable, Mapping

from agents.contracts.base import stable_id
from agents.contracts.identity_key import normalize_identity
from agents.contracts.identity import make_identity_candidate, make_identity_unit
from agents.owner.generic import consolidate_owner_hypotheses, generate_owner_hypotheses
from agents.owner.surface_patterns import formal_signatures
from agents.identity.quality import (
    aggregate_occurrence_roles,
    aggregate_type_evidence,
    assess_surface,
    callable_tokens,
    classify_occurrence_role,
    explicit_usage_role,
    infer_type_evidence,
    is_defining_role,
    is_type_context,
    typed_leading_label,
)
from agents.identity.structural_roles import (
    infer_table_column_roles,
    is_header_row,
    split_surface_items,
    table_metadata_roles,
)


def _dedupe_type_evidence_local(values: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Keep the strongest generic type signal for each type/reason/source."""
    strength_rank = {"STRONG": 3, "MEDIUM": 2, "WEAK": 1}
    best: dict[tuple[str, str, str], dict[str, Any]] = {}
    for value in values:
        feature_type = str(value.get("type") or "").upper()
        if feature_type not in {"FUNCTION", "OPERATOR", "DATATYPE"}:
            continue
        row = dict(value)
        row["type"] = feature_type
        key = (
            feature_type,
            str(row.get("reason") or ""),
            str(row.get("source") or ""),
        )
        current = best.get(key)
        if current is None or strength_rank.get(str(row.get("strength") or "WEAK").upper(), 0) > strength_rank.get(str(current.get("strength") or "WEAK").upper(), 0):
            best[key] = row
    return sorted(best.values(), key=lambda row: (
        str(row.get("type") or ""),
        -strength_rank.get(str(row.get("strength") or "WEAK").upper(), 0),
        str(row.get("reason") or ""),
        str(row.get("source") or ""),
    ))


_STOPWORDS = {
    "A", "AN", "AND", "THE", "FOR", "FROM", "IN", "OF", "ON", "OR", "TO",
    "WITH", "THIS", "FUNCTION", "FUNCTIONS", "OPERATOR", "OPERATORS", "DATA", "TYPE",
    "TYPES", "EXAMPLE", "EXAMPLES", "SYNTAX", "DESCRIPTION", "PARAMETERS",
    "PARAMETER", "RETURNS", "RETURN", "REFERENCE", "OVERVIEW", "USAGE", "NOTE",
}
_GRAMMAR_TOKENS = {
    "SELECT", "FROM", "WHERE", "GROUP", "ORDER", "BY", "HAVING", "JOIN",
    "LEFT", "RIGHT", "FULL", "INNER", "OUTER", "CROSS", "ON", "USING",
    "AS", "CASE", "WHEN", "THEN", "ELSE", "END", "OVER", "PARTITION",
    "WINDOW", "DISTINCT", "ALL", "UNION", "INTERSECT", "EXCEPT", "VALUES",
    "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "TABLE",
    "VIEW", "INDEX", "NULL", "TRUE", "FALSE", "IS", "NOT", "IN", "EXISTS",
    "BETWEEN", "LIKE", "LIMIT", "OFFSET", "ASC", "DESC", "AND", "OR",
}
_IDENTITY_RE = re.compile(r"(?<![A-Za-z0-9_$])([A-Za-z_][A-Za-z0-9_$]*)\s*(\([^\n)]*\))?")
_OPERATOR_RE = re.compile(r"(?<![A-Za-z0-9_])([+*/<>=!|&^~%?@\-\[\]]{1,4})(?![A-Za-z0-9_])")
_CALL_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_$]*)\s*\(")
_PHRASE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*(?:\s+[A-Za-z_][A-Za-z0-9_$]*){1,5}$")
_CALLABLE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_$])([A-Za-z_][A-Za-z0-9_$]*)\s*\([^\n)]*\)")
_OPERATOR_WORDS = {
    "AND", "OR", "IS", "IN", "BETWEEN", "LIKE", "GLOB", "REGEXP", "MATCH",
    "ISNULL", "NOTNULL", "COLLATE", "ESCAPE",
}
_OPERATOR_LABEL_RE = re.compile(
    r"\b(?:operator|operators|predicate|predicates|expression|expressions)\b\s*[:\-]\s*(.+)$",
    re.I,
)
_OPERATOR_TRAILING_LABEL_RE = re.compile(
    r"^(.+?)\s+\b(?:operator|operators|predicate|predicates)\b\s*#?$",
    re.I,
)
_OPERATOR_ROLE_RE = re.compile(r"\b(?:operator|operators|predicate|predicates|expression|expressions)\b", re.I)


def _compound_operator_values(text: str, ancestry: str = "") -> list[str]:
    """Extract bounded symbolic/compound operator labels from structure.

    This parser is intentionally vocabulary-free.  It accepts a short symbol
    or an all-caps phrase only when an operator/predicate/expression heading or
    explicit label makes the surface defining; it never scans ordinary prose
    into identities.
    """
    title = re.sub(r"\s+", " ", str(text or "")).strip().rstrip("#").strip()
    if not title:
        return []
    def phrase(value: str, *, explicit: bool = False) -> str | None:
        value = re.sub(r"\s+", " ", str(value or "")).strip(" #:;,.")
        if re.fullmatch(r"[+*/<>=!|&^~%?@\-\[\]]{1,4}", value):
            return value
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*(?:\s+[A-Za-z][A-Za-z0-9]*){0,5}", value):
            return None
        if not explicit and value != value.upper():
            return None
        words = value.upper().split()
        if all(word in {"OPERATOR", "OPERATORS", "PREDICATE", "PREDICATES", "EXPRESSION", "EXPRESSIONS", "FUNCTION", "FUNCTIONS", "ROUTINE", "ROUTINES", "COMPARISON", "RANGE", "LOGICAL", "QUANTIFIED"} for word in words):
            return None
        if any(word in {"THE", "THIS", "USED", "WITH", "WHICH", "AND"} for word in words):
            return None
        return " ".join(words)
    values: list[str] = []
    match = _OPERATOR_LABEL_RE.search(title)
    if match:
        values.extend(
            value for value in (
                phrase(part, explicit=True)
                for part in re.split(r"\s*(?:,|;|\band\b)\s*", match.group(1), flags=re.I)
            ) if value
        )
    match = _OPERATOR_TRAILING_LABEL_RE.match(title)
    if match:
        value = phrase(match.group(1), explicit=True)
        if value:
            values.append(value)
    if _OPERATOR_ROLE_RE.search(f"{ancestry} {title}"):
        parts = re.split(r"\s*(?:,|;|\band\b)\s*", title, flags=re.I)
        if len(parts) > 1:
            values.extend(value for value in (phrase(part) for part in parts) if value)
        elif ancestry and title:
            value = phrase(title, explicit=True)
            if value:
                values.append(value)
    return list(dict.fromkeys(values))
_EXPLICIT_IDENTITY_HEADER_RE = re.compile(
    r"^(?:function|functions|routine|routines|operator|operators|predicate|predicates|"
    r"name|names|keyword|keywords|callback|callbacks|command|commands|statement|statements|"
    r"syntax|signature|signatures|expression|expressions|data\s+types?|"
    r"datatype|type\s+name|declared\s+type)(?:\s*\d+)?$",
    re.I,
)


def _operator_context(block: Mapping[str, Any]) -> bool:
    metadata = block.get("table_metadata") or {}
    headers = " ".join(str(value or "") for value in (metadata.get("headers") or []))
    ancestry = " ".join(str(value or "") for value in (block.get("heading_ancestry") or []))
    return bool(re.search(r"\b(?:operator|operators|predicate|predicates|precedence|expression|expressions)\b", f"{headers} {ancestry}", re.I))


def _has_explicit_identity_header(headers: list[str]) -> bool:
    return any(_EXPLICIT_IDENTITY_HEADER_RE.fullmatch(re.sub(r"\s+", " ", str(header or "")).strip()) for header in headers)


def _table_identity_values(block: Mapping[str, Any]) -> list[tuple[str, bool]]:
    """Return identity slots and whether the slot came from an operator cell."""
    metadata = block.get("table_metadata") or {}
    headers = [re.sub(r"\s+", " ", str(value or "")).strip() for value in (metadata.get("headers") or [])]
    cells = [re.sub(r"\s+", " ", str(value or "")).strip() for value in (metadata.get("cells") or []) if str(value or "").strip()]
    groups = [
        [re.sub(r"\s+", " ", str(value or "")).strip() for value in group if str(value or "").strip()]
        for group in (metadata.get("cell_items") or [])
        if isinstance(group, list) and any(str(value or "").strip() for value in group)
    ]
    operator_surface = _operator_context(block)
    identity_header = _has_explicit_identity_header(headers)
    explicit_operator_header = any(
        re.fullmatch(r"(?:operator|operators|predicate|predicates|expression|expressions)(?:\s*\d+)?", header, re.I)
        for header in headers
    )
    if groups and len(groups) == len(cells):
        if len(groups) == 1 and len(groups[0]) > 1 and operator_surface:
            return [(value, True) for value in groups[0]]
        if not identity_header and not explicit_operator_header and not is_type_context(block):
            return []
        indices = [index for index, header in enumerate(headers) if _EXPLICIT_IDENTITY_HEADER_RE.fullmatch(header)]
        if not indices:
            indices = [0] if (identity_header or explicit_operator_header or is_type_context(block)) else []
        values = [(" ".join(groups[index]), operator_surface) for index in indices if index < len(groups)]
        if values and len(values) == len(headers) and all(
            re.sub(r"\s+", " ", values[index][0]).strip().casefold()
            == re.sub(r"\s+", " ", headers[index]).strip().casefold()
            for index in range(min(len(values), len(headers)))
        ):
            return []
        return values
    if not identity_header and not explicit_operator_header and not is_type_context(block):
        return []
    return [(cells[0] if cells else str(block.get("text") or ""), operator_surface)]


def _operator_word_matches(text: str) -> list[str]:
    """Extract word operators only inside a structurally operator-labelled cell."""
    clean = re.sub(r"\[[^\]]+\]", " ", text)
    clean = re.sub(r"\b\d+\b", " ", clean)
    tokens = [token.upper() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_$]*", clean)]
    values: list[str] = []
    for index, token in enumerate(tokens):
        following = tokens[index + 1:index + 4]
        if token == "IS":
            if following[:2] == ["NOT", "DISTINCT"] and len(following) >= 3 and following[2] == "FROM":
                values.append("IS NOT DISTINCT FROM")
            elif following[:2] == ["DISTINCT", "FROM"]:
                values.append("IS DISTINCT FROM")
            elif following[:1] == ["NOT"]:
                values.append("IS NOT")
            else:
                values.append("IS")
        elif token == "NOT" and following and following[0] == "NULL":
            values.append("NOT NULL")
        elif token == "NOT" and following and following[0] in _OPERATOR_WORDS:
            values.append(f"NOT {following[0]}")
        elif token in _OPERATOR_WORDS:
            values.append(token)
    return sorted(set(values))


def _heading_identity_surface(block: Mapping[str, Any]) -> str:
    """Return the nearest bounded identity in heading ancestry, if any."""
    for heading in reversed(list(block.get("heading_ancestry") or [])):
        value = re.sub(r"\s+", " ", str(heading or "")).strip()
        calls = _CALLABLE_TOKEN_RE.findall(value)
        if calls:
            return str(calls[-1]).strip()
        labelled = re.match(r"^([A-Za-z_][A-Za-z0-9_$]*)\s+(?:function|operator|datatype|data\s+type)$", value, re.I)
        if labelled:
            return labelled.group(1)
        if re.fullmatch(r"[+*/<>=!|&^~%?-]{1,3}", value):
            return value
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", value) and value.upper() not in _STOPWORDS:
            return value
    return ""


def _canonical_block_id(blocks: list[Mapping[str, Any]], current: Mapping[str, Any], canonical: str) -> str:
    """Find the nearest preceding bounded block for an alias relation."""
    current_order = int(current.get("order") or 0)
    canonical_key = normalize_identity(canonical)
    for candidate in reversed(sorted(
        [row for row in blocks if int(row.get("order") or 0) < current_order],
        key=lambda row: int(row.get("order") or 0),
    )):
        values = _heading_matches(str(candidate.get("text") or "")) if candidate.get("block_kind") == "HEADING" else []
        values = values or _signature_matches(str(candidate.get("text") or ""), "signature")
        if any(normalize_identity(value[0]) == canonical_key for value in values):
            return str(candidate.get("block_id") or "")
    return ""


def _table_surface_records(block: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extract identity-bearing table cells with explicit structural roles."""
    metadata = block.get("table_metadata") or {}
    roles = table_metadata_roles(block)
    if not roles or is_header_row(block, roles):
        return []
    cells = list(metadata.get("cells") or [])
    groups = list(metadata.get("cell_items") or [])
    if len(groups) != len(cells):
        groups = [[value] for value in cells]
    canonical_surfaces: list[dict[str, Any]] = []
    alias_columns: list[tuple[dict[str, Any], list[str]]] = []
    records: list[dict[str, Any]] = []
    for role_info in roles:
        index = int(role_info["index"])
        role = str(role_info.get("structural_role") or "PROSE")
        values = groups[index] if index < len(groups) else ([cells[index]] if index < len(cells) else [])
        values = [str(value or "").strip() for value in values if str(value or "").strip()]
        if not values and index < len(cells) and str(cells[index] or "").strip():
            values = [str(cells[index]).strip()]
        if role in {"DESCRIPTION", "PARAMETER", "EXAMPLE", "PROSE", "CODE_CONTEXT"}:
            continue
        if role == "ALIAS":
            alias_columns.append((role_info, values))
            continue
        for cell_value in values:
            if role == "SIGNATURE":
                matches = _signature_matches(cell_value, "table_row")
            else:
                pieces = split_surface_items(cell_value, split_separators=False)
                matches = []
                for piece in pieces:
                    matches.extend(_identity_matches(
                        piece, "table_row", strict=True,
                        allow_contextual_keyword=is_type_context(block),
                    ))
            for raw, signals, source in matches:
                item = {
                    "raw": raw,
                    "signals": sorted(set(signals) | {"TABLE_CELL", f"STRUCTURAL_{role}"}),
                    "source": source,
                    "structural_role": role,
                    "surface_text": cell_value,
                    "header": str(role_info.get("header") or ""),
                    "cell_index": index,
                    "relation_type": "CANONICAL",
                    "alias_of": "",
                    "alias_of_block_id": "",
                    "surface_type_hint": role_info.get("surface_type_hint"),
                }
                canonical_surfaces.append(item)
                records.append(item)

    canonical = canonical_surfaces[0] if canonical_surfaces else None
    if canonical:
        for role_info, values in alias_columns:
            for cell_value in values:
                for piece in split_surface_items(cell_value):
                    matches = _signature_matches(piece, "table_row") if "(" in piece else _identity_matches(
                        piece, "table_row", strict=True,
                    )
                    for raw, signals, source in matches:
                        records.append({
                            "raw": raw,
                            "signals": sorted(set(signals) | {"TABLE_CELL", "STRUCTURAL_ALIAS", "EXPLICIT_ALIAS_RELATION"}),
                            "source": source,
                            "structural_role": "ALIAS",
                            "surface_text": cell_value,
                            "header": str(role_info.get("header") or ""),
                            "cell_index": int(role_info["index"]),
                            "relation_type": "ALIAS_OF",
                            "alias_of": str(canonical.get("raw") or ""),
                            "alias_of_block_id": "",
                            "surface_type_hint": None,
                        })
    return records


def _alias_row_records(
    block: Mapping[str, Any],
    blocks: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Extract a standalone Alias/Synonym row only when owner context binds it."""
    metadata = block.get("table_metadata") or {}
    cells = [str(value or "").strip() for value in (metadata.get("cells") or []) if str(value or "").strip()]
    if not cells:
        text = re.sub(r"\s+", " ", str(block.get("text") or "")).strip()
        match = re.match(r"^(aliases?|synonyms?)\s*[:]?\s+(.+)$", text, re.I)
        if not match:
            return []
        cells = [match.group(1), match.group(2)]
    label = cells[0].casefold().rstrip(":")
    if label not in {"alias", "aliases", "synonym", "synonyms"} or len(cells) < 2:
        return []
    canonical = _heading_identity_surface(block)
    if not canonical:
        return []
    canonical_block_id = _canonical_block_id(blocks, block, canonical)
    records: list[dict[str, Any]] = []
    for cell_value in cells[1:]:
        for piece in split_surface_items(cell_value):
            matches = _signature_matches(piece, "table_row") if "(" in piece else _identity_matches(
                piece, "table_row", strict=True,
            )
            for raw, signals, source in matches:
                records.append({
                    "raw": raw,
                    "signals": sorted(set(signals) | {"ALIAS_LABEL_ROW", "EXPLICIT_ALIAS_RELATION"}),
                    "source": source,
                    "structural_role": "ALIAS",
                    "surface_text": cell_value,
                    "header": cells[0],
                    "cell_index": 1,
                    "relation_type": "ALIAS_OF",
                    "alias_of": canonical,
                    "alias_of_block_id": canonical_block_id,
                    "surface_type_hint": None,
                })
    return records


def _non_defining_surfaces(block,role,reason):
    """Retain references in a proved body without creating declarations."""
    if block.get('block_kind') not in {'CODE','TEXT','DEFINITION'}:return []
    from agents.contracts.definition_entry import CALLABLE_HEAD
    return [{'raw':raw,'signals':signals,'source':source,'structural_role':'CODE_CONTEXT',
             'surface_text':str(block.get('text') or ''),'header':'','cell_index':None,
             'relation_type':'CANONICAL','alias_of':'','alias_of_block_id':'','surface_type_hint':None,
             'non_defining_role':role,'identity_role_reason':reason}
            for raw,signals,source in _surface_values(block) if CALLABLE_HEAD.match(raw)]


def _surface_records(
    block: Mapping[str, Any],
    *,
    page: Mapping[str, Any],
    owner: Mapping[str, Any],
    blocks: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    from agents.owner.profile_execution import revalidate_explicit_authority
    authoritative = []
    for proof in owner.get('definition_section_proofs', []):
        if (proof.get('structure_execution') or {}).get('authority') != 'EXPLICIT_DEFINITION':continue
        for name in proof.get('identity_values', []):
            parsed = revalidate_explicit_authority(proof, block, name)
            if parsed:
                authoritative.append({'raw': name, 'signals': ['PROVEN_LOCAL_DEFINITION_ANCHOR'],
                    'source': 'heading', 'structural_role': 'FEATURE_NAME',
                    'surface_text': block.get('text', ''), 'header': '', 'cell_index': None,
                    'relation_type': 'CANONICAL', 'alias_of': '', 'alias_of_block_id': '',
                    'surface_type_hint': None, 'definition_feature_type': parsed['feature_type'],
                    'definition_section_id': proof['section_id'], 'explicit_profile_authority': True})
    if authoritative:return authoritative
    if block.get('preservation_only'):
        return []  # Unclassified source text is content, not a new declaration.
    kind = str(block.get("block_kind") or "OTHER")
    observation = block.get('definition_context') or {}
    from agents.owner.profile_execution import identity_authorized
    if not identity_authorized(owner,block.get('block_id')):
        if any(block.get('block_id') in p.get('block_ids',[]) for p in owner.get('definition_section_proofs',[])):
            return _non_defining_surfaces(block,explicit_usage_role(block) or 'CROSS_REFERENCE','PROFILE_NON_DECLARATION_FIELD')
        return []  # A failed explicit identity locator cannot be bypassed.
    if observation.get('role')=='DEVELOPER_API_DECLARATION':return []
    usage=explicit_usage_role(block)
    if usage:return _non_defining_surfaces(block,usage,'EXPLICIT_USE_SITE')
    # The proved anchor/body boundary also applies to legacy definition
    # proofs. Unproven body calls are references, not declaration votes.
    containing=[p for p in owner.get('definition_section_proofs',[])
                if block.get('block_id') in p.get('block_ids',[])]
    if containing and not any(block.get('block_id') in p.get('definition_anchor_block_ids',[]) for p in containing):
        return _non_defining_surfaces(block,'CROSS_REFERENCE','PROVEN_DEFINITION_BODY_NOT_ANCHOR')
    from agents.owner.structure_execution import composed_definition
    from agents.owner.definition_patterns import verified_pattern
    for proof in owner.get('definition_section_proofs') or []:
        if not composed_definition(proof) or block.get('block_id') not in proof.get('block_ids', []):
            continue
        if block['block_id'] not in proof.get('definition_anchor_block_ids', []):
            return []  # A field value is content, never a second Feature name.
        if (proof.get('structure_execution') or {}).get('structure')=='D':
            existing=_table_surface_records(block)
            if existing:
                selected=[]
                for record in existing:
                    name=next((n for n in proof['identity_values'] if normalize_identity(record['raw'])==normalize_identity(n)),None)
                    if name is None:continue
                    parsed=verified_pattern(proof,block,name)
                    if parsed:selected.append(dict(record,definition_section_id=proof['section_id'],
                                                    definition_feature_type=parsed['feature_type']))
                if selected:return selected
        result=[]
        for name in proof['identity_values']:
            parsed=verified_pattern(proof,block,name)
            if not parsed:continue
            formal=parsed.get('formal_value') if parsed['feature_type']=='FUNCTION' else None
            result.append({'raw':formal or name,'signals':['PROVEN_LOCAL_DEFINITION_ANCHOR'],
                'source':('table_row' if (proof.get('structure_execution') or {}).get('structure')=='D' else
                          'signature' if formal else 'heading'),
                'structural_role':'SIGNATURE' if formal else 'FEATURE_NAME',
                'surface_text':block['text'],'header':'','cell_index':None,'relation_type':'CANONICAL',
                'alias_of':'','alias_of_block_id':'','surface_type_hint':None,
                'definition_feature_type':parsed['feature_type'],'definition_section_id':proof['section_id']})
        if result:return result
    if any(p.get('definition_pattern')=='REFERENCE_DECLARATION_SECTION'
           and block.get('block_id') in p.get('block_ids',[])
           and block.get('block_id') not in p.get('definition_anchor_block_ids',[])
           for p in owner.get('definition_section_proofs',[])):
        return []  # Body is semantic evidence, not a second identity declaration.
    for proof in owner.get('definition_section_proofs') or []:
        if proof.get('definition_pattern')=='FUNCTION_DECLARATION_SECTION' and block.get('block_id') in proof['definition_anchor_block_ids']:
            from agents.owner.function_definitions import verified_function
            values=[]
            for name in proof['identity_values']:
                parsed=verified_function(proof,block,name)
                if not parsed:continue
                values.append({'raw':parsed['formal_value'] or name,'signals':['PROVEN_LOCAL_DEFINITION_ANCHOR'],
                    'source':'signature' if parsed['formal_value'] else 'heading',
                    'structural_role':'SIGNATURE' if parsed['formal_value'] else 'FEATURE_NAME',
                    'surface_text':block['text'],'header':'','cell_index':None,'relation_type':'CANONICAL',
                    'alias_of':'','alias_of_block_id':'','surface_type_hint':None,
                    'definition_feature_type':'FUNCTION','definition_section_id':proof['section_id']})
            return values
        if (proof.get('definition_pattern') in {'SIGNATURE_LED_LIST_ITEM','OPERATOR_LED_LIST_ITEM','DATATYPE_SELF_DEFINITION','PARAGRAPH_LABEL_SIGNATURE_SECTION','PROFILE_LOCAL_DEFINITION','SOURCE_RELATION_DEFINITION','REFERENCE_DECLARATION_SECTION'}
                and block.get('block_id') in proof.get('definition_anchor_block_ids', [])):
            names = set(proof.get('identity_values') or [])
            kind=proof.get('feature_type')
            if kind in {'OPERATOR','DATATYPE'}:
                return [{'raw':name,'signals':['PROVEN_LOCAL_DEFINITION_ANCHOR'],
                    'source':'text_operator' if kind=='OPERATOR' else 'text',
                    'structural_role':'FEATURE_NAME','surface_text':block['text'],
                    'header':'','cell_index':None,'relation_type':'CANONICAL','alias_of':'','alias_of_block_id':'',
                    'surface_type_hint':None,'definition_feature_type':kind,'definition_section_id':proof['section_id']}
                    for name in sorted(names)]
            declared=(proof.get('formal_signature_values') or {}).get(block['block_id'])
            if declared is not None and proof.get('reference_mode')=='COMPOUND_CATALOG_ROW':
                # The local definition proof has already separated the formal
                # declaration from its return type and prose. Reuse its grammar
                # for compound signatures instead of truncating/rejecting them
                # with the older NAME(args)-only surface parser.
                from agents.owner.reference_definitions import verified_reference
                # Consume the same revalidated local declaration members as
                # Owner/Type. Re-parsing a proven SQL grammar with the older
                # call-shape parser silently discarded quoted alternatives.
                matches=[(name,value) for name in sorted(names)
                         if verified_reference(proof,block,name)
                         for values in (proof.get('subject_formal_values') or {}).get(name,{}).values()
                         for value in values]
            else:
                matches=[(name,raw) for value in (declared if declared is not None else [block['text']])
                         for name,raw in formal_signatures(value)]
            return [{'raw': raw, 'signals': signals + ['PROVEN_LOCAL_DEFINITION_ANCHOR'],
                     'source': 'signature', 'structural_role': 'SIGNATURE',
                     'surface_text': block['text'], 'header': '', 'cell_index': None,
                     'relation_type': 'CANONICAL', 'alias_of': '', 'alias_of_block_id': '',
                     'surface_type_hint': None, 'definition_section_id': proof['section_id'],
                     'definition_feature_type': 'FUNCTION' if proof.get('reference_mode')=='COMPOUND_CATALOG_ROW' else None}
                    for name,raw in matches
                    for signals in [['SIGNATURE','IDENTITY_SHAPED_SURFACE']]
                    if name in names]
    if observation.get('role')=='DECLARATION_CATALOG' and observation.get('domain')!='EMBEDDED_QUERY_LANGUAGE':
        declared=[{'raw':m['raw_entry'] if m['feature_type']=='FUNCTION' else m['identity'],
            'signals':['EXPLICIT_CATALOG_MEMBER'],'source':'table_row','structural_role':'FEATURE_NAME',
            'surface_text':m['raw_entry'],'header':m['feature_type'],'cell_index':m['cell_index'],
            'relation_type':'CANONICAL','alias_of':'','alias_of_block_id':'','surface_type_hint':m['feature_type']}
            for m in observation['members']]
        occupied={m['cell_index'] for m in observation['members']}
        # Declaration columns compose. Adding an Operator column must not
        # replace the existing callable/alias columns of the same source row.
        legacy=_table_surface_records(block)+_alias_row_records(block,blocks)
        return declared+[r for r in legacy if r.get('cell_index') not in occupied]
    if observation.get('role')=='OPERATOR_CATALOG' and observation.get('domain')!='EMBEDDED_QUERY_LANGUAGE':
        return [{'raw':m['identity'],'signals':['OPERATOR_SURFACE','EXPLICIT_CATALOG_MEMBER'],
            'source':'table_row','structural_role':'FEATURE_NAME','surface_text':m['raw_entry'],
            'header':'Operators','cell_index':m['cell_index'],'relation_type':'CANONICAL','alias_of':'',
            'alias_of_block_id':'','surface_type_hint':'OPERATOR'} for m in observation['members']]
    if observation.get('role') in {'STORAGE_CLASS_DEFINITION','DATATYPE_DEFINITION_HEADING'}:
        return [{'raw':observation['identity'],'signals':['EXPLICIT_DATATYPE_DEFINITION','ATOMIC_SURFACE'],
            'source':'heading' if kind=='HEADING' else 'text','structural_role':'FEATURE_NAME',
            'surface_text':block['text'],'header':'Data type','cell_index':None,
            'relation_type':'CANONICAL','alias_of':'','alias_of_block_id':'','surface_type_hint':'DATATYPE'}]
    if observation.get('role') == 'PAGE_FUNCTION_DEFINITION':
        # The empty-argument spelling is literally present in the defining
        # sentence; it is an identity occurrence, not invented formal syntax.
        return [{'raw': observation['identity']+'()', 'signals': ['EXPLICIT_SELF_DEFINITION'],
                 'source': 'signature', 'structural_role': 'SIGNATURE', 'surface_text': block['text'],
                 'header': '', 'cell_index': None, 'relation_type': 'CANONICAL', 'alias_of': '',
                 'alias_of_block_id': '', 'surface_type_hint': None}]
    if kind in {"TABLE_ROW", "TABLE"}:
        records = _table_surface_records(block)
        records.extend(_alias_row_records(block, blocks))
        if records:
            return records
    # Consume a complete, physically marked formal declaration directly.
    # A body mention merely containing inline code is insufficient; SQL use
    # and proved non-anchor fields have already returned above.
    if kind=='TEXT':
        from agents.contracts.definition_entry import observed_callable_declaration
        from agents.owner.profile_execution import NEGATIVE
        nodes=block.get('inline_nodes',[])
        literal=' '.join(str(block.get('text') or '').split())
        whole_code=next((n for n in nodes if n.get('element_name')=='code'
                        and ' '.join(str(n.get('text') or '').split())==literal),None)
        replaceable=any('replaceable' in n.get('semantic_classes',[]) for n in nodes)
        # Recover the variadic layout that the legacy token scanner truncates.
        # Do not turn every parenthesized keyword (e.g. a predicate grammar)
        # into a new callable merely because it has a replaceable argument.
        variadic=bool(re.search(r',\s*(?:\.\.\.|…)',literal))
        parsed=observed_callable_declaration(literal,nodes) if whole_code and replaceable and variadic else None
        if parsed and not NEGATIVE.search(' / '.join(block.get('heading_ancestry',[]))):
            return [{'raw':parsed[1],'signals':['OBSERVED_COMPLETE_FORMAL_DECLARATION'],
                'source':'signature','structural_role':'SIGNATURE','surface_text':literal,
                'header':'','cell_index':None,'relation_type':'CANONICAL','alias_of':'','alias_of_block_id':'',
                'surface_type_hint':None,'identity_role_reason':'OBSERVED_COMPLETE_FORMAL_DECLARATION',
                'declaration_locator':whole_code.get('dom_path')}]
    values = _surface_values(block)
    records = []
    for raw, signals, source in values:
        if source in {"signature", "definition_list"} and "(" in raw:
            structural_role = "SIGNATURE"
        elif source in {"heading", "table_row", "list_item", "text_operator"}:
            structural_role = "FEATURE_NAME"
        elif source in {"inline_code", "text"}:
            structural_role = "CODE_CONTEXT" if source == "inline_code" else "PROSE"
        else:
            structural_role = "PROSE"
        records.append({
            "raw": raw,
            "signals": list(signals),
            "source": source,
            "structural_role": structural_role,
            "surface_text": str(block.get("text") or ""),
            "header": "",
            "cell_index": None,
            "relation_type": "CANONICAL",
            "alias_of": "",
            "alias_of_block_id": "",
            "surface_type_hint": None,
        })
    return records


def _surface_values(block: Mapping[str, Any]) -> list[tuple[str, list[str], str]]:
    if block.get('preservation_only'):
        return []
    text = re.sub(r"\s+", " ", str(block.get("text") or "")).strip()
    if not text:
        return []
    kind = str(block.get("block_kind") or "OTHER")
    values: list[tuple[str, list[str], str]] = []
    if kind in {"TABLE_ROW", "TABLE"}:
        slots = _table_identity_values(block)
        if slots:
            # A catalog row is normally identified by its first identity-like
            # column.  If the header declares multiple type/name columns,
            # each such column is an identity surface; description/example
            # columns remain semantic evidence and are not tokenized.
            for cell_text, operator_surface in slots:
                if cell_text:
                    values.extend(_identity_matches(
                        cell_text, "table_row", strict=True,
                        allow_contextual_keyword=is_type_context(block),
                    ))
                    if operator_surface:
                        for operator_word in _operator_word_matches(cell_text):
                            values.extend(_identity_matches(operator_word, "table_row", strict=True))
        else:
            values.extend(_identity_matches(
                text, "table_row", strict=True,
                allow_contextual_keyword=is_type_context(block),
            ))
    elif kind == "CODE":
        # A code example is not a vocabulary list.  Keep callable/operator
        # surfaces, but do not tokenize SELECT, argument names, or prose-like
        # words from the rest of the example.
        values.extend(_signature_matches(text, "signature"))
    elif kind == "DEFINITION":
        values.extend(_definition_matches(text))
    elif kind == "LIST_ITEM":
        # List items frequently contain a feature label followed by a long
        # description.  Inline literals are the bounded identity surface; a
        # bare list item is accepted only when the complete item is one
        # callable/identifier, never by scanning its prose.
        literals = (block.get("code_metadata") or {}).get("inline_literals") or []
        for literal in literals:
            values.extend(_identity_matches(str(literal), "list_item", strict=True))
        if not literals:
            values.extend(_identity_matches(text, "list_item", strict=True))
    elif kind == "HEADING":
        ancestry = " ".join(str(value or "") for value in (block.get("heading_ancestry") or []))
        operator_values = _compound_operator_values(text, ancestry)
        if operator_values:
            values.extend((value, ["heading", "OPERATOR_SURFACE", "IDENTITY_SHAPED_SURFACE"], "heading") for value in operator_values)
        else:
            values.extend(_heading_matches(text))
    elif kind == "LINK":
        values.extend(_identity_matches(text, "anchor", strict=True))
    else:
        # Text is a weak surface.  Only a callable surface is eligible; the
        # rest of a sentence is semantic evidence, not identity evidence.
        ancestry = " ".join(str(value or "") for value in (block.get("heading_ancestry") or []))
        if kind == "TEXT" and (_OPERATOR_ROLE_RE.search(text) or _OPERATOR_ROLE_RE.search(ancestry)):
            operator_values = []
            for match in re.finditer(
                r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9]*(?:\s+[A-Z][A-Z0-9]*){0,5})\s+\b(?:operator|predicate)\b",
                text,
            ):
                operator_values.extend(_compound_operator_values(match.group(1), "operator"))
            for match in re.finditer(r"\b(?:written|denoted|represented)\s+(?:with|by)\s+([^\s,.;:)]+)", text, re.I):
                operator_values.extend(_compound_operator_values(match.group(1), "operator"))
            for match in re.finditer(
                r"\b(?:use|using|apply|applied)\s+([A-Z][A-Z0-9]*(?:\s+[A-Z][A-Z0-9]*){0,5})(?=\s*[:.,;]|\s+operator\b)",
                text,
            ):
                operator_values.extend(_compound_operator_values(match.group(1), "operator"))
            for value in dict.fromkeys(operator_values):
                values.append((value, ["text", "OPERATOR_SURFACE", "IDENTITY_SHAPED_SURFACE"], "text_operator"))
        elif kind == "TEXT" and is_type_context(block):
            first = typed_leading_label(block, text)
            if first:
                values.extend(_identity_matches(
                    first, "text", strict=True,
                    allow_contextual_keyword=True,
                ))
        else:
            callable_values = callable_tokens(text)
            residue = re.sub(r"[A-Za-z_][A-Za-z0-9_$]*\s*\([^\n)]*\)", "", text).strip(" \t,;|/")
            if callable_values and not residue:
                values.extend([
                    (value, ["text", "IDENTITY_SHAPED_SURFACE", "SIGNATURE"], "text")
                    for value in callable_values
                ])
    inline = (block.get("code_metadata") or {}).get("inline_literals") or []
    for literal in inline:
        values.extend(_identity_matches(str(literal), "inline_code", strict=True))
    return values


def _identity_matches(
    text: str,
    source: str,
    strict: bool = False,
    *,
    allow_contextual_keyword: bool = False,
) -> list[tuple[str, list[str], str]]:
    values: list[tuple[str, list[str], str]] = []
    normalized_text = re.sub(r"\s+", " ", str(text or "")).strip()
    if strict and _PHRASE_RE.fullmatch(normalized_text) and normalized_text == normalized_text.upper():
        values.append((normalized_text, [source, "IDENTITY_SHAPED_SURFACE", "ATOMIC_SURFACE"], source))
        return values
    for match in _IDENTITY_RE.finditer(text):
        name, suffix = match.group(1), match.group(2) or ""
        normalized = name.upper()
        # A word such as AND/OR/IN can be a real operator when it comes from
        # an explicit operator-labelled surface.  Keep the normal stop-word
        # guard everywhere else so prose does not become identity evidence.
        if (
            normalized in _STOPWORDS
            and not (strict and normalized in _OPERATOR_WORDS)
            and not (allow_contextual_keyword and normalized in {"NULL", "TRUE", "FALSE"})
        ) or len(name) > 96:
            continue
        if normalized in _GRAMMAR_TOKENS and not (strict and normalized_text.upper() == normalized):
            continue
        if strict and not suffix and normalized_text.upper() != normalized:
            continue
        raw = (name + suffix).strip()
        signals = [source, "IDENTITY_SHAPED_SURFACE"]
        if strict and normalized_text.upper() == normalized:
            signals.append("ATOMIC_SURFACE")
        if suffix:
            signals.append("SIGNATURE")
        values.append((raw, signals, source))
    for match in _OPERATOR_RE.finditer(text):
        values.append((match.group(1), [source, "SYMBOL_SURFACE"], source))
    return values


def _signature_matches(text: str, source: str) -> list[tuple[str, list[str], str]]:
    values = []
    normalized_text = re.sub(r"\s+", " ", str(text or "")).strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", normalized_text) or (_PHRASE_RE.fullmatch(normalized_text) and normalized_text == normalized_text.upper()):
        values.extend(_identity_matches(normalized_text, source, strict=True))
        return values
    for match in _IDENTITY_RE.finditer(text):
        name, suffix = match.group(1), match.group(2) or ""
        if not suffix:
            continue
        normalized = name.upper()
        if normalized in _STOPWORDS or normalized in _GRAMMAR_TOKENS or len(name) > 96:
            continue
        values.append(((name + suffix).strip(), [source, "IDENTITY_SHAPED_SURFACE", "SIGNATURE"], source))
    # Preserve symbolic operator definitions such as `a + b`, but not the
    # punctuation in a multi-statement example containing ordinary SQL.
    if not re.search(r"\b(?:SELECT|FROM|WHERE|INSERT|UPDATE|DELETE)\b", text, re.I):
        for match in _OPERATOR_RE.finditer(text):
            values.append((match.group(1), [source, "SYMBOL_SURFACE"], source))
    return values


def _identity_header(header: str) -> bool:
    words = {word.upper() for word in re.findall(r"[A-Za-z_][A-Za-z0-9_$]*", header)}
    return bool(words & {"TYPE", "TYPES", "FUNCTION", "FUNCTIONS", "OPERATOR", "OPERATORS", "NAME", "NAMES", "KEYWORD", "KEYWORDS", "COMMAND", "STATEMENT", "SYNTAX", "SIGNATURE", "EXPRESSION"})


def _heading_matches(text: str, ancestry: str = "") -> list[tuple[str, list[str], str]]:
    normalized_text = re.sub(r"\s+", " ", str(text or "")).strip().rstrip("#").strip()
    callable_values = callable_tokens(normalized_text)
    if callable_values:
        return [
            (value, ["heading", "IDENTITY_SHAPED_SURFACE", "SIGNATURE"], "heading")
            for value in callable_values
        ]
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", normalized_text):
        return _identity_matches(normalized_text, "heading", strict=True)
    # A single heading may document an alias pair or a pair of equivalent
    # declaration spellings.  Preserve each bounded label instead of making
    # ``INTEGER or INT`` one unrelated identity.
    alias_parts = [part.strip() for part in re.split(r"\s+(?:OR|/|,|\|)\s+", normalized_text, flags=re.I) if part.strip()]
    if len(alias_parts) > 1 and all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", part) for part in alias_parts):
        return [
            (part, ["heading", "IDENTITY_SHAPED_SURFACE", "ALIAS_HEADING"], "heading")
            for part in alias_parts
        ]
    if re.fullmatch(r"[+*/<>=!|&^~%?@\-\[\]]{1,4}", normalized_text):
        return [(normalized_text, ["heading", "SYMBOL_SURFACE"], "heading")]
    return []


def _definition_matches(text: str) -> list[tuple[str, list[str], str]]:
    # Definition-list bodies frequently contain calls such as
    # ``foo()``.  Only inspect the bounded leading term; never scan the body
    # as if every call were a new feature identity.
    first = re.split(r"\s+(?:returns?|is|means|describes?)\s+|[:;—–-]", text, maxsplit=1, flags=re.I)[0].strip()
    tokens = callable_tokens(first)
    if tokens:
        return [
            (token, ["definition_list", "IDENTITY_SHAPED_SURFACE", "SIGNATURE"], "definition_list")
            for token in tokens
        ]
    return _identity_matches(first, "definition_list", strict=True)


def _tier(owner: Mapping[str, Any], raw: str, source: str) -> str:
    if "(" in raw or source in {"table_row", "definition_list", "text_operator"} and owner.get("tier") == "STRONG":
        return "STRONG"
    if source in {"signature", "heading", "definition_list", "table_row", "text_operator"}:
        return "PROBABLE"
    return "WEAK"


def _risk(owner: Mapping[str, Any], source: str) -> list[str]:
    risks: list[str] = []
    if source in {"anchor", "link"}:
        risks.append("NAVIGATION_ONLY")
    if source in {"text", "heading"}:
        risks.append("WEAK_SURFACE")
    if owner.get("tier") == "WEAK":
        risks.append("LOW_OWNER_CONFIDENCE")
    return risks


def _owner_type_hint(owner: Mapping[str, Any]) -> str:
    hint = owner.get("profile_hint") or {}
    value = str(hint.get("type_hint") or owner.get("type_hint") or "UNCERTAIN").upper()
    return value if value in {"FUNCTION", "OPERATOR", "DATATYPE", "UNCERTAIN"} else "UNCERTAIN"


def _owner_local_binding(
    owner: Mapping[str, Any],
    surface_record: Mapping[str, Any],
    normalized_identity: str,
    block: Mapping[str, Any],
) -> tuple[bool, str]:
    """Validate that an identity surface belongs to this Owner hypothesis.

    Owner ids are lineage identifiers, not proof of subject ownership.  A
    broad/page owner may contain several sibling features, while a bounded
    owner normally carries an explicit structural anchor.  Keep unbound rows
    in the occurrence ledger for diagnostics, but prevent them from becoming
    candidates or type evidence.
    """
    role = str(surface_record.get("structural_role") or "").upper()
    source = str(surface_record.get("source") or "").lower()
    kind = str(block.get("block_kind") or block.get("block_type") or "").upper()
    observed=block.get('definition_context') or {}
    if surface_record.get('non_defining_role'):
        heads={normalize_identity(n) for n in owner.get('owner_scope_identity_values',[])}
        heads.update(normalize_identity(n) for p in owner.get('definition_section_proofs',[]) for n in p.get('identity_values',[]))
        if heads and normalized_identity not in heads:return False,'FOREIGN_USAGE_SURFACE'
        return True,'OWNER_ALIGNED_USAGE' if heads else 'LOCAL_USAGE_ONLY'
    if surface_record.get('definition_section_id') and any(
            p['section_id']==surface_record['definition_section_id']
            and normalized_identity in p.get('identity_values',[])
            and block.get('block_id') in p.get('definition_anchor_block_ids',[])
            for p in owner.get('definition_section_proofs',[])):
        return True,'PROVEN_LOCAL_DEFINITION_ANCHOR'
    if (observed.get('role') in {'STORAGE_CLASS_DEFINITION','DATATYPE_DEFINITION_HEADING'}
        and normalize_identity(observed.get('identity'))==normalized_identity
        and any(normalized_identity in p.get('identity_values',[]) and block.get('block_id') in p.get('block_ids',[])
                for p in owner.get('definition_section_proofs',[]))):
        return True,'OBSERVED_LOCAL_DEFINITION_ANCHOR'
    anchors = {
        normalize_identity(value)
        for value in (owner.get("owner_scope_identity_values") or owner.get("identity_values") or [])
        if normalize_identity(value)
    }
    primary = normalize_identity(str(owner.get("primary_identity") or ""))
    if primary:
        anchors.add(primary)
    alias_target = normalize_identity(str(surface_record.get("alias_of") or ""))
    if anchors:
        if normalized_identity in anchors:
            return True, "EXPLICIT_IDENTITY_ANCHOR"
        if alias_target and alias_target in anchors:
            return True, "EXPLICIT_ALIAS_TARGET"
        # A bounded operator section can introduce a second surface in
        # prose (for example a negated form) without repeating it in the
        # heading.  The surface itself must still be explicitly structural;
        # ordinary prose never passes this branch.
        if (
            source == "text_operator"
            and role in {"FEATURE_NAME", "SIGNATURE", "ALIAS"}
            and kind in {"HEADING", "TEXT"}
            and str(owner.get("owner_scope_locality") or "BOUNDED").upper() == "BOUNDED"
        ):
            return True, "BOUNDED_OPERATOR_CONTEXT_SURFACE"
        return False, "FOREIGN_LOCAL_SURFACE"

    if role in {"FEATURE_NAME", "SIGNATURE", "ALIAS"} and source in {
        "heading", "signature", "definition_list", "table_row", "list_item", "text_operator",
    }:
        return True, "STRUCTURAL_IDENTITY_SURFACE"
    if kind in {"HEADING", "DEFINITION", "TABLE_ROW", "LIST_ITEM"} and role in {"FEATURE_NAME", "SIGNATURE", "ALIAS"}:
        return True, "STRUCTURAL_IDENTITY_SURFACE"
    return False, "UNBOUNDED_SURFACE"


def build_identity_occurrences(
    documents: Iterable[Mapping[str, Any]],
    owners: Iterable[Mapping[str, Any]],
    profile: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Retain every raw identity surface before candidate aggregation."""
    pages = {str(row["page_id"]): row for row in documents}
    blocks_by_page = {
        page_id: {str(block["block_id"]): block for block in page.get("blocks", [])}
        for page_id, page in pages.items()
    }
    owner_ids_by_page_block: dict[tuple[str, str], set[str]] = defaultdict(set)
    for owner in owners:
        page_id = str(owner.get("page_id") or "")
        owner_id = str(owner.get("owner_id") or "")
        if not page_id or not owner_id:
            continue
        for block_id in owner.get("member_block_ids", []):
            owner_ids_by_page_block[(page_id, str(block_id))].add(owner_id)
    occurrences: list[dict[str, Any]] = []
    for owner in owners:
        page_id = str(owner.get("page_id") or "")
        page = pages.get(page_id)
        if not page:
            continue
        blocks = blocks_by_page.get(page_id, {})
        for block_id in owner.get("member_block_ids", []):
            block = blocks.get(str(block_id))
            if not block:
                continue
            for surface_index, surface_record in enumerate(_surface_records(
                block, page=page, owner=owner, blocks=list(page.get("blocks") or []),
            )):
                raw = str(surface_record.get("raw") or "")
                signals = list(surface_record.get("signals") or [])
                source = str(surface_record.get("source") or "")
                structural_role = str(surface_record.get("structural_role") or "PROSE")
                normalized = normalize_identity(raw)
                if not normalized:
                    continue
                # A successful physical slot authorizes its declared subjects,
                # not every token the compatibility parser finds inside it.
                from agents.owner.profile_execution import identity_authorized
                if (structural_role in {'FEATURE_NAME','SIGNATURE','ALIAS'}
                        and not surface_record.get('non_defining_role')
                        and not identity_authorized(owner,block_id,normalized)):continue
                owner_local_binding, owner_binding_reason = _owner_local_binding(
                    owner, surface_record, normalized, block,
                )
                surface = assess_surface(
                    block,
                    page=page,
                    owner=owner,
                    profile_hint=(owner.get("profile_hint") or {}) if owner else None,
                )
                if surface_record.get('definition_section_id'):
                    # A validated native container proof, not the Profile prior,
                    # establishes this particular block as a defining anchor.
                    surface = dict(surface, surface_role='FEATURE_DEFINITION',
                                   surface_confidence=0.92, surface_confidence_label='HIGH',
                                   feature_evidence=list(surface.get('feature_evidence') or []) +
                                   ['PROVEN_LOCAL_DEFINITION_ANCHOR'])
                occurrence_role = classify_occurrence_role(
                    raw_identity=raw,
                    source_type=source,
                    block=block,
                    surface=surface,
                )
                if surface_record.get('non_defining_role'):
                    occurrence_role=surface_record['non_defining_role']
                if surface_record.get('explicit_profile_authority'):
                    occurrence_role = 'DEFINING_IDENTITY'
                type_evidence = infer_type_evidence(
                    raw_identity=raw,
                    occurrence_role=occurrence_role,
                    block=block,
                    owner=owner,
                    page=page,
                    surface=surface,
                    structural_role=structural_role,
                    surface_type_hint=surface_record.get("surface_type_hint"),
                )
                if surface_record.get('definition_feature_type'):
                    # _surface_records grants this flag only after re-reading
                    # the explicit Profile declaration from frozen source.
                    # Its local type must control grouping now, not merely be
                    # corrected after a mixed chapter heading splits the unit.
                    if surface_record.get('explicit_profile_authority'):
                        type_evidence = []
                    type_evidence.append({'type':surface_record['definition_feature_type'],'strength':'STRONG',
                        'reason':'proven_local_definition_anchor','source':'definition_section'})
                if structural_role in {"FEATURE_NAME", "SIGNATURE", "ALIAS"} and occurrence_role not in {
                    'EXAMPLE_TOKEN','CROSS_REFERENCE'}:
                    # Structural identity surfaces remain in the ledger even
                    # when the semantic type is still unknown.  An explicit
                    # alias relation is also a defining identity surface, but
                    # it is never merged by string similarity here.
                    if occurrence_role not in {"DEFINING_IDENTITY", "FORMAL_VARIANT"}:
                        occurrence_role = "DEFINING_IDENTITY"
                defining = is_defining_role(occurrence_role)
                occurrence_id = stable_id(
                    "identity_occurrence",
                    [owner.get("owner_id"), block_id, raw, source, structural_role, surface_index],
                )
                occurrences.append({
                    "schema_version": "IdentityOccurrence/v1",
                    "occurrence_id": occurrence_id,
                    "raw_identity": raw,
                    "normalized_identity": normalized,
                    "owner_id": str(owner.get("owner_id") or ""),
                    "page_id": page_id,
                    "source_url": str(page.get("url") or ""),
                    "source_block_id": str(block_id),
                    "source_type": source,
                    "raw_evidence": str(block.get("text") or ""),
                    "surface_text": str(surface_record.get("surface_text") or raw),
                    "structural_role": structural_role,
                    "definition_section_id": surface_record.get('definition_section_id'),
                    "header_context": str(surface_record.get("header") or ""),
                    "cell_index": surface_record.get("cell_index"),
                    "row_index": (block.get("table_metadata") or {}).get("row_index"),
                    "table_id": (block.get("table_metadata") or {}).get("table_id"),
                    "relation_type": str(surface_record.get("relation_type") or "CANONICAL"),
                    "alias_of": str(surface_record.get("alias_of") or ""),
                    "alias_of_normalized": normalize_identity(str(surface_record.get("alias_of") or "")) if surface_record.get("alias_of") else "",
                    "alias_of_block_id": str(surface_record.get("alias_of_block_id") or ""),
                    "alias_of_owner_id": "",
                    "identity_signals": sorted(set(signals)),
                    "type_hint": _owner_type_hint(owner),
                    "occurrence_role": occurrence_role,
                    "defining": defining,
                    "surface_role": surface["surface_role"],
                    "surface_confidence": surface["surface_confidence"],
                    "surface_confidence_label": surface["surface_confidence_label"],
                    "surface_evidence": list(surface.get("surface_evidence") or []),
                    "feature_evidence": list(surface.get("feature_evidence") or []),
                        "type_evidence": type_evidence,
                    "owner_local_binding": owner_local_binding,
                    "owner_binding_reason": owner_binding_reason,
                    "identity_role_reason": surface_record.get('identity_role_reason'),
                    "declaration_locator": surface_record.get('declaration_locator'),
                    "owner_anchor_identities": sorted({
                        normalize_identity(value)
                        for value in (owner.get("owner_scope_identity_values") or owner.get("identity_values") or [])
                        if normalize_identity(value)
                    }),
                    "owner_scope_block_ids": list(owner.get("owner_scope_block_ids") or owner.get("member_block_ids") or []),
                    "owner_scope_mode": str(owner.get("owner_scope_mode") or ""),
                    "owner_scope_locality": str(owner.get("owner_scope_locality") or ""),
                    "source_context": {
                        "owner_kind": str(owner.get("owner_kind") or ""),
                        "template": str(owner.get("template") or ""),
                        "profile_hint": dict(owner.get("profile_hint") or {}),
                        "table_headers": list((block.get("table_metadata") or {}).get("headers") or []),
                        "heading_ancestry": list(block.get("heading_ancestry") or []),
                        "block_kind": str(block.get("block_kind") or ""),
                        "structural_role": structural_role,
                        "owner_local_binding": owner_local_binding,
                        "owner_binding_reason": owner_binding_reason,
                        "header": str(surface_record.get("header") or ""),
                        "cell_index": surface_record.get("cell_index"),
                    },
                    "provenance": {
                        "dbms": str(page.get("dbms") or ""),
                        "source_url": str(page.get("url") or ""),
                        "page_id": page_id,
                        "page_hash": str(page.get("page_hash") or ""),
                        "html_path": str(page.get("html_path") or ""),
                        "owner_id": str(owner.get("owner_id") or ""),
                        "source_block_id": str(block_id),
                        "owner_local_binding": owner_local_binding,
                        "owner_binding_reason": owner_binding_reason,
                        "owner_scope_block_ids": list(owner.get("owner_scope_block_ids") or owner.get("member_block_ids") or []),
                        "owner_scope_identity_values": list(owner.get("owner_scope_identity_values") or owner.get("identity_values") or []),
                        "owner_scope_mode": str(owner.get("owner_scope_mode") or ""),
                        "discovery": dict(page.get("discovery_provenance") or {}),
                    },
                })
    for occurrence in occurrences:
        if occurrence.get("owner_local_binding") is False:
            continue
        target_block_id = str(occurrence.get("alias_of_block_id") or "")
        if occurrence.get("relation_type") != "ALIAS_OF" or not target_block_id:
            continue
        owner_ids = sorted(owner_ids_by_page_block.get((
            str(occurrence.get("page_id") or ""), target_block_id,
        ), set()))
        if len(owner_ids) == 1:
            occurrence["alias_of_owner_id"] = owner_ids[0]
    # Explicit alias relations inherit the canonical surface's type signal.
    # This is a structural relation, not a name-similarity merge, and keeps
    # the alias identity present even when the alias spelling has no type
    # lexical cue of its own.
    canonical_by_page: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    canonical_by_page_owner: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for occurrence in occurrences:
        if occurrence.get("owner_local_binding") is False:
            continue
        if occurrence.get("relation_type") == "CANONICAL" and occurrence.get("defining"):
            page_key = str(occurrence.get("page_id") or "")
            normalized_key = str(occurrence.get("normalized_identity") or "")
            owner_key = str(occurrence.get("owner_id") or "")
            canonical_by_page[(page_key, normalized_key)].append(occurrence)
            canonical_by_page_owner[(page_key, owner_key, normalized_key)].append(occurrence)
    for occurrence in occurrences:
        if occurrence.get("owner_local_binding") is False:
            continue
        if occurrence.get("relation_type") != "ALIAS_OF":
            continue
        key = (str(occurrence.get("page_id") or ""), str(occurrence.get("alias_of_normalized") or ""))
        owner_key = str(occurrence.get("owner_id") or "")
        same_owner_targets = canonical_by_page_owner.get((key[0], owner_key, key[1]), [])
        page_targets = canonical_by_page.get(key, [])
        targets = same_owner_targets if same_owner_targets else page_targets if len({str(row.get("owner_id") or "") for row in page_targets}) == 1 else []
        if len(targets) == 1:
            target = targets[0]
            occurrence["alias_of_owner_id"] = str(target.get("owner_id") or "")
            if not occurrence.get("alias_of_block_id"):
                occurrence["alias_of_block_id"] = str(target.get("source_block_id") or "")
        inherited: list[dict[str, str]] = []
        # Type inheritance remains page-local to the explicit alias target.
        # Owner provenance is resolved conservatively above, but duplicate
        # owner hypotheses for the same target must not erase an otherwise
        # valid canonical type signal.
        for canonical in canonical_by_page.get(key, []):
            for item in canonical.get("type_evidence") or []:
                feature_type = str(item.get("type") or "")
                if feature_type not in {"FUNCTION", "OPERATOR", "DATATYPE"}:
                    continue
                inherited.append({
                    "type": feature_type,
                    "strength": "MEDIUM" if str(item.get("strength") or "") == "STRONG" else str(item.get("strength") or "WEAK"),
                    "reason": "explicit_alias_relation_inherited",
                    "source": "alias_relation",
                })
        occurrence["type_evidence"] = _dedupe_type_evidence_local(inherited + list(occurrence.get("type_evidence") or []))
        occurrence["type_inherited"] = bool(inherited)
    return sorted(occurrences, key=lambda row: (row["source_url"], row["normalized_identity"], row["occurrence_id"]))


def build_candidate_ledger(
    documents: Iterable[Mapping[str, Any]],
    owners: Iterable[Mapping[str, Any]],
    occurrences: Iterable[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Aggregate occurrences into owner-local candidates without losing roles."""
    pages = {str(row["page_id"]): row for row in documents}
    owner_rows = {str(row.get("owner_id") or ""): row for row in owners}
    if occurrences is None:
        occurrences = build_identity_occurrences(documents, owners)
    occurrences=list(occurrences)
    defining_pairs={(str(o.get('owner_id') or ''),str(o.get('normalized_identity') or ''))
                    for o in occurrences if o.get('defining') and o.get('owner_local_binding') is not False}
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for occurrence in occurrences:
        owner_id = str(occurrence.get("owner_id") or "")
        normalized = str(occurrence.get("normalized_identity") or "")
        page = pages.get(str(occurrence.get("page_id") or ""))
        owner = owner_rows.get(owner_id)
        if not owner or not page or not normalized:
            continue
        # Keep foreign surfaces in the raw occurrence artifact, but never let
        # them enter the candidate ledger or later IdentityUnit grouping.
        if occurrence.get("owner_local_binding") is False:
            continue
        if (occurrence.get('identity_role_reason') and (owner.get('definition_section_proofs')
                or occurrence.get('identity_role_reason')=='EXPLICIT_USE_SITE')
                and (owner_id,normalized) not in defining_pairs):
            continue  # Retained in occurrences; no new owner/identity from a field.
        raw = str(occurrence.get("raw_identity") or normalized)
        source = str(occurrence.get("source_type") or "")
        key = (owner_id, normalized)
        row = merged.get(key)
        if row is None:
            row = {
                "dbms": str(page["dbms"]),
                "page_id": str(page["page_id"]),
                "source_url": str(page["url"]),
                "page_hash": str(page["page_hash"]),
                "html_path": str(page["html_path"]),
                "owner_id": owner_id,
                "raw_identity": raw,
                "normalized_identity": normalized,
                "source_block_ids": [],
                "occurrence_ids": [],
            "occurrence_records": [],
                "recall_tier": _tier(owner, raw, source),
                "identity_signals": [],
                "risk_signals": [],
                "source_types": [],
                "surface_roles": [],
                "occurrence_roles": Counter(),
                "type_evidence": [],
                "type_hint": "UNCERTAIN",
            }
            merged[key] = row
        if raw not in row["raw_identity"].split(" | "):
            row["raw_identity"] = " | ".join(sorted(set(row["raw_identity"].split(" | ") + [raw])))
        row["source_block_ids"].append(str(occurrence.get("source_block_id") or ""))
        row["occurrence_ids"].append(str(occurrence.get("occurrence_id") or ""))
        row["identity_signals"] = sorted(set(row["identity_signals"]) | set(occurrence.get("identity_signals") or []))
        row["risk_signals"] = sorted(set(row["risk_signals"]) | set(_risk(owner, source)))
        row["source_types"] = sorted(set(row["source_types"]) | {source})
        row["surface_roles"].append(str(occurrence.get("surface_role") or "UNKNOWN"))
        occurrence_role = str(occurrence.get("occurrence_role") or "UNKNOWN")
        row["occurrence_roles"][occurrence_role] += 1
        row["type_evidence"].extend(list(occurrence.get("type_evidence") or []))
        row["occurrence_records"].append({
            "occurrence_id": str(occurrence.get("occurrence_id") or ""),
            "raw_identity": raw,
            "occurrence_role": occurrence_role,
            "defining": bool(occurrence.get("defining")),
            "source_type": source,
            "source_block_id": str(occurrence.get("source_block_id") or ""),
            "source_url": str(occurrence.get("source_url") or page.get("url") or ""),
            "raw_evidence": str(occurrence.get("raw_evidence") or ""),
            "surface_text": str(occurrence.get("surface_text") or raw),
            "surface_role": str(occurrence.get("surface_role") or "UNKNOWN"),
            "structural_role": str(occurrence.get("structural_role") or "PROSE"),
            "definition_section_id": occurrence.get('definition_section_id'),
            "header_context": str(occurrence.get("header_context") or ""),
            "cell_index": occurrence.get("cell_index"),
            "row_index": occurrence.get("row_index"),
            "table_id": occurrence.get("table_id"),
            "relation_type": str(occurrence.get("relation_type") or "CANONICAL"),
            "alias_of": str(occurrence.get("alias_of") or ""),
            "alias_of_normalized": str(occurrence.get("alias_of_normalized") or ""),
            "alias_of_block_id": str(occurrence.get("alias_of_block_id") or ""),
            "alias_of_owner_id": str(occurrence.get("alias_of_owner_id") or ""),
            "type_inherited": bool(occurrence.get("type_inherited")),
            "owner_local_binding": occurrence.get("owner_local_binding", True),
            "owner_binding_reason": str(occurrence.get("owner_binding_reason") or ""),
            "identity_role_reason": occurrence.get('identity_role_reason'),
            "declaration_locator": occurrence.get('declaration_locator'),
            "owner_scope_block_ids": list(occurrence.get("owner_scope_block_ids") or []),
            "owner_scope_identity_values": list(occurrence.get("owner_anchor_identities") or []),
            "surface_confidence": occurrence.get("surface_confidence", 0.0),
            "type_evidence": list(occurrence.get("type_evidence") or []),
        })
        if row["recall_tier"] == "WEAK" and _tier(owner, raw, source) != "WEAK":
            row["recall_tier"] = _tier(owner, raw, source)

    candidates: list[dict[str, Any]] = []
    rank = {"STRONG": 3, "MEDIUM": 2, "WEAK": 1}
    for row in merged.values():
        source_ids = sorted({value for value in row.pop("source_block_ids") if value})
        occurrence_ids = sorted({value for value in row.pop("occurrence_ids") if value})
        type_evidence = sorted({
            (str(item.get("type") or ""), str(item.get("strength") or ""),
             str(item.get("reason") or ""), str(item.get("source") or ""))
            for item in row.pop("type_evidence")
            if item.get("type") and item.get("strength")
        }, key=lambda value: (-rank.get(value[1], 0), value))
        type_names = {item[0] for item in type_evidence if item[0] in {"FUNCTION", "OPERATOR", "DATATYPE"}}
        strong_names = {item[0] for item in type_evidence if item[1] == "STRONG"}
        provisional_type = next(iter(strong_names)) if len(strong_names) == 1 else (
            next(iter(type_names)) if len(type_names) == 1 else "UNCERTAIN"
        )
        candidate = make_identity_candidate(
            dbms=row["dbms"], page_id=row["page_id"], source_url=row["source_url"],
            page_hash=row["page_hash"], html_path=row["html_path"],
            raw_identity=row["raw_identity"], normalized_identity=row["normalized_identity"],
            owner_id=row["owner_id"], source_block_ids=source_ids,
            recall_tier=row["recall_tier"], validation_status="UNVALIDATED",
            identity_signals=row["identity_signals"], risk_signals=row["risk_signals"],
            parent_ids=[row["owner_id"], *source_ids, *occurrence_ids],
        )
        candidate["source_types"] = row["source_types"]
        candidate["type_hint"] = provisional_type
        candidate["occurrence_ids"] = occurrence_ids
        candidate["occurrence_records"] = sorted(row["occurrence_records"], key=lambda value: value["occurrence_id"])
        candidate["occurrence_roles"] = dict(sorted(row["occurrence_roles"].items()))
        candidate["defining_occurrence_ids"] = sorted({
            value["occurrence_id"] for value in row["occurrence_records"] if value.get("defining")
        })
        candidate["surface_roles"] = sorted(set(row["surface_roles"]))
        candidate["structural_roles"] = sorted({
            str(value.get("structural_role") or "PROSE")
            for value in row["occurrence_records"]
        })
        candidate["alias_relations"] = [{
            "relation_type": str(value.get("relation_type") or ""),
            "alias": str(value.get("raw_identity") or ""),
            "alias_normalized": normalize_identity(str(value.get("raw_identity") or "")),
            "canonical": str(value.get("alias_of") or ""),
            "canonical_normalized": str(value.get("alias_of_normalized") or ""),
            "source_block_id": str(value.get("source_block_id") or ""),
            "header_context": str(value.get("header_context") or ""),
        } for value in row["occurrence_records"] if str(value.get("relation_type") or "") == "ALIAS_OF"]
        candidate["type_evidence"] = [
            {"type": item[0], "strength": item[1], "reason": item[2], "source": item[3]}
            for item in type_evidence
        ]
        candidate["observed_variants"] = sorted({value["raw_identity"] for value in row["occurrence_records"]})
        candidate["variants"] = sorted({value["raw_identity"] for value in row["occurrence_records"] if value.get('defining')})
        candidates.append(candidate)
    return sorted(candidates, key=lambda row: (row["provenance"]["source_url"], row["normalized_identity"], row["candidate_id"]))


def validate_candidate_ledger(
    candidates: Iterable[Mapping[str, Any]],
    owners: Iterable[Mapping[str, Any]],
    *,
    retain_quality_evidence: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    owner_ids = {str(row["owner_id"]): row for row in owners}
    owner_context: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"arguments": set(), "returns": set(), "signatures": set()})
    for candidate in candidates:
        owner_id = str(candidate.get("owner_id") or "")
        identity = normalize_identity(candidate.get("normalized_identity"))
        for raw in str(candidate.get("raw_identity") or "").split(" | "):
            from agents.contracts.definition_entry import CALLABLE_HEAD, QUALIFIED_IDENTIFIER
            call = CALLABLE_HEAD.match(raw.strip())
            if not call:
                continue
            owner_context[owner_id]["signatures"].add(normalize_identity(call.group('name')))
            inside = raw[raw.find("(") + 1: raw.rfind(")") if ")" in raw else len(raw)]
            for token in re.finditer(QUALIFIED_IDENTIFIER, inside):
                key=normalize_identity(token[0])
                if key not in _STOPWORDS and key not in _GRAMMAR_TOKENS:
                    owner_context[owner_id]["arguments"].add(key)
            return_match = re.search(r"(?:->|→|\bRETURNS?\b)\s*("+QUALIFIED_IDENTIFIER+r")", raw, re.I)
            if return_match:
                owner_context[owner_id]["returns"].add(normalize_identity(return_match.group(1)))
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for raw in candidates:
        row = dict(raw)
        owner = owner_ids.get(str(row.get("owner_id")))
        reasons: list[str] = []
        identity = str(row.get("normalized_identity") or "")
        if not owner:
            reasons.append("OWNER_NOT_FOUND")
        if not identity:
            reasons.append("EMPTY_IDENTITY")
        if identity in _STOPWORDS:
            reasons.append("STRUCTURAL_TERM")
        atomic_surface = "ATOMIC_SURFACE" in row.get("identity_signals", [])
        structural_surface = any(source in {"table_row", "definition_list", "heading", "list_item"} for source in row.get("source_types", []))
        if identity in _GRAMMAR_TOKENS and not atomic_surface and not structural_surface:
            reasons.append("GRAMMAR_TOKEN")
        context = owner_context.get(str(row.get("owner_id") or ""), {})
        if identity in context.get("arguments", set()) and identity not in context.get("signatures", set()):
            reasons.append("PARAMETER_AS_IDENTITY")
        if identity in context.get("returns", set()) and identity not in context.get("signatures", set()):
            reasons.append("RETURN_VALUE_TERM")
        if "NAVIGATION_ONLY" in row.get("risk_signals", []) and row.get("recall_tier") == "WEAK":
            reasons.append("NAVIGATION_ONLY")
        if "WEAK_SURFACE" in row.get("risk_signals", []) and row.get("recall_tier") == "WEAK" and not any(
            source in {"signature", "table_row", "definition_list"} for source in row.get("source_types", [])
        ):
            reasons.append("NO_FEATURE_IDENTITY_EVIDENCE")
        if owner and identity:
            from agents.owner.profile_execution import revalidate_explicit_authority
            explicit = any(revalidate_explicit_authority(proof, citation, name)
                for proof in owner.get('definition_section_proofs', [])
                if (proof.get('structure_execution') or {}).get('authority') == 'EXPLICIT_DEFINITION'
                for name in proof.get('identity_values', []) if normalize_identity(name) == identity
                for citation in (proof.get('source_citations') or {}).values())
            if explicit:reasons = [r for r in reasons if r in {'OWNER_NOT_FOUND', 'EMPTY_IDENTITY'}]
        if reasons and any(reason in {"OWNER_NOT_FOUND", "EMPTY_IDENTITY"} for reason in reasons):
            row["validation_status"] = "REJECTED"
            row["validation_reason"] = ";".join(reasons)
            rejected.append(row)
        elif reasons and retain_quality_evidence:
            # Quality warnings must not erase the occurrence.  The candidate
            # remains available to reconciliation and can become
            # NON_FEATURE_ONLY, while the reason remains auditable.
            row["validation_status"] = "RETAINED_EVIDENCE"
            row["validation_reason"] = ";".join(reasons)
            row["quality_only_reasons"] = list(reasons)
            accepted.append(row)
        elif reasons:
            row["validation_status"] = "REJECTED"
            row["validation_reason"] = ";".join(reasons)
            rejected.append(row)
        else:
            row["validation_status"] = "VALIDATED"
            row["validation_reason"] = "STRUCTURAL_IDENTITY_EVIDENCE"
            accepted.append(row)
    return accepted, rejected


def build_identity_units(accepted: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in accepted:
        groups[(
            str(row["normalized_identity"]),
            str(row.get("type_hint") or "UNCERTAIN").upper(),
        )].append(row)
    units: list[dict[str, Any]] = []
    for (canonical, type_hint), rows in sorted(groups.items()):
        rows = sorted(rows, key=lambda row: row["candidate_id"])
        first = rows[0]
        variants: list[str] = []
        for row in rows:
            values = row.get("variants") or []
            if isinstance(values, str):
                values = [values]
            if not values:
                values = str(row.get("raw_identity") or "").split(" | ")
            variants.extend(str(value).strip() for value in values if str(value).strip())
        unit = make_identity_unit(
            dbms=str(first["provenance"]["dbms"]), page_id=str(first["page_id"]),
            source_url=str(first["provenance"]["source_url"]), page_hash=str(first["provenance"]["page_hash"]),
            html_path=str(first["provenance"]["html_path"]), canonical_identity=canonical,
            candidate_ids=[row["candidate_id"] for row in rows],
            owner_ids=[row["owner_id"] for row in rows],
            variants=variants, validation_status="VALIDATED",
            merge_decisions=([{
                "decision": "MERGED", "reason": "same_normalized_identity",
                "candidate_ids": [row["candidate_id"] for row in rows],
            }] if len(rows) > 1 else []),
            parent_ids=[row["candidate_id"] for row in rows] + [row["owner_id"] for row in rows],
        )
        unit["type_hint"] = type_hint
        unit["feature_type"] = type_hint
        unit["group_key"] = f"{canonical}|{type_hint}"
        unit["occurrence_ids"] = sorted({
            str(occurrence_id)
            for row in rows
            for occurrence_id in (row.get("occurrence_ids") or [])
            if occurrence_id
        })
        unit["source_urls"] = sorted({
            str(row.get("provenance", {}).get("source_url") or "")
            for row in rows
            if row.get("provenance", {}).get("source_url")
        })
        unit["evidence"] = [{
            "candidate_id": str(row.get("candidate_id") or ""),
            "owner_id": str(row.get("owner_id") or ""),
            "source_url": str(row.get("provenance", {}).get("source_url") or ""),
            "source_block_ids": list(row.get("source_block_ids") or []),
            "occurrence_ids": list(row.get("occurrence_ids") or []),
            "raw_identity": str(row.get("raw_identity") or ""),
            "variants": list(row.get("variants") or []),
        } for row in rows]
        units.append(unit)
    return units


def _candidate_records(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    records = [dict(row) for row in (candidate.get("occurrence_records") or [])]
    if records:
        return records
    # Compatibility for callers that still provide pre-quality candidates.
    raw_values = str(candidate.get("raw_identity") or "").split(" | ")
    return [{
        "occurrence_id": "",
        "raw_identity": value,
        "occurrence_role": "DEFINING_IDENTITY",
        "defining": True,
        "source_type": "legacy_candidate",
        "source_block_id": "",
        "source_url": str(candidate.get("provenance", {}).get("source_url") or ""),
        "raw_evidence": value,
        "surface_role": "FEATURE_REFERENCE",
        "surface_confidence": 0.0,
        "type_evidence": ([{
            "type": str(candidate.get("type_hint") or "UNCERTAIN"),
            "strength": "MEDIUM",
            "reason": "legacy_candidate_type_hint",
            "source": "legacy_candidate",
        }] if str(candidate.get("type_hint") or "UNCERTAIN") in {"FUNCTION", "OPERATOR", "DATATYPE"} else []),
    } for value in raw_values if value]


def _candidate_type_evidence(candidate: Mapping[str, Any]) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    values.extend(dict(item) for item in (candidate.get("type_evidence") or []))
    for record in _candidate_records(candidate):
        values.extend(dict(item) for item in (record.get("type_evidence") or []))
    deduped = {
        (str(item.get("type") or ""), str(item.get("strength") or ""),
         str(item.get("reason") or ""), str(item.get("source") or "")): item
        for item in values
        if item.get("type") in {"FUNCTION", "OPERATOR", "DATATYPE"}
        and item.get("strength") in {"STRONG", "MEDIUM", "WEAK"}
    }
    return sorted(deduped.values(), key=lambda item: (item["type"], item["strength"], item.get("reason", "")))


def _candidate_strong_types(candidate: Mapping[str, Any]) -> set[str]:
    scores = _effective_type_scores(candidate)
    strong = {
        feature_type: score
        for feature_type, score in scores.items()
        if score >= 5
    }
    if not strong:
        return set()
    ordered = sorted(strong.items(), key=lambda item: (-item[1], item[0]))
    # Supporting/context evidence is intentionally excluded above.  If an
    # owner still contains two equally authoritative surfaces, retain both so
    # the normal dual-surface split can represent them independently.
    if len(ordered) == 1 or ordered[0][1] - ordered[1][1] >= 2:
        return {ordered[0][0]}
    return {feature_type for feature_type, score in ordered if ordered[0][1] - score <= 1}


def _candidate_medium_types(candidate: Mapping[str, Any]) -> set[str]:
    strong = _candidate_strong_types(candidate)
    if strong:
        return strong
    return {
        feature_type for feature_type, score in _effective_type_scores(candidate).items()
        if score > 0
    }


def _effective_type_scores(candidate: Mapping[str, Any]) -> dict[str, int]:
    """Score authoritative type signals without equal-weight context noise."""
    records = _candidate_records(candidate)
    if not records:
        records = [{
            "defining": True,
            "structural_role": "FEATURE_NAME",
            "type_evidence": list(candidate.get("type_evidence") or []),
        }]
    scores: dict[str, int] = defaultdict(int)
    for record in records:
        structural_role = str(record.get("structural_role") or "")
        defining = bool(record.get("defining")) or is_defining_role(str(record.get("occurrence_role") or ""))
        if structural_role and structural_role not in {"FEATURE_NAME", "SIGNATURE", "ALIAS"} and not defining:
            continue
        if not defining and structural_role not in {"FEATURE_NAME", "SIGNATURE", "ALIAS"}:
            continue
        for item in record.get("type_evidence") or []:
            feature_type = str(item.get("type") or "")
            strength = str(item.get("strength") or "")
            if feature_type not in {"FUNCTION", "OPERATOR", "DATATYPE"}:
                continue
            score = {"STRONG": 5, "MEDIUM": 3, "WEAK": 1}.get(strength, 0)
            if not score:
                continue
            reason = str(item.get("reason") or "")
            if reason in {
                "explicit_structural_column_role",
                "type_declaration_context",
                "callable_signature",
                "symbol_definition",
                "explicit_operator_context",
                "explicit_function_context",
            }:
                score += 2
            if reason == "explicit_alias_relation_inherited":
                score = min(score, 4)
            if structural_role == "SIGNATURE":
                score += 1
            scores[feature_type] += score
    resolution = candidate.get('context_type_resolution') or {}
    if resolution.get('rule') == 'PROVEN_DEFINITION_OVER_MIXED_CATALOG_CONTEXT':
        selected = resolution['feature_type']
        return {selected: scores[selected]} if scores.get(selected) else {}
    return dict(scores)


def _resolve_mixed_catalog_context(rows):
    """Disambiguate context-only references, never a competing definition.

    Reconciliation already groups identical names. Only an observed native
    definition anchor may resolve a mixed function/operator catalog heading.
    Explicit datatype, operator-column, symbol and alias evidence is untouched.
    Raw evidence is retained and the resolution records its exact provenance.
    """
    direct_types = set()
    witnesses = []
    for row in rows:
        for record in _candidate_records(row):
            if not record.get('definition_section_id'):
                continue
            types = _candidate_strong_types(row)
            if len(types) == 1:
                direct_types.update(types)
                witnesses.append({'candidate_id': row['candidate_id'],
                                  'owner_id': row['owner_id'],
                                  'source_block_id': record['source_block_id'],
                                  'section_id': record['definition_section_id']})
    if len(direct_types) != 1:
        return rows
    selected = next(iter(direct_types))
    result = []
    for row in rows:
        strong = _candidate_strong_types(row)
        records = _candidate_records(row)
        signals = [s for r in records for s in r.get('type_evidence') or []
                   if s.get('strength') == 'STRONG']
        context_only = signals and all(s.get('reason') in {
            'explicit_function_context', 'explicit_operator_context'} for s in signals)
        catalog_only = records and all(r.get('source_type') == 'table_row' for r in records)
        if len(strong) > 1 and selected in strong and context_only and catalog_only:
            row = dict(row, context_type_resolution={
                'rule': 'PROVEN_DEFINITION_OVER_MIXED_CATALOG_CONTEXT',
                'feature_type': selected, 'original_strong_types': sorted(strong),
                'definition_witnesses': witnesses})
        result.append(row)
    return result


def _build_reconciled_unit(
    canonical: str,
    final_type: str,
    rows: list[Mapping[str, Any]],
    *,
    conflict: bool = False,
    reason: str = "same_normalized_identity",
) -> dict[str, Any]:
    rows = sorted(rows, key=lambda row: str(row.get("candidate_id") or ""))
    first = rows[0]
    records = [record for row in rows for record in _candidate_records(row)]
    records = sorted(records, key=lambda record: str(record.get("occurrence_id") or ""))
    defining_records = [
        record for record in records
        if bool(record.get("defining")) or is_defining_role(str(record.get("occurrence_role") or ""))
    ]
    occurrence_roles = Counter(str(record.get("occurrence_role") or "UNKNOWN") for record in records)
    type_evidence = aggregate_type_evidence(records)
    effective_scores: dict[str, int] = defaultdict(int)
    for row in rows:
        for feature_type in _candidate_strong_types(row):
            effective_scores[feature_type] += 5
        if not _candidate_strong_types(row):
            for feature_type in _candidate_medium_types(row):
                effective_scores[feature_type] += 3
    strong_types = set(effective_scores)
    medium_types = {
        feature_type for feature_type, counts in type_evidence.items()
        if counts.get("MEDIUM", 0) > 0
    }
    if conflict:
        group_status = "CONFLICT"
    elif not defining_records:
        group_status = "NON_FEATURE_ONLY"
    elif final_type in strong_types:
        group_status = "CONFIRMED_FEATURE"
    elif final_type in medium_types or final_type in effective_scores:
        group_status = "PROBABLE_FEATURE"
    else:
        group_status = "UNCERTAIN_FEATURE"
    candidate_ids = [str(row.get("candidate_id") or "") for row in rows if row.get("candidate_id")]
    owner_ids = [str(row.get("owner_id") or "") for row in rows if row.get("owner_id")]
    observed_variants = sorted({
        str(record.get("raw_identity") or "")
        for record in records if record.get("raw_identity")
    })
    defining_variants = sorted({
        str(record.get("raw_identity") or "")
        for record in defining_records if record.get("raw_identity")
    })
    variants=defining_variants
    occurrence_ids = sorted({
        str(record.get("occurrence_id") or "")
        for record in records if record.get("occurrence_id")
    })
    source_urls = sorted({
        str(record.get("source_url") or row.get("provenance", {}).get("source_url") or "")
        for row in rows for record in _candidate_records(row)
        if record.get("source_url") or row.get("provenance", {}).get("source_url")
    })
    evidence = [{
        "candidate_id": str(row.get("candidate_id") or ""),
        "owner_id": str(row.get("owner_id") or ""),
        "source_url": str(row.get("provenance", {}).get("source_url") or ""),
        "source_block_ids": list(row.get("source_block_ids") or []),
        "occurrence_ids": sorted({
            str(record.get("occurrence_id") or "")
            for record in _candidate_records(row) if record.get("occurrence_id")
        }),
        "raw_identity": str(row.get("raw_identity") or ""),
          "context_type_resolution": row.get('context_type_resolution'),
        "variants": sorted({
            str(record.get("raw_identity") or "")
            for record in _candidate_records(row) if record.get("raw_identity") and record.get('defining')
        }),
        "occurrence_roles": dict(sorted(Counter(
            str(record.get("occurrence_role") or "UNKNOWN")
            for record in _candidate_records(row)
        ).items())),
        "occurrence_records": [dict(record) for record in _candidate_records(row)],
        "structural_roles": sorted({
            str(record.get("structural_role") or "PROSE")
            for record in _candidate_records(row)
        }),
        "alias_relations": [dict(record) for record in _candidate_records(row)
                            if str(record.get("relation_type") or "") == "ALIAS_OF"],
        "validation_status": str(row.get("validation_status") or ""),
        "quality_only_reasons": list(row.get("quality_only_reasons") or []),
    } for row in rows]
    unit = make_identity_unit(
        dbms=str(first.get("provenance", {}).get("dbms") or first.get("dbms") or ""),
        page_id=str(first.get("page_id") or ""),
        source_url=str(first.get("provenance", {}).get("source_url") or first.get("source_url") or ""),
        page_hash=str(first.get("provenance", {}).get("page_hash") or first.get("page_hash") or ""),
        html_path=str(first.get("provenance", {}).get("html_path") or first.get("html_path") or ""),
        canonical_identity=canonical,
        candidate_ids=candidate_ids,
        owner_ids=owner_ids,
        variants=variants,
        validation_status="VALIDATED",
        merge_decisions=[{
            "decision": "MERGED" if len(rows) > 1 else "RETAINED",
            "reason": reason,
            "candidate_ids": candidate_ids,
        }],
        parent_ids=candidate_ids + owner_ids + occurrence_ids,
    )
    unit.update({
        "type_hint": final_type,
        "feature_type": final_type,
        "group_key": f"{canonical}|{final_type}",
        "group_status": group_status,
        "semantic_ready": group_status != "NON_FEATURE_ONLY",
        "occurrence_ids": occurrence_ids,
        "source_urls": source_urls,
        "variants": variants,
        "observed_variants": observed_variants,
        "defining_variants": defining_variants,
        "defining_occurrence_ids": sorted({
            str(record.get("occurrence_id") or "")
            for record in defining_records if record.get("occurrence_id")
        }),
        "non_defining_occurrence_ids": sorted({
            str(record.get("occurrence_id") or "")
            for record in records
            if record.get("occurrence_id") and record not in defining_records
        }),
        "occurrence_roles": {role: int(occurrence_roles.get(role, 0)) for role in (
            "DEFINING_IDENTITY", "FORMAL_VARIANT", "MENTION", "EXAMPLE_TOKEN",
            "PARAMETER_OR_FIELD", "OUTPUT_TEXT", "CROSS_REFERENCE", "UNKNOWN",
        )},
        "type_evidence": type_evidence,
        "strong_type_evidence": sorted(strong_types),
        "occurrence_records": [dict(record) for record in records],
        "structural_roles": sorted({
            str(record.get("structural_role") or "PROSE")
            for record in records
        }),
        "alias_relations": [dict(record) for record in records
                            if str(record.get("relation_type") or "") == "ALIAS_OF"],
        "evidence": evidence,
        "reconciliation_reason": reason,
        "conflict": conflict,
        "semantic_reference_owner_ids": sorted({str(r['owner_id']) for r in rows if r.get('semantic_reference_only')}),
    })
    return unit


def reconcile_identity_groups(
    accepted: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Reconcile occurrence buckets before deciding final group type/status."""
    by_identity: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in accepted:
        canonical = normalize_identity(row.get("normalized_identity"))
        if canonical:
            by_identity[canonical].append(row)
    units: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    before_duplicate_count = 0
    for canonical, rows in sorted(by_identity.items()):
        rows = _resolve_mixed_catalog_context(rows)
        # A verified definition cannot inherit an unrelated owner's reference
        # row merely because the spelling matches. Keep those observations as
        # an explicit unresolved branch; do not discard or assign their type.
        declared=[r for r in rows if any(str(o.get('definition_section_id') or '').startswith(('function_definition:','reference_definition:'))
                    for o in _candidate_records(r))]
        if declared:
            declared_types=set().union(*(_candidate_strong_types(r) for r in declared))
            # Compatible typed usage (for example a declaration example) may
            # supply semantic evidence without proving a new identity. Do not
            # erase it when separating unrelated untyped contextual mentions.
            reference_only=[r for r in rows if r not in declared
                and not (_candidate_medium_types(r) & declared_types)
                and _candidate_records(r) and all(o.get('surface_role')=='SUPPORTING_CONTEXT'
                    and not o.get('definition_section_id') for o in _candidate_records(r))
                and not any(re.match(r'^\s*(?:mysql>\s*)?(?:SELECT|WITH|VALUES|INSERT|UPDATE|DELETE|CREATE\s+TABLE)\b',
                    str(o.get('raw_evidence') or o.get('surface_text') or ''),re.I)
                    for o in _candidate_records(r))]
            # A usage signature can be useful evidence for an ALREADY proved
            # type/operator without being another definition vote. Keep its
            # physical Owner/occurrences, and explicitly mark the type boundary.
            usages=[r for r in reference_only if declared_types & {'DATATYPE','OPERATOR'}
                and all(o.get('source_type')=='signature' for o in _candidate_records(r))]
            if usages:
                converted=[]
                for r in usages:
                    obs=[dict(o,defining=False,occurrence_role='CROSS_REFERENCE',type_evidence=[])
                         for o in _candidate_records(r)]
                    converted.append(dict(r,semantic_reference_only=True,occurrence_records=obs))
                rows=[r for r in rows if r not in usages]+converted
                reference_only=[r for r in reference_only if r not in usages]
            if reference_only:
                isolated=[]
                for r in reference_only:
                    observations=[dict(o,defining=False,occurrence_role='CROSS_REFERENCE') for o in _candidate_records(r)]
                    isolated.append(dict(r,occurrence_records=observations))
                unit=_build_reconciled_unit(canonical,'UNCERTAIN',isolated,
                    reason='untyped_reference_not_merged_with_verified_definition')
                units.append(unit)
                rows=[r for r in rows if r not in reference_only]
        provisional_types = {
            str(row.get("type_hint") or "UNCERTAIN").upper()
            for row in rows
            if str(row.get("type_hint") or "UNCERTAIN").upper() in {"FUNCTION", "OPERATOR", "DATATYPE", "UNCERTAIN"}
        }
        if len(provisional_types) > 1:
            before_duplicate_count += 1
        strong_types = set().union(*(_candidate_strong_types(row) for row in rows)) if rows else set()
        if len(strong_types) <= 1:
            if len(strong_types) == 1:
                final_type = next(iter(strong_types))
                reason = "strong_defining_type_reconciled"
            else:
                medium_types = set().union(*(_candidate_medium_types(row) for row in rows)) if rows else set()
                final_type = next(iter(medium_types)) if len(medium_types) == 1 else "UNCERTAIN"
                reason = "weak_or_medium_type_evidence"
            units.append(_build_reconciled_unit(canonical, final_type, list(rows), reason=reason))
            continue

        buckets: dict[str, list[Mapping[str, Any]]] = {feature_type: [] for feature_type in sorted(strong_types)}
        ambiguous: list[Mapping[str, Any]] = []
        for row in rows:
            row_strong = _candidate_strong_types(row)
            if len(row_strong) == 1:
                buckets[next(iter(row_strong))].append(row)
            else:
                ambiguous.append(row)
        if ambiguous:
            buckets.setdefault("UNCERTAIN", []).extend(ambiguous)
        group_descriptions: list[dict[str, Any]] = []
        for final_type, bucket_rows in buckets.items():
            if not bucket_rows:
                continue
            unit = _build_reconciled_unit(
                canonical,
                final_type,
                list(bucket_rows),
                # A legitimate dual-surface identity (for example a word
                # callable and an operator with the same spelling) is split
                # into independent typed units.  Only an ambiguous bucket is
                # a conflict; marking every split bucket as CONFLICT would
                # make both real surfaces fail deterministic admission.
                conflict=final_type == "UNCERTAIN",
                reason="multiple_strong_defining_types_split" if len(strong_types) > 1 else "same_normalized_identity",
            )
            units.append(unit)
            group_descriptions.append({
                "group_key": unit["group_key"],
                "feature_type": unit["feature_type"],
                "group_status": unit["group_status"],
                "candidate_ids": list(unit["candidate_ids"]),
                "occurrence_ids": list(unit["occurrence_ids"]),
            })
        conflicts.append({
            "normalized_identity": canonical,
            "groups": group_descriptions,
            "provisional_type_hints": sorted(provisional_types),
            "strong_type_evidence": sorted(strong_types),
            "defining_evidence": [
                record for row in rows for record in _candidate_records(row)
                if record.get("defining") or is_defining_role(str(record.get("occurrence_role") or ""))
            ],
            "reason": "multiple_strong_defining_types",
        })
    after_counts = Counter(str(unit.get("canonical_identity") or "") for unit in units)
    audit = {
        "same_normalized_identity_multi_group_count": sum(1 for count in after_counts.values() if count > 1),
        "cross_type_duplicates_before": before_duplicate_count,
        "cross_type_duplicates_after": sum(1 for count in after_counts.values() if count > 1),
        "cross_type_conflicts": conflicts,
        "groups_with_defining_evidence": sum(bool(unit.get("defining_occurrence_ids")) for unit in units),
        "groups_without_defining_evidence": sum(not bool(unit.get("defining_occurrence_ids")) for unit in units),
        "groups_by_status": dict(sorted(Counter(str(unit.get("group_status") or "UNKNOWN") for unit in units).items())),
    }
    return sorted(units, key=lambda unit: (unit.get("canonical_identity", ""), unit.get("group_key", ""), unit.get("identity_unit_id", ""))), audit


def run_identity_owner_path(
    documents: Iterable[Mapping[str, Any]],
    profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    documents = list(documents)
    from agents.owner.source_relations import prepare_relation_documents
    documents = prepare_relation_documents(documents,profile)
    raw_owners = generate_owner_hypotheses(documents, profile=profile)
    owners = consolidate_owner_hypotheses(raw_owners)
    occurrences = build_identity_occurrences(documents, owners, profile=profile)
    candidates = build_candidate_ledger(documents, owners, occurrences=occurrences)
    accepted, rejected = validate_candidate_ledger(
        candidates,
        owners,
        retain_quality_evidence=True,
    )
    units, quality_audit = reconcile_identity_groups(accepted)
    type_counts = defaultdict(int)
    for unit in units:
        type_counts[str(unit.get("type_hint") or "UNCERTAIN")] += 1
    occurrence_role_counts = aggregate_occurrence_roles(occurrences)
    validated_count = sum(row.get("validation_status") == "VALIDATED" for row in accepted)
    retained_evidence_count = sum(row.get("validation_status") == "RETAINED_EVIDENCE" for row in accepted)
    return {
        "documents": documents, "owners": owners, "raw_owner_count": len(raw_owners),
        "occurrences": occurrences, "candidates": candidates,
        "accepted": accepted, "rejected": rejected,
        "units": units,
        "quality_audit": quality_audit,
        "stats": {
            "pages": len(documents), "owners": len(owners), "raw_owners": len(raw_owners),
            "identity_occurrences": len(occurrences),
            "candidates": len(candidates), "accepted": len(accepted),
            "rejected": len(rejected), "identity_units": len(units),
            "groups_by_type": dict(sorted(type_counts.items())),
            "validated_candidates": validated_count,
            "retained_evidence_candidates": retained_evidence_count,
            "occurrence_roles": occurrence_role_counts,
            "groups_with_defining_evidence": quality_audit["groups_with_defining_evidence"],
            "groups_without_defining_evidence": quality_audit["groups_without_defining_evidence"],
            "same_normalized_identity_multi_group_count": quality_audit["same_normalized_identity_multi_group_count"],
            "groups_by_status": quality_audit["groups_by_status"],
        },
    }


def build_production_target_identity_layer(
    documents: Iterable[Mapping[str, Any]],
    owners: Iterable[Mapping[str, Any]],
    *,
    profile: Mapping[str, Any] | None = None,
    table_index: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the production Target Feature Evidence and Identity Groups.

    The deterministic target-evidence classifier is shared with the validated
    precision replay.  Keeping the call here as the production API prevents a
    second production-only interpretation of ``document_defining`` and
    ``target_feature_defining`` from emerging.

    This stage is intentionally offline and ends at Identity Groups.  It does
    not invoke semantic extraction, canonicalization, or frontend projection.
    """
    from agents.identity.target_evidence_precision import (
        _enrich_occurrences,
        _occurrence_index,
    )
    from agents.identity.target_evidence_shadow import _aggregate_group
    from agents.owner.generic_surface_recovery import build_table_role_index

    pages = [dict(row) for row in documents]
    owner_rows = [dict(row) for row in owners]
    if table_index is None:
        table_index, _ = build_table_role_index(pages)
    occurrences = build_identity_occurrences(pages, owner_rows, profile=profile)
    page_index = {str(row.get("page_id") or ""): row for row in pages}
    block_index = {
        (str(page.get("page_id") or ""), str(block.get("block_id") or "")): block
        for page in pages
        for block in page.get("blocks") or []
    }
    owner_index = {str(row.get("owner_id") or ""): row for row in owner_rows}
    enriched = _enrich_occurrences(
        occurrences,
        pages=page_index,
        blocks=block_index,
        owners=owner_index,
        table_index=table_index,
    )
    for occurrence in enriched:
        evidence = dict(occurrence.get("target_feature_evidence") or {})
        provenance = dict(evidence.get("provenance") or {})
        provenance.update({
            "producer_stage": "target_feature_evidence_production",
            "producer_version": "mysql-feature-pipeline-v2/v1",
        })
        evidence["provenance"] = provenance
        occurrence["target_feature_evidence"] = evidence
        occurrence["target_feature_evidence_summary"] = {
            "target_types": evidence.get("target_types", []),
            "strength": evidence.get("strength"),
            "reason_codes": evidence.get("reason_codes", []),
            "non_target_codes": sorted({
                item.get("code") for item in evidence.get("non_target_evidence", []) if item.get("code")
            }),
            "table_role": (evidence.get("precision_context") or {}).get("table_role"),
            "local_section_role": (evidence.get("precision_context") or {}).get("local_section_role"),
        }
    by_identity = _occurrence_index(enriched)
    groups = sorted(
        (_aggregate_group(identity, rows) for identity, rows in by_identity.items()),
        key=lambda row: (
            str(row.get("canonical_identity") or ""),
            str(row.get("feature_type") or ""),
            str(row.get("identity_group_id") or row.get("identity_unit_id") or ""),
        ),
    )
    for group in groups:
        group["schema_version"] = "IdentityGroup/production-v1"
        group["production_stage"] = "identity_group_production"
        provenance = dict(group.get("provenance") or {})
        provenance.update({
            "producer_stage": "identity_group_production",
            "producer_version": "mysql-feature-pipeline-v2/v1",
        })
        group["provenance"] = provenance
    return {
        "documents": pages,
        "owners": owner_rows,
        "occurrences": enriched,
        "occurrences_by_identity": by_identity,
        "groups": groups,
        "stats": {
            "occurrences": len(enriched),
            "groups": len(groups),
            "groups_by_status": dict(sorted(Counter(
                str(row.get("group_status") or "UNKNOWN") for row in groups
            ).items())),
            "groups_by_type": dict(sorted(Counter(
                str(row.get("feature_type") or "UNCERTAIN") for row in groups
            ).items())),
            "target_groups": sum(
                str(row.get("group_status") or "") in {"CONFIRMED_FEATURE", "PROBABLE_FEATURE"}
                for row in groups
            ),
        },
    }
