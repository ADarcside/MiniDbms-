import unittest

from minidb import ast_nodes as A
from minidb.errors import LexError, ParseError
from minidb.lexer import tokenize
from minidb.parser import parse


class LexerTests(unittest.TestCase):
    def test_tokens_and_positions(self):
        toks = tokenize("SELECT name FROM students WHERE age > 18;")
        self.assertEqual([t.value for t in toks[:-1]],
                         ["SELECT", "name", "FROM", "students", "WHERE", "age", ">", 18, ";"])
        self.assertEqual((toks[1].line, toks[1].col), (1, 8))

    def test_string_escape_and_case(self):
        toks = tokenize("insert INTO T values ('it''s')")
        self.assertEqual(toks[1].value, "INTO")
        self.assertEqual(toks[2].value, "t")  # identifiers normalized to lowercase
        self.assertEqual(toks[5].value, "it's")

    def test_errors(self):
        with self.assertRaises(LexError):
            tokenize("SELECT 'oops")
        with self.assertRaises(LexError):
            tokenize("SELECT @")


class ParserTests(unittest.TestCase):
    def one(self, sql):
        stmts = parse(tokenize(sql))
        self.assertEqual(len(stmts), 1)
        return stmts[0]

    def test_select_ast(self):
        s = self.one("SELECT name FROM students WHERE age > 18;")
        self.assertEqual(s, A.SelectStmt(["name"], "students",
                         A.Comparison(A.Column("age"), ">", A.Literal(18))))

    def test_precedence_and_parens(self):
        s = self.one("SELECT * FROM t WHERE a = 1 OR b = 2 AND NOT c = 3")
        self.assertEqual(s.where.op, "OR")
        self.assertEqual(s.where.operands[1].op, "AND")
        s = self.one("SELECT * FROM t WHERE (a = 1 OR b = 2) AND c = 3")
        self.assertEqual(s.where.op, "AND")

    def test_is_null_order_limit(self):
        s = self.one("SELECT * FROM t WHERE a IS NOT NULL ORDER BY a DESC, b LIMIT 5")
        self.assertTrue(s.where.negated)
        self.assertEqual(s.order_by, [("a", "DESC"), ("b", "ASC")])
        self.assertEqual(s.limit, 5)

    def test_create_table_and_insert(self):
        s = self.one("CREATE TABLE t (id INT PRIMARY KEY, n TEXT NOT NULL)")
        self.assertTrue(s.columns[0].primary_key)
        self.assertEqual(s.columns[0].type, "INTEGER")
        s = self.one("INSERT INTO t (id, n) VALUES (1, 'a'), (-2, NULL)")
        self.assertEqual(s.rows[1][0], A.Literal(-2))

    def test_syntax_errors_have_location(self):
        for bad in ["SELEC * FROM t", "SELECT FROM t", "SELECT * FROM", "SELECT * FROM t WHERE",
                    "CREATE TABLE t (id)", "INSERT INTO t VALUES 1", "SELECT * FROM t garbage"]:
            with self.assertRaises(ParseError, msg=bad) as cm:
                parse(tokenize(bad))
            self.assertIn("line 1", str(cm.exception))

    def test_multiple_statements(self):
        self.assertEqual(len(parse(tokenize("USE a; SHOW TABLES;;"))), 2)


if __name__ == "__main__":
    unittest.main()
