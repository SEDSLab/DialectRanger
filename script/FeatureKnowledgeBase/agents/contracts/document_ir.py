"""DocumentIR/v1 and adapters from legacy cards and v2 topology.

The structural-containment fields added here are optional and additive.  They
make DOM ownership evidence available to future Owner inference without
invalidating existing DocumentIR/v1 artifacts.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

from agents.contracts.base import (
    build_provenance,
    ensure_list,
    require_fields,
    stable_id,
    text_hash,
    validate_provenance,
)


SCHEMA_VERSION = "DocumentIR/v1"
BLOCK_KINDS = {
    "HEADING", "TEXT", "CODE", "TABLE", "TABLE_ROW",
    "LIST", "LIST_ITEM", "DEFINITION", "LINK", "OTHER",
}
_V2_BLOCK_KIND = {
    "heading": "HEADING",
    "paragraph": "TEXT",
    "code": "CODE",
    "table": "TABLE",
    "table_row": "TABLE_ROW",
    "list": "LIST",
    "list_item": "LIST_ITEM",
    "definition_term": "DEFINITION",
    "definition_description": "DEFINITION",
    "anchor": "LINK",
}
_LEGACY_BLOCK_KIND = {
    "heading": "HEADING",
    "paragraph": "TEXT",
    "signature": "CODE",
    "code": "CODE",
    "table": "TABLE",
    "table_row": "TABLE_ROW",
    "list": "LIST",
    "note": "OTHER",
    "warning": "OTHER",
    "definition_term": "DEFINITION",
    "definition_description": "DEFINITION",
    "anchor": "LINK",
}


def canonical_url(url: str) -> str:
    parsed = urlsplit(str(url))
    return urlunsplit((
        parsed.scheme.casefold(), parsed.netloc.casefold(),
        parsed.path or "/", parsed.query, "",
    ))


def resolve_html_path(page_records_path: str | Path, html_path: str | Path) -> Path:
    """Resolve one frozen path under the shared path contract."""
    recorded = Path(str(html_path))
    if recorded.is_absolute():
        return recorded.resolve()
    return (Path(page_records_path).resolve().parent / recorded).resolve()


def _page_hash(path: Path, fallback: str = "") -> str:
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    return fallback


def _block_kind(raw: str) -> str:
    value = str(raw or "").casefold()
    return _V2_BLOCK_KIND.get(value, _LEGACY_BLOCK_KIND.get(value, "OTHER"))


def _block(
    *,
    block_id: str,
    block_kind: str,
    text: str,
    order: int,
    dom_path: str = "",
    section_id: str | None = None,
    source_span: dict[str, Any] | None = None,
    heading_ancestry: list[str] | None = None,
    parent_block_id: str | None = None,
    previous_block_id: str | None = None,
    next_block_id: str | None = None,
    table_metadata: dict[str, Any] | None = None,
    list_metadata: dict[str, Any] | None = None,
    code_metadata: dict[str, Any] | None = None,
    structural_parent_id: str | None = None,
    structural_parent_kind: str | None = None,
    ancestor_chain: list[dict[str, Any] | str] | None = None,
    sibling_index: int | None = None,
    structural_boundary_id: str | None = None,
    structural_boundary_kind: str | None = None,
    semantic_role: str | None = None,
    semantic_source: str | None = None,
    semantic_attributes: dict[str, Any] | None = None,
    inline_nodes: list[dict[str, Any]] | None = None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if block_kind not in BLOCK_KINDS:
        raise ValueError(f"unsupported DocumentIR block_kind: {block_kind}")
    return {
        "block_id": str(block_id),
        "block_kind": block_kind,
        "dom_path": dom_path or "",
        "source_span": source_span,
        "text": str(text or ""),
        "raw_text_hash": text_hash(text),
        "heading_level": (
            int(code_metadata["heading_level"])
            if code_metadata and code_metadata.get("heading_level") is not None
            else None
        ),
        "heading_ancestry": list(heading_ancestry or []),
        "parent_block_id": parent_block_id,
        "previous_block_id": previous_block_id,
        "next_block_id": next_block_id,
        "table_metadata": table_metadata,
        "list_metadata": list_metadata,
        "code_metadata": code_metadata,
        "section_id": section_id,
        "order": int(order),
        "structural_parent_id": structural_parent_id,
        "structural_parent_kind": structural_parent_kind,
        "ancestor_chain": list(ancestor_chain or []),
        "sibling_index": sibling_index,
        "structural_boundary_id": structural_boundary_id,
        "structural_boundary_kind": structural_boundary_kind,
        "semantic_role": semantic_role,
        "semantic_source": semantic_source,
        "semantic_attributes": dict(semantic_attributes or {}),
        "inline_nodes": list(inline_nodes or []),
        "provenance": provenance or {},
    }


def make_document_ir(
    *,
    dbms: str,
    page_id: str,
    url: str,
    html_path: str,
    page_hash: str,
    blocks: list[dict[str, Any]],
    document_id: str | None = None,
    title: str = "",
    doc_version: str = "current",
    producer_stage: str = "document_ir",
    producer_version: str = "document-ir-adapter/v1",
    parent_ids: Iterable[str] = (),
    reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    source_url = canonical_url(url)
    provenance = build_provenance(
        dbms=dbms, source_url=source_url, page_id=page_id, page_hash=page_hash,
        html_path=html_path, producer_stage=producer_stage,
        producer_version=producer_version, parent_ids=parent_ids,
        reason_codes=reason_codes,
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "document_id": document_id or stable_id("document", [source_url, page_hash]),
        "page_id": page_id,
        "dbms": dbms,
        "doc_version": doc_version,
        "url": url,
        "canonical_url": source_url,
        "html_path": html_path,
        "page_hash": page_hash,
        "title": title,
        "blocks": blocks,
        "provenance": provenance,
    }
    validate_document_ir(result)
    return result


def validate_document_ir(value: Mapping[str, Any]) -> None:
    require_fields(
        value,
        ("schema_version", "document_id", "page_id", "dbms", "url",
         "canonical_url", "html_path", "page_hash", "blocks", "provenance"),
        "DocumentIR",
    )
    if value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("DocumentIR schema_version must be DocumentIR/v1")
    validate_provenance(value["provenance"], label="DocumentIR.provenance")
    if value["provenance"]["page_id"] != value["page_id"]:
        raise ValueError("DocumentIR provenance page_id mismatch")
    blocks = ensure_list(value["blocks"], "DocumentIR.blocks")
    ids = [block.get("block_id") for block in blocks]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("DocumentIR block IDs must be present and unique")
    known = set(ids)
    for block in blocks:
        require_fields(
            block,
            ("block_id", "block_kind", "dom_path", "source_span", "text",
             "raw_text_hash", "heading_ancestry", "parent_block_id",
             "previous_block_id", "next_block_id", "provenance"),
            "DocumentIR.block",
        )
        if block["block_kind"] not in BLOCK_KINDS:
            raise ValueError(f"unknown DocumentIR block kind: {block['block_kind']}")
        validate_provenance(block["provenance"], label="DocumentIR.block.provenance")
        if block["provenance"]["page_id"] != value["page_id"]:
            raise ValueError("DocumentIR block provenance page_id mismatch")
        for field in ("parent_block_id", "previous_block_id", "next_block_id"):
            if block[field] is not None and block[field] not in known:
                raise ValueError(f"DocumentIR block {field} is not a known block")
        if not isinstance(block.get("ancestor_chain", []), list):
            raise ValueError("DocumentIR block ancestor_chain must be a list")
        sibling_index = block.get("sibling_index")
        if sibling_index is not None and int(sibling_index) < 0:
            raise ValueError("DocumentIR block sibling_index must be non-negative")


def from_v2_page(
    page: Mapping[str, Any],
    *,
    dbms: str,
    html_path: str | Path,
    doc_version: str = "current",
) -> dict[str, Any]:
    """Adapt one v2 topology page without changing its structural semantics."""
    page_id = str(page["page_id"])
    raw_blocks = list(page.get("blocks", []))
    blocks: list[dict[str, Any]] = []
    headings: list[str] = []
    heading_stack: list[tuple[int, str]] = []
    boundary_counts: dict[tuple[str, str], int] = {}
    for index, raw in enumerate(raw_blocks):
        kind = _block_kind(raw.get("block_type"))
        level = raw.get("heading_level")
        if kind == "HEADING":
            level = int(level or 1)
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, str(raw.get("text", ""))))
            headings = [value for _, value in heading_stack]
        block_id = str(raw.get("block_id") or stable_id(
            "block", [page_id, index, raw.get("dom_path", "")],
        ))
        boundary_id = str(raw.get("boundary_id") or "") or None
        boundary_kind = str(raw.get("boundary_kind") or "") or None
        structural_parent_id = str(raw.get("structural_parent_id") or boundary_id or "") or None
        structural_parent_kind = str(raw.get("structural_parent_kind") or boundary_kind or "") or None
        boundary_key = (structural_parent_kind or "", structural_parent_id or "")
        sibling_index = boundary_counts.get(boundary_key, 0) if boundary_id else None
        if boundary_id:
            boundary_counts[boundary_key] = int(sibling_index) + 1
        raw_ancestors = raw.get("ancestor_chain")
        ancestor_chain = list(raw_ancestors) if isinstance(raw_ancestors, list) else []
        if not ancestor_chain and structural_parent_id:
            ancestor_chain = [{"id": structural_parent_id, "kind": structural_parent_kind or "UNKNOWN"}]
        previous = blocks[-1]["block_id"] if blocks else None
        block = _block(
            block_id=block_id,
            block_kind=kind,
            text=raw.get("text", ""),
            order=raw.get("order", index),
            dom_path=raw.get("dom_path", ""),
            section_id=raw.get("section_id"),
            heading_ancestry=headings[:-1] if kind == "HEADING" else headings,
            previous_block_id=previous,
            structural_parent_id=structural_parent_id,
            structural_parent_kind=structural_parent_kind,
            ancestor_chain=ancestor_chain,
            sibling_index=sibling_index,
            structural_boundary_id=(str(raw.get("structural_boundary_id") or structural_parent_id or "") or None),
            structural_boundary_kind=(str(raw.get("structural_boundary_kind") or structural_parent_kind or "") or None),
            table_metadata={
                "cells": raw.get("cells", []),
                "literal_cells": raw.get("literal_cells", []),
                "cell_items": raw.get("cell_items", []),
                "headers": raw.get("table_headers", []),
            } if kind in {"TABLE", "TABLE_ROW"} else None,
            code_metadata={
                "element_name": raw.get("element_name"),
                "inline_literals": raw.get("inline_literals", []),
                "semantic_classes": raw.get("semantic_classes", []),
                "heading_level": level,
            } if kind in {"CODE", "HEADING"} else None,
            semantic_role=str(raw.get("semantic_role") or "") or None,
            semantic_source=str(raw.get("semantic_source") or "") or None,
            semantic_attributes=raw.get("semantic_attributes") or {},
            inline_nodes=raw.get("inline_nodes") or [],
            provenance=build_provenance(
                dbms=dbms, source_url=str(page["url"]), page_id=page_id,
                page_hash=str(page.get("page_hash", "")), html_path=str(html_path),
                producer_stage="document_ir",
                producer_version="v2-topology-adapter/v1",
                parent_ids=[str(raw.get("boundary_id", ""))],
            ),
        )
        block['definition_context'] = dict(raw.get('definition_context') or {})
        block['source_node_kind'] = str(raw.get('element_name') or '').upper()
        for key in ('source_text', 'normalized_text', 'source_text_origin', 'source_parent_locator',
                    'source_dom_order', 'source_text_node_indexes', 'preservation_only', 'content_interpretation'):
            if key in raw:
                block[key] = raw[key]
        if 'raw_text' in raw and 'source_text' not in block:
            block['source_text'] = raw['raw_text']
            block['normalized_text'] = block['text']
            block['source_text_origin'] = 'FROZEN_TEXT_SURFACE'
        if raw.get('source_cell_grid') is not None and block.get('table_metadata') is not None:
            block['table_metadata']['source_cell_grid'] = raw['source_cell_grid']
        blocks.append(block)
    # Retain actual table containers as whole-span witnesses. Rows remain the
    # fine-grained identity/field evidence; a container does not declare a Feature.
    # This permits a same-table invocation/result to retain one literal witness
    # rather than weakening grounding to accept arbitrary concatenated refs.
    from bs4 import BeautifulSoup
    import re
    source_path = Path(html_path)
    table_paths = {}
    existing_paths = {b['dom_path'] for b in blocks}
    for block in blocks:
        if block['block_kind'] == 'TABLE_ROW':
            match = re.match(r'^(.* > table(?:#[^ >]+|:nth-of-type\(\d+\))?)(?: > |$)', block['dom_path'])
            if match and match.group(1) not in existing_paths:
                table_paths.setdefault(match.group(1), block)
    if table_paths and source_path.is_file():
        soup = BeautifulSoup(source_path.read_bytes(), 'lxml')
        inserted = []
        for path, first in table_paths.items():
            try:
                node = soup.select_one(path)
            except Exception:
                continue
            if node is None or node.name != 'table':
                continue
            container = _block(block_id=stable_id('block', [page_id, 'source_table', path]),
                block_kind='TABLE', text=node.get_text(' ', strip=True), order=first['order'] - 0.5,
                dom_path=path, heading_ancestry=first.get('heading_ancestry'),
                provenance=dict(first['provenance']), table_metadata={'container_witness': True})
            container['source_node_kind'] = 'TABLE'
            container['source_container_witness'] = True
            container['source_text'] = node.get_text('', strip=False)
            container['normalized_text'] = container['text']
            container['source_text_origin'] = 'FROZEN_DOM_TEXT_CONTENT'
            from agents.document_structure_v2.topology import _dom_path
            container['source_parent_locator'] = _dom_path(node.parent) if node.parent else None
            inserted.append(container)
        blocks = sorted([*blocks, *inserted], key=lambda b: b['order'])
        for index, block in enumerate(blocks):
            block['order'] = index
            block['previous_block_id'] = blocks[index - 1]['block_id'] if index else None
    for index, block in enumerate(blocks):
        block["next_block_id"] = blocks[index + 1]["block_id"] if index + 1 < len(blocks) else None
        if index and block["parent_block_id"] is None and 'source_parent_locator' not in block:
            block["parent_block_id"] = blocks[index - 1]["block_id"] if block["block_kind"] == "TEXT" else None
    # True nearest represented DOM ancestor, distinct from Owner boundary IDs.
    # Keep legacy adapters without source locators compatible.
    by_path = {b['dom_path']: b for b in blocks if not b.get('preservation_only')}
    for block in blocks:
        if 'source_parent_locator' not in block:
            continue
        path = block['dom_path']
        parent = None
        while ' > ' in path:
            path = path.rsplit(' > ', 1)[0]
            if path in by_path:
                parent = by_path[path]['block_id']
                break
        block['parent_block_id'] = parent
        block['dom_parent_block_id'] = parent
    result = make_document_ir(
        dbms=dbms, page_id=page_id, url=str(page["url"]),
        html_path=str(Path(html_path).resolve()), page_hash=str(page.get("page_hash", "")),
        blocks=blocks, title=str(page.get("title", "")), doc_version=doc_version,
        parent_ids=[str(page.get("source", ""))] if page.get("source") else (),
    )
    if page.get('source_preservation'):
        result['source_preservation'] = page['source_preservation']
    return result


def from_legacy_section_cards(
    cards: Iterable[Mapping[str, Any]],
    *,
    page_record: Mapping[str, Any],
    page_records_path: str | Path,
    dbms: str,
    doc_version: str = "current",
) -> dict[str, Any]:
    """Adapt legacy section cards while preserving their block IDs for evidence refs."""
    cards = sorted(list(cards), key=lambda row: (row.get("section_index", 0), row.get("section_id", "")))
    url = str(page_record.get("url") or cards[0].get("url") or "")
    raw_page_id = str(page_record.get("page_id") or cards[0].get("page_id") or "")
    page_id = stable_id("page", canonical_url(url)) if url else raw_page_id
    recorded_path = page_record.get("html_path", "")
    html_path = resolve_html_path(page_records_path, recorded_path) if recorded_path else Path("")
    page_hash = str(page_record.get("page_hash") or _page_hash(html_path))
    blocks: list[dict[str, Any]] = []
    headings: list[str] = []
    section_counts: dict[str, int] = {}
    for card in cards:
        heading_path = [str(value) for value in card.get("heading_path", [])]
        if heading_path:
            headings = heading_path
        elif card.get("heading"):
            headings = [str(card["heading"])]
        for raw in card.get("blocks", []):
            raw_type = str(raw.get("block_type", "other"))
            kind = _block_kind(raw_type)
            block_id = str(raw.get("block_id") or stable_id(
                "block", [page_id, card.get("section_id"), raw.get("order", len(blocks)), raw.get("text", "")],
            ))
            previous = blocks[-1]["block_id"] if blocks else None
            section_id = str(card.get("section_id") or "") or None
            structural_parent_id = f"section:{section_id}" if section_id else None
            structural_parent_kind = "LEGACY_SECTION_CARD" if section_id else None
            sibling_index = section_counts.get(section_id or "", 0) if section_id else None
            if section_id:
                section_counts[section_id] = int(sibling_index) + 1
            blocks.append(_block(
                block_id=block_id, block_kind=kind, text=raw.get("text", ""),
                order=len(blocks), dom_path=str(raw.get("dom_path") or raw.get("source_anchor") or ""),
                section_id=str(card.get("section_id") or ""),
                heading_ancestry=headings,
                previous_block_id=previous,
                structural_parent_id=structural_parent_id,
                structural_parent_kind=structural_parent_kind,
                ancestor_chain=([{"id": structural_parent_id, "kind": structural_parent_kind}] if structural_parent_id else []),
                sibling_index=sibling_index,
                structural_boundary_id=structural_parent_id,
                structural_boundary_kind=structural_parent_kind,
                table_metadata={
                    "cells": raw.get("cells", []),
                    "headers": raw.get("table_headers", []),
                } if kind in {"TABLE", "TABLE_ROW"} else None,
                code_metadata={
                    "language": raw.get("language", ""),
                    "html_tag": raw.get("html_tag", ""),
                    "role": raw.get("role", ""),
                } if kind in {"CODE", "HEADING"} else None,
                provenance=build_provenance(
                    dbms=dbms, source_url=url, page_id=page_id,
                    page_hash=page_hash, html_path=str(html_path),
                    producer_stage="document_ir",
                    producer_version="legacy-section-card-adapter/v1",
                    parent_ids=[raw_page_id, str(card.get("section_id") or "")],
                ),
            ))
    for index, block in enumerate(blocks):
        block["next_block_id"] = blocks[index + 1]["block_id"] if index + 1 < len(blocks) else None
    return make_document_ir(
        dbms=dbms, page_id=page_id, url=url, html_path=str(html_path),
        page_hash=page_hash, blocks=blocks,
        title=str(page_record.get("title", "")), doc_version=doc_version,
        parent_ids=[
            raw_page_id,
            *[str(card.get("section_id")) for card in cards if card.get("section_id")],
        ],
    )


def document_ir_schema() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "required": [
            "document_id", "page_id", "dbms", "url", "canonical_url",
            "html_path", "page_hash", "blocks", "provenance",
        ],
        "block_kinds": sorted(BLOCK_KINDS),
        "block_required": [
            "block_id", "block_kind", "dom_path", "source_span", "text",
            "raw_text_hash", "heading_ancestry", "parent_block_id",
            "previous_block_id", "next_block_id", "provenance",
        ],
        "block_optional_structural_containment": [
            "structural_parent_id", "structural_parent_kind", "ancestor_chain",
            "sibling_index", "structural_boundary_id", "structural_boundary_kind",
        ],
    }
