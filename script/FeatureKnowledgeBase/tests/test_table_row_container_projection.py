"""Generic DOM-topology regression cases from frozen H2, PostgreSQL, and SQLite surfaces."""

from pathlib import Path

from bs4 import BeautifulSoup
import pytest

from agents.document_structure_v2.topology import _is_block_node, table_row_structure




def _rows(markup):
    return BeautifulSoup(markup, "lxml").find_all("tr")


def test_nested_row_container_suppressed():
    outer, inner = _rows("<table><tr><td><table><tr><td>ABS(X)</td></tr></table></td></tr></table>")
    assert table_row_structure(outer)["row_role"] == "LAYOUT_CONTAINER"
    assert table_row_structure(outer)["descendant_tr_count"] == 1
    assert table_row_structure(outer)["nested_table_count"] == 1
    assert not _is_block_node(outer)
    assert _is_block_node(inner)


def test_heading_leaf_row_preserved():
    row = _rows("<table><tr><td><h3>Feature</h3><p>Description.</p></td></tr></table>")[0]
    assert table_row_structure(row)["row_role"] == "LOGICAL_LEAF"
    assert table_row_structure(row)["descendant_tr_count"] == 0
    assert _is_block_node(row)


def test_postgresql_feature_row_preserved():
    # Reduced from frozen functions-admin.html: the real row has a signature,
    # description, and nested Caution heading but no descendant table row.
    row = _rows("""<table><tr><td class="func_table_entry" id="PG-SYNC-REPLICATION-SLOTS">
      <p class="func_signature"><code class="function">pg_sync_replication_slots</code> ()</p>
      <p>Synchronize the logical failover replication slots.</p>
      <div class="caution"><h3>Caution</h3><p>Use with caution.</p></div>
      </td></tr></table>""")[0]
    assert table_row_structure(row)["row_role"] == "LOGICAL_LEAF"
    assert _is_block_node(row)


def test_sqlite_heading_leaf_row_preserved():
    # Reduced from frozen books.html; structural fidelity does not depend on
    # whether the row describes a SQL feature.
    row = _rows("<table><tr><td><h2>SQLite Forensics</h2><p>Author.</p><p>Publisher.</p></td></tr></table>")[0]
    assert table_row_structure(row)["row_role"] == "LOGICAL_LEAF"
    assert _is_block_node(row)


def test_h2_outer_layout_row_suppressed():
    outer, first, second = _rows("""<table><tr><td><h2>Functions</h2><table>
      <tr><td>ABS(X)</td></tr><tr><td>ACOS(X)</td></tr>
      </table><pre>ABS(X)</pre></td></tr></table>""")
    assert table_row_structure(outer)["descendant_tr_count"] == 2
    assert not _is_block_node(outer)
    assert _is_block_node(first) and _is_block_node(second)
    assert outer.find("h2") and outer.find("pre")


def test_lxml_repaired_sibling_rows_are_not_false_nested_rows():
    first, second = _rows("<table><tr><td>First<tr><td>Second</table>")
    assert table_row_structure(first)["descendant_tr_count"] == 0
    assert table_row_structure(second)["descendant_tr_count"] == 0
    assert _is_block_node(first) and _is_block_node(second)






