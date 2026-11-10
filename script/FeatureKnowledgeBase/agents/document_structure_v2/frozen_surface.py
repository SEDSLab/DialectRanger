"""Format-aware topology adapters for already frozen documentation surfaces.

The production HTML topology remains the authority for HTML.  This module
only supplies the missing structural adapter for Markdown/plain text frozen
representations so that the same downstream Owner/Identity/Type code can be
used without a second database-specific parser.
"""
from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urljoin, urlsplit

from agents.document_structure_v2.models import clean_text, stable_id
from agents.document_structure_v2.topology import normalize_url


_MARKDOWN_LINK_RE = re.compile(
    r"(?<![!\\])\[([^\]]{1,500})\]\(\s*(?:<([^>]+)>|([^\s)]+))\s*(?:\"[^\"]*\"|'[^']*')?\s*\)",
    re.MULTILINE,
)
_REFERENCE_LINK_RE = re.compile(r"^\s{0,3}\[([^\]]+)\]:\s*(?:<([^>]+)>|(\S+))\s*$")
_ABSOLUTE_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})\s*([^\s]*)?.*$")
_LIST_RE = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+(.+?)\s*$")
_TABLE_SEPARATOR_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)
_CARD_RE = re.compile(r"<([A-Za-z][A-Za-z0-9_]*)\b([^>]*)/?>", re.I)
_ATTR_RE = re.compile(r"([A-Za-z_:][A-Za-z0-9_.:-]*)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))")


def _strip_quote_prefix(value: str) -> str:
    return re.sub(r"^\s*>\s?", "", value, count=1)


def _plain_markdown(value: str) -> str:
    text = html.unescape(str(value or ""))
    text = _MARKDOWN_LINK_RE.sub(lambda match: match.group(1), text)
    text = re.sub(r"!?\[([^\]]+)\]\[[^\]]+\]", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"[*_~]", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return clean_text(text)


def _slug(value: str) -> str:
    text = _plain_markdown(value).casefold()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "section"


def _cells(value: str) -> list[str]:
    text = value.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith("\\|"):
        text = text[:-1]
    parts = re.split(r"(?<!\\)\|", text)
    return [_plain_markdown(part.strip().replace("\\|", "|")) for part in parts]


def _block(
    *,
    page_id: str,
    url: str,
    line_no: int,
    block_type: str,
    text: str,
    heading_level: int | None,
    heading_ancestry: list[str],
    feature_context: list[str],
    cells: list[str] | None = None,
    table_headers: list[str] | None = None,
    language: str = "",
    boundary_kind: str = "page_section",
    raw_text: str | None = None,
) -> dict[str, Any]:
    block_id = stable_id("block", [page_id, "frozen-text", line_no, block_type, text])
    section_token = _slug(heading_ancestry[-1]) if heading_ancestry else "document"
    section_id = f"{page_id}#{section_token}"
    block_row: dict[str, Any] = {
        "block_id": block_id,
        "section_id": section_id,
        "block_type": block_type,
        "text": clean_text(text),
        "dom_path": f"text[line={line_no + 1}]",
        "order": line_no,
        "element_name": "pre" if block_type == "code" else block_type.casefold(),
        "semantic_classes": [],
        "inline_literals": re.findall(r"`([^`]+)`", text),
        "inline_nodes": [],
        "heading_ancestry": list(heading_ancestry),
        "boundary_kind": boundary_kind,
        "boundary_id": stable_id("boundary", [page_id, section_id, line_no]),
        "feature_context": list(feature_context),
    }
    if raw_text is not None:
        block_row["raw_text"] = str(raw_text)
    if heading_level is not None:
        block_row["heading_level"] = heading_level
        block_row["anchor_fragments"] = [_slug(text)]
    if block_type in {"table", "table_row"}:
        values = list(cells or [])
        block_row["cells"] = values
        block_row["cell_items"] = [[value] for value in values]
        block_row["table_headers"] = list(table_headers or [])
    if block_type == "code":
        block_row["code_language"] = language
        block_row["code_metadata"] = {
            "language": language,
            "element_name": "pre",
            "semantic_classes": [],
        }
    if block_type in {"list", "list_item"}:
        block_row["list_metadata"] = {"marker": "markdown", "ordered": False}
    return block_row


def _component_blocks(
    *,
    line: str,
    page_id: str,
    line_no: int,
    heading_ancestry: list[str],
    feature_context: list[str],
) -> list[dict[str, Any]]:
    """Represent generic documentation components as link-bearing list items."""
    result: list[dict[str, Any]] = []
    for match in _CARD_RE.finditer(line):
        attrs = {
            name.casefold(): (first if first is not None else second if second is not None else third or "")
            for name, first, second, third in _ATTR_RE.findall(match.group(2))
        }
        title = attrs.get("title") or attrs.get("name")
        href = attrs.get("href") or attrs.get("url")
        if not title and not href:
            continue
        text = clean_text(" ".join(value for value in (title, href) if value))
        block = _block(
            page_id=page_id, url="", line_no=line_no, block_type="list_item", text=text,
            heading_level=None, heading_ancestry=heading_ancestry,
            feature_context=feature_context,
        )
        if href:
            block["component_href"] = href
        result.append(block)
    return result


def _links_for_text(text: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    occupied: list[tuple[int, int]] = []
    for match in _MARKDOWN_LINK_RE.finditer(text):
        label = clean_text(match.group(1))
        target = (match.group(2) or match.group(3) or "").strip()
        if target and (target, label) not in seen:
            result.append((target, label))
            seen.add((target, label))
            occupied.append(match.span())
    for match in _REFERENCE_LINK_RE.finditer(text):
        label = clean_text(match.group(1))
        target = (match.group(2) or match.group(3) or "").strip()
        if target and (target, label) not in seen:
            result.append((target, label))
            seen.add((target, label))
    for match in _ABSOLUTE_URL_RE.finditer(text):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        target = match.group(0).rstrip(".,;:!?)]}>")
        if target and (target, target) not in seen:
            result.append((target, target))
            seen.add((target, target))
    return result


def build_text_page_topology(
    spec: Mapping[str, Any],
    *,
    content: str,
    detected_format: str,
) -> dict[str, Any]:
    """Build the same topology contract for Markdown/plain text.

    This is deliberately vocabulary-free.  It recognizes documentation
    structure (headings, fences, tables, lists, links, paragraphs), not a
    database's feature names or types.
    """
    url = normalize_url(str(spec["url"]))
    page_id = stable_id("page", url)
    feature_context = sorted({str(value) for value in spec.get("feature_context", []) if str(value)})
    lines = content.splitlines()
    blocks: list[dict[str, Any]] = []
    anchors: list[dict[str, Any]] = []
    current_headings: list[str] = []
    pending: list[str] = []
    pending_line = 0
    in_fence = False
    fence_token = ""
    fence_language = ""
    code_lines: list[str] = []
    code_start = 0

    def flush_paragraph() -> None:
        nonlocal pending
        if not pending:
            return
        raw_text = " ".join(pending)
        text = _plain_markdown(raw_text)
        if text:
            blocks.append(_block(
                page_id=page_id, url=url, line_no=pending_line, block_type="paragraph",
                text=text, heading_level=None, heading_ancestry=current_headings,
                feature_context=feature_context, raw_text=raw_text,
            ))
        pending = []

    index = 0
    while index < len(lines):
        raw = lines[index]
        line = _strip_quote_prefix(raw).rstrip()
        fence = _FENCE_RE.match(line)
        if in_fence:
            if fence and fence.group(1)[0] == fence_token[0]:
                blocks.append(_block(
                    page_id=page_id, url=url, line_no=code_start, block_type="code",
                    text="\n".join(code_lines), heading_level=None,
                    heading_ancestry=current_headings, feature_context=feature_context,
                    language=fence_language,
                ))
                in_fence = False
                fence_token = ""
                fence_language = ""
                code_lines = []
            else:
                code_lines.append(_strip_quote_prefix(raw))
            index += 1
            continue
        if fence:
            flush_paragraph()
            in_fence = True
            fence_token = fence.group(1)
            fence_language = str(fence.group(2) or "")
            code_start = index
            code_lines = []
            index += 1
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            flush_paragraph()
            level = len(heading.group(1))
            value = _plain_markdown(heading.group(2))
            current_headings = current_headings[: max(level - 1, 0)]
            current_headings.append(value)
            blocks.append(_block(
                page_id=page_id, url=url, line_no=index, block_type="heading",
                text=value, heading_level=level, heading_ancestry=current_headings[:-1],
                feature_context=feature_context,
            ))
            anchors.append({
                "anchor_id": stable_id("anchor", [page_id, _slug(value)]),
                "page_id": page_id, "fragment": _slug(value),
                "dom_path": f"text[line={index + 1}]", "dom_index": index,
            })
            index += 1
            continue

        if line and index + 1 < len(lines) and re.match(r"^\s*(?:=+|-+)\s*$", _strip_quote_prefix(lines[index + 1])):
            flush_paragraph()
            level = 1 if "=" in _strip_quote_prefix(lines[index + 1]) else 2
            value = _plain_markdown(line)
            current_headings = current_headings[: max(level - 1, 0)]
            current_headings.append(value)
            blocks.append(_block(
                page_id=page_id, url=url, line_no=index, block_type="heading",
                text=value, heading_level=level, heading_ancestry=current_headings[:-1],
                feature_context=feature_context,
            ))
            anchors.append({
                "anchor_id": stable_id("anchor", [page_id, _slug(value)]),
                "page_id": page_id, "fragment": _slug(value),
                "dom_path": f"text[line={index + 1}]", "dom_index": index,
            })
            index += 2
            continue

        if _TABLE_SEPARATOR_RE.match(line):
            # A Markdown table header may still be buffered in ``pending``.
            # Flush it before inspecting the previous block, but only promote
            # a pipe-bearing paragraph; an unrelated paragraph immediately
            # before a horizontal rule is not a table header.
            if pending:
                flush_paragraph()
        if (
            _TABLE_SEPARATOR_RE.match(line)
            and blocks
            and blocks[-1].get("block_type") == "paragraph"
            and "|" in str(blocks[-1].get("text") or "")
        ):
            # The previous paragraph is the Markdown header candidate.  Convert
            # it into table metadata and let following pipe rows become rows.
            header_block = blocks.pop()
            header_values = _cells(str(header_block.get("text") or ""))
            row_index = index + 1
            while row_index < len(lines):
                row_line = _strip_quote_prefix(lines[row_index]).strip()
                if not row_line or "|" not in row_line or _TABLE_SEPARATOR_RE.match(row_line):
                    break
                row_values = _cells(row_line)
                blocks.append(_block(
                    page_id=page_id, url=url, line_no=row_index, block_type="table_row",
                    text=" | ".join(row_values), heading_level=None,
                    heading_ancestry=current_headings, feature_context=feature_context,
                    cells=row_values, table_headers=header_values,
                    boundary_kind="table_row",
                ))
                row_index += 1
            if row_index == index + 1:
                blocks.append(header_block)
            index = row_index
            continue

        components = _component_blocks(
            line=line, page_id=page_id, line_no=index,
            heading_ancestry=current_headings, feature_context=feature_context,
        )
        if components:
            flush_paragraph()
            blocks.extend(components)
            index += 1
            continue

        list_match = _LIST_RE.match(line)
        if list_match:
            flush_paragraph()
            blocks.append(_block(
                page_id=page_id, url=url, line_no=index, block_type="list_item",
                text=_plain_markdown(list_match.group(1)), heading_level=None,
                heading_ancestry=current_headings, feature_context=feature_context,
                boundary_kind="repeated_unit",
            ))
            index += 1
            continue

        if not line.strip():
            flush_paragraph()
        else:
            if not pending:
                pending_line = index
            pending.append(line)
        index += 1
    if in_fence:
        blocks.append(_block(
            page_id=page_id, url=url, line_no=code_start, block_type="code",
            text="\n".join(code_lines), heading_level=None,
            heading_ancestry=current_headings, feature_context=feature_context,
            language=fence_language,
        ))
    flush_paragraph()

    links: list[dict[str, Any]] = []
    for block in blocks:
        block_text = str(block.get("raw_text") or block.get("text") or "")
        for target, label in _links_for_text(block_text):
            absolute = urljoin(url, target)
            parsed = urlsplit(absolute)
            resolved = normalize_url(absolute) if parsed.scheme in {"http", "https"} else None
            links.append({
                "link_id": stable_id("link", [page_id, block["block_id"], target, label]),
                "source_page_id": page_id, "source_block_id": block["block_id"],
                "anchor_text": label, "raw_href": target,
                "resolved_url": resolved, "fragment": parsed.fragment or None,
                "dom_path": block["dom_path"], "dom_index": block["order"],
                "relation": "page_cross_references_page", "resolution_status": "unresolved",
            })
        component_href = block.get("component_href")
        if component_href:
            target = str(component_href)
            absolute = urljoin(url, target)
            parsed = urlsplit(absolute)
            links.append({
                "link_id": stable_id("link", [page_id, block["block_id"], target]),
                "source_page_id": page_id, "source_block_id": block["block_id"],
                "anchor_text": str(block.get("text") or ""), "raw_href": target,
                "resolved_url": normalize_url(absolute) if parsed.scheme in {"http", "https"} else None,
                "fragment": parsed.fragment or None, "dom_path": block["dom_path"],
                "dom_index": block["order"], "relation": "page_cross_references_page",
                "resolution_status": "unresolved",
            })

    return {
        "schema_version": "document-topology/v2",
        "page_id": page_id, "url": url,
        "title": current_headings[0] if current_headings else url.rstrip("/").rsplit("/", 1)[-1],
        "page_hash": str(spec.get("page_hash") or hashlib.sha256(content.encode("utf-8")).hexdigest()),
        "crawl_depth": spec.get("crawl_depth", 0), "toc_depth": None,
        "feature_context": feature_context, "blocks": blocks,
        "anchors": anchors, "links": links,
        "source_format": detected_format,
    }


__all__ = ["build_text_page_topology"]
