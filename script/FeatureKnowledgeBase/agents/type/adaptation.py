"""Generic structural type adaptation for unseen documentation surfaces.

This module is deliberately small and optional.  It does not know a DBMS
vocabulary and it does not consult an inventory.  It only adds evidence when
the current owner-local block has the shape of an operator slot or a type
declaration slot.  The normal TypeEvidence/TypeDecision path remains the
default; this module is used by an offline evaluation replay.
"""

from __future__ import annotations

from collections import defaultdict
import copy
import hashlib
import re
from typing import Any, Iterable, Mapping, Sequence

from agents.type.decision import decide_type
from agents.contracts.identity_credibility import make_adaptation_provenance_record
from agents.contracts.base import build_provenance
from agents.type.evidence import (
    ALIGN_EXACT,
    ALIGN_SLOT,
    MEDIUM,
    NON_TARGET,
    STRONG,
    TARGET_TYPES,
    TARGET_UNCERTAIN,
    _evidence,
    build_typed_feature_evidence,
)


_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_$]*"
_OPERAND = re.compile(
    r"\b(?:expr|expression|value|values|operand|left|right|lhs|rhs|"
    r"pattern|condition|predicate|low|high|zone|arr|array|argument|"
    r"input|output|column|field|a|b|c|x|y|z)\b",
    re.I,
)
_OPERATOR_ROLE = re.compile(
    r"\b(?:operator|operators|predicate|predicates|precedence|"
    r"comparison|logical|membership|infix|postfix|syntax|operation)\b",
    re.I,
)
_OPERATOR_STRONG_ROLE = re.compile(
    r"\b(?:operator|operators|predicate|predicates|precedence|"
    r"comparison|logical|membership|infix|postfix)\b",
    re.I,
)
_TYPE_ROLE = re.compile(
    r"\b(?:data[- ]?type|datatype|type|declared|declaration|column|field|"
    r"cast|returns?|returning|type\s+mapping|type\s+constructor|"
    r"storage\s+class|type\s+name)\b",
    re.I,
)
_TYPE_HEADER = re.compile(
    r"\b(?:type|data\s+type|datatype|declared\s+type|column\s+type|"
    r"return(?:s|ing)?\s+type|declaration|type\s+name)\b",
    re.I,
)
_NOISE_WORDS = {
    "operator", "operators", "operation", "operations", "predicate",
    "predicates", "syntax", "expression", "expressions", "function",
    "functions", "example", "examples", "equivalent", "returns",
    "return", "result", "type", "types", "data", "column", "columns",
    "logical", "comparison", "working", "with", "numeric", "multiplication",
    "division", "addition", "subtraction", "access", "checking", "boolean",
    "values", "null", "dates", "times", "sets", "creation", "negation",
    "conditional", "concatenation", "lambda", "composition", "array",
    "tuple", "associativity", "from", "infix", "postfix", "prefix",
}
_OPERATOR_NOISE_WORDS = {
    # Generic SQL/prose structure, not a feature vocabulary.  These tokens
    # are statement scaffolding or operands and cannot be an operator surface
    # when they are captured by a broad syntax pattern.
    "a", "an", "as", "create", "table", "insert", "into", "select",
    "where", "join", "values", "when", "then", "else", "json", "null",
    "op", "type", "types", "value", "values", "column", "field", "name",
}
_TYPE_NOISE_WORDS = {
    "a", "an", "and", "another", "any", "be", "between", "column",
    "data", "description", "field", "from", "in", "is", "like", "name",
    "not", "of", "or", "other", "output", "parameter", "parameters",
    "read", "result", "resulting", "required", "search", "searched", "set",
    "table", "the", "to", "type", "types", "when", "which", "would",
    "null",
}
_TOKEN = re.compile(r"\b[A-Z][A-Z0-9_$]*(?![a-z])\b|[!<>=~%&|^+*/-]{1,3}")
_SURFACE_TOKEN = re.compile(r"^(?:[A-Z][A-Z0-9_$]*|[!<>=~%&|^+*/-]{1,3})$")
_TYPE_DECLARATION = re.compile(
    r"(?:\b(?:column|field|attribute|name)\s+[_A-Za-z][_A-Za-z0-9$]*\s+|"
    r"\b(?:cast|return(?:s|ing)?)\s*\([^)]*?\bAS\s+|"
    r"::\s*|\b(?:return(?:s|ing)?\s+type|type|data\s+type|"
    r"datatype|declared\s+type|column\s+type)\s*[:=]?\s*)"
    r"(?P<subject>[_A-Za-z][_A-Za-z0-9$]*)\s*(?:\([^)]*\))?",
    re.I,
)


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def _values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [_clean(item) for item in value if _clean(item)]
    item = _clean(value)
    return [item] if item else []


def _subject_in_text(subject: str, text: str) -> bool:
    if not subject or not text:
        return False
    return bool(re.search(
        r"(?<![A-Za-z0-9_$])" + re.escape(subject) + r"(?![A-Za-z0-9_$])",
        text,
        re.I,
    ))


def _owner_local_text(block: Mapping[str, Any]) -> str:
    values = [
        _clean(block.get("text")),
        " ".join(_values(block.get("cells"))),
        " ".join(_values(block.get("table_headers"))),
        " ".join(_values(block.get("heading_ancestry"))),
    ]
    return " | ".join(value for value in values if value)


def _role_context(block: Mapping[str, Any]) -> str:
    return " | ".join(
        value
        for value in [
            " ".join(_values(block.get("heading_ancestry"))),
            " ".join(_values(block.get("table_headers"))),
            _clean(block.get("text")),
        ]
        if value
    )


def _normal_surface(value: str) -> str:
    from agents.contracts.identity_key import identity_text
    value = _clean(value).strip("()[]{}:;,.'")
    value = re.sub(r"\s+", " ", value)
    return identity_text(value)


def _looks_like_type_name(value: str) -> bool:
    value = _clean(value).strip("()[]{}:;,.'\"")
    if not value or value.casefold() in _TYPE_NOISE_WORDS:
        return False
    if len(value) < 2:
        return False
    # Declaration surfaces conventionally use a capitalized/camel/constant
    # identifier or carry a width/precision.  This rejects prose fragments
    # such as ``column of a`` without maintaining a DBMS type dictionary.
    return bool(
        value[0].isupper()
        or any(char.isdigit() for char in value)
        or (value.isupper() and value.replace("_", "").isalnum())
    )


def _operator_candidates(text: str) -> list[tuple[str, str, str]]:
    """Return ``(surface, category, matched_text)`` from syntax-like text.

    The parser is grammar-shaped rather than vocabulary-shaped.  It looks for
    operand placeholders on either side of uppercase/punctuation tokens.  A
    local block still needs an operator/predicate/syntax context before the
    result is used as production evidence.
    """

    text = _clean(text)
    if not text:
        return []
    results: list[tuple[str, str, str]] = []

    def add(surface: str, category: str, matched: str) -> None:
        raw_tokens = _clean(surface).split()
        if not raw_tokens or not all(_SURFACE_TOKEN.fullmatch(token) for token in raw_tokens):
            return
        if any(
            any(char.isalpha() for char in token) and token != token.upper()
            for token in raw_tokens
        ):
            return
        surface = _normal_surface(surface)
        if not surface or surface.casefold() in _NOISE_WORDS:
            return
        tokens = surface.split()
        if not all(_SURFACE_TOKEN.fullmatch(token) for token in tokens):
            return
        if any(token.casefold() in _OPERATOR_NOISE_WORDS for token in tokens):
            return
        # Do not turn an operand/type or operand/value explanation into a
        # keyword operator just because the owner is under a comparison
        # heading.
        surface_pattern = re.escape(surface).replace(r"\ ", r"\s+")
        if re.search(
            rf"\b{surface_pattern}\s+(?:type|value|values)\b",
            _clean(matched),
            re.I,
        ):
            return
        # A prefix matcher can see the first words of a longer uppercase
        # surface (for example AT TIME in AT TIME ZONE).  Keep the complete
        # surface and discard the shorter prefix; a following lowercase
        # operand is intentionally not treated as part of this guard.
        if re.search(
            rf"\b{surface_pattern}\s+[A-Z][A-Z0-9_$]*\b",
            _clean(matched),
        ):
            return
        # C/C++-style postfix punctuation and SQL comment markers are not
        # operator slots in the generic documentation grammar.
        if surface in {"++", "--"} and re.search(
            rf"\b[A-Za-z0-9_$]+\s*{re.escape(surface)}\s*$",
            _clean(matched),
        ):
            return
        results.append((surface, category, _clean(matched)))

    # A general infix/ternary form: expr OP rhs, including a parenthesized
    # right hand side and the low/high placeholders of BETWEEN-like syntax.
    # Keep placeholder matching case-insensitive, but do not make the
    # operator surface token case-insensitive.  With a module-level ``re.I``
    # flag, ordinary prose such as ``a or word`` can be captured as a
    # supposed operator surface and can also prevent BETWEEN-like patterns
    # from reaching the ternary classification below.
    operand = r"(?i:(?:expr|expression|value|operand|left|right|lhs|rhs|pattern|condition|low|high|zone|arr|array|a|b|c|x|y|z|input|output|column|field))"
    token = r"(?:[A-Z][A-Z0-9_$]*|[!<>=~%&|^+*/-]{1,3})"
    infix = re.compile(
        rf"\b{operand}\b\s+(?P<surface>{token}(?:\s+{token}){{0,3}})\s+"
        rf"(?=(?:\b{operand}\b|\(|\[))",
    )
    for match in infix.finditer(text):
        surface = match.group("surface")
        # OP is a documentation placeholder, not part of the identity.
        if surface.upper().startswith("OP "):
            surface = surface[3:]
        tail = text[match.end(): match.end() + 100]
        category = "TERNARY_PREDICATE" if (
            re.search(r"\blow\b", tail, re.I)
            and re.search(r"\bhigh\b", tail, re.I)
        ) else (
            "MULTI_TOKEN_INFIX" if len(surface.split()) > 1
            else "KEYWORD_INFIX"
        )
        # In BETWEEN-like syntax, AND is a grammar connector, not a second
        # independent surface owned by the same ternary predicate.
        if surface.casefold() == "and" and re.search(
            r"\bbetween\b[^.;]{0,80}\blow\s+and\s+high\b",
            text,
            re.I,
        ):
            continue
        add(surface, category, match.group(0))

    # Symbols need not be uppercase, and a symbol may be adjacent to a
    # literal rather than a named placeholder.
    symbolic = re.compile(
        rf"\b{operand}\b\s*(?P<surface>[!<>=~%&|^+*/-]{{1,3}})\s*"
        rf"(?=\b{operand}\b|[0-9\[\('\"])",
    )
    for match in symbolic.finditer(text):
        add(match.group("surface"), "SYMBOLIC_INFIX", match.group(0))

    # Postfix predicates and prefix unary forms.  The look-ahead keeps prose
    # after a sentence from becoming part of the operator surface.
    postfix = re.compile(
        rf"\b{operand}\b\s+(?P<surface>{token}(?:\s+{token}){{0,3}})"
        rf"(?=\s*(?:(?i:returns?|means?|is|equivalent)|[.,;:]|$))",
    )
    for match in postfix.finditer(text):
        surface = match.group("surface")
        if surface.upper().startswith("OP "):
            surface = surface[3:]
        add(
            surface,
            "POSTFIX_PREDICATE" if not any(ch in surface for ch in "!<>=~%&|^+*/-") else "SYMBOLIC_INFIX",
            match.group(0),
        )

    prefix = re.compile(
        rf"\b(?P<surface>{token}(?:\s+{token}){{0,2}})\s+\b{operand}\b",
    )
    for match in prefix.finditer(text):
        add(match.group("surface"), "KEYWORD_INFIX", match.group(0))

    # Quantifier-like surfaces are expressed as OP SOME(arr)/OP ALL(arr).  OP
    # is structural notation, so only the following token is an identity.
    quantifier = re.compile(r"(?i:\bOP)\s+(?P<surface>[A-Z][A-Z0-9_$]*)\s*\(")
    for match in quantifier.finditer(text):
        add(match.group("surface"), "KEYWORD_INFIX", match.group(0))

    # Prefer a complete multi-token surface over a shorter prefix generated
    # by the prefix matcher (for example AT TIME vs AT TIME ZONE).
    filtered: list[tuple[str, str, str]] = []
    for row in results:
        surface = row[0]
        if any(
            surface != other[0]
            and other[0].startswith(surface + " ")
            and row[2] in other[2]
            for other in results
        ):
            continue
        filtered.append(row)

    unique: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in filtered:
        key = (row[0], row[1])
        if key not in seen:
            seen.add(key)
            unique.append(row)
    # A ternary classification is more specific than the generic keyword
    # classification emitted by an overlapping prefix match.
    ternary_surfaces = {row[0] for row in unique if row[1] == "TERNARY_PREDICATE"}
    return [
        row for row in unique
        if not (row[0] in ternary_surfaces and row[1] == "KEYWORD_INFIX")
        and not (ternary_surfaces and row[0] == "AND")
    ]


def _heading_operator_surfaces(block: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """Extract explicit surfaces from operator-shaped headings only."""

    headings = [_clean(value).lstrip("\u200b") for value in _values(block.get("heading_ancestry"))]
    text = _clean(block.get("text")).lstrip("\u200b")
    values = list(headings)
    if str(block.get("block_type") or "").lower() == "heading" and text:
        values.append(text)
    results: list[tuple[str, str, str]] = []
    for value in values:
        if not value or not _OPERATOR_ROLE.search(value):
            continue
        explicit_surface_marker = bool(re.search(
            r"\b(?:operator|predicate|infix|postfix)\b",
            value,
            re.I,
        ))
        # Parenthetical punctuation is the most explicit surface form.
        for match in re.finditer(r"\((?P<surface>[!<>=~%&|^+*/-]{1,3})\)", value):
            results.append((_normal_surface(match.group("surface")), "SYMBOLIC_INFIX", value))
        if not explicit_surface_marker:
            continue
        words = [_normal_surface(token) for token in _TOKEN.findall(value)]
        if not words:
            continue
        # Keep uppercase/symbol tokens and discard role/documentation words.
        kept = [
            word for word in words
            if word.lower() not in _NOISE_WORDS
            and word.lower() not in _OPERATOR_NOISE_WORDS
        ]
        if kept and all(_SURFACE_TOKEN.fullmatch(word) for word in kept):
            # A heading such as "Logical AND Operator" becomes AND.  A
            # heading with only descriptive words produces no candidate.
            if len(kept) <= 4:
                results.append((_normal_surface(" ".join(kept)), "KEYWORD_INFIX", value))
        # Some headings enumerate two postfix surfaces with "and".
        for part in re.split(r"\s+and\s+|\s+/\s+", value, flags=re.I):
            part_tokens = [
                _normal_surface(token) for token in _TOKEN.findall(part)
                if token.lower() not in _NOISE_WORDS
                and token.lower() not in _OPERATOR_NOISE_WORDS
            ]
            if part_tokens and len(part_tokens) <= 4:
                results.append((_normal_surface(" ".join(part_tokens)), "MULTI_TOKEN_INFIX", value))
    unique: list[tuple[str, str, str]] = []
    for row in results:
        surface_pattern = re.escape(row[0]).replace(r"\ ", r"\s+")
        longer_token = re.search(
            rf"\b{surface_pattern}\s+(?P<next>[A-Z][A-Z0-9_$]*)\b",
            row[2],
            re.I,
        )
        if longer_token and longer_token.group("next").casefold() not in {
            "operator", "predicate", "infix", "postfix"
        }:
            continue
        if any(
            row[0] != other[0]
            and other[0].startswith(row[0] + " ")
            and row[2] == other[2]
            for other in results
        ):
            continue
        if row not in unique:
            unique.append(row)
    return unique


def _adapted_evidence(
    context: Mapping[str, Any],
    *,
    subject: str,
    block: Mapping[str, Any],
    candidate_type: str,
    evidence_kind: str,
    alignment: str,
    category: str,
    signature: str,
    reason_codes: Sequence[str],
    adaptation_rule: str,
) -> dict[str, Any]:
    row = _evidence(
        context,
        subject,
        str(context.get("owner_id") or ""),
        candidate_type,
        evidence_kind,
        alignment,
        STRONG,
        block,
        reason_codes,
        signature=signature,
    )
    row.update({
        "adaptation_rule": adaptation_rule,
        "structural_role": category,
        "owner_local_structural_evidence": True,
        "type_adaptation": True,
    })
    return row


def _datatype_matches(context: Mapping[str, Any], subject: str, block: Mapping[str, Any]) -> list[dict[str, Any]]:
    if (block.get('definition_context') or {}).get('role') == 'BEHAVIOR_TRUTH_TABLE':
        return []
    # A type declaration is materialized as a separate adapted identity unit.
    # Existing units can have the same normalized surface as a function; adding
    # declaration evidence to that merged unit would turn a valid function
    # into a datatype merely because another owner mentions its return type.
    if context.get("adapted_identity_kind") != "DATATYPE":
        return []
    text = _owner_local_text(block)
    if not _subject_in_text(subject, text):
        return []
    headers = " ".join(_values(block.get("table_headers")))
    headings = " | ".join(_values(block.get("heading_ancestry")))
    role_text = " | ".join(value for value in [headers, headings, text] if value)
    type_role = bool(_TYPE_ROLE.search(role_text))
    block_type = str(block.get("block_type") or "").casefold()
    code_like = block_type in {"code", "pre", "code_block", "fenced_code", "codeblock"}
    rows: list[dict[str, Any]] = []
    declaration = False
    rule = ""
    for match in _TYPE_DECLARATION.finditer(text):
        if match.group("subject").casefold() == subject.casefold():
            matched_text = _clean(match.group(0))
            column_style = bool(re.match(
                r"(?:column|field|attribute|name)\b",
                matched_text,
                re.I,
            ))
            if column_style and "(" not in matched_text:
                trailing = text[match.end():]
                if not code_like or not re.match(r"\s*(?:[,;)=\n]|$)", trailing):
                    continue
            declaration = True
            rule = "TYPE_POSITION_DECLARATION"
            break
    cells = [_clean(value) for value in block.get("cells") or [] if _clean(value)]
    first_cell = bool(cells and cells[0].casefold() == subject.casefold())
    header_type_slot = bool(_TYPE_HEADER.search(headers) and first_cell)
    exact_heading = any(_normal_surface(value) == _normal_surface(subject) for value in _values(block.get("heading_ancestry")))
    if header_type_slot:
        declaration = True
        rule = "TYPE_TABLE_DECLARATION"
    elif exact_heading and type_role:
        declaration = True
        rule = "TYPE_HEADING_DECLARATION"
    elif (
        type_role
        and bool(_TYPE_HEADER.search(headers + " | " + headings))
        and re.search(
            r"(?<![A-Za-z0-9_$])" + re.escape(subject) + r"\s*\([^)]*\)", text, re.I
        )
        and not re.search(
            r"\b(?:select|where|having|order\s+by)\b[^\n]*"
            + re.escape(subject) + r"\s*\(",
            text,
            re.I,
        )
    ):
        declaration = True
        rule = "PARAMETERIZED_TYPE_CONSTRUCTOR"
    if not declaration:
        return rows
    rows.append(_adapted_evidence(
        context,
        subject=subject,
        block=block,
        candidate_type="DATATYPE",
        evidence_kind="DATATYPE_DECLARATION",
        alignment=ALIGN_EXACT if exact_heading or header_type_slot else ALIGN_SLOT,
        category="PARAMETERIZED_DATATYPE" if "CONSTRUCTOR" in rule else "DATATYPE_DECLARATION",
        signature=text if re.search(r"\([^)]*\)", text) else "",
        reason_codes=["GENERIC_TYPE_POSITION", rule, "OWNER_LOCAL_DATATYPE_SURFACE"],
        adaptation_rule=rule,
    ))
    return rows


def _operator_matches(context: Mapping[str, Any], subject: str, block: Mapping[str, Any]) -> list[dict[str, Any]]:
    if (block.get('definition_context') or {}).get('role') == 'BEHAVIOR_TRUTH_TABLE':
        return []
    if context.get("adapted_identity_kind") != "OPERATOR":
        return []
    text = _owner_local_text(block)
    if not _subject_in_text(subject, text):
        return []
    role_text = _role_context(block)
    role_context = bool(_OPERATOR_STRONG_ROLE.search(role_text))
    if not role_context:
        return []
    observations = _operator_candidates(text) + _heading_operator_surfaces(block)
    # Operator Closure may carry an explicit heading-derived or ownerless
    # surface observation that the original parser intentionally did not
    # infer from the block text alone. It remains owner-local and is never a
    # vocabulary lookup.
    block_id = str(block.get("block_id") or "")
    for row in context.get("operator_closure_observations") or []:
        if str(row.get("block_id") or "") != block_id:
            continue
        observations.append((
            str(row.get("surface") or ""),
            str(row.get("category") or "KEYWORD_INFIX"),
            str(row.get("matched_text") or text),
        ))
    normalized = _normal_surface(subject)
    rows: list[dict[str, Any]] = []
    for surface, category, matched in observations:
        if _normal_surface(surface) != normalized:
            continue
        rows.append(_adapted_evidence(
            context,
            subject=subject,
            block=block,
            candidate_type="OPERATOR",
            evidence_kind="OPERATOR_SLOT",
            alignment=ALIGN_EXACT,
            category=category,
            signature=matched,
            reason_codes=["GENERIC_OPERATOR_SLOT", category, "OWNER_LOCAL_OPERATOR_SURFACE"],
            adaptation_rule="STRUCTURAL_OPERATOR_SURFACE",
        ))
    # Explicit operator headings may contain an identity that is not repeated
    # in the heading block itself (for example a following syntax row).  Exact
    # subject presence plus a syntax/operator header is enough, but only for a
    # word surface; this is still owner-local and never a vocabulary lookup.
    if not rows and re.search(r"\b(?:syntax|operator|predicate)\b", role_text, re.I):
        if any(_normal_surface(value) == normalized for value in _values(block.get("heading_ancestry")) + _values(block.get("text"))):
            rows.append(_adapted_evidence(
                context,
                subject=subject,
                block=block,
                candidate_type="OPERATOR",
                evidence_kind="OPERATOR_SLOT",
                alignment=ALIGN_EXACT,
                category="KEYWORD_INFIX",
                signature=text,
                reason_codes=["GENERIC_OPERATOR_HEADING", "OWNER_LOCAL_OPERATOR_SURFACE"],
                adaptation_rule="STRUCTURAL_OPERATOR_SURFACE",
            ))
    return rows


def build_adapted_typed_feature_evidence(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Add bounded generic structural evidence to the existing evidence."""

    base = [dict(row) for row in build_typed_feature_evidence(context)]
    subject = _clean(context.get("canonical_subject") or context.get("subject"))
    if not subject:
        return base
    seen = {str(row.get("evidence_id") or "") for row in base}
    for block in context.get("blocks") or []:
        if not isinstance(block, Mapping):
            continue
        for row in _operator_matches(context, subject, block) + _datatype_matches(context, subject, block):
            if row.get("evidence_id") not in seen:
                seen.add(str(row.get("evidence_id") or ""))
                base.append(row)
    base.sort(key=lambda row: (
        str(row.get("identity_unit_id") or ""),
        str(row.get("owner_id") or ""),
        str(row.get("candidate_type") or ""),
        str(row.get("evidence_id") or ""),
    ))
    return base


def adapted_decide_type(identity: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Prefer precise structural adaptation evidence without global guesses."""

    baseline = decide_type(identity, evidence)
    adapted = [
        row for row in evidence
        if row.get("type_adaptation")
        and row.get("definition_vote_eligible", True) is not False
        and row.get("owner_local_binding", True) is not False
        and row.get("candidate_type") in TARGET_TYPES
        and row.get("strength") == STRONG
    ]
    if not adapted:
        return baseline
    if "SUBJECT_TYPE_CONFLICT" in baseline.get("reason_codes", []):
        return baseline
    by_type: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in adapted:
        by_type[str(row.get("candidate_type"))].append(row)
    types = sorted(by_type)
    owners_by_type = {
        kind: {str(row.get("owner_id") or "") for row in rows}
        for kind, rows in by_type.items()
    }
    same_owner_conflict = any(
        bool(owners_by_type[left] & owners_by_type[right])
        for index, left in enumerate(types)
        for right in types[index + 1:]
    )
    if len(types) != 1 or same_owner_conflict:
        result = dict(baseline)
        result.setdefault("reason_codes", [])
        result["reason_codes"] = list(dict.fromkeys(
            list(result["reason_codes"])
            + ["GENERIC_STRUCTURAL_TYPE_CONFLICT", "TYPE_REVIEW_REQUIRED"]
        ))
        result["type_status"] = "REVIEW_REQUIRED"
        result["primary_type"] = TARGET_UNCERTAIN
        result["alternate_types"] = sorted(set(result.get("alternate_types") or []) | set(types))
        result["confidence"] = min(float(result.get("confidence") or 0.0), 0.62)
        result["adaptation"] = {
            "structural_types": types,
            "same_owner_conflict": same_owner_conflict,
            "evidence_ids": [str(row.get("evidence_id")) for row in adapted],
        }
        return result
    primary = types[0]
    structural_ids = [str(row.get("evidence_id")) for row in adapted if row.get("evidence_id")]
    result = dict(baseline)
    result["primary_type"] = primary
    result["type_status"] = "CONFIRMED"
    result["confidence"] = max(float(result.get("confidence") or 0.0), 0.96)
    result["supporting_evidence_ids"] = sorted(set(result.get("supporting_evidence_ids") or []) | set(structural_ids))
    result["reason_codes"] = list(dict.fromkeys(
        list(result.get("reason_codes") or [])
        + ["GENERIC_STRUCTURAL_TYPE_PRIORITY", "OWNER_LOCAL_STRUCTURAL_EVIDENCE"]
    ))
    result["adaptation"] = {
        "structural_types": [primary],
        "same_owner_conflict": False,
        "evidence_ids": structural_ids,
        "base_primary_type": baseline.get("primary_type"),
    }
    return result


def _stable_id(parts: Iterable[Any]) -> str:
    value = "|".join(_clean(part) for part in parts)
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:24]


def _surface_from_block(block: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    role_text = _role_context(block)
    # ``Syntax`` alone is common on function pages.  It is not enough to turn
    # an expression token into an operator; the block must carry an explicit
    # operator/predicate/comparison role.
    if not _OPERATOR_STRONG_ROLE.search(role_text):
        return []
    text = _owner_local_text(block)
    results = _operator_candidates(text) + _heading_operator_surfaces(block)
    return list(dict.fromkeys(results))


def _datatype_surfaces_from_block(block: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """Find type names only in declaration/type-table positions."""

    text = _owner_local_text(block)
    headers = " ".join(_values(block.get("table_headers")))
    headings = " | ".join(_values(block.get("heading_ancestry")))
    block_type = str(block.get("block_type") or "").lower()
    results: list[tuple[str, str, str]] = []

    def add(surface: str, rule: str, matched: str) -> None:
        surface = _clean(surface).strip("()[]{}:;,.'\"")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", surface):
            return
        if not _looks_like_type_name(surface):
            return
        results.append((_normal_surface(surface), rule, _clean(matched)))

    # Explicit cast annotation and PostgreSQL-style type annotation are
    # structural type positions, regardless of the surrounding page.
    for pattern, rule in (
        (r"::\s*(?P<surface>[A-Za-z_][A-Za-z0-9_$]*)\s*(?:\([^)]*\))?", "TYPE_POSITION_CAST"),
        (r"\bCAST\s*\([^)]*?\bAS\s+(?P<surface>[A-Za-z_][A-Za-z0-9_$]*)\s*(?:\([^)]*\))?", "TYPE_POSITION_CAST"),
        (r"\b(?:column|field|attribute|name)\s+[_A-Za-z][_A-Za-z0-9$]*\s+(?P<surface>[A-Za-z_][A-Za-z0-9_$]*)\s*(?:\([^)]*\))?", "TYPE_POSITION_DECLARATION"),
    ):
        for match in re.finditer(pattern, text, re.I):
            if rule == "TYPE_POSITION_CAST" and "::" in match.group(0):
                if re.search(r"\b(?:std|DB|shared_ptr|unsigned|long)\s*::", text):
                    continue
                if not re.search(r"\b(?:select|from|where|cast|create\s+table|insert|update|delete|as)\b", text, re.I):
                    continue
            if rule == "TYPE_POSITION_DECLARATION":
                surface = match.group("surface")
                type_like_context = bool(
                    re.search(r"\([^)]*\)", match.group(0))
                    or any(char.isdigit() for char in surface)
                    or (
                        block_type in {"code", "pre", "code_block", "fenced_code", "codeblock"}
                        and re.match(r"\s*(?:[,;)=\n]|$)", text[match.end():])
                    )
                )
                if not type_like_context:
                    continue
            add(match.group("surface"), rule, match.group(0))

    # A type table has a declared type header and an identity-like first cell.
    cells = [_clean(value) for value in block.get("cells") or [] if _clean(value)]
    if cells and _TYPE_HEADER.search(headers):
        # A return-type column describes the callable, not the first cell's
        # declaration role. Recover only from the actual declared-type slot.
        declared_slots=[i for i,h in enumerate(_values(block.get('table_headers')))
            if _TYPE_HEADER.search(h) and not re.search(r'\b(?:return\w*|result|output)\b',h,re.I)]
        for i in declared_slots:
            if i>=len(cells):continue
            cell=cells[i]
            if cell.casefold() in {header.casefold() for header in _values(block.get("table_headers"))}:
                continue
            match = re.match(r"(?P<surface>[A-Za-z_][A-Za-z0-9_$]*)\s*(?:\([^)]*\))?", cell)
            if match and match.group(0).strip() == cell.strip():
                add(match.group("surface"), "TYPE_TABLE_DECLARATION", cell)

    # Binary/type mapping tables often put the type name in the first column
    # and use a header such as "ClickHouse data type".  The same generic rule
    # applies to any documentation source; no page or DB name is inspected.
    if cells and re.search(r"\b(?:data\s+type|type\s+mapping|declared\s+type)\b", headers + " | " + headings, re.I):
        for cell in cells[:1]:
            if cell.casefold() in {header.casefold() for header in _values(block.get("table_headers"))}:
                continue
            match = re.match(r"(?P<surface>[A-Za-z_][A-Za-z0-9_$]*)\s*(?:\([^)]*\))?", cell)
            if match and match.group(0).strip() == cell.strip():
                add(match.group("surface"), "TYPE_MAPPING_DECLARATION", cell)

    # Exact headings under a type/declaration heading are safe even when the
    # page body is sparse.  Do not use the URL or the page title as a type
    # decision here.
    if block_type == "heading" and _TYPE_HEADER.search(headings):
        heading = _clean(block.get("text")).lstrip("\u200b")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*(?:\s*\([^)]*\))?", heading):
            add(re.match(r"[A-Za-z_][A-Za-z0-9_$]*", heading).group(0), "TYPE_HEADING_DECLARATION", heading)
    return list(dict.fromkeys(results))


def augment_identity_units_for_type_adaptation(
    identity: Mapping[str, Any],
    documents: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Recover only structurally explicit operator/type surfaces.

    This is not a second general identity crawler.  It is limited to a local
    operator slot or a type-declaration table/heading and keeps the discovered
    owner/block provenance on every adapted unit.
    """

    result = copy.deepcopy(dict(identity))
    units = list(result.get("units") or [])
    owners = list(result.get("owners") or [])
    page_by_id = {str(row.get("page_id") or ""): row for row in documents}
    adapted_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    audit: list[dict[str, Any]] = []
    created = 0
    attached = 0
    # Recovery must not rematerialize an identity already defined by the exact
    # same native scope. This is block-level coverage, never name-only merge.
    from agents.owner.surface_patterns import native_definition_coverage
    covered_native=native_definition_coverage(units,owners,documents)

    def make_unit(surface: str, owner: Mapping[str, Any], page: Mapping[str, Any], block: Mapping[str, Any], kind: str) -> dict[str, Any]:
        key = _normal_surface(surface)
        unit_id = "identity_unit:adapted:" + _stable_id((key, kind))
        return {
            "identity_unit_id": unit_id,
            "canonical_identity": key,
            "variants": [surface],
            "owner_ids": [str(owner.get("owner_id") or "")],
            "candidate_ids": [],
            "occurrence_ids": [],
            "defining_occurrence_ids": [],
            "evidence": [],
            "occurrence_records": [],
            "aliases": [],
            "alias_relations": [],
            "merge_decisions": [],
            "non_defining_occurrence_ids": [],
            "group_key": f"{key}|ADAPTED",
            "adapted_identity_kind": kind,
            "group_status": "PROBABLE_FEATURE",
            "feature_type": "UNCERTAIN",
            "type_hint": "UNCERTAIN",
            "semantic_ready": False,
            "adaptation_provenance": [make_adaptation_provenance_record(
                adaptation_kind=kind,
                adaptation_rule_id=f"GENERIC_{kind}_OWNER_SURFACE",
                owner_id=owner.get("owner_id"),
                page_id=page.get("page_id"),
                source_url=page.get("url"),
                block_id=block.get("block_id"),
                raw_text=_owner_local_text(block),
                page_hash=page.get("page_hash"),
            )],
            "provenance": {
                **build_provenance(
                    dbms=str(page.get("dbms") or ""),
                    source_url=str(page.get("url") or ""),
                    page_id=str(page.get("page_id") or ""),
                    page_hash=str(page.get("page_hash") or ""),
                    html_path=str(page.get("html_path") or ""),
                    producer_stage="identity_unit",
                    producer_version="identity-unit-adapted/v1",
                    parent_ids=[str(owner.get("owner_id") or ""), str(block.get("block_id") or "")],
                    reason_codes=["GENERIC_STRUCTURAL_ADAPTATION"],
                ),
            },
        }

    for owner in owners:
        page = page_by_id.get(str(owner.get("page_id") or ""))
        if not page:
            continue
        blocks_by_id = {
            str(block.get("block_id") or ""): block
            for block in page.get("blocks") or []
            if isinstance(block, Mapping)
        }
        for block_id in owner.get("member_block_ids") or []:
            block = blocks_by_id.get(str(block_id))
            if not block:
                continue
            typed = {
                "block_id": block.get("block_id"),
                "block_type": str(block.get("block_kind") or "text").casefold(),
                "text": block.get("text") or "",
                "cells": list((block.get("table_metadata") or {}).get("cells") or []),
                "table_headers": list((block.get("table_metadata") or {}).get("headers") or []),
                "heading_ancestry": list(block.get("heading_ancestry") or []),
            }
            observations = [
                (surface, category, matched, "OPERATOR")
                for surface, category, matched in _surface_from_block(typed)
            ] + [
                (surface, rule, matched, "DATATYPE")
                for surface, rule, matched in _datatype_surfaces_from_block(typed)
            ]
            for surface, category, matched, kind in observations:
                key = (_normal_surface(surface), kind)
                from agents.owner.profile_execution import identity_authorized
                if not identity_authorized(owner,str(block_id),key[0]):
                    audit.append({'identity':key[0],'owner_id':owner.get('owner_id'),'block_id':block_id,
                                  'identity_action':'BLOCKED_BY_EXPLICIT_PROFILE_LOCATION'})
                    continue
                native_id=covered_native.get((str(page.get('page_id')),str(block_id),key[0],kind))
                if native_id:
                    audit.append({'identity':key[0],'surface':surface,'category':category,
                        'owner_id':owner.get('owner_id'),'page_id':page.get('page_id'),'source_url':page.get('url'),
                        'block_id':block_id,'matched_text':matched,'identity_action':'COVERED_BY_PROVEN_NATIVE_DEFINITION',
                        'native_identity_unit_id':native_id,'adapted_identity_kind':kind})
                    continue
                existing = adapted_by_key.get(key)
                action = "ATTACHED"
                if existing is None:
                    existing = make_unit(surface, owner, page, typed, kind)
                    adapted_by_key[key] = existing
                    units.append(existing)
                    created += 1
                    action = "CREATED"
                else:
                    owner_id = str(owner.get("owner_id") or "")
                    if owner_id and owner_id not in existing.setdefault("owner_ids", []):
                        existing["owner_ids"].append(owner_id)
                        attached += 1
                    if surface not in existing.setdefault("variants", []):
                        existing["variants"].append(surface)
                    existing.setdefault("adaptation_provenance", []).append(
                        make_adaptation_provenance_record(
                            adaptation_kind=kind,
                            adaptation_rule_id=f"GENERIC_{kind}_OWNER_SURFACE",
                            owner_id=owner.get("owner_id"),
                            page_id=page.get("page_id"),
                            source_url=page.get("url"),
                            block_id=block.get("block_id"),
                            raw_text=_owner_local_text(typed),
                            page_hash=page.get("page_hash"),
                        )
                    )
                audit.append({
                    "identity": _normal_surface(surface),
                    "surface": surface,
                    "category": category,
                    "owner_id": owner.get("owner_id"),
                    "page_id": page.get("page_id"),
                    "source_url": page.get("url"),
                    "block_id": block.get("block_id"),
                    "matched_text": matched,
                    "identity_action": action,
                    "adapted_identity_kind": kind,
                })
    for unit in units:
        unit["owner_ids"] = sorted(set(str(value) for value in unit.get("owner_ids") or [] if str(value)))
        unit["variants"] = sorted(set(str(value) for value in unit.get("variants") or [] if str(value)))
    result["units"] = units
    result.setdefault("stats", {})
    result["stats"].update({
        "type_adaptation_identity_units_before": len(identity.get("units") or []),
        "type_adaptation_identity_units_after": len(units),
        "type_adaptation_identity_units_created": created,
        "type_adaptation_identity_units_owner_attachments": attached,
    })
    return result, audit


__all__ = [
    "adapted_decide_type",
    "augment_identity_units_for_type_adaptation",
    "build_adapted_typed_feature_evidence",
]
