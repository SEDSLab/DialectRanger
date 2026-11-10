"""Official-link-first mention linking and feature-type canonicalization."""
from __future__ import annotations

from collections import defaultdict
import re

from agents.document_structure_v2.models import stable_id


def link_mentions(mentions: list[dict], pages: list[dict]) -> list[dict]:
    pages_by_id = {page["page_id"]: page for page in pages}
    page_by_url = {page["url"]: page for page in pages}
    by_page_name = defaultdict(list)
    for mention in mentions:
        evidence = mention["evidence"][0]
        by_page_name[(evidence["page_id"], mention["feature_type"], mention["normalized_name"])].append(mention)

    relations = []
    incoming_by_link = defaultdict(list)
    for mention in mentions:
        if mention.get("incoming_official_link"):
            incoming_by_link[mention["incoming_official_link"]].append(mention)
    for source in mentions:
        link = source.get("detail_link")
        if not link:
            continue
        for target in incoming_by_link.get(link["link_id"], []):
            if (target["mention_id"] != source["mention_id"]
                    and target["feature_type"] == source["feature_type"]
                    and target["normalized_name"] == source["normalized_name"]):
                relations.append({
                    "schema_version": "feature-relation/v2",
                    "relation_id": stable_id("feature-relation", [source["mention_id"], target["mention_id"], "official_href_to_owner"]),
                    "relation_type": "reference_links_to_definition",
                    "source_mention_id": source["mention_id"], "target_mention_id": target["mention_id"],
                    "link_id": link["link_id"], "linking_method": "official_href_to_owner",
                })
        target_page = page_by_url.get(link["url"])
        if not target_page:
            continue
        candidates = by_page_name[(target_page["page_id"], source["feature_type"], source["normalized_name"])]
        for target in candidates:
            if target["mention_id"] == source["mention_id"]:
                continue
            target_link = target.get("detail_link") or {}
            fragment_matches = not link.get("fragment") or target_link.get("fragment") == link["fragment"]
            if not fragment_matches:
                continue
            method = "official_same_page_anchor" if link.get("fragment") else "official_href"
            relations.append({
                "schema_version": "feature-relation/v2",
                "relation_id": stable_id("feature-relation", [source["mention_id"], target["mention_id"], method]),
                "relation_type": "reference_links_to_definition",
                "source_mention_id": source["mention_id"], "target_mention_id": target["mention_id"],
                "link_id": link["link_id"], "linking_method": method,
            })
    unique = {row["relation_id"]: row for row in relations}
    return sorted(unique.values(), key=lambda row: row["relation_id"])


def _components(mentions: list[dict], relations: list[dict]) -> list[list[dict]]:
    by_id = {row["mention_id"]: row for row in mentions}
    parent = {key: key for key in by_id}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    for relation in relations:
        left, right = find(relation["source_mention_id"]), find(relation["target_mention_id"])
        if left != right:
            parent[right] = left

    # Same normalized owner is a lower-priority fallback, never a source of new identities.
    anchored = defaultdict(list)
    for mention in mentions:
        discriminator = None
        if mention["feature_type"] == "operator":
            discriminator = mention.get("formal_shape") or _operator_shape(
                mention.get("formal_signature") or "", mention["name"])
            discriminator = tuple(sorted(discriminator.items()))
        anchored[(mention["feature_type"], mention["normalized_name"], discriminator)].append(mention)
    for group in anchored.values():
        if len(group) < 2 or not any(row.get("formal_signature") for row in group):
            continue
        first = find(group[0]["mention_id"])
        for mention in group[1:]:
            other = find(mention["mention_id"])
            if first != other:
                parent[other] = first

    result = defaultdict(list)
    for mention_id, mention in by_id.items():
        result[find(mention_id)].append(mention)
    return list(result.values())


def _function_shape(signature: str) -> dict:
    match = re.match(r"\s*[A-Za-z_][A-Za-z0-9_$]*\s*\(", signature, re.S)
    if not match:
        return {"kind": "unknown"}
    start = match.end()
    depth, end = 1, None
    for index, char in enumerate(signature[start:], start=start):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                end = index
                break
    if end is None:
        return {"kind": "unknown"}
    args = signature[start:end].strip()
    if not args:
        return {"kind": "unspecified", "arity_min": None, "arity_max": None}
    parts = [part.strip() for part in args.split(",") if part.strip()]
    variadic = bool(parts and re.search(r"\.\.\.|\bvariadic\b", parts[-1], re.I))
    optional = sum(bool(re.search(r"\[.*\]|\boptional\b", part, re.I)) for part in parts)
    required = len(parts) - optional
    return {"kind": "range" if variadic or optional else "fixed",
            "arity_min": required, "arity_max": None if variadic else len(parts)}


def _operator_shape(signature: str, name: str) -> dict:
    syntax = re.split(r"\s*(?:→|⇒)\s*", signature, maxsplit=1)[0]
    position = re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", syntax, re.I)
    if not position:
        return {"kind": "unknown"}
    left, right = syntax[:position.start()].strip(), syntax[position.end():].strip()
    if not left and (not right or re.fullmatch(r"[,\s!%&*+./:<=>?@^|~-]+", right)):
        return {"kind": "unknown"}
    if left and right:
        markers = re.findall(r"\[[^]]+\]", syntax)
        mixfix_segments = re.findall(r"\b[A-Z][A-Z_]*(?:\s+[A-Z][A-Z_]*)*\b\s+[^\s,;]+", right)
        return {"kind": "fixed", "operands": max(2, len(markers), 2 + len(mixfix_segments))}
    return {"kind": "prefix" if right else "postfix", "operands": 1}


def canonicalize(dbms: str, mentions: list[dict], owners: list[dict], relations: list[dict]) -> list[dict]:
    owners_by_id = {owner["owner_group_id"]: owner for owner in owners}
    relation_ids = defaultdict(list)
    for relation in relations:
        relation_ids[relation["source_mention_id"]].append(relation["relation_id"])
        relation_ids[relation["target_mention_id"]].append(relation["relation_id"])
    operator_keys_by_name = defaultdict(set)
    for mention in mentions:
        if mention["feature_type"] == "operator" and mention.get("official_key"):
            operator_keys_by_name[mention["normalized_name"]].add(mention["official_key"])
    records = []
    component_groups = []
    for group in _components(mentions, relations):
        if group and group[0]["feature_type"] == "operator":
            by_shape = defaultdict(list)
            formal_shapes = {tuple(sorted((row.get("formal_shape") or _operator_shape(
                row.get("formal_signature") or "", row["name"])).items()))
                for row in group if row.get("formal_signature")}
            for row in group:
                if not row.get("formal_signature") and len(formal_shapes) == 1:
                    shape_key = next(iter(formal_shapes))
                else:
                    shape = row.get("formal_shape") or _operator_shape(
                        row.get("formal_signature") or "", row["name"])
                    shape_key = tuple(sorted(shape.items()))
                by_shape[shape_key].append(row)
            component_groups.extend(by_shape.values())
        else:
            component_groups.append(group)
    for group in component_groups:
        formal = [row for row in group if row.get("formal_signature")]
        if not formal:
            continue
        preferred = formal[0]
        kind, name = preferred["feature_type"], preferred["name"]
        signatures = sorted({row["formal_signature"] for row in formal})
        notations = sorted({row["reference_notation"] for row in group if row.get("reference_notation")})
        claims = []
        for mention in group:
            claims.extend(mention.get("field_claims", []))
        syntax_variants = sorted(set(signatures + notations))
        if kind == "function":
            shapes = [row.get("formal_shape") or _function_shape(row["formal_signature"])
                      for row in formal]
            known = [value for value in shapes if value["kind"] != "unknown"
                      and value.get("arity_min") is not None]
            unspecified = [row["formal_shape"] for row in formal
                           if row.get("formal_shape", {}).get("kind") == "arity_unspecified"]
            if known:
                discriminator = {
                    "kind": "arity_range",
                    "arity_min": min(value["arity_min"] for value in known),
                    "arity_max": (None if any(value["arity_max"] is None for value in known)
                                  else max(value["arity_max"] for value in known)),
                }
            elif unspecified:
                discriminator = {
                    "kind": "arity_unspecified", "arity_min": None, "arity_max": None,
                }
            else:
                continue
        elif kind == "operator":
            shapes = [(row.get("formal_shape") or _operator_shape(row["formal_signature"], name), row["formal_signature"])
                      for row in formal]
            valid_shapes = [shape for shape, _ in shapes if shape["kind"] != "unknown"]
            if not valid_shapes:
                continue
            discriminator = next((row["formal_shape"] for row in formal if row.get("formal_shape")), valid_shapes[0])
            official_keys = sorted({row["official_key"] for row in group if row.get("official_key")})
            if len(operator_keys_by_name[name.casefold()]) > 1 and official_keys:
                discriminator = {**discriminator, "official_key": official_keys[0]}
        else:
            discriminator = {"kind": "base_datatype", "parameterized": any("(" in value or "[" in value for value in syntax_variants)}
        identity = {"dbms": dbms.casefold(), "feature_type": kind, "name": name.casefold(), "discriminator": discriminator}
        mention_ids = sorted(row["mention_id"] for row in group)
        owner_ids = sorted({row["owner_group_id"] for row in group})
        records.append({
            "schema_version": "canonical-feature/v2", "canonical_id": stable_id("feature", identity),
            "identity": identity, "display_name": name, "syntax_variants": syntax_variants,
            "reference_notations": notations,
            "mention_ids": mention_ids, "owner_group_ids": owner_ids,
            "relation_ids": sorted({item for key in mention_ids for item in relation_ids[key]}),
            "claims": claims,
            "evidence": [value for row in group for value in row["evidence"]]
                        + [claim["evidence"] for claim in claims],
            "trace": {
                "feature_to_owner": [{"canonical_id": stable_id("feature", identity), "owner_group_id": value}
                                     for value in owner_ids if value in owners_by_id],
                "owner_to_mention": [{"owner_group_id": row["owner_group_id"], "mention_id": row["mention_id"]}
                                     for row in group],
                "mention_to_page": [{"mention_id": row["mention_id"], "page_id": row["evidence"][0]["page_id"]}
                                    for row in group],
                "mention_to_href_relation": [
                    {"mention_id": mention_id, "relation_id": relation["relation_id"],
                     "link_id": relation["link_id"], "linking_method": relation["linking_method"]}
                    for mention_id in mention_ids for relation in relations
                    if mention_id in {relation["source_mention_id"], relation["target_mention_id"]}
                ],
                "evidence_refs": [
                    {"page_id": value["page_id"], "block_id": value["block_id"],
                     "evidence_role": value["evidence_role"], "evidence_span": value["evidence_span"]}
                    for value in ([item for row in group for item in row["evidence"]]
                                  + [claim["evidence"] for claim in claims])
                ],
            },
        })
    return sorted(records, key=lambda row: row["canonical_id"])
