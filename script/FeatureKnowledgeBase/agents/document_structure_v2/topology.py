"""DOM-aware page topology with lossless href occurrence capture."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from agents.document_structure_v2.models import clean_text, stable_id
from agents.document_structure_v2.local_definition_context import observe_local_definition_context, observe_page_function_definition, observe_implementation_relation
from agents.document_structure_v2.semantic_regions import (
    iter_source_semantic_regions,
    semantic_region_role,
)


_BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "pre", "tr", "dt", "dd"}


def _explicit_semantic_role(node: Tag) -> str | None:
    return semantic_region_role(node)[0]


def _is_legacy_block_node(node: Tag) -> bool:
    if node.name in _BLOCK_TAGS:
        return True
    # Componentized list items commonly wrap their text in an explicit
    # semantic paragraph (``<li><span data-as=\"p\">...</span></li>``).
    # The list item is still the owner-visible block; treating the semantic
    # child as a reason to discard the parent loses the item because nested
    # semantic regions are intentionally deduplicated below.
    return node.name == "li" and not node.find([*_BLOCK_TAGS, "li"])


def _is_explicit_semantic_node(node: Tag) -> bool:
    return _explicit_semantic_role(node) is not None


def table_row_structure(node: Tag) -> dict[str, int | str]:
    """Describe a row's DOM topology without making a SQL/feature judgment."""
    if node.name != "tr":
        raise ValueError("table_row_structure requires a tr node")
    descendant_rows = node.find_all("tr")
    nested_tables = node.find_all("table")
    return {
        "row_role": "LAYOUT_CONTAINER" if descendant_rows else (
            "AMBIGUOUS" if nested_tables else "LOGICAL_LEAF"
        ),
        "descendant_tr_count": len(descendant_rows),
        "nested_table_count": len(nested_tables),
        "direct_child_tr_count": sum(child.name == "tr" for child in node.children if isinstance(child, Tag)),
    }


def _is_block_node(node: Tag) -> bool:
    # A row containing other rows is a container. A heading alone does not
    # make a leaf row a container: it may own a real definition or description.
    if node.name == "tr" and node.find("tr"):
        return False
    if _is_legacy_block_node(node):
        return True
    return _is_explicit_semantic_node(node)


def _iter_block_nodes(root: Tag):
    """Return top-level block surfaces without nested semantic duplicates."""
    for node in root.find_all(_is_block_node):
        if node.get('role') == 'row':
            yield node
            continue
        if _is_explicit_semantic_node(node) and any(
            _is_block_node(parent) for parent in node.parents if isinstance(parent, Tag)
        ):
            continue
        yield node


def _content_block_count(node: Tag) -> int:
    return sum(1 for _ in _iter_block_nodes(node))


def _is_heading_node(node: Tag) -> bool:
    return node.name in {f"h{i}" for i in range(1, 7)} or _explicit_semantic_role(node) == "HEADING"


def _previous_heading(node: Tag) -> Tag | None:
    return node.find_previous(_is_heading_node)


def normalize_url(value: str) -> str:
    parsed = urlsplit(value)
    path = re.sub(r"/{2,}", "/", parsed.path) or "/"
    return urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(), path, parsed.query, ""))


def _dom_path(node: Tag) -> str:
    parts = []
    current = node
    while isinstance(current, Tag) and current.name != "[document]":
        part = current.name
        if current.get("id"):
            part += "#" + current["id"]
        else:
            siblings = [item for item in current.parent.find_all(current.name, recursive=False)] if current.parent else []
            if len(siblings) > 1:
                # BeautifulSoup Tag equality is structural, so ``list.index``
                # can return the first of two identical sibling nodes.  Use
                # object identity to keep DOM paths unique.
                sibling_index = next((index for index, item in enumerate(siblings) if item is current), 0)
                part += f":nth-of-type({sibling_index + 1})"
        parts.append(part)
        current = current.parent
    return " > ".join(reversed(parts))


def _main(soup: BeautifulSoup, selectors: list[str]) -> Tag:
    body = soup.body or soup
    body_blocks = _content_block_count(body)
    body_text = len(clean_text(body.get_text(" ", strip=True)))
    for selector in selectors:
        candidates = soup.select(selector)
        candidates = [node for node in candidates if _content_block_count(node)]
        if candidates:
            selected = max(candidates, key=lambda node: (
                _content_block_count(node),
                len(clean_text(node.get_text(" ", strip=True))),
            ))
            selected_blocks = _content_block_count(selected)
            selected_text = len(clean_text(selected.get_text(" ", strip=True)))
            if (selected is not body and body_blocks >= 5
                    and selected_blocks <= body_blocks * 0.2 and selected_text < body_text * 0.2):
                return body
            return selected
    return body


def _section_id(page_id: str, node: Tag) -> str:
    heading = node if _is_heading_node(node) else _previous_heading(node)
    anchor = heading.find("a", attrs={"name": True}) if heading else None
    token = (anchor.get("name") if anchor else None) or (heading.get("id") if heading else None)
    return f"{page_id}#{token}" if token else f"{page_id}#document"


def _owner_boundary(page_id: str, node: Tag) -> tuple[str, str]:
    row = node if node.name == "tr" else node.find_parent("tr")
    if row is not None:
        return "table_row", stable_id("boundary", [page_id, _dom_path(row)])
    item = node if node.name in {"li", "dt", "dd"} else node.find_parent(["li", "dt", "dd"])
    if item is not None:
        if item.name == "dd":
            term = item.find_previous_sibling("dt")
            if term is not None:
                item = term
        anchor = item.find("a", attrs={"name": True}) or item.find(attrs={"id": True})
        token = str(anchor.get("name") or anchor.get("id")) if anchor else _dom_path(item)
        return "repeated_unit", stable_id("boundary", [page_id, token])
    # A source-semantic paragraph and the native code/list nodes around it
    # belong to the same logical feature section.  Carry the heading boundary
    # across all block surfaces so downstream Owner/Evidence cannot retain the
    # signature while dropping the adjacent prose merely because the source
    # used ``span[data-as=p]`` instead of ``p``.
    heading = node if _is_heading_node(node) else _previous_heading(node)
    if heading is not None:
        return "heading_section", stable_id("boundary", [page_id, _dom_path(heading)])
    section = node.find_parent("div", class_=re.compile(r"\bsection\b"))
    return "page_section", stable_id("boundary", [page_id, _dom_path(section or node)])


def _cell_items(cell: Tag) -> list[str]:
    html = str(cell)
    lines = re.split(r"<br\s*/?>", html, flags=re.I)
    result = []
    for line in lines:
        text = BeautifulSoup(line, "lxml").get_text(" ", strip=True)
        for value in re.split(r"(?:\xa0|\s){2,}", text):
            value = clean_text(value)
            if value:
                result.append(value)
    return result


def build_page_topology(spec: dict, profile: dict) -> dict:
    html = Path(spec["html_path"]).read_bytes().decode('utf-8', errors='replace')
    soup = BeautifulSoup(html, "lxml")
    for selector in profile.get("noise_selectors", []):
        for node in soup.select(selector):
            node.decompose()
    root = _main(soup, profile.get("main_selectors", ["main", "article", "body"]))
    # Provenance identifies the frozen bytes, not Python's universal-newline
    # decoding (which would give Windows CRLF files a different hash).
    page_hash = hashlib.sha256(Path(spec['html_path']).read_bytes()).hexdigest()
    page_id = stable_id("page", normalize_url(spec["url"]))
    blocks, node_to_block = [], {}
    legacy_index = 0
    for index, node in enumerate(_iter_block_nodes(root)):
        legacy_node = _is_legacy_block_node(node)
        legacy_index_for_node = None if not legacy_node or node.name in {"dt", "dd"} else legacy_index
        if legacy_node and node.name not in {"dt", "dd"}:
            # Count empty legacy block nodes too: the previous topology used
            # enumerate() before its empty-text check, and frozen block IDs
            # must remain stable after adding definition surfaces.
            legacy_index += 1
        text = clean_text(node.get_text(" ", strip=True))
        if not text:
            continue
        semantic_role, semantic_source, semantic_heading_level = semantic_region_role(node)
        # Native HTML carries the same semantic contract as the explicit
        # component attributes.  Recording it here makes source-semantic
        # preservation inspectable without requiring callers to remember
        # which renderer produced the node.
        if semantic_role is None:
            native_roles = {
                "h1": ("HEADING", 1), "h2": ("HEADING", 2),
                "h3": ("HEADING", 3), "h4": ("HEADING", 4),
                "h5": ("HEADING", 5), "h6": ("HEADING", 6),
                "p": ("PARAGRAPH", None), "pre": ("CODE", None),
                "tr": ("TABLE_ROW", None), "li": ("LIST_ITEM", None),
                "dt": ("DEFINITION", None), "dd": ("DEFINITION", None),
            }
            native_role = native_roles.get(str(node.name).casefold())
            if native_role:
                semantic_role, semantic_source, semantic_heading_level = native_role[0], "native-element", native_role[1]
        block_type = "heading" if node.name.startswith("h") or semantic_role == "HEADING" else {
            "p": "paragraph", "pre": "code", "tr": "table_row", "li": "list_item",
            "dt": "definition_term", "dd": "definition_description",
        }.get(node.name, {
            "PARAGRAPH": "paragraph", "CODE": "code", "TABLE": "table", "TABLE_ROW": "table_row",
            "LIST": "list", "LIST_ITEM": "list_item", "HEADING": "heading",
        }.get(semantic_role or "", "paragraph"))
        # Preserve the pre-definition-surface block IDs for all existing
        # topology nodes.  Adding dt/dd must not renumber later p/pre/tr/li
        # evidence, otherwise frozen audit lineage becomes incomparable.
        if not legacy_node:
            block_id = stable_id("block", [page_id, "semantic", _dom_path(node)])
        elif node.name in {"dt", "dd"}:
            block_id = stable_id("block", [page_id, "definition", _dom_path(node)])
        else:
            block_id = stable_id("block", [page_id, legacy_index_for_node, _dom_path(node)])
        block = {
            "block_id": block_id,
            "section_id": _section_id(page_id, node), "block_type": block_type,
            "text": text, "dom_path": _dom_path(node), "order": index,
            "element_name": node.name, "semantic_classes": sorted(node.get("class", [])),
        }
        if node.name.startswith("h") or semantic_role == "HEADING":
            block["heading_level"] = int(node.name[1]) if node.name.startswith("h") else semantic_heading_level or 1
            block["anchor_fragments"] = sorted({str(value) for value in (
                [node.get("id")] + [anchor.get("name") for anchor in node.find_all("a", attrs={"name": True})]
            ) if value})
        if semantic_role:
            block["semantic_role"] = semantic_role
            block["semantic_source"] = semantic_source
            block["semantic_attributes"] = {
                str(key): str(value) for key, value in node.attrs.items()
                if key in {"data-as", "role", "aria-level", "data-component", "data-testid", "itemprop"}
            }
        block["inline_literals"] = [
            clean_text(value.get_text(" ", strip=True)) for value in node.find_all("code")
            if clean_text(value.get_text(" ", strip=True))
        ]
        block["inline_nodes"] = [
            {"element_name": value.name, "text": clean_text(value.get_text(" ", strip=True)),
              "semantic_classes": sorted(value.get("class", [])), "dom_path": _dom_path(value),
              "inline_index": inline_index}
            for inline_index, value in enumerate(
                node.find_all(["code", "span", "em", "strong"], recursive=True))
            if clean_text(value.get_text(" ", strip=True))
        ]
        block["boundary_kind"], block["boundary_id"] = _owner_boundary(page_id, node)
        block['definition_context'] = observe_local_definition_context(node)
        if not block['definition_context']:
            block['definition_context'] = observe_page_function_definition(
                node, soup.title.get_text(' ', strip=True) if soup.title else '')
        if not block['definition_context']:
            block['definition_context'] = observe_implementation_relation(node)
        if node.name == "tr" or node.get('role') == 'row':
            from agents.contracts.dom_table import nearest_table, row_cells, table_rows
            table = nearest_table(node)
            cells = row_cells(node)
            block["cells"] = [clean_text(cell.get_text(" ", strip=True)) for cell in cells]
            # Identity matching keeps its normalized cells. Field witnesses
            # need the source-visible content too: whitespace inside a quoted
            # SQL string is data, not layout to collapse.
            block["literal_cells"] = [cell.get_text(" ", strip=True) for cell in cells]
            block["cell_items"] = [_cell_items(cell) for cell in cells]
            block["table_headers"] = [clean_text(cell.get_text(" ", strip=True)) for cell in table.find_all("th")[:len(block["cells"])]] if table else []
            if table is not None and table.get('role')=='table':
                block['table_headers']=[clean_text(c.get_text(' ',strip=True)) for r in table_rows(table) for c in row_cells(r) if c.get('role')=='columnheader'][:len(cells)]
        blocks.append(block)
        node_to_block[id(node)] = block["block_id"]

    # Preserve source text separately: legacy text and IDs remain matching keys.
    from agents.document_structure_v2.source_preservation import preserve_source, grid_observations
    preservation = preserve_source(root, blocks, page_id, _dom_path)
    grids = {}
    for node in root.find_all('table'):
        grids.update(grid_observations(node, _dom_path))
    for block in blocks:
        if block['dom_path'] in grids and block.get('block_type') == 'table_row':
            block['source_cell_grid'] = grids[block['dom_path']]

    anchors = []
    for index, node in enumerate(root.find_all(attrs={"id": True}) + root.find_all("a", attrs={"name": True})):
        fragment = str(node.get("id") or node.get("name") or "").strip()
        if fragment:
            anchors.append({
                "anchor_id": stable_id("anchor", [page_id, fragment]), "page_id": page_id,
                "fragment": fragment, "dom_path": _dom_path(node), "dom_index": index,
            })

    links = []
    for index, node in enumerate(root.find_all("a", href=True)):
        raw_href = str(node.get("href"))
        absolute = urljoin(spec["url"], raw_href)
        parsed = urlsplit(absolute)
        resolved_url = normalize_url(absolute) if parsed.scheme in {"http", "https"} else None
        parent = node.find_parent(_is_block_node)
        links.append({
            "link_id": stable_id("link", [page_id, index, _dom_path(node), raw_href]),
            "source_page_id": page_id, "source_block_id": node_to_block.get(id(parent)),
            "anchor_text": clean_text(node.get_text(" ", strip=True)), "raw_href": raw_href,
            "resolved_url": resolved_url, "fragment": parsed.fragment or None,
            "dom_path": _dom_path(node), "dom_index": index,
            "relation": _navigation_relation(node), "resolution_status": "unresolved",
        })

    breadcrumb = soup.select_one("[aria-label*=breadcrumb i], #docs-breadcrumbs, .breadcrumb, .breadcrumbs")
    toc_depth = len(breadcrumb.find_all("a", href=True)) if breadcrumb else None
    return {
        "schema_version": "document-topology/v2", "page_id": page_id,
        "url": normalize_url(spec["url"]), "title": clean_text(soup.title.get_text(" ") if soup.title else ""),
        "page_hash": page_hash, "crawl_depth": spec.get("crawl_depth", 0), "toc_depth": toc_depth,
        "feature_context": sorted(set(spec.get("feature_context", []))),
        "blocks": blocks, "anchors": anchors, "links": links,
        "source_preservation": preservation,
    }


def _navigation_relation(node: Tag) -> str:
    value = " ".join([
        str(node.get("rel") or ""), str(node.get("aria-label") or ""),
        clean_text(node.get_text(" ", strip=True)),
    ]).casefold()
    for token, relation in (("previous", "previous_page"), ("prev", "previous_page"),
                            ("next", "next_page"), ("up", "up_page")):
        if re.search(rf"\b{token}\b", value):
            return relation
    if node.find_parent(attrs={"id": re.compile("breadcrumb", re.I)}):
        return "toc_parent"
    return "page_cross_references_page"


def resolve_links(pages: list[dict]) -> list[dict]:
    by_url = {page["url"]: page for page in pages}
    for page in pages:
        for link in page["links"]:
            target = by_url.get(link["resolved_url"])
            if not target:
                link["resolution_status"] = "unfetched" if link["resolved_url"] else "non_http"
                continue
            link["target_page_id"] = target["page_id"]
            if link["fragment"]:
                anchor = next((row for row in target["anchors"] if row["fragment"] == link["fragment"]), None)
                link["target_anchor_id"] = anchor["anchor_id"] if anchor else None
                link["resolution_status"] = "resolved_anchor" if anchor else "unresolved_fragment"
            else:
                link["resolution_status"] = "resolved_page"
    return pages


def document_relations(pages: list[dict]) -> list[dict]:
    result = []
    for page in pages:
        for link in page["links"]:
            if not link.get("target_page_id"):
                continue
            result.append({
                "relation_id": stable_id("relation", [link["link_id"], link["relation"]]),
                "document_relation": link["relation"], "source_page_id": page["page_id"],
                "target_page_id": link["target_page_id"], "link_id": link["link_id"],
                "target_fragment": link["fragment"], "status": link["resolution_status"],
            })
    return sorted(result, key=lambda row: row["relation_id"])
