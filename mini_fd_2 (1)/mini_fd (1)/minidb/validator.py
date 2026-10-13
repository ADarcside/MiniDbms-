"""Semantic validation against the catalog (names and static type compatibility)."""
from typing import List, Union

from . import ast_nodes as A
from .catalog import TableSchema, validate_name
from .errors import SemanticError

NUMERIC = {"INTEGER", "FLOAT"}


def validate_create_table(stmt):
    validate_name(stmt.table, "table name")
    seen, pks = set(), 0
    for c in stmt.columns:
        validate_name(c.name, "column name")
        if c.name in seen:
            raise SemanticError(f"Duplicate column '{c.name}'")
        seen.add(c.name)
        if c.primary_key:
            pks += 1
            c.not_null = True
    if pks > 1:
        raise SemanticError("Only one PRIMARY KEY column is supported")


def resolve_column(col_name: str, schemas: Union[TableSchema, List[TableSchema]]):
    """Resolve a possibly qualified column name (e.g. 'id' or 'students.id') against schema(s)."""
    schema_list = [schemas] if isinstance(schemas, TableSchema) else schemas
    if "." in col_name:
        tbl, col = col_name.split(".", 1)
        for s in schema_list:
            if s.name == tbl:
                return s.column(col)
        raise SemanticError(f"Unknown column '{col_name}'")
    else:
        matches = []
        for s in schema_list:
            try:
                matches.append(s.column(col_name))
            except SemanticError:
                pass
        if not matches:
            if len(schema_list) == 1:
                schema_list[0].column(col_name)  # will raise appropriate SemanticError
            raise SemanticError(f"Unknown column '{col_name}'")
        if len(matches) > 1:
            raise SemanticError(f"Ambiguous column '{col_name}' in query")
        return matches[0]


def _kind(expr, schemas):
    """Static type class of an expression, or None if unknown (e.g. NULL literal)."""
    if isinstance(expr, A.Column):
        col_def = resolve_column(expr.name, schemas)
        t = col_def.type
        return "NUMBER" if t in NUMERIC else t
    if isinstance(expr, A.Literal):
        v = expr.value
        if v is None:
            return None
        if isinstance(v, bool):
            return "BOOLEAN"
        if isinstance(v, (int, float)):
            return "NUMBER"
        return "TEXT"
    return "BOOLEAN"  # Comparison, Logical, IsNull


def validate_expr(expr, schemas):
    if expr is None:
        return
    if isinstance(expr, A.Column):
        resolve_column(expr.name, schemas)
    elif isinstance(expr, A.IsNull):
        resolve_column(expr.column.name, schemas)
    elif isinstance(expr, A.Comparison):
        validate_expr(expr.left, schemas)
        validate_expr(expr.right, schemas)
        lk, rk = _kind(expr.left, schemas), _kind(expr.right, schemas)
        if lk and rk and lk != rk:
            raise SemanticError(f"Cannot compare {lk} with {rk} in WHERE clause")
    elif isinstance(expr, A.Logical):
        for o in expr.operands:
            validate_expr(o, schemas)
            k = _kind(o, schemas)
            if k not in (None, "BOOLEAN"):
                raise SemanticError(f"{expr.op} requires boolean operands, got {k}")


def validate_columns(names, schemas):
    for n in names:
        resolve_column(n, schemas)
