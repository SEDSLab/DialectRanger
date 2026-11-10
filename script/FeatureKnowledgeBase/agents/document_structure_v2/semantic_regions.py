"""Generic source-semantic regions shared by DOM topology and audits.

The source sites used by the crawler do not all express document semantics
with native HTML tags.  This module recognizes only explicit semantic
attributes/roles; it intentionally does not infer paragraphs from arbitrary
``span`` or ``div`` elements.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

from bs4 import Tag


_HEADING_DATA_AS = {f"h{level}" for level in range(1, 7)}
_PARAGRAPH_DATA_AS = {"p", "paragraph", "text", "prose"}
_CODE_DATA_AS = {"pre", "code", "code-block", "syntax", "example"}
_TABLE_DATA_AS = {"table", "table-row", "row"}
_LIST_DATA_AS = {"list", "list-item", "item"}


def semantic_region_role(node: Tag) -> tuple[str | None, str | None, int | None]:
    """Return ``(role, source, heading_level)`` for an explicit node signal."""
    data_as = str(node.get("data-as") or "").strip().casefold()
    role = str(node.get("role") or "").strip().casefold()
    aria_level = str(node.get("aria-level") or "").strip()
    if data_as in _PARAGRAPH_DATA_AS or role in {"paragraph", "doc-paragraph"}:
        return "PARAGRAPH", "data-as" if data_as else "role", None
    if data_as in _HEADING_DATA_AS:
        return "HEADING", "data-as", int(data_as[1])
    if role in {"heading", "doc-heading"} or aria_level:
        try:
            level = int(aria_level) if aria_level else None
        except ValueError:
            level = None
        return "HEADING", "role" if role else "aria-level", level
    if data_as in _CODE_DATA_AS or role in {"code", "code-block"}:
        return "CODE", "data-as" if data_as else "role", None
    if data_as in _TABLE_DATA_AS or role in {"table", "row"}:
        return "TABLE_ROW" if data_as in {"table-row", "row"} or role == "row" else "TABLE", "data-as" if data_as else "role", None
    if data_as in _LIST_DATA_AS or role in {"list", "listitem"}:
        return "LIST_ITEM" if data_as in {"list-item", "item"} or role == "listitem" else "LIST", "data-as" if data_as else "role", None
    return None, None, None


def source_semantic_region(
    node: Tag,
    *,
    dom_path: Callable[[Tag], str],
) -> dict[str, Any] | None:
    """Create a serializable SourceSemanticRegion for one explicit node."""
    semantic_role, semantic_source, heading_level = semantic_region_role(node)
    if not semantic_role:
        return None
    text = " ".join(node.get_text(" ", strip=True).split())
    if not text:
        return None
    attributes = {
        str(key): str(value)
        for key, value in node.attrs.items()
        if key in {"data-as", "role", "aria-level", "data-component", "data-testid", "itemprop"}
    }
    return {
        "semantic_role": semantic_role,
        "semantic_source": semantic_source,
        "heading_level": heading_level,
        "element_name": node.name,
        "attributes": attributes,
        "dom_path": dom_path(node),
        "text": text,
    }


def iter_source_semantic_regions(
    root: Tag,
    *,
    dom_path: Callable[[Tag], str],
) -> Iterable[dict[str, Any]]:
    """Yield explicit semantic regions, suppressing nested duplicate regions."""
    for node in root.find_all(True):
        region = source_semantic_region(node, dom_path=dom_path)
        if region is None:
            continue
        # A semantic child inside another semantic block is already represented
        # by the outer source region.  Inline ``code``/``strong`` nodes without
        # an explicit semantic role are never yielded here.
        if any(source_semantic_region(parent, dom_path=dom_path) for parent in node.parents if isinstance(parent, Tag)):
            continue
        yield region


__all__ = ["iter_source_semantic_regions", "semantic_region_role", "source_semantic_region"]
