"""Validate paragraph claims individually with the unchanged grounding rules."""
from agents.semantic_stage.grounding import validate_semantic_output


def validate_extractive_fragments(parsed, bundle):
    paragraphs = parsed.get('description')
    if not isinstance(paragraphs, list):
        return validate_semantic_output(parsed, bundle)
    base = validate_semantic_output({**parsed, 'description': None}, bundle)
    accepted = []
    seen = set()
    for paragraph in paragraphs:
        result = validate_semantic_output({'description': paragraph}, bundle)
        item = result['fields']['description']
        # One retained container can witness several distinct paragraphs.
        # References alone identify the source, not the individual claim.
        marker = (tuple(item['evidence_refs']),item['text']) if item else None
        if marker is not None and marker in seen:
            continue
        if item:
            accepted.append(item)
            seen.add(marker)
        for key in ('accepted_claims', 'rejected_claims', 'invalid_evidence_refs', 'ungrounded_claims'):
            base[key] += result[key]
        base['diagnostics'].extend(result['diagnostics'])
        base['claim_results'].extend(result['claim_results'])
    base['fields']['description'] = accepted or None
    return base
