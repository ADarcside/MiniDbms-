import unittest

from tests.base import EngineTestCase


class ExplainTests(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.seed()

    def test_explain_table_scan(self):
        res = self.sql("EXPLAIN SELECT name FROM students WHERE age > 18;")
        self.assertEqual(res.columns, ["step", "operator", "description"])
        ops = [row[1] for row in res.rows]
        self.assertIn("Table Scan", ops)
        self.assertIn("Filter", ops)
        self.assertIn("Projection", ops)

    def test_explain_index_scan(self):
        self.sql("CREATE INDEX idx_age ON students (age);")
        res = self.sql("EXPLAIN SELECT name FROM students WHERE age = 19;")
        ops = [row[1] for row in res.rows]
        self.assertIn("Index Scan", ops)
        self.assertIn("Projection", ops)

    def test_explain_btree_index_range_scan(self):
        self.sql("CREATE BTREE INDEX idx_btree_age ON students (age);")
        res = self.sql("EXPLAIN SELECT name FROM students WHERE age >= 18;")
        ops = [row[1] for row in res.rows]
        self.assertIn("Index Range Scan", ops)

    def test_explain_join(self):
        self.sql("CREATE TABLE grades (id INTEGER PRIMARY KEY, student_id INTEGER);")
        res = self.sql("EXPLAIN SELECT * FROM students JOIN grades ON students.id = grades.student_id;")
        ops = [row[1] for row in res.rows]
        self.assertIn("Join Scan", ops)


if __name__ == "__main__":
    unittest.main()
