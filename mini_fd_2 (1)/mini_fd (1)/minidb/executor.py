"""Query executor. Consumes AST nodes; never sees raw SQL or file paths.
Writes are statement-atomic: new state is built and validated in memory, then saved once.
Supports Index Lookups, Nested-Loop Joins, Transactions (Undo Log), and EXPLAIN plans.
"""
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional

from . import ast_nodes as A
from .catalog import TableSchema
from .constraints import check_primary_key_unique, coerce
from .errors import SemanticError, StorageError
from .fileio import atomic_write_json, read_json
from .indexes import IndexManager
from .transactions import TransactionManager
from .validator import resolve_column, validate_columns, validate_create_table, validate_expr



@dataclass
class Session:
    db: Optional[str] = None
    in_transaction: bool = False
    tx_db: Optional[str] = None


@dataclass
class ResultSet:
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    message: str = ""
    stats: dict = field(default_factory=dict)


def _truth(v):
    if v is None or isinstance(v, bool):
        return v
    raise SemanticError("WHERE expression must be boolean")


def _lookup_value(name: str, values: dict) -> Any:
    """Resolve column name against row dictionary (handling both qualified and unqualified)."""
    if name in values:
        return values[name]
    if "." in name:
        _, col = name.split(".", 1)
        if col in values:
            return values[col]
    else:
        matches = [v for k, v in values.items() if k.endswith(f".{name}")]
        if len(matches) == 1:
            return matches[0]
    raise SemanticError(f"Column '{name}' not found in values")


def evaluate(expr, values: dict):
    """Evaluate with SQL three-valued logic (None = unknown)."""
    if isinstance(expr, A.Column):
        return _lookup_value(expr.name, values)
    if isinstance(expr, A.Literal):
        return expr.value
    if isinstance(expr, A.IsNull):
        val = _lookup_value(expr.column.name, values)
        return (val is None) != expr.negated
    if isinstance(expr, A.Comparison):
        l, r = evaluate(expr.left, values), evaluate(expr.right, values)
        if l is None or r is None:
            return None
        return {"=": l == r, "!=": l != r, "<": l < r,
                "<=": l <= r, ">": l > r, ">=": l >= r}[expr.op]
    if isinstance(expr, A.Logical):
        vals = [_truth(evaluate(o, values)) for o in expr.operands]
        if expr.op == "NOT":
            return None if vals[0] is None else not vals[0]
        if expr.op == "AND":
            if any(v is False for v in vals):
                return False
            return None if any(v is None for v in vals) else True
        if any(v is True for v in vals):
            return True
        return None if any(v is None for v in vals) else False
    raise SemanticError("Unsupported expression")


def matches(where, values: dict) -> bool:
    return where is None or _truth(evaluate(where, values)) is True


class Executor:
    def __init__(self, catalog, storage, indexes=None, transactions=None):
        self.catalog = catalog
        self.storage = storage
        self.indexes = indexes or IndexManager(catalog.root, catalog)
        self.transactions = transactions or TransactionManager(catalog.root)

    def execute(self, stmt, session):
        handler = getattr(self, "_exec_" + type(stmt).__name__, None)
        if handler is None:
            raise SemanticError(f"Unsupported statement {type(stmt).__name__}")
        return handler(stmt, session)

    def _db(self, session):
        if session.db is None:
            raise SemanticError("No database selected. Use: USE <database>;")
        return session.db

    # ---- database commands ----
    def _exec_CreateDatabaseStmt(self, s, session):
        self.catalog.create_database(s.name)
        return ResultSet(message=f"Database '{s.name}' created")

    def _exec_DropDatabaseStmt(self, s, session):
        self.catalog.drop_database(s.name)
        if session.db == s.name:
            session.db = None
        return ResultSet(message=f"Database '{s.name}' dropped")

    def _exec_UseStmt(self, s, session):
        if not self.catalog.database_exists(s.name):
            raise SemanticError(f"Database '{s.name}' does not exist")
        session.db = s.name
        return ResultSet(message=f"Database changed to '{s.name}'")

    def _exec_ShowDatabasesStmt(self, s, session):
        return ResultSet(["database"], [(n,) for n in self.catalog.list_databases()])

    # ---- table commands ----
    def _exec_CreateTableStmt(self, s, session):
        db = self._db(session)
        validate_create_table(s)
        with self.transactions.write_lock(db, session):
            if self.catalog.table_exists(db, s.table):
                raise SemanticError(f"Table '{s.table}' already exists")
            # Write-ahead: Record DDL intent in journal BEFORE creating persistent files
            self.transactions.record_table_create_intent(db, s.table)
            self.storage.create_table(db, s.table)
            self.catalog.create_table(db, TableSchema(s.table, s.columns))
            return ResultSet(message=f"Table '{s.table}' created")

    def _exec_DropTableStmt(self, s, session):
        db = self._db(session)
        with self.transactions.write_lock(db, session):
            if not self.catalog.table_exists(db, s.table):
                raise SemanticError(f"Table '{s.table}' does not exist in database '{db}'")
            # Write-ahead: Snapshot table and index data BEFORE dropping persistent files
            self.transactions.record_table_drop_intent(db, s.table, self.storage, self.indexes)
            self.catalog.drop_table(db, s.table)
            self.storage.drop_table(db, s.table)
            self.indexes.drop_table_indexes(db, s.table)
            return ResultSet(message=f"Table '{s.table}' dropped")

    def _exec_ShowTablesStmt(self, s, session):
        return ResultSet(["table"], [(n,) for n in self.catalog.list_tables(self._db(session))])

    def _exec_DescribeStmt(self, s, session):
        schema = self.catalog.get_table(self._db(session), s.table)
        rows = [(c.name, c.type, "NO" if (c.not_null or c.primary_key) else "YES",
                 "PRI" if c.primary_key else "") for c in schema.columns]
        return ResultSet(["column", "type", "nullable", "key"], rows)

    # ---- index commands ----
    def _exec_CreateIndexStmt(self, s, session):
        db = self._db(session)
        schema = self.catalog.get_table(db, s.table)
        schema.column(s.column)  # ensures column exists
        with self.transactions.write_lock(db, session):
            # Write-ahead: Record DDL intent in journal BEFORE creating index files
            self.transactions.record_index_create_intent(db, s.name)
            self.indexes.create_index(db, s.table, s.name, s.column, s.index_type, self.storage)
            return ResultSet(message=f"Index '{s.name}' created on '{s.table}({s.column})'")

    def _exec_DropIndexStmt(self, s, session):
        db = self._db(session)
        with self.transactions.write_lock(db, session):
            # Write-ahead: Snapshot index data BEFORE dropping index file
            self.transactions.record_index_drop_intent(db, s.name, self.indexes)
            self.indexes.drop_index(db, s.name)
            return ResultSet(message=f"Index '{s.name}' dropped")


    # ---- transaction commands ----
    def _exec_BeginStmt(self, s, session):
        db = self._db(session)
        self.transactions.begin(db, session)
        return ResultSet(message="Transaction started")

    def _exec_CommitStmt(self, s, session):
        db = self._db(session)
        self.transactions.commit(db, session)
        return ResultSet(message="Transaction committed")

    def _exec_RollbackStmt(self, s, session):
        db = self._db(session)
        self.transactions.rollback(db, session, self.catalog, self.storage, self.indexes)
        return ResultSet(message="Transaction rolled back")

    # ---- atomic storage and index write helper ----
    def _apply_table_and_index_write(self, db, table, new_table_data, index_callback):
        """Applies storage and index writes as one logical atomic operation.
        If any index update fails, the table and all affected indexes are rolled back."""
        original_table_data = self.storage.load(db, table)
        affected_indexes = self.catalog.list_indexes(db, table)
        original_index_snapshots = {}
        for idx_name in affected_indexes:
            idx_p = self.indexes._path(db, idx_name)
            if idx_p.is_file():
                original_index_snapshots[idx_name] = read_json(idx_p)

        try:
            self.storage.save(db, table, new_table_data)
            index_callback()
        except Exception as e:
            # Revert table file
            self.storage.save(db, table, original_table_data)
            # Revert all index files
            for idx_name, idx_data in original_index_snapshots.items():
                atomic_write_json(self.indexes._path(db, idx_name), idx_data)
            # Clear in-memory index cache
            for idx_name in affected_indexes:
                self.indexes._cache.pop((db, idx_name), None)
            raise StorageError(f"Multi-file write failed; table and indexes rolled back: {e}") from e

    # ---- data commands ----
    def _exec_InsertStmt(self, s, session):
        db = self._db(session)
        schema = self.catalog.get_table(db, s.table)
        cols = s.columns if s.columns is not None else schema.column_names()
        validate_columns(cols, schema)
        if len(set(cols)) != len(cols):
            raise SemanticError("Duplicate column in INSERT column list")

        with self.transactions.write_lock(db, session):
            if getattr(session, "in_transaction", False):
                self.transactions.record_table_pre_image(db, s.table, self.storage)

            data = self.storage.load(db, s.table)
            next_rid, new_rows = data["next_rid"], []
            for tup in s.rows:
                if len(tup) != len(cols):
                    raise SemanticError(f"Expected {len(cols)} values, got {len(tup)}")
                given = {c: lit.value for c, lit in zip(cols, tup)}
                values = {c.name: coerce(c, given.get(c.name)) for c in schema.columns}
                new_rows.append({"rid": next_rid, "values": values})
                next_rid += 1
            all_rows = data["rows"] + new_rows
            check_primary_key_unique(schema, all_rows)

            new_table_data = {"version": 1, "next_rid": next_rid, "rows": all_rows}

            def update_indexes():
                for nr in new_rows:
                    self.indexes.on_insert(db, s.table, nr["rid"], nr["values"])

            self._apply_table_and_index_write(db, s.table, new_table_data, update_indexes)

            return ResultSet(message=f"{len(new_rows)} row(s) inserted",
                             stats={"rows_affected": len(new_rows)})

    def _exec_UpdateStmt(self, s, session):
        db = self._db(session)
        schema = self.catalog.get_table(db, s.table)
        validate_columns(list(s.assignments), schema)
        validate_expr(s.where, schema)

        with self.transactions.write_lock(db, session):
            if getattr(session, "in_transaction", False):
                self.transactions.record_table_pre_image(db, s.table, self.storage)

            data = self.storage.load(db, s.table)
            new_rows, changed_rows = [], []
            for row in data["rows"]:
                if matches(s.where, row["values"]):
                    old_values = dict(row["values"])
                    values = dict(row["values"])
                    for c, lit in s.assignments.items():
                        values[c] = coerce(schema.column(c), lit.value)
                    new_row = {"rid": row["rid"], "values": values}
                    new_rows.append(new_row)
                    changed_rows.append((row["rid"], old_values, values))
                else:
                    new_rows.append(row)
            check_primary_key_unique(schema, new_rows)

            if changed_rows:
                new_table_data = {**data, "rows": new_rows}

                def update_indexes():
                    for rid, old_v, new_v in changed_rows:
                        self.indexes.on_update(db, s.table, rid, old_v, new_v)

                self._apply_table_and_index_write(db, s.table, new_table_data, update_indexes)

            return ResultSet(message=f"{len(changed_rows)} row(s) updated",
                             stats={"rows_affected": len(changed_rows)})

    def _exec_DeleteStmt(self, s, session):
        db = self._db(session)
        schema = self.catalog.get_table(db, s.table)
        validate_expr(s.where, schema)

        with self.transactions.write_lock(db, session):
            if getattr(session, "in_transaction", False):
                self.transactions.record_table_pre_image(db, s.table, self.storage)

            data = self.storage.load(db, s.table)
            keep, removed_rows = [], []
            for r in data["rows"]:
                if matches(s.where, r["values"]):
                    removed_rows.append(r)
                else:
                    keep.append(r)

            if removed_rows:
                new_table_data = {**data, "rows": keep}

                def update_indexes():
                    for r in removed_rows:
                        self.indexes.on_delete(db, s.table, r["rid"], r["values"])

                self._apply_table_and_index_write(db, s.table, new_table_data, update_indexes)

            return ResultSet(message=f"{len(removed_rows)} row(s) deleted",
                             stats={"rows_affected": len(removed_rows)})


    def _extract_index_plan(self, db, table, where):
        """Rule-based query optimization: determine if WHERE can use an index."""
        if not isinstance(where, A.Comparison):
            return None

        # Check for equality condition: col = lit or lit = col
        col_name, target_val = None, None
        if isinstance(where.left, A.Column) and isinstance(where.right, A.Literal):
            col_name, target_val = where.left.name, where.right.value
        elif isinstance(where.right, A.Column) and isinstance(where.left, A.Literal):
            col_name, target_val = where.right.name, where.left.value

        if col_name and "." in col_name:
            col_name = col_name.split(".", 1)[1]

        if not col_name:
            return None

        idx_info = self.indexes.get_index_for_column(db, table, col_name)
        if not idx_info:
            return None

        idx_name, idx_obj, meta = idx_info
        if where.op == "=":
            return ("EQ", idx_name, idx_obj, col_name, target_val)

        if meta.get("type") == "BTREE" and where.op in ("<", "<=", ">", ">="):
            return ("RANGE", idx_name, idx_obj, col_name, where.op, target_val)

        return None

    def _exec_SelectStmt(self, s, session):
        start = time.perf_counter()
        db = self._db(session)

        # Handle JOIN queries
        if s.join is not None:
            return self._exec_join_select(s, session, start)

        schema = self.catalog.get_table(db, s.table)
        cols = s.columns if s.columns is not None else schema.column_names()
        validate_columns(cols, schema)
        validate_expr(s.where, schema)
        if s.order_by:
            validate_columns([c for c, _ in s.order_by], schema)

        # Check for index access path
        plan = self._extract_index_plan(db, s.table, s.where)
        all_table_data = self.storage.load(db, s.table)["rows"]
        row_map = {r["rid"]: r["values"] for r in all_table_data}

        if plan and plan[0] == "EQ":
            _, idx_name, idx_obj, col_name, val = plan
            matching_rids = idx_obj.lookup(val)
            examined = len(matching_rids)
            rows = [row_map[rid] for rid in matching_rids if rid in row_map]
            access_path = f"index scan ({idx_name})"
        elif plan and plan[0] == "RANGE":
            _, idx_name, idx_obj, col_name, op, val = plan
            if op == ">":
                matching_rids = idx_obj.range(lo=val, hi=None, inc_lo=False)
            elif op == ">=":
                matching_rids = idx_obj.range(lo=val, hi=None, inc_lo=True)
            elif op == "<":
                matching_rids = idx_obj.range(lo=None, hi=val, inc_hi=False)
            else:  # <=
                matching_rids = idx_obj.range(lo=None, hi=val, inc_hi=True)
            examined = len(matching_rids)
            rows = [row_map[rid] for rid in matching_rids if rid in row_map]
            access_path = f"btree index range scan ({idx_name})"
        else:
            rows = [r["values"] for r in all_table_data]
            examined = len(rows)
            access_path = "full scan"

        # Filter (in case index was a prefix or range, or full scan)
        rows = [v for v in rows if matches(s.where, v)]

        # Sort
        for col, direction in reversed(s.order_by or []):
            def sort_key(v, c=col):
                val = _lookup_value(c, v)
                return (val is not None, val)
            rows.sort(key=sort_key, reverse=(direction == "DESC"))

        # Limit
        if s.limit is not None:
            rows = rows[:s.limit]

        # Project
        out = [tuple(_lookup_value(c, v) for c in cols) for v in rows]
        elapsed = (time.perf_counter() - start) * 1000
        return ResultSet(cols, out, stats={
            "rows_examined": examined, "rows_returned": len(out),
            "elapsed_ms": elapsed, "access_path": access_path})

    def _exec_join_select(self, s, session, start):
        db = self._db(session)
        outer_table = s.table
        inner_table = s.join.table
        schema1 = self.catalog.get_table(db, outer_table)
        schema2 = self.catalog.get_table(db, inner_table)
        schemas = [schema1, schema2]

        validate_expr(s.where, schemas)
        # Determine output columns
        if s.columns is None:
            cols = [f"{schema1.name}.{c}" for c in schema1.column_names()] + \
                   [f"{schema2.name}.{c}" for c in schema2.column_names()]
        else:
            cols = s.columns
            validate_columns(cols, schemas)

        left_col = s.join.left_col
        right_col = s.join.right_col
        resolve_column(left_col, schemas)
        resolve_column(right_col, schemas)

        outer_rows = self.storage.load(db, outer_table)["rows"]
        inner_rows = self.storage.load(db, inner_table)["rows"]

        # Check for index on inner table join column (Index Nested-Loop Join)
        inner_col_name = right_col.split(".", 1)[1] if "." in right_col else right_col
        inner_idx_info = self.indexes.get_index_for_column(db, inner_table, inner_col_name)
        inner_row_map = {r["rid"]: r["values"] for r in inner_rows}

        joined_records = []
        examined = 0

        if inner_idx_info:
            idx_name, idx_obj, _ = inner_idx_info
            access_path = f"index nested-loop join ({idx_name})"
            for o in outer_rows:
                examined += 1
                o_val = o["values"]
                join_val = _lookup_value(left_col, o_val)
                inner_rids = idx_obj.lookup(join_val)
                examined += len(inner_rids)
                for irid in inner_rids:
                    if irid in inner_row_map:
                        i_val = inner_row_map[irid]
                        combined = self._merge_joined_rows(schema1, o_val, schema2, i_val)
                        if matches(s.where, combined):
                            joined_records.append(combined)
        else:
            access_path = "nested-loop join"
            for o in outer_rows:
                o_val = o["values"]
                for i in inner_rows:
                    examined += 1
                    i_val = i["values"]
                    combined = self._merge_joined_rows(schema1, o_val, schema2, i_val)
                    # Evaluate ON join equality
                    if _lookup_value(left_col, combined) == _lookup_value(right_col, combined):
                        if matches(s.where, combined):
                            joined_records.append(combined)

        # Sort
        for col, direction in reversed(s.order_by or []):
            def sort_key(v, c=col):
                val = _lookup_value(c, v)
                return (val is not None, val)
            joined_records.sort(key=sort_key, reverse=(direction == "DESC"))

        # Limit
        if s.limit is not None:
            joined_records = joined_records[:s.limit]

        # Project
        out = [tuple(_lookup_value(c, v) for c in cols) for v in joined_records]
        elapsed = (time.perf_counter() - start) * 1000
        return ResultSet(cols, out, stats={
            "rows_examined": examined, "rows_returned": len(out),
            "elapsed_ms": elapsed, "access_path": access_path})

    def _merge_joined_rows(self, s1: TableSchema, v1: dict, s2: TableSchema, v2: dict) -> dict:
        combined = {}
        for c, val in v1.items():
            combined[f"{s1.name}.{c}"] = val
            combined[c] = val
        for c, val in v2.items():
            combined[f"{s2.name}.{c}"] = val
            if c not in v1:
                combined[c] = val
            else:
                combined.pop(c, None)  # ambiguous unqualified
        return combined

    def _exec_ExplainStmt(self, s, session):
        """Explain the execution plan for a statement."""
        stmt = s.stmt
        if not isinstance(stmt, A.SelectStmt):
            return ResultSet(["step", "operator", "description"],
                             [(1, type(stmt).__name__, "Direct execution")])

        db = self._db(session)
        plan_rows = []
        step = 1

        if stmt.join is not None:
            plan_rows.append((step, "Join Scan",
                              f"Join tables '{stmt.table}' and '{stmt.join.table}' on {stmt.join.left_col} = {stmt.join.right_col}"))
            step += 1
        else:
            plan = self._extract_index_plan(db, stmt.table, stmt.where)
            if plan and plan[0] == "EQ":
                plan_rows.append((step, "Index Scan",
                                  f"Index Scan using '{plan[1]}' on {stmt.table}({plan[3]}) equality lookup"))
            elif plan and plan[0] == "RANGE":
                plan_rows.append((step, "Index Range Scan",
                                  f"B+ Tree Index Range Scan using '{plan[1]}' on {stmt.table}({plan[3]})"))
            else:
                plan_rows.append((step, "Table Scan", f"Full Table Scan on '{stmt.table}'"))
            step += 1

        if stmt.where:
            plan_rows.append((step, "Filter", f"Apply WHERE predicate"))
            step += 1
        if stmt.order_by:
            cols_str = ", ".join(f"{c} {d}" for c, d in stmt.order_by)
            plan_rows.append((step, "Sort", f"Sort by {cols_str}"))
            step += 1
        if stmt.limit is not None:
            plan_rows.append((step, "Limit", f"Limit to {stmt.limit} rows"))
            step += 1
        cols = "*" if stmt.columns is None else ", ".join(stmt.columns)
        plan_rows.append((step, "Projection", f"Project columns: {cols}"))

        return ResultSet(["step", "operator", "description"], plan_rows)
