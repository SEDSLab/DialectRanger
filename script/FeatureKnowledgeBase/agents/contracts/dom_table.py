"""Physical native/ARIA table equivalence; original nodes/locators preserved."""
def is_table(node):
    return node.name == 'table' or node.get('role') == 'table'

def is_row(node):
    return node.name == 'tr' or node.get('role') == 'row'

def nearest_table(node):
    return node.find_parent(is_table)

def table_rows(table):
    return [r for r in table.find_all(is_row) if nearest_table(r) is table]

def row_cells(row):
    return [c for c in row.find_all(lambda n: n.name in {'td','th'} or n.get('role') in {'cell','columnheader','rowheader'})
            if c.find_parent(is_row) is row]
