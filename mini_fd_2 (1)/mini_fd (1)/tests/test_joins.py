import unittest

from minidb.errors import SemanticError
from tests.base import EngineTestCase


class JoinTests(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.seed()
        self.sql("CREATE TABLE grades (id INTEGER PRIMARY KEY, student_id INTEGER, course TEXT, score INTEGER);")
        self.sql("""INSERT INTO grades VALUES
            (10, 1, 'Math', 95),
            (20, 1, 'CS', 90),
            (30, 2, 'Math', 80),
            (40, 4, 'Physics', 88);""")

    def test_basic_nested_loop_join(self):
        res = self.sql("SELECT students.name, grades.course, grades.score FROM students JOIN grades ON students.id = grades.student_id ORDER BY grades.score DESC;")
        self.assertEqual(res.rows, [
            ("Asha", "Math", 95),
            ("Asha", "CS", 90),
            ("Kiran", "Physics", 88),
            ("Ravi", "Math", 80),
        ])
        self.assertEqual(res.stats["access_path"], "nested-loop join")

    def test_join_with_where_filter(self):
        res = self.sql("SELECT students.name, grades.course FROM students JOIN grades ON students.id = grades.student_id WHERE grades.score > 85 ORDER BY students.name;")
        self.assertEqual(res.rows, [
            ("Asha", "Math"),
            ("Asha", "CS"),
            ("Kiran", "Physics"),
        ])

    def test_join_with_star_projection(self):
        res = self.sql("SELECT * FROM students JOIN grades ON students.id = grades.student_id WHERE students.id = 2;")
        self.assertEqual(len(res.rows), 1)
        # Check that both student columns and grade columns are present
        self.assertIn("students.name", res.columns)
        self.assertIn("grades.course", res.columns)

    def test_index_nested_loop_join(self):
        # Create an index on the inner table join column
        self.sql("CREATE INDEX idx_student_id ON grades (student_id);")
        res = self.sql("SELECT students.name, grades.course FROM students JOIN grades ON students.id = grades.student_id WHERE students.id = 1 ORDER BY grades.course;")
        self.assertEqual(res.rows, [
            ("Asha", "CS"),
            ("Asha", "Math"),
        ])
        self.assertIn("index nested-loop join", res.stats["access_path"])

    def test_join_errors(self):
        with self.assertRaises(SemanticError):
            self.sql("SELECT * FROM students JOIN ghost ON students.id = ghost.id;")
        with self.assertRaises(SemanticError):
            self.sql("SELECT * FROM students JOIN grades ON students.ghost = grades.id;")


if __name__ == "__main__":
    unittest.main()
