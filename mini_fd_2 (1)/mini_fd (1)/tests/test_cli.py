import unittest

from minidb.cli import Shell, format_result
from minidb.executor import ResultSet
from tests.base import EngineTestCase


class CliTests(EngineTestCase):
    def run_shell(self, lines):
        feed, out = iter(lines), []

        def fake_input(prompt):
            try:
                return next(feed)
            except StopIteration:
                raise EOFError

        Shell(self.engine, fake_input, out.append).run()
        return "\n".join(out)

    def test_builtin_commands(self):
        out = self.run_shell(["help", "version", "exit"])
        self.assertIn("SQL (end each statement", out)
        self.assertIn("MiniDBMS 0.1.0", out)
        self.assertIn("Bye.", out)

    def test_multiline_sql_and_error_survival(self):
        out = self.run_shell([
            "CREATE DATABASE d;", "USE d;",
            "CREATE TABLE t (id INTEGER PRIMARY KEY,", "  name TEXT);",
            "INSERT INTO t VALUES (1,'a');", "INSERT INTO t VALUES (1,'b');",
            "SELEC broken;", "SELECT * FROM t;", "exit"])
        self.assertIn("Error: Duplicate primary key", out)
        self.assertIn("Error: Syntax error at line 1", out)
        self.assertIn("| 1  | a    |", out)  # session continued after errors
        self.assertIn("1 row(s)", out)

    def test_format_result(self):
        r = ResultSet(["a", "b"], [(None, True), (1.5, "x")])
        text = format_result(r)
        self.assertIn("NULL", text)
        self.assertIn("TRUE", text)
        self.assertTrue(text.endswith("2 row(s)"))


if __name__ == "__main__":
    unittest.main()
