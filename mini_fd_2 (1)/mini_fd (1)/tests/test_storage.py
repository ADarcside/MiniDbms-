import json
import os

from minidb.engine import Engine
from minidb.errors import StorageError
from minidb.fileio import atomic_write_json, read_json
from tests.base import EngineTestCase


class StorageTests(EngineTestCase):
    def test_atomic_write_leaves_no_tmp(self):
        p = os.path.join(self.dir, "x.json")
        atomic_write_json(p, {"a": 1})
        self.assertEqual(read_json(p), {"a": 1})
        self.assertFalse(os.path.exists(p + ".tmp"))

    def test_interrupted_write_keeps_previous_data(self):
        self.seed()
        table = os.path.join(self.dir, "college", "tables", "students.json")
        with open(table + ".tmp", "w") as f:  # simulate crash mid-write
            f.write('{"version": 1, "rows": [garbage')
        engine2 = Engine(self.dir)  # startup recovery removes stray tmp
        self.assertFalse(os.path.exists(table + ".tmp"))
        engine2.execute("USE college;")
        self.assertEqual(len(engine2.execute("SELECT * FROM students;").rows), 4)

    def test_corrupt_file_raises_storage_error(self):
        self.seed()
        table = os.path.join(self.dir, "college", "tables", "students.json")
        with open(table, "w") as f:
            f.write("{not json")
        with self.assertRaises(StorageError):
            self.sql("SELECT * FROM students;")

    def test_rids_are_stable_and_never_reused(self):
        self.seed()
        self.sql("DELETE FROM students WHERE id = 4;")
        self.sql("INSERT INTO students VALUES (5,'Zed',30);")
        with open(os.path.join(self.dir, "college", "tables", "students.json")) as f:
            data = json.load(f)
        self.assertEqual([r["rid"] for r in data["rows"]], [1, 2, 3, 5])
        self.assertEqual(data["next_rid"], 6)
