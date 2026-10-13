import os

from minidb.errors import SemanticError
from tests.base import EngineTestCase


class CatalogTests(EngineTestCase):
    def test_database_lifecycle(self):
        self.sql("CREATE DATABASE college;")
        self.assertEqual(self.rows("SHOW DATABASES;"), [("college",)])
        with self.assertRaises(SemanticError):
            self.sql("CREATE DATABASE college;")  # duplicate
        self.sql("USE college;")
        self.sql("DROP DATABASE college;")
        self.assertEqual(self.rows("SHOW DATABASES;"), [])
        self.assertIsNone(self.engine.session.db)  # selection cleared on drop

    def test_invalid_and_missing_names(self):
        with self.assertRaises(SemanticError):
            self.sql("USE nope;")
        with self.assertRaises(SemanticError):
            self.sql("DROP DATABASE nope;")
        from minidb.catalog import validate_name
        for bad in ["../evil", "9abc", "a b", "", "x" * 65]:
            with self.assertRaises(SemanticError):
                validate_name(bad)

    def test_table_requires_selected_database(self):
        with self.assertRaises(SemanticError):
            self.sql("SHOW TABLES;")

    def test_table_lifecycle_and_describe(self):
        self.sql("CREATE DATABASE d; USE d;")
        self.sql("CREATE TABLE t (id INTEGER PRIMARY KEY, n TEXT NOT NULL, x FLOAT);")
        self.assertEqual(self.rows("SHOW TABLES;"), [("t",)])
        self.assertEqual(self.rows("DESCRIBE t;"),
                         [("id", "INTEGER", "NO", "PRI"), ("n", "TEXT", "NO", ""), ("x", "FLOAT", "YES", "")])
        with self.assertRaises(SemanticError):
            self.sql("CREATE TABLE t (id INTEGER);")
        self.sql("DROP TABLE t;")
        self.assertEqual(self.rows("SHOW TABLES;"), [])
        with self.assertRaises(SemanticError):
            self.sql("DROP TABLE t;")

    def test_schema_validation(self):
        self.sql("CREATE DATABASE d; USE d;")
        with self.assertRaises(SemanticError):
            self.sql("CREATE TABLE t (a INTEGER, a TEXT);")
        with self.assertRaises(SemanticError):
            self.sql("CREATE TABLE t (a INTEGER PRIMARY KEY, b INTEGER PRIMARY KEY);")

    def test_selection_is_session_state(self):
        self.sql("CREATE DATABASE a; CREATE DATABASE b;")
        s1, s2 = self.engine.new_session(), self.engine.new_session()
        self.engine.execute("USE a;", s1)
        self.engine.execute("USE b;", s2)
        self.assertEqual((s1.db, s2.db), ("a", "b"))
