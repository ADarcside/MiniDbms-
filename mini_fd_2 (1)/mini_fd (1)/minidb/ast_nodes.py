"""AST produced by the parser and consumed by validator/executor."""
from dataclasses import dataclass
from typing import Optional, Union


# ---- expressions ----
@dataclass
class Column:
    name: str


@dataclass
class Literal:
    value: object  # int, float, str, bool or None


@dataclass
class Comparison:
    left: "Expr"
    op: str  # = != < <= > >=
    right: "Expr"


@dataclass
class Logical:
    op: str  # AND, OR, NOT
    operands: list


@dataclass
class IsNull:
    column: Column
    negated: bool = False


Expr = Union[Column, Literal, Comparison, Logical, IsNull]


# ---- schema pieces ----
@dataclass
class ColumnDef:
    name: str
    type: str  # INTEGER, FLOAT, TEXT, BOOLEAN
    not_null: bool = False
    primary_key: bool = False


# ---- statements ----
@dataclass
class CreateDatabaseStmt:
    name: str


@dataclass
class DropDatabaseStmt:
    name: str


@dataclass
class UseStmt:
    name: str


@dataclass
class ShowDatabasesStmt:
    pass


@dataclass
class CreateTableStmt:
    table: str
    columns: list


@dataclass
class DropTableStmt:
    table: str


@dataclass
class ShowTablesStmt:
    pass


@dataclass
class DescribeStmt:
    table: str


@dataclass
class InsertStmt:
    table: str
    columns: Optional[list]  # None -> all columns in schema order
    rows: list  # list of list[Literal]


@dataclass
class JoinClause:
    table: str
    left_col: str
    right_col: str
    join_type: str = "INNER"


@dataclass
class SelectStmt:
    columns: Optional[list]  # None -> *
    table: str
    where: Optional[Expr] = None
    order_by: Optional[list] = None  # list of (column, "ASC"|"DESC")
    limit: Optional[int] = None
    join: Optional[JoinClause] = None


@dataclass
class UpdateStmt:
    table: str
    assignments: dict  # column -> Literal
    where: Optional[Expr] = None


@dataclass
class DeleteStmt:
    table: str
    where: Optional[Expr] = None


@dataclass
class CreateIndexStmt:
    name: str
    table: str
    column: str
    index_type: str = "HASH"  # HASH or BTREE


@dataclass
class DropIndexStmt:
    name: str


@dataclass
class BeginStmt:
    pass


@dataclass
class CommitStmt:
    pass


@dataclass
class RollbackStmt:
    pass


@dataclass
class ExplainStmt:
    stmt: object

