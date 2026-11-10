"""Profile v2 contract: documentation templates and generic extraction recipes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from agents.document_structure_v2.models import ARCHETYPES, FEATURE_TYPES, stable_id


RECIPE_IDS = {
    "reference_table_recipe", "definition_list_recipe", "heading_owned_section_recipe",
    "aggregate_syntax_recipe", "single_feature_detail_recipe",
    "mixed_function_operator_recipe", "mapping_table_recipe", "precedence_table_recipe",
    "structural_page_recipe",
}


def default_profile_v2(dbms: str) -> dict:
    templates = [
        {"template_id": "generic.reference-table", "archetype": "feature_reference_index", "recipe": "reference_table_recipe"},
        {"template_id": "generic.aggregate-syntax", "archetype": "aggregate_syntax", "recipe": "aggregate_syntax_recipe"},
        {"template_id": "generic.multi-detail", "archetype": "multi_feature_detail", "recipe": "definition_list_recipe"},
        {"template_id": "generic.single-detail", "archetype": "single_feature_detail", "recipe": "single_feature_detail_recipe"},
        {"template_id": "generic.mapping", "archetype": "mapping_table", "recipe": "mapping_table_recipe"},
        {"template_id": "generic.precedence", "archetype": "precedence_table", "recipe": "precedence_table_recipe"},
        {"template_id": "generic.structural-page", "archetype": "navigation_overview", "recipe": "structural_page_recipe"},
    ]
    profile = {
        "schema_version": "featurecrawler-profile/v2", "dbms": dbms,
        "main_selectors": ["#docs-body", "main", "article", "body"],
        "noise_selectors": ["nav", "header", "footer", "aside", "script", "style"],
        "page_templates": templates,
        "recipes": [{"recipe_id": value, "enabled": True} for value in sorted(RECIPE_IDS)],
        "induction": {
            "llm_role": "evidence_bounded_structure_rule_inducer",
            "allowed_outputs": ["page_archetype", "owner_boundary", "field_role_binding", "recipe_selection"],
            "forbidden_outputs": ["feature_inventory", "canonical_identity", "unsupported_feature_knowledge"],
        },
    }
    profile["profile_id"] = stable_id("profile", profile)
    return profile


def validate_profile_v2(profile: dict) -> None:
    if profile.get("schema_version") != "featurecrawler-profile/v2":
        raise ValueError("Profile v2 schema_version is required")
    if not profile.get("dbms"):
        raise ValueError("Profile v2 dbms is required")
    for template in profile.get("page_templates", []):
        if template.get("archetype") not in ARCHETYPES:
            raise ValueError(f"Unknown page archetype: {template.get('archetype')}")
        if template.get("recipe") not in RECIPE_IDS:
            raise ValueError(f"Unknown recipe: {template.get('recipe')}")
    forbidden = {"known_features", "feature_inventory", "canonicalization", "source_urls"}
    if forbidden & set(profile):
        raise ValueError("Profile v2 contains a forbidden knowledge field")


def write_profile_v2(path: str | Path, profile: dict) -> None:
    validate_profile_v2(profile)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")


def load_profile_v2(path: str | Path) -> dict:
    profile = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    validate_profile_v2(profile)
    return profile


def build_induction_payload(pages: list[dict], max_characters: int = 120_000) -> dict:
    summaries = []
    for page in pages:
        summaries.append({
            "page_id": page["page_id"], "url": page["url"], "title": page["title"],
            "crawl_depth": page["crawl_depth"], "toc_depth": page["toc_depth"],
            "feature_context": page["feature_context"], "blocks": page["blocks"],
            "links": page["links"], "adjacent_blocks": [
                [left["block_id"], right["block_id"]]
                for left, right in zip(page["blocks"], page["blocks"][1:])
            ],
        })
    payload = {
        "schema_version": "profile-v2-induction-input/v1",
        "task": "induce_page_archetype_owner_boundaries_and_recipe_bindings",
        "evidence_policy": "all output IDs and spans must resolve to this input",
        "pages": summaries,
    }
    if len(json.dumps(payload, ensure_ascii=False)) > max_characters:
        raise ValueError("Profile v2 induction input exceeds the bounded size")
    return payload


def validate_induction_output(proposal: dict, payload: dict) -> None:
    pages = {page["page_id"]: page for page in payload["pages"]}
    block_ids = {block["block_id"] for page in pages.values() for block in page["blocks"]}
    link_ids = {link["link_id"] for page in pages.values() for link in page["links"]}
    for result in proposal.get("pages", []):
        if result.get("page_id") not in pages or result.get("page_archetype") not in ARCHETYPES:
            raise ValueError("LLM proposal references an unknown page or archetype")
        for owner in result.get("owner_groups", []):
            refs = set(owner.get("identity_blocks", []) + owner.get("syntax_blocks", [])
                       + owner.get("description_blocks", []) + owner.get("example_blocks", []))
            if not refs <= block_ids:
                raise ValueError("LLM proposal references an unknown block")
            if not set(owner.get("link_ids", [])) <= link_ids:
                raise ValueError("LLM proposal references an unknown href")
            span = owner.get("name_span")
            if span:
                block = next(block for page in pages.values() for block in page["blocks"] if block["block_id"] == span.get("block_id"))
                if not (0 <= span.get("start", -1) < span.get("end", -1) <= len(block["text"])):
                    raise ValueError("LLM proposal contains an invalid Evidence span")
