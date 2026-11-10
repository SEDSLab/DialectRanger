"""DOM-to-DocumentIR structural containment for offline shadow runs.

The existing v2 topology already captures stable block IDs and raw boundary
IDs.  This module adds the smallest generic DOM lineage needed by the new Owner
inference path.  It never fetches HTML and it has no DBMS or feature-name
knowledge.
"""
from __future__ import annotations

from collections import Counter
import hashlib
from pathlib import Path
from typing import Any, Mapping

from bs4 import BeautifulSoup, Tag

from agents.contracts.base import stable_id
from agents.contracts.document_ir import from_v2_page
from agents.document_structure_v2.topology import (
    _dom_path,
    _is_block_node,
    _main,
    build_page_topology,
)


SCHEMA_VERSION = "DocumentStructuralContainment/v1"


def _tag_kind(node: Tag | None, *, root: Tag | None = None) -> str:
    if node is None:
        return "PAGE"
    name = str(node.name or "").casefold()
    if name == "tr":
        return "TABLE_ROW"
    if name == "li":
        return "LIST_ITEM"
    if name in {"dt", "dd"}:
        return "DEFINITION_ENTRY"
    if name == "section" or "section" in {str(value).casefold() for value in (node.get("class") or [])}:
        return "SECTION"
    if name in {"article", "main"} or str(node.get("role") or "").casefold() == "main":
        return "ARTICLE_MAIN"
    if name == "body":
        return "PAGE"
    if name == "div" and (node.get("id") or node.get("class")):
        return "GENERIC_BLOCK_CONTAINER"
    if root is not None and node is root:
        return "ARTICLE_MAIN" if name != "body" else "PAGE"
    return "GENERIC_BLOCK_CONTAINER"


def _is_section_div(node: Tag) -> bool:
    classes = {str(value).casefold() for value in (node.get("class") or [])}
    return node.name == "section" or "section" in classes


def _nearest_container(node: Tag, root: Tag) -> tuple[Tag, str]:
    """Find the strongest generic container for one topology block."""
    row = node if node.name == "tr" else node.find_parent("tr")
    if row is not None:
        return row, "TABLE_ROW"

    item = node if node.name == "li" else node.find_parent("li")
    if item is not None:
        return item, "LIST_ITEM"

    definition = node if node.name in {"dt", "dd"} else node.find_parent(["dt", "dd"])
    if definition is not None:
        term = definition if definition.name == "dt" else definition.find_previous_sibling("dt")
        return term or definition, "DEFINITION_ENTRY"

    for parent in node.parents:
        if not isinstance(parent, Tag):
            continue
        if _is_section_div(parent):
            return parent, "SECTION"
        if parent.name in {"article", "main"} or str(parent.get("role") or "").casefold() == "main":
            return parent, "ARTICLE_MAIN"
        if parent is root:
            break

    if root.name == "body":
        return root, "PAGE"
    return root, "ARTICLE_MAIN"


def _container_id(page_id: str, node: Tag, kind: str, raw_boundary_id: str | None) -> str:
    # Keep topology boundary IDs for strong repeated structures so old audit
    # block lineage remains comparable.  Page/section boundaries use the
    # actual DOM container instead of the old per-block page_section fallback.
    if raw_boundary_id and kind in {"TABLE_ROW", "LIST_ITEM", "DEFINITION_ENTRY"}:
        return str(raw_boundary_id)
    return stable_id("structural_container", [page_id, kind, _dom_path(node)])


def _ancestor_chain(page_id: str, node: Tag, root: Tag, parent_id: str, parent_kind: str) -> list[dict[str, str]]:
    container, _ = _nearest_container(node, root)
    result: list[dict[str, str]] = [{
        "id": parent_id,
        "kind": parent_kind,
        "dom_path": _dom_path(container if parent_kind in {"TABLE_ROW", "LIST_ITEM", "DEFINITION_ENTRY"} else root),
    }]
    seen = {parent_id}
    current: Tag | None = node
    while current is not None and isinstance(current, Tag):
        for ancestor in current.parents:
            if not isinstance(ancestor, Tag):
                continue
            if ancestor is root or ancestor.name in {"li", "dt", "dd", "tr", "section", "article", "main", "body"} or (
                ancestor.name == "div" and (ancestor.get("id") or ancestor.get("class"))
            ):
                kind = _tag_kind(ancestor, root=root)
                identifier = _container_id(page_id, ancestor, kind, None)
                if identifier not in seen:
                    result.append({"id": identifier, "kind": kind, "dom_path": _dom_path(ancestor)})
                    seen.add(identifier)
        break
    return result


def augment_topology_structural_containment(
    page: Mapping[str, Any],
    *,
    html_path: str | Path,
    parser_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Add structural parent/ancestor metadata to a v2 topology page."""
    path = Path(html_path).resolve()
    html = path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")
    profile = dict(parser_profile or {})
    root = _main(soup, profile.get("main_selectors", ["main", "article", "body"]))
    nodes_by_path = {
        _dom_path(node): node
        for node in root.find_all(_is_block_node)
    }
    counts: Counter[str] = Counter()
    unresolved: list[str] = []
    container_counts: Counter[str] = Counter()
    blocks: list[dict[str, Any]] = []
    for raw in page.get("blocks", []):
        row = dict(raw)
        node = nodes_by_path.get(str(row.get("dom_path") or ""))
        if node is None:
            unresolved.append(str(row.get("block_id") or ""))
            parent_id = stable_id("structural_container", [page["page_id"], "PAGE"])
            parent_kind = "PAGE"
            chain = [{"id": parent_id, "kind": parent_kind, "dom_path": ""}]
        else:
            container, parent_kind = _nearest_container(node, root)
            raw_boundary_id = str(row.get("boundary_id") or "") or None
            parent_id = _container_id(str(page["page_id"]), container, parent_kind, raw_boundary_id)
            chain = _ancestor_chain(str(page["page_id"]), node, root, parent_id, parent_kind)
        row["structural_parent_id"] = parent_id
        row["structural_parent_kind"] = parent_kind
        row["structural_boundary_id"] = parent_id
        row["structural_boundary_kind"] = parent_kind
        row["ancestor_chain"] = chain
        row["sibling_index"] = counts[parent_id]
        counts[parent_id] += 1
        container_counts[parent_kind] += 1
        blocks.append(row)
    result = dict(page)
    result["blocks"] = blocks
    result["structural_containment"] = {
        "schema_version": SCHEMA_VERSION,
        "resolved_blocks": len(blocks) - len(unresolved),
        "unresolved_blocks": len(unresolved),
        "unresolved_block_ids": sorted(item for item in unresolved if item),
        "container_counts": dict(sorted(container_counts.items())),
        "parent_count": len(counts),
    }
    return result


def build_structural_document(
    page_spec: Mapping[str, Any],
    *,
    dbms: str,
    parser_profile: Mapping[str, Any] | None = None,
    doc_version: str = "current",
) -> dict[str, Any]:
    """Build a DocumentIR/v1 document from one already-frozen local HTML file."""
    topology = build_page_topology(dict(page_spec), dict(parser_profile or {}))
    topology = augment_topology_structural_containment(
        topology, html_path=str(page_spec["html_path"]), parser_profile=parser_profile,
    )
    document = from_v2_page(
        topology, dbms=dbms, html_path=str(Path(page_spec["html_path"]).resolve()),
        doc_version=doc_version,
    )
    document["structural_containment"] = dict(topology.get("structural_containment") or {})
    for key in ("feature_context", "discovery_provenance", "discovery_classification"):
        if key in page_spec:
            document[key] = page_spec[key]
    return document
