import os
import unittest

from minidb.errors import SemanticError
from tests.base import EngineTestCase


class IndexTests(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.seed()

    def test_create_and_drop_hash_index(self):
        r = self.sql("CREATE INDEX idx_age ON students (age);")
        self.assertIn("idx_age", r.message)
        indexes = self.engine.catalog.list_indexes("college", "students")
        self.assertIn("idx_age", indexes)
        self.assertEqual(indexes["idx_age"]["type"], "HASH")

        # Dropping index
        r2 = self.sql("DROP INDEX idx_age;")
        self.assertIn("dropped", r2.message)
        self.assertNotIn("idx_age", self.engine.catalog.list_indexes("college", "students"))

    def test_create_and_drop_btree_index(self):
        r = self.sql("CREATE BTREE INDEX idx_btree_age ON students (age);")
        self.assertIn("idx_btree_age", r.message)
        indexes = self.engine.catalog.list_indexes("college", "students")
        self.assertIn("idx_btree_age", indexes)
        self.assertEqual(indexes["idx_btree_age"]["type"], "BTREE")

        self.sql("DROP INDEX idx_btree_age;")
        self.assertNotIn("idx_btree_age", self.engine.catalog.list_indexes("college", "students"))

    def test_hash_index_equality_lookup_and_stats(self):
        self.sql("CREATE INDEX idx_age ON students (age);")
        res = self.sql("SELECT name FROM students WHERE age = 19;")
        self.assertEqual(res.rows, [("Asha",)])
        self.assertEqual(res.stats["access_path"], "index scan (idx_age)")
        self.assertEqual(res.stats["rows_examined"], 1)

    def test_btree_index_range_scan(self):
        self.sql("CREATE BTREE INDEX idx_btree_age ON students (age);")
        res = self.sql("SELECT name FROM students WHERE age > 18 ORDER BY age ASC;")
        self.assertEqual(res.rows, [("Asha",), ("Kiran",)])
        self.assertIn("btree index range scan", res.stats["access_path"])
        self.assertEqual(res.stats["rows_examined"], 2)

    def test_index_maintenance_on_insert_update_delete(self):
        self.sql("CREATE INDEX idx_age ON students (age);")

        # Insert maintenance
        self.sql("INSERT INTO students VALUES (5, 'Zoe', 19);")
        res = self.sql("SELECT name FROM students WHERE age = 19 ORDER BY name;")
        self.assertEqual(res.rows, [("Asha",), ("Zoe",)])

        # Update maintenance
        self.sql("UPDATE students SET age = 20 WHERE name = 'Asha';")
        res_old = self.sql("SELECT name FROM students WHERE age = 19;")
        self.assertEqual(res_old.rows, [("Zoe",)])
        res_new = self.sql("SELECT name FROM students WHERE age = 20;")
        self.assertEqual(res_new.rows, [("Asha",)])

        # Delete maintenance
        self.sql("DELETE FROM students WHERE name = 'Zoe';")
        res_deleted = self.sql("SELECT name FROM students WHERE age = 19;")
        self.assertEqual(res_deleted.rows, [])

    def test_index_and_scan_results_identical_invariant(self):
        """Index Rule: Indexed and scan-based queries must return identical results."""
        # Query without index (full scan)
        scan_res = self.rows("SELECT id, name FROM students WHERE age = 17;")

        # Query with index
        self.sql("CREATE INDEX idx_age ON students (age);")
        idx_res = self.rows("SELECT id, name FROM students WHERE age = 17;")

        self.assertEqual(scan_res, idx_res)

    def test_invalid_index_operations(self):
        with self.assertRaises(SemanticError):
            self.sql("CREATE INDEX idx_bad ON students (ghost);")  # non-existent column
        with self.assertRaises(SemanticError):
            self.sql("CREATE INDEX idx_bad ON ghost (id);")       # non-existent table
        self.sql("CREATE INDEX idx_dup ON students (age);")
        with self.assertRaises(SemanticError):
            self.sql("CREATE INDEX idx_dup ON students (age);")   # duplicate index name
        with self.assertRaises(SemanticError):
            self.sql("DROP INDEX ghost_idx;")                     # non-existent index


if __name__ == "__main__":
    unittest.main()
