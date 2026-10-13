from minidb.errors import ConstraintError, SemanticError
from tests.base import EngineTestCase


class ConstraintTests(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.seed()

    def snapshot(self):
        return self.rows("SELECT * FROM students ORDER BY id;")

    def test_duplicate_pk_insert_leaves_data_unchanged(self):
        before = self.snapshot()
        with self.assertRaises(ConstraintError):
            self.sql("INSERT INTO students VALUES (1,'Dup',20);")
        self.assertEqual(self.snapshot(), before)

    def test_batch_insert_is_atomic(self):
        before = self.snapshot()
        with self.assertRaises(ConstraintError):
            self.sql("INSERT INTO students VALUES (10,'ok',1),(1,'dup',2);")
        self.assertEqual(self.snapshot(), before)
        with self.assertRaises(ConstraintError):
            self.sql("INSERT INTO students VALUES (10,'ok',1),(10,'same',2);")
        self.assertEqual(self.snapshot(), before)

    def test_update_cannot_bypass_pk(self):
        before = self.snapshot()
        with self.assertRaises(ConstraintError):
            self.sql("UPDATE students SET id = 1 WHERE id = 2;")
        with self.assertRaises(ConstraintError):
            self.sql("UPDATE students SET id = 99;")  # would duplicate across rows
        with self.assertRaises(ConstraintError):
            self.sql("UPDATE students SET id = NULL WHERE id = 2;")
        self.assertEqual(self.snapshot(), before)

    def test_not_null(self):
        with self.assertRaises(ConstraintError):
            self.sql("INSERT INTO students (id) VALUES (9);")  # name missing
        with self.assertRaises(ConstraintError):
            self.sql("INSERT INTO students VALUES (9, NULL, 1);")
        with self.assertRaises(ConstraintError):
            self.sql("UPDATE students SET name = NULL;")

    def test_types(self):
        for bad in ["INSERT INTO students VALUES ('x','n',1);",
                    "INSERT INTO students VALUES (9,5,1);",
                    "INSERT INTO students VALUES (9,'n',1.5);",
                    "INSERT INTO students VALUES (9,'n',TRUE);"]:
            with self.assertRaises(ConstraintError, msg=bad):
                self.sql(bad)
        self.sql("CREATE TABLE m (f FLOAT, b BOOLEAN);")
        self.sql("INSERT INTO m VALUES (3, TRUE);")  # INTEGER -> FLOAT allowed
        self.assertEqual(self.rows("SELECT * FROM m;"), [(3.0, True)])

    def test_unknown_columns_and_tables(self):
        for bad in ["INSERT INTO students (nope) VALUES (1);", "SELECT nope FROM students;",
                    "UPDATE students SET nope = 1;", "SELECT * FROM students WHERE nope = 1;",
                    "SELECT * FROM students ORDER BY nope;", "SELECT * FROM ghost;",
                    "INSERT INTO students VALUES (1,'a');",
                    "SELECT * FROM students WHERE age = 'x';"]:
            with self.assertRaises(SemanticError, msg=bad):
                self.sql(bad)
