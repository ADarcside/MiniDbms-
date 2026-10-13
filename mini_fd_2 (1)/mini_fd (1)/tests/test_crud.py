from tests.base import EngineTestCase


class CrudTests(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.seed()

    def test_select_where_projection(self):
        self.assertEqual(self.rows("SELECT name FROM students WHERE age > 18;"),
                         [("Asha",), ("Kiran",)])  # NULL age excluded

    def test_boolean_logic_and_null(self):
        self.assertEqual(self.rows("SELECT id FROM students WHERE age < 18 OR age > 20;"), [(2,), (4,)])
        self.assertEqual(self.rows("SELECT id FROM students WHERE age IS NULL;"), [(3,)])
        self.assertEqual(self.rows("SELECT id FROM students WHERE age IS NOT NULL AND NOT age = 19;"), [(2,), (4,)])
        self.assertEqual(self.rows("SELECT id FROM students WHERE age = NULL;"), [])  # unknown, never true
        self.assertEqual(self.rows("SELECT id FROM students WHERE (age > 18 OR age < 18) AND id != 4;"), [(1,), (2,)])

    def test_order_limit(self):
        self.assertEqual(self.rows("SELECT id FROM students ORDER BY age;"), [(3,), (2,), (1,), (4,)])  # NULL first
        self.assertEqual(self.rows("SELECT id FROM students ORDER BY age DESC LIMIT 2;"), [(4,), (1,)])
        self.assertEqual(self.rows("SELECT name FROM students ORDER BY name DESC LIMIT 1;"), [("Ravi",)])

    def test_update_delete_counts(self):
        r = self.sql("UPDATE students SET age = 30 WHERE age > 18;")
        self.assertEqual(r.stats["rows_affected"], 2)
        self.assertEqual(self.rows("SELECT age FROM students WHERE id = 4;"), [(30,)])
        r = self.sql("DELETE FROM students WHERE age IS NULL;")
        self.assertEqual(r.stats["rows_affected"], 1)
        self.assertEqual(len(self.rows("SELECT * FROM students;")), 3)
        self.sql("DELETE FROM students;")
        self.assertEqual(self.rows("SELECT * FROM students;"), [])

    def test_stats(self):
        r = self.sql("SELECT * FROM students WHERE age > 18;")
        self.assertEqual((r.stats["rows_examined"], r.stats["rows_returned"]), (4, 2))

    def test_syntax_error_runs_nothing(self):
        from minidb.errors import ParseError
        with self.assertRaises(ParseError):
            self.engine.execute_many("DELETE FROM students; SELEC oops;")
        self.assertEqual(len(self.rows("SELECT * FROM students;")), 4)
