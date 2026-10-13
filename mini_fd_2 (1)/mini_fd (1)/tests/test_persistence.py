from minidb.engine import Engine
from tests.base import EngineTestCase


class PersistenceTests(EngineTestCase):
    def test_everything_survives_restart(self):
        self.seed()
        self.sql("UPDATE students SET age = 20 WHERE id = 2;")
        self.sql("DELETE FROM students WHERE id = 3;")
        e2 = Engine(self.dir)  # simulated restart: brand new engine and session
        self.assertEqual(e2.execute("SHOW DATABASES;").rows, [("college",)])
        self.assertIsNone(e2.session.db)  # selection is session-only
        e2.execute("USE college;")
        self.assertEqual(e2.execute("DESCRIBE students;").rows[0], ("id", "INTEGER", "NO", "PRI"))
        self.assertEqual(e2.execute("SELECT id, age FROM students ORDER BY id;").rows,
                         [(1, 19), (2, 20), (4, 25)])
        with self.assertRaises(Exception):
            e2.execute("INSERT INTO students VALUES (1,'dup',1);")  # PK still enforced after restart

    def test_drop_persists(self):
        self.seed()
        self.sql("DROP TABLE students;")
        e2 = Engine(self.dir)
        e2.execute("USE college;")
        self.assertEqual(e2.execute("SHOW TABLES;").rows, [])
