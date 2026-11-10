"""Closed generic recipe library producing owner-aware FeatureMention IR."""
from __future__ import annotations

from collections import defaultdict
import re

from agents.document_structure_v2.models import clean_text, evidence, stable_id


_CALLABLE = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_$]*)\s*\((?P<args>.*)\)$")
_CALLABLE_ITEM = re.compile(r"(?<![A-Za-z0-9_$])(?P<name>[A-Za-z_][A-Za-z0-9_$]*)\s*\((?P<args>[^()]*)\)")
_DATATYPE = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_$]*(?:\s+(?:PRECISION|VARYING))?)(?=\s*(?:\(|\[|$))", re.I)


def _field_claim(page: dict, block: dict, field: str) -> dict:
    return {"field": field, "value": block["text"], "evidence": evidence(page, block, role=field)}


def _prose_claims(page: dict, blocks: list[dict]) -> list[dict]:
    claims = []
    for block in blocks:
        if block["block_type"] != "paragraph":
            continue
        claims.append(_field_claim(page, block, "description"))
        if re.search(r"\b(?:must|cannot|can range|range (?:is|from)|minimum|maximum|default is|between\s+\d+\s+and\s+\d+)\b", block["text"], re.I):
            claims.append(_field_claim(page, block, "constraint"))
    return claims


def _mention(page: dict, block: dict, feature_type: str, name: str, role: str,
             recipe: str, *, notation: str | None = None, signature: str | None = None,
             link: dict | None = None) -> dict:
    payload = [page["page_id"], block["block_id"], feature_type, name, role, recipe]
    return {
        "schema_version": "feature-mention/v2", "mention_id": stable_id("mention", payload),
        "feature_type": feature_type, "name": name, "normalized_name": name.casefold(),
        "mention_role": role, "reference_notation": notation,
        "formal_signature": signature, "page_archetype": page["page_archetype"],
        "recipe_id": recipe, "owner_group_id": block["boundary_id"],
        "detail_link": ({"link_id": link["link_id"], "url": link["resolved_url"],
                         "fragment": link["fragment"]} if link else None),
        "evidence": [evidence(page, block, name, "identity")],
    }


def _link_for_block(page: dict, block_id: str) -> dict | None:
    return next((link for link in page["links"] if link.get("source_block_id") == block_id), None)


def _feature_type(text: str, link: dict | None, contexts: list[str]) -> str | None:
    callable_match = _CALLABLE.fullmatch(text)
    if callable_match:
        return "function"
    if len(contexts) == 1:
        return contexts[0]
    return None


def _name_from_operator_syntax(text: str, fragment: str | None) -> str | None:
    symbolic = re.findall(r"[+*/%<>=!~^&|@?#:.-]+", text)
    if len(symbolic) == 1:
        return symbolic[0]
    normalized = clean_text(text)
    if re.fullmatch(r"[A-Z][A-Z_]*(?:\s+[A-Z][A-Z_]*)*", normalized):
        return normalized.upper()
    segments = [value.strip() for value in re.split(r"\s*\.\.\.\s*", normalized) if value.strip()]
    if segments and all(re.fullmatch(r"[A-Z][A-Z_]*(?:\s+[A-Z][A-Z_]*)*", value)
                        for value in segments):
        return segments[0].upper()
    lexical_spans = re.findall(r"(?<![A-Z_])[A-Z][A-Z_]*(?:\s+[A-Z][A-Z_]*)*(?![A-Z_])", text)
    if lexical_spans and re.search(r"\b[a-z][A-Za-z0-9_]*\b", text):
        return clean_text(lexical_spans[0]).upper()
    return None


def reference_table_recipe(page: dict) -> tuple[list[dict], list[dict]]:
    mentions, owners = [], []
    for block in page["blocks"]:
        cells = block.get("cells", [])
        if block["block_type"] != "table_row" or len(cells) < 2:
            continue
        headers = [value.casefold() for value in block.get("table_headers", [])]
        if cells[0].casefold() in headers or cells[0].casefold() in {"name", "operator", "type"}:
            continue
        link = _link_for_block(page, block["block_id"])
        kind = _feature_type(cells[0], link, page["feature_context"])
        if kind is None:
            continue
        if kind == "function":
            match = _CALLABLE.fullmatch(cells[0])
            if not match:
                continue
            name = match.group("name")
        elif kind == "operator":
            name = _name_from_operator_syntax(cells[0], (link or {}).get("fragment"))
            if not name:
                continue
        else:
            match = _DATATYPE.match(cells[0])
            if not match:
                continue
            name = match.group("name")
        mention = _mention(page, block, kind, name, "index_entry", "reference_table_recipe",
                           notation=cells[0], link=link)
        mention["summary"] = cells[1]
        mention["field_claims"] = [{"field": "reference_summary", "value": cells[1],
                                     "evidence": evidence(page, block, cells[1], "reference_summary")}]
        mentions.append(mention)
        owners.append(_owner(page, block["boundary_id"], kind, name, [block], mention, mention["field_claims"]))
    return mentions, owners


def aggregate_syntax_recipe(page: dict) -> tuple[list[dict], list[dict]]:
    return _repeated_owner_recipe(page, "aggregate_syntax_recipe", aggregate=True)


def definition_list_recipe(page: dict) -> tuple[list[dict], list[dict]]:
    return _repeated_owner_recipe(page, "definition_list_recipe", aggregate=False)


def heading_owned_section_recipe(page: dict) -> tuple[list[dict], list[dict]]:
    return single_feature_detail_recipe(page)


def mixed_function_operator_recipe(page: dict) -> tuple[list[dict], list[dict]]:
    table_mentions, table_owners = reference_table_recipe(page)
    detail_mentions, detail_owners = definition_list_recipe(page)
    return table_mentions + detail_mentions, table_owners + detail_owners


def mapping_table_recipe(page: dict) -> tuple[list[dict], list[dict]]:
    # Mapping rows describe equivalence claims, not canonical feature ownership.
    return [], []


def precedence_table_recipe(page: dict) -> tuple[list[dict], list[dict]]:
    # Precedence rows become relations only after their owners are established elsewhere.
    return [], []


def structural_page_recipe(page: dict) -> tuple[list[dict], list[dict]]:
    """Record template execution without manufacturing entities from navigation or prose."""
    return [], []


def single_feature_detail_recipe(page: dict) -> tuple[list[dict], list[dict]]:
    blocks = page["blocks"]
    heading = next((block for block in blocks if block["block_type"] == "heading"), None)
    if not heading or not page["feature_context"]:
        return [], []
    kind = page["feature_context"][0]
    name = None
    if kind == "datatype":
        values = re.findall(r"\b[A-Z][A-Z0-9_]*(?:\s+(?:PRECISION|VARYING))?\b", heading["text"])
        name = values[-1] if values else None
    if not name:
        return [], []
    formal_signature = None
    if kind == "datatype":
        for block in blocks:
            formal_signature = next((literal for literal in block.get("inline_literals", [])
                                     if re.match(rf"^{re.escape(name)}\s*[([]", literal, re.I)), None)
            if formal_signature:
                break
    mention = _mention(page, heading, kind, name, "formal_definition", "single_feature_detail_recipe",
                       signature=formal_signature)
    claims = _prose_claims(page, blocks)
    if formal_signature:
        syntax_block = next(block for block in blocks if formal_signature in block.get("inline_literals", []))
        claims.insert(0, {"field": "syntax", "value": formal_signature,
                          "evidence": evidence(page, syntax_block, formal_signature, "formal_signature")})
    claims += [_field_claim(page, block, "examples") for block in blocks if block["block_type"] == "code"]
    mention["field_claims"] = claims
    return [mention], [_owner(page, heading["boundary_id"], kind, name, blocks, mention, claims)]


def _repeated_owner_recipe(page: dict, recipe: str, aggregate: bool) -> tuple[list[dict], list[dict]]:
    grouped = defaultdict(list)
    for block in page["blocks"]:
        if block["boundary_kind"] == "repeated_unit":
            grouped[block["boundary_id"]].append(block)
    mentions, owners = [], []
    for boundary_id, blocks in sorted(grouped.items()):
        identity = blocks[0]
        link = next((_link_for_block(page, block["block_id"]) for block in blocks
                     if _link_for_block(page, block["block_id"])), None)
        text = identity["text"]
        contexts = page["feature_context"]
        multi_callable_owner = False
        if aggregate:
            match = _DATATYPE.match(text)
            kind, name, signature = ("datatype", match.group("name"), text) if match else (None, None, None)
        else:
            kind = _feature_type(text, link, contexts)
            callable_match = _CALLABLE.fullmatch(text)
            callable_matches = list(_CALLABLE_ITEM.finditer(text))
            residue = _CALLABLE_ITEM.sub(" ", text)
            single_callable_owner = (len(callable_matches) == 1
                                     and callable_matches[0].span() == (0, len(text)))
            multi_callable_owner = (len(callable_matches) > 1
                                    and not re.search(r"[A-Za-z0-9_$]", residue)
                                    and "function" in contexts)
            if kind == "function" and callable_match and single_callable_owner:
                name = callable_match.group("name")
                formal_block = next((block for block in blocks[1:]
                                     if block["block_type"] == "code"
                                     and (candidate := _CALLABLE.fullmatch(block["text"]))
                                     and candidate.group("name").casefold() == name.casefold()), None)
                signature = formal_block["text"] if formal_block else text
            elif multi_callable_owner:
                kind = "function"
                name = callable_matches[0].group("name")
                signature = callable_matches[0].group(0)
            elif kind == "operator":
                name = _name_from_operator_syntax(text, (link or {}).get("fragment"))
                signature = text
            else:
                name, signature = None, None
        if not kind or not name:
            continue
        signatures = ([(match.group("name"), match.group(0)) for match in callable_matches]
                      if multi_callable_owner else [(name, signature)])
        owner_mentions = []
        for item_name, item_signature in signatures:
            mention = _mention(page, identity, kind, item_name, "formal_syntax", recipe,
                               signature=item_signature, link=link)
            if multi_callable_owner:
                mention["mention_id"] = stable_id("mention", [page["page_id"], identity["block_id"],
                                                                  kind, item_name, item_signature, recipe])
            if (kind == "function"
                    and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*\s*\(\s*\)", item_signature)
                    and re.search(r">\s*dt(?:\W|$)", identity.get("dom_path", ""))):
                mention["formal_shape"] = {"kind": "fixed", "arity_min": 0, "arity_max": 0}
            syntax_block = next((block for block in blocks if item_signature in block["text"]), identity)
            claims = [{"field": "syntax", "value": item_signature,
                       "evidence": evidence(page, syntax_block, item_signature, "formal_signature")}]
            claims += _prose_claims(page, blocks[1:])
            claims += [_field_claim(page, block, "examples") for block in blocks[1:] if block["block_type"] == "code"]
            mention["field_claims"] = claims
            mentions.append(mention); owner_mentions.append(mention)
        owner = _owner(page, boundary_id, kind, name, blocks, owner_mentions[0], owner_mentions[0]["field_claims"])
        owner["owner_mention_ids"] = [row["mention_id"] for row in owner_mentions]
        owner["entity_relation"] = "overload" if len({row["normalized_name"] for row in owner_mentions}) == 1 else "sibling"
        owner["entity_members"] = [{"name": row["name"], "formal_signature": row["formal_signature"]}
                                   for row in owner_mentions]
        owners.append(owner)
    return mentions, owners


def _owner(page: dict, boundary_id: str, kind: str, name: str, blocks: list[dict],
           mention: dict, claims: list[dict]) -> dict:
    return {
        "schema_version": "feature-owner-group/v2", "owner_group_id": boundary_id,
        "owner_type": kind, "owner_name": name, "page_id": page["page_id"],
        "owner_mention_ids": [mention["mention_id"]], "ownership_mode": "exclusive",
        "identity_blocks": [mention["evidence"][0]["block_id"]],
        "syntax_blocks": sorted({claim["evidence"]["block_id"] for claim in claims if claim["field"] == "syntax"}),
        "description_blocks": sorted({claim["evidence"]["block_id"] for claim in claims if claim["field"] == "description"}),
        "example_blocks": sorted({claim["evidence"]["block_id"] for claim in claims if claim["field"] == "examples"}),
        "constraint_blocks": sorted({claim["evidence"]["block_id"] for claim in claims if claim["field"] == "constraint"}),
        "all_blocks": [block["block_id"] for block in blocks],
    }


def execute_page_recipe(page: dict) -> tuple[list[dict], list[dict]]:
    archetype = page["page_archetype"]
    if archetype == "feature_reference_index":
        return reference_table_recipe(page)
    if archetype == "mixed_feature_reference":
        return mixed_function_operator_recipe(page)
    if archetype == "aggregate_syntax":
        return aggregate_syntax_recipe(page)
    if archetype == "multi_feature_detail":
        return definition_list_recipe(page)
    if archetype == "single_feature_detail":
        if any(block["boundary_kind"] == "repeated_unit" for block in page["blocks"]):
            mentions, owners = definition_list_recipe(page)
            if mentions:
                return mentions, owners
        return single_feature_detail_recipe(page)
    if archetype == "mapping_table":
        return mapping_table_recipe(page)
    if archetype == "precedence_table":
        return precedence_table_recipe(page)
    return [], []


def execute_profile_recipe(page: dict, template: dict) -> tuple[list[dict], list[dict]]:
    """Execute the recipe compiled into an induced template, with no archetype dispatch fallback."""
    recipes = {
        "reference_table_recipe": reference_table_recipe,
        "aggregate_syntax_recipe": aggregate_syntax_recipe,
        "definition_list_recipe": definition_list_recipe,
        "heading_owned_section_recipe": heading_owned_section_recipe,
        "single_feature_detail_recipe": single_feature_detail_recipe,
        "mixed_function_operator_recipe": mixed_function_operator_recipe,
        "mapping_table_recipe": mapping_table_recipe,
        "precedence_table_recipe": precedence_table_recipe,
        "structural_page_recipe": structural_page_recipe,
    }
    recipe = recipes.get(template.get("recipe"))
    if recipe is None:
        raise ValueError(f"Unsupported compiled recipe: {template.get('recipe')}")
    return recipe(page)
