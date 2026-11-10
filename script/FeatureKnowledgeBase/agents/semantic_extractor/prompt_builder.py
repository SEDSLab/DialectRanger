from __future__ import annotations

import json


SYSTEM_PROMPT = (
    "You are a strict, evidence-grounded database documentation extraction assistant. "
    "Your ONLY job is to extract semantic information from the provided context blocks. "
    "You MUST NOT use any external knowledge, general database expertise, or prior training. "
    "If the context does not support a claim, do NOT make it. "
    "If uncertain, output empty arrays. "
    "You MUST output ONLY valid JSON. No markdown, no explanations, no commentary."
)

USER_PROMPT_TEMPLATE = """Extract semantic information for the following MySQL function.

## Target Feature
- feature_id: {feature_id}
- name: {name}
- normalized_name: {normalized_name}
- signature: {signature}

### Existing Syntax
{syntax_entries}

### Existing Examples
{example_entries}

## Focused Context Blocks
{focused_context}

## Allowed Evidence Refs
{allowed_refs}

## Output JSON Schema

Output ONLY this exact JSON structure. No markdown fences, no explanations.

{{
  "feature_id": "{feature_id}",
  "description_claims": [
    {{
      "text": "Concise, factual description based ONLY on the context. Short sentence.",
      "evidence_refs": ["block_id_from_allowed_list"],
      "confidence": 0.9
    }}
  ],
  "constraints": [
    {{
      "text": "A specific constraint or behavioral note, as a single sentence.",
      "type": "null_behavior | empty_string_behavior | error_behavior | range_limit | synonym | version_note | other",
      "evidence_refs": ["block_id_from_allowed_list"],
      "confidence": 0.8
    }}
  ],
  "example_roles": [
    {{
      "example_index": 0,
      "role": "basic_usage | multi_case_example | null_behavior | error_case | edge_case",
      "evidence_refs": ["block_id_from_allowed_list"]
    }}
  ],
  "semantic_notes": [],
  "warnings": []
}}

## CRITICAL Rules

1. Output ONLY valid JSON. No markdown fences, no explanations.
2. evidence_refs MUST come from the Allowed Evidence Refs list. No other block_ids allowed.
3. Every claim/constraint MUST have at least one evidence_ref. Empty evidence_refs arrays are forbidden.
4. Do NOT invent behavior not present in the context blocks.
5. Do NOT use your general knowledge or prior training.
6. Do NOT add, modify, or remove syntax entries.
7. Do NOT add, modify, or remove examples.
8. Do NOT modify the feature_id.
9. description_claims and constraints text should be concise (1 sentence), grounded in the context.
10. Do NOT generate overly long text. Each claim should fit in 1-2 short sentences.
11. If unsure about any field, output an empty array [].
12. confidence must be between 0.0 and 1.0.

## Constraint Type Definitions

- null_behavior: Text discusses NULL handling (returns NULL, is NULL, NULL if, NULL otherwise)
- empty_string_behavior: Text discusses empty string or zero-length behavior
- error_behavior: Text discusses errors (produces an error, raises an error, cannot be stored)
- range_limit: Text discusses value limits (maximum, minimum, range, greater than, less than)
- synonym: Text states this is a synonym for another function
- version_note: Text mentions MySQL version info (deprecated, removed, available as of)
- other: Any other constraint type

## Example Role Definitions

- basic_usage: Simple, straightforward usage
- multi_case_example: Example showing multiple usage cases (multiple mysql> prompts or SELECT)
- null_behavior: Example demonstrating NULL behavior
- error_case: Example demonstrating error behavior
- edge_case: Example demonstrating edge case or unusual usage
"""


def build_prompt(feature_draft: dict, focused_context: list[dict], max_blocks: int = 40) -> str:
    feature_id = feature_draft.get("feature_id", "unknown")
    name = feature_draft.get("name", "unknown")
    normalized_name = feature_draft.get("normalized_name", "")
    signature = feature_draft.get("signature", "")

    syntax_entries = _format_syntax(feature_draft.get("syntax", []))
    example_entries = _format_examples(feature_draft.get("examples", []))

    allowed_refs = sorted({blk["block_id"] for blk in focused_context if "block_id" in blk})

    truncated = focused_context[:max_blocks]
    context_str = _format_context_blocks(truncated)

    prompt = USER_PROMPT_TEMPLATE.format(
        feature_id=feature_id,
        name=name,
        normalized_name=normalized_name,
        signature=signature,
        syntax_entries=syntax_entries,
        example_entries=example_entries,
        focused_context=context_str,
        allowed_refs=json.dumps(allowed_refs, indent=2),
    )
    return prompt


def _format_syntax(syntax_items: list[dict]) -> str:
    if not syntax_items:
        return "(none)"
    lines = []
    for i, item in enumerate(syntax_items):
        pattern = item.get("pattern", "")
        lines.append(f"  {i}. {pattern}")
    return "\n".join(lines)


def _format_examples(examples: list[dict]) -> str:
    if not examples:
        return "(none)"
    lines = []
    for i, ex in enumerate(examples):
        sql = ex.get("sql", "")
        sql_short = sql[:120] + ("..." if len(sql) > 120 else "")
        lines.append(f"  {i}. {sql_short}")
    return "\n".join(lines)


def _format_context_blocks(blocks: list[dict]) -> str:
    lines = []
    for i, blk in enumerate(blocks):
        bid = blk.get("block_id", f"block_{i}")
        btype = blk.get("block_type", "unknown")
        role = blk.get("role", "")
        text = blk.get("text", "")
        anchor = blk.get("source_anchor", "")

        text_short = text[:200] + ("..." if len(text) > 200 else "")

        lines.append(
            f"### Block {i}\n"
            f"- block_id: {bid}\n"
            f"- block_type: {btype}\n"
            f"- role: {role}\n"
            f"- source_anchor: {anchor}\n"
            f"- text: {text_short}"
        )
    return "\n\n".join(lines)
