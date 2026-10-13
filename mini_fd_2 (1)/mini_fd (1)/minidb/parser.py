"""Recursive-descent parser: list[Token] -> list[Statement] (AST)."""
from . import ast_nodes as A
from .errors import ParseError, SemanticError

COMPARISON_OPS = {"=", "!=", "<", "<=", ">", ">="}
TYPE_KEYWORDS = {"INTEGER": "INTEGER", "INT": "INTEGER", "FLOAT": "FLOAT",
                 "TEXT": "TEXT", "BOOLEAN": "BOOLEAN"}


def parse(tokens):
    return Parser(tokens).parse_script()


class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    # ---- helpers ----
    def peek(self):
        return self.tokens[self.pos]

    def advance(self):
        tok = self.tokens[self.pos]
        if tok.kind != "EOF":
            self.pos += 1
        return tok

    def at_kw(self, *kws):
        t = self.peek()
        return t.kind == "KEYWORD" and t.value in kws

    def match_kw(self, *kws):
        return self.advance() if self.at_kw(*kws) else None

    def expect_kw(self, kw):
        if not self.at_kw(kw):
            self.error(kw)
        return self.advance()

    def at_punct(self, p):
        t = self.peek()
        return t.kind == "PUNCT" and t.value == p

    def match_punct(self, p):
        return self.advance() if self.at_punct(p) else None

    def expect_punct(self, p):
        if not self.at_punct(p):
            self.error(repr(p))
        return self.advance()

    def expect_ident(self, what="a name"):
        if self.peek().kind != "IDENT":
            self.error(what)
        return self.advance().value

    def expect_column_name(self, what="a column name"):
        name = self.expect_ident(what)
        if self.match_punct("."):
            col = self.expect_ident("a column name")
            return f"{name}.{col}"
        return name

    def error(self, expected):
        t = self.peek()
        found = "end of input" if t.kind == "EOF" else repr(t.value)
        raise ParseError(f"Syntax error at line {t.line}, column {t.col}: "
                         f"expected {expected}, found {found}")

    # ---- script / statements ----
    def parse_script(self):
        stmts = []
        while True:
            while self.match_punct(";"):
                pass
            if self.peek().kind == "EOF":
                return stmts
            stmts.append(self.statement())
            if self.peek().kind != "EOF" and not self.at_punct(";"):
                self.error("';' or end of input")

    def statement(self):
        t = self.peek()
        if t.kind != "KEYWORD":
            self.error("a statement")
        kw = t.value
        if kw == "CREATE":
            self.advance()
            if self.match_kw("DATABASE"):
                return A.CreateDatabaseStmt(self.expect_ident("a database name"))
            if self.match_kw("TABLE"):
                return self.create_table()
            if self.match_kw("INDEX"):
                return self.create_index("HASH")
            if self.match_kw("HASH"):
                self.expect_kw("INDEX")
                return self.create_index("HASH")
            if self.match_kw("BTREE"):
                self.expect_kw("INDEX")
                return self.create_index("BTREE")
            self.error("DATABASE, TABLE, or INDEX")
        if kw == "DROP":
            self.advance()
            if self.match_kw("DATABASE"):
                return A.DropDatabaseStmt(self.expect_ident("a database name"))
            if self.match_kw("TABLE"):
                return A.DropTableStmt(self.expect_ident("a table name"))
            if self.match_kw("INDEX"):
                return A.DropIndexStmt(self.expect_ident("an index name"))
            self.error("DATABASE, TABLE, or INDEX")
        if kw == "SHOW":
            self.advance()
            if self.match_kw("DATABASES"):
                return A.ShowDatabasesStmt()
            if self.match_kw("TABLES"):
                return A.ShowTablesStmt()
            self.error("DATABASES or TABLES")
        if kw == "USE":
            self.advance()
            return A.UseStmt(self.expect_ident("a database name"))
        if kw == "DESCRIBE":
            self.advance()
            return A.DescribeStmt(self.expect_ident("a table name"))
        if kw == "INSERT":
            return self.insert()
        if kw == "SELECT":
            return self.select()
        if kw == "UPDATE":
            return self.update()
        if kw == "DELETE":
            return self.delete()
        if kw == "BEGIN":
            self.advance()
            self.match_kw("TRANSACTION")
            return A.BeginStmt()
        if kw == "COMMIT":
            self.advance()
            self.match_kw("TRANSACTION")
            return A.CommitStmt()
        if kw == "ROLLBACK":
            self.advance()
            self.match_kw("TRANSACTION")
            return A.RollbackStmt()
        if kw == "EXPLAIN":
            self.advance()
            return A.ExplainStmt(self.statement())
        self.error("a statement")

    def create_index(self, default_type="HASH"):
        name = self.expect_ident("an index name")
        self.expect_kw("ON")
        table = self.expect_ident("a table name")
        self.expect_punct("(")
        column = self.expect_ident("a column name")
        self.expect_punct(")")
        return A.CreateIndexStmt(name, table, column, default_type)


    def create_table(self):
        table = self.expect_ident("a table name")
        self.expect_punct("(")
        cols = [self.column_def()]
        while self.match_punct(","):
            cols.append(self.column_def())
        self.expect_punct(")")
        return A.CreateTableStmt(table, cols)

    def column_def(self):
        name = self.expect_ident("a column name")
        t = self.peek()
        if not (t.kind == "KEYWORD" and t.value in TYPE_KEYWORDS):
            self.error("a column type (INTEGER, FLOAT, TEXT, BOOLEAN)")
        col = A.ColumnDef(name, TYPE_KEYWORDS[self.advance().value])
        while True:
            if self.match_kw("PRIMARY"):
                self.expect_kw("KEY")
                col.primary_key = True
            elif self.match_kw("NOT"):
                self.expect_kw("NULL")
                col.not_null = True
            else:
                return col

    def insert(self):
        self.expect_kw("INSERT")
        self.expect_kw("INTO")
        table = self.expect_ident("a table name")
        columns = None
        if self.match_punct("("):
            columns = [self.expect_ident("a column name")]
            while self.match_punct(","):
                columns.append(self.expect_ident("a column name"))
            self.expect_punct(")")
        self.expect_kw("VALUES")
        rows = [self.value_tuple()]
        while self.match_punct(","):
            rows.append(self.value_tuple())
        return A.InsertStmt(table, columns, rows)

    def value_tuple(self):
        self.expect_punct("(")
        vals = [self.literal()]
        while self.match_punct(","):
            vals.append(self.literal())
        self.expect_punct(")")
        return vals

    def select(self):
        self.expect_kw("SELECT")
        if self.match_punct("*"):
            columns = None
        else:
            columns = [self.expect_column_name("a column name or '*'")]
            while self.match_punct(","):
                columns.append(self.expect_column_name("a column name"))
        self.expect_kw("FROM")
        table = self.expect_ident("a table name")
        join = None
        if self.at_kw("JOIN", "INNER"):
            if self.match_kw("INNER"):
                self.expect_kw("JOIN")
            else:
                self.expect_kw("JOIN")
            join_table = self.expect_ident("a table name")
            self.expect_kw("ON")
            left_col = self.expect_column_name("a column name")
            t = self.peek()
            if not (t.kind == "OP" and t.value == "="):
                self.error("'='")
            self.advance()
            right_col = self.expect_column_name("a column name")
            join = A.JoinClause(join_table, left_col, right_col)
        where = self.expr() if self.match_kw("WHERE") else None
        order_by = None
        if self.match_kw("ORDER"):
            self.expect_kw("BY")
            order_by = [self.order_item()]
            while self.match_punct(","):
                order_by.append(self.order_item())
        limit = None
        if self.match_kw("LIMIT"):
            t = self.peek()
            if t.kind != "INT":
                self.error("an integer")
            limit = self.advance().value
        return A.SelectStmt(columns, table, where, order_by, limit, join)

    def order_item(self):
        col = self.expect_column_name("a column name")
        direction = "ASC"
        if self.match_kw("DESC"):
            direction = "DESC"
        else:
            self.match_kw("ASC")
        return (col, direction)


    def update(self):
        self.expect_kw("UPDATE")
        table = self.expect_ident("a table name")
        self.expect_kw("SET")
        assignments = {}
        while True:
            col = self.expect_ident("a column name")
            t = self.peek()
            if not (t.kind == "OP" and t.value == "="):
                self.error("'='")
            self.advance()
            if col in assignments:
                raise SemanticError(f"Column '{col}' assigned more than once")
            assignments[col] = self.literal()
            if not self.match_punct(","):
                break
        where = self.expr() if self.match_kw("WHERE") else None
        return A.UpdateStmt(table, assignments, where)

    def delete(self):
        self.expect_kw("DELETE")
        self.expect_kw("FROM")
        table = self.expect_ident("a table name")
        where = self.expr() if self.match_kw("WHERE") else None
        return A.DeleteStmt(table, where)

    # ---- expressions: OR < AND < NOT < comparison < primary ----
    def expr(self):
        operands = [self.and_expr()]
        while self.match_kw("OR"):
            operands.append(self.and_expr())
        return operands[0] if len(operands) == 1 else A.Logical("OR", operands)

    def and_expr(self):
        operands = [self.not_expr()]
        while self.match_kw("AND"):
            operands.append(self.not_expr())
        return operands[0] if len(operands) == 1 else A.Logical("AND", operands)

    def not_expr(self):
        if self.match_kw("NOT"):
            return A.Logical("NOT", [self.not_expr()])
        return self.comparison()

    def comparison(self):
        left = self.primary()
        if self.at_kw("IS"):
            self.advance()
            negated = bool(self.match_kw("NOT"))
            self.expect_kw("NULL")
            if not isinstance(left, A.Column):
                self.error("a column before IS [NOT] NULL")
            return A.IsNull(left, negated)
        t = self.peek()
        if t.kind == "OP" and t.value in COMPARISON_OPS:
            self.advance()
            return A.Comparison(left, t.value, self.primary())
        return left

    def primary(self):
        t = self.peek()
        if t.kind == "IDENT":
            col_name = self.expect_column_name()
            return A.Column(col_name)
        if self.match_punct("("):
            e = self.expr()
            self.expect_punct(")")
            return e
        return self.literal()

    def literal(self):
        t = self.peek()
        if t.kind == "OP" and t.value == "-":
            self.advance()
            n = self.peek()
            if n.kind not in ("INT", "FLOAT"):
                self.error("a number after '-'")
            self.advance()
            return A.Literal(-n.value)
        if t.kind in ("INT", "FLOAT", "STRING"):
            self.advance()
            return A.Literal(t.value)
        if self.match_kw("TRUE"):
            return A.Literal(True)
        if self.match_kw("FALSE"):
            return A.Literal(False)
        if self.match_kw("NULL"):
            return A.Literal(None)
        self.error("a value or column")
