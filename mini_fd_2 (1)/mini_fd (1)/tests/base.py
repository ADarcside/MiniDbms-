import tempfile
import unittest

from minidb.engine import Engine


class EngineTestCase(unittest.TestCase):
    """Fresh engine on a temporary directory; never touches real data/."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self.engine = Engine(self.dir)

    def sql(self, text):
        return self.engine.execute(text)

    def rows(self, text):
        return self.sql(text).rows

    def seed(self):
        self.sql("CREATE DATABASE college; USE college;")
        self.sql("CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT NOT NULL, age INTEGER);")
        self.sql("INSERT INTO students VALUES (1,'Asha',19),(2,'Ravi',17),(3,'Meena',NULL),(4,'Kiran',25);")
