import json
import os
import unittest
from unittest.mock import patch

from minidb.engine import Engine
from minidb.errors import MiniDBError, SemanticError, StorageError
from tests.base import EngineTestCase


class TransactionTests(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.seed()

    def test_begin_and_commit(self):
        self.sql("BEGIN;")
        self.sql("INSERT INTO students VALUES (10, 'Alice', 22);")
        self.sql("UPDATE students SET age = 21 WHERE id = 1;")
        self.sql("COMMIT;")

        # Verify changes persist
        res = self.rows("SELECT id, age FROM students WHERE id = 1 OR id = 10 ORDER BY id;")
        self.assertEqual(res, [(1, 21), (10, 22)])
        self.assertEqual(self.rows("SELECT name FROM students WHERE id = 10;"), [("Alice",)])

    def test_begin_and_rollback(self):
        before = self.rows("SELECT * FROM students ORDER BY id;")
        self.sql("BEGIN;")
        self.sql("INSERT INTO students VALUES (20, 'Temp', 33);")
        self.sql("UPDATE students SET age = 99;")
        self.sql("DELETE FROM students WHERE id = 1;")
        self.sql("ROLLBACK;")

        after = self.rows("SELECT * FROM students ORDER BY id;")
        self.assertEqual(before, after)

    def test_rollback_restores_indexes(self):
        self.sql("CREATE INDEX idx_age ON students (age);")
        self.sql("BEGIN;")
        self.sql("INSERT INTO students VALUES (30, 'Bob', 19);")
        # In tx, index has Bob
        res_mid = self.sql("SELECT name FROM students WHERE age = 19 ORDER BY name;")
        self.assertEqual(res_mid.rows, [("Asha",), ("Bob",)])

        self.sql("ROLLBACK;")
        # After rollback, Bob is removed from both table and index
        res_after = self.sql("SELECT name FROM students WHERE age = 19;")
        self.assertEqual(res_after.rows, [("Asha",)])

    def test_create_table_rollback_defect1(self):
        """Defect 1 fix: CREATE TABLE inside tx followed by ROLLBACK removes table file and schema."""
        table_file = os.path.join(self.dir, "college", "tables", "t.json")
        self.sql("BEGIN;")
        self.sql("CREATE TABLE t (id INTEGER PRIMARY KEY);")
        self.assertTrue(os.path.isfile(table_file))
        self.sql("ROLLBACK;")

        # Table must not exist in catalog
        self.assertEqual(self.rows("SHOW TABLES;"), [("students",)])
        # Physical table file must not exist on disk
        self.assertFalse(os.path.isfile(table_file))
        # Re-creating table t should succeed cleanly
        self.sql("CREATE TABLE t (id INTEGER PRIMARY KEY);")
        self.assertIn("t", [r[0] for r in self.rows("SHOW TABLES;")])

    def test_drop_table_rollback_defect2(self):
        """Defect 2 fix: DROP TABLE inside tx followed by ROLLBACK restores table file, schema, and rows."""
        table_file = os.path.join(self.dir, "college", "tables", "students.json")
        self.sql("BEGIN;")
        self.sql("DROP TABLE students;")
        self.assertFalse(os.path.isfile(table_file))
        self.assertEqual(self.rows("SHOW TABLES;"), [])
        self.sql("ROLLBACK;")

        # Table and rows must be fully restored
        self.assertEqual(self.rows("SHOW TABLES;"), [("students",)])
        self.assertTrue(os.path.isfile(table_file))
        rows = self.rows("SELECT id, name FROM students ORDER BY id;")
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0], (1, "Asha"))

    def test_create_index_rollback_defect3(self):
        """Defect 3 fix: CREATE INDEX inside tx followed by ROLLBACK removes index file and catalog entry."""
        index_file = os.path.join(self.dir, "college", "indexes", "idx_new.json")
        self.sql("BEGIN;")
        self.sql("CREATE INDEX idx_new ON students (age);")
        self.assertTrue(os.path.isfile(index_file))
        self.sql("ROLLBACK;")

        self.assertFalse(os.path.isfile(index_file))
        indexes = self.engine.catalog.list_indexes("college", "students")
        self.assertNotIn("idx_new", indexes)

    def test_drop_index_rollback(self):
        """DROP INDEX inside tx followed by ROLLBACK restores index file and catalog entry."""
        self.sql("CREATE INDEX idx_age ON students (age);")
        index_file = os.path.join(self.dir, "college", "indexes", "idx_age.json")
        self.assertTrue(os.path.isfile(index_file))

        self.sql("BEGIN;")
        self.sql("DROP INDEX idx_age;")
        self.assertFalse(os.path.isfile(index_file))
        self.sql("ROLLBACK;")

        self.assertTrue(os.path.isfile(index_file))
        indexes = self.engine.catalog.list_indexes("college", "students")
        self.assertIn("idx_age", indexes)
        # Verify index scan works after rollback
        res = self.sql("SELECT name FROM students WHERE age = 19;")
        self.assertEqual(res.rows, [("Asha",)])
        self.assertEqual(res.stats["access_path"], "index scan (idx_age)")

    def test_ddl_rollback_survives_restart(self):
        """Verify that DDL rollback state persists cleanly after engine restart."""
        self.sql("BEGIN;")
        self.sql("CREATE TABLE t (id INTEGER PRIMARY KEY);")
        self.sql("ROLLBACK;")

        # Restart engine
        engine2 = Engine(self.dir)
        engine2.execute("USE college;")
        self.assertEqual(engine2.execute("SHOW TABLES;").rows, [("students",)])
        table_file = os.path.join(self.dir, "college", "tables", "t.json")
        self.assertFalse(os.path.isfile(table_file))

    def test_durable_commit_marker_not_undone_on_restart(self):
        """Verify that committed transaction marked COMMITTED is not undone by recovery."""
        self.sql("BEGIN;")
        self.sql("INSERT INTO students VALUES (77, 'CommittedUser', 28);")
        self.sql("COMMIT;")

        # Simulate leftover journal with COMMITTED status
        journal_path = os.path.join(self.dir, "college", ".journal.json")
        with open(journal_path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "status": "COMMITTED", "db": "college", "tables": {}}, f)

        engine2 = Engine(self.dir)
        engine2.execute("USE college;")
        res = engine2.execute("SELECT name FROM students WHERE id = 77;").rows
        self.assertEqual(res, [("CommittedUser",)])
        self.assertFalse(os.path.isfile(journal_path))

    def test_recovery_fails_safely_on_corrupt_journal(self):
        """Recovery must fail safely and raise StorageError if journal restoration fails."""
        journal_path = os.path.join(self.dir, "college", ".journal.json")
        with open(journal_path, "w", encoding="utf-8") as f:
            f.write("corrupted garbage json not parseable")

        with self.assertRaises(StorageError):
            Engine(self.dir)

        # Clean up corrupted file so cleanup fixtures pass
        try:
            os.unlink(journal_path)
        except OSError:
            pass

    def test_table_and_index_atomicity_on_injected_failure(self):
        """Verify multi-file atomicity: failure updating an index rolls back table changes."""
        self.sql("CREATE INDEX idx_age ON students (age);")
        before_rows = self.rows("SELECT * FROM students ORDER BY id;")

        # Inject failure into IndexManager.on_insert
        with patch.object(self.engine.indexes, "on_insert", side_effect=OSError("Disk write error")):
            with self.assertRaises(StorageError):
                self.sql("INSERT INTO students VALUES (99, 'FailedInsert', 25);")

        # Verify table file was rolled back and contains no partial insert!
        after_rows = self.rows("SELECT * FROM students ORDER BY id;")
        self.assertEqual(before_rows, after_rows)

    def test_crash_recovery_on_startup(self):
        """Simulate a crash mid-transaction before commit, test startup recovery."""
        before = self.rows("SELECT * FROM students ORDER BY id;")
        self.sql("BEGIN;")
        self.sql("INSERT INTO students VALUES (50, 'CrashRow', 40);")
        self.sql("DELETE FROM students WHERE id = 2;")

        # Simulate crash: old engine terminated, leaving uncommitted state on disk
        self.engine.close()
        engine2 = Engine(self.dir)
        engine2.execute("USE college;")
        after_restart = engine2.execute("SELECT * FROM students ORDER BY id;").rows

        self.assertEqual(before, after_restart)

    def test_transaction_errors(self):
        with self.assertRaises(SemanticError):
            self.sql("COMMIT;")  # no active tx
        with self.assertRaises(SemanticError):
            self.sql("ROLLBACK;")  # no active tx

        self.sql("BEGIN;")
        with self.assertRaises(SemanticError):
            self.sql("BEGIN;")  # already in tx
        self.sql("ROLLBACK;")

    def test_single_writer_lock_handling(self):
        self.sql("BEGIN;")
        # Verify lock file exists
        lock_file = os.path.join(self.dir, "college", ".lock")
        self.assertTrue(os.path.isfile(lock_file))

        # Attempting another concurrent transaction on same database raises error
        session2 = self.engine.new_session()
        session2.db = "college"
        with self.assertRaises(MiniDBError):
            self.engine.executor.transactions.begin("college", session2)

        self.sql("COMMIT;")
        self.assertFalse(os.path.isfile(lock_file))

    # ---- Crash-injection tests at persistence boundaries ----

    def test_begin_releases_lock_on_journal_write_failure(self):
        """HIGH: If writing the initial journal fails during BEGIN, the lock must be released."""
        lock_file = os.path.join(self.dir, "college", ".lock")
        session2 = self.engine.new_session()
        session2.db = "college"

        with patch("minidb.transactions.atomic_write_json", side_effect=OSError("Disk full")):
            with self.assertRaises(OSError):
                self.engine.executor.transactions.begin("college", session2)

        # Lock must NOT be left behind
        self.assertFalse(os.path.isfile(lock_file))
        # Session must NOT be marked in-transaction
        self.assertFalse(getattr(session2, "in_transaction", False))

        # Subsequent transaction must succeed
        self.sql("BEGIN;")
        self.sql("COMMIT;")

    def test_recovery_preserves_lock_and_tx_of_active_same_process_engine(self):
        """CRITICAL: Recovery must not remove a lock or roll back a tx of another active engine in the same process."""
        self.sql("BEGIN;")
        self.sql("INSERT INTO students VALUES (99, 'ActiveInProcess', 21);")
        lock_file = os.path.join(self.dir, "college", ".lock")
        journal_file = os.path.join(self.dir, "college", ".journal.json")
        self.assertTrue(os.path.isfile(lock_file))
        self.assertTrue(os.path.isfile(journal_file))

        # A second engine initialized in the same process must NOT interfere with active engine
        engine2 = Engine(self.dir)

        # Lock and active journal must be preserved
        self.assertTrue(os.path.isfile(lock_file))
        self.assertTrue(os.path.isfile(journal_file))

        # The active engine can continue its transaction and commit cleanly
        self.sql("COMMIT;")
        self.assertFalse(os.path.isfile(lock_file))
        self.assertFalse(os.path.isfile(journal_file))
        self.assertIn(("ActiveInProcess",), [r[1:2] for r in self.rows("SELECT * FROM students WHERE id = 99;")])

    def test_recovery_removes_abandoned_same_process_lock(self):
        """HIGH: Recovery removes an abandoned lock in the same process (no active engine holds it)."""
        lock_file = os.path.join(self.dir, "college", ".lock")
        os.makedirs(os.path.dirname(lock_file), exist_ok=True)
        # Lock file with our PID but not held by any active engine instance
        with open(lock_file, "w", encoding="utf-8") as f:
            f.write(f"pid={os.getpid()};time=0;owner=abandoned_no_active_engine\n")

        # Startup recovery should recognize that no active engine holds this lock and clean it up
        engine2 = Engine(self.dir)
        self.assertFalse(os.path.isfile(lock_file))

    def test_recovery_fails_safely_on_unreadable_lock_file(self):
        """HIGH: Recovery must fail safely and not delete unreadable or corrupt lock files."""
        lock_file = os.path.join(self.dir, "college", ".lock")
        os.makedirs(os.path.dirname(lock_file), exist_ok=True)
        with open(lock_file, "w", encoding="utf-8") as f:
            f.write("corrupted_unreadable_lock_data_without_pid\n")

        # Must fail safely with StorageError and NOT delete the lock file
        with self.assertRaises(StorageError):
            Engine(self.dir)

        self.assertTrue(os.path.isfile(lock_file))
        # Clean up for teardown
        try:
            os.unlink(lock_file)
        except OSError:
            pass

    def test_recovery_does_not_interfere_with_foreign_live_process_transaction(self):
        """CRITICAL: Recovery must not remove lock or roll back active journal of a foreign live process."""
        lock_file = os.path.join(self.dir, "college", ".lock")
        journal_file = os.path.join(self.dir, "college", ".journal.json")
        os.makedirs(os.path.dirname(lock_file), exist_ok=True)

        with open(lock_file, "w", encoding="utf-8") as f:
            f.write("pid=99999;time=0;owner=foreign_live\n")

        catalog_path = os.path.join(self.dir, "college", "catalog.json")
        with open(catalog_path, "r", encoding="utf-8") as f:
            cat = json.load(f)

        with open(journal_file, "w", encoding="utf-8") as f:
            json.dump({
                "version": 1,
                "status": "ACTIVE",
                "db": "college",
                "catalog": cat,
                "tables": {},
                "created_tables": ["foreign_active_table"],
                "created_indexes": [],
            }, f)

        from minidb.transactions import TransactionManager
        with patch.object(TransactionManager, "_is_process_alive", return_value=True):
            engine2 = Engine(self.dir)
            # Both lock and journal must be untouched by startup recovery
            self.assertTrue(os.path.isfile(lock_file))
            self.assertTrue(os.path.isfile(journal_file))

        # Clean up so tearDown succeeds
        try:
            os.unlink(lock_file)
            os.unlink(journal_file)
        except OSError:
            pass

    def test_recovery_removes_lock_owned_by_dead_process(self):
        """Recovery must remove a lock file when the owning process is dead."""
        lock_file = os.path.join(self.dir, "college", ".lock")
        os.makedirs(os.path.dirname(lock_file), exist_ok=True)
        with open(lock_file, "w", encoding="utf-8") as f:
            f.write("pid=99999;time=0;owner=dead\n")

        from minidb.transactions import TransactionManager
        with patch.object(TransactionManager, "_is_process_alive", return_value=False):
            engine2 = Engine(self.dir)
            # Lock from dead process must be cleaned up
            self.assertFalse(os.path.isfile(lock_file))

    def test_crash_between_journal_write_and_ddl_creates_table(self):
        """Simulate crash after journal records CREATE TABLE intent but before table file exists."""
        journal_path = os.path.join(self.dir, "college", ".journal.json")
        catalog_path = os.path.join(self.dir, "college", "catalog.json")

        # Read current catalog
        with open(catalog_path, "r", encoding="utf-8") as f:
            original_catalog = json.load(f)

        # Manually write a journal that says "t was created" but don't actually create the table
        journal_data = {
            "version": 1,
            "status": "ACTIVE",
            "db": "college",
            "catalog": original_catalog,
            "tables": {},
            "indexes": {},
            "created_tables": ["phantom_table"],
            "created_indexes": [],
        }
        with open(journal_path, "w", encoding="utf-8") as f:
            json.dump(journal_data, f)

        # Recovery should roll back: remove phantom_table entry from catalog, remove file if exists
        engine2 = Engine(self.dir)
        engine2.execute("USE college;")
        # phantom_table must not appear in catalog
        tables = [r[0] for r in engine2.execute("SHOW TABLES;").rows]
        self.assertNotIn("phantom_table", tables)
        self.assertFalse(os.path.isfile(journal_path))

    def test_crash_after_commit_marker_does_not_rollback(self):
        """Simulate crash after commit marker is written: recovery must NOT undo the transaction."""
        # Insert a row in a transaction
        self.sql("BEGIN;")
        self.sql("INSERT INTO students VALUES (88, 'DurableUser', 30);")
        self.sql("COMMIT;")

        # Now manually create a committed journal (simulating a crash after marker but before unlink)
        journal_path = os.path.join(self.dir, "college", ".journal.json")
        catalog_path = os.path.join(self.dir, "college", "catalog.json")
        with open(catalog_path, "r", encoding="utf-8") as f:
            old_catalog = json.load(f)
        # Committed journal: recovery should just delete it, NOT rollback
        committed_journal = {
            "version": 1,
            "status": "COMMITTED",
            "db": "college",
            "catalog": old_catalog,
            "tables": {},
            "indexes": {},
            "created_tables": [],
            "created_indexes": [],
        }
        with open(journal_path, "w", encoding="utf-8") as f:
            json.dump(committed_journal, f)

        engine2 = Engine(self.dir)
        engine2.execute("USE college;")
        res = engine2.execute("SELECT name FROM students WHERE id = 88;").rows
        self.assertEqual(res, [("DurableUser",)])
        self.assertFalse(os.path.isfile(journal_path))

    def test_crash_during_dml_inside_transaction_recovers_on_restart(self):
        """Simulate crash mid-transaction with mixed DML: recovery restores pre-tx state exactly."""
        before = self.rows("SELECT * FROM students ORDER BY id;")
        self.sql("BEGIN;")
        self.sql("INSERT INTO students VALUES (60, 'CrashA', 35);")
        self.sql("UPDATE students SET age = 100 WHERE id = 1;")
        self.sql("DELETE FROM students WHERE id = 4;")

        # Verify in-tx state is different
        mid_tx = self.rows("SELECT * FROM students ORDER BY id;")
        self.assertNotEqual(before, mid_tx)

        # Simulate crash (do not COMMIT or ROLLBACK) — restart engine
        self.engine.close()
        engine2 = Engine(self.dir)
        engine2.execute("USE college;")
        after_recovery = engine2.execute("SELECT * FROM students ORDER BY id;").rows
        self.assertEqual(before, after_recovery)

    def test_crash_during_journal_replacement(self):
        """Crash during journal replacement: leftover .tmp file is cleaned up and database safely recovered."""
        self.sql("BEGIN;")
        self.sql("INSERT INTO students VALUES (70, 'PreCrash', 27);")
        journal_path = os.path.join(self.dir, "college", ".journal.json")
        self.assertTrue(os.path.isfile(journal_path))

        # Simulate interrupted atomic write to journal that left a .tmp file
        tmp_journal = journal_path + ".tmp"
        with open(tmp_journal, "w", encoding="utf-8") as f:
            f.write("partial write in progress during crash")

        # Simulate crash of current engine
        self.engine.close()

        # Restart engine: cleanup_tmp removes .tmp file and recovery rolls back uncommitted tx
        engine2 = Engine(self.dir)
        self.assertFalse(os.path.isfile(tmp_journal))
        self.assertFalse(os.path.isfile(journal_path))

        engine2.execute("USE college;")
        rows = engine2.execute("SELECT name FROM students WHERE id = 70;").rows
        self.assertEqual(rows, [])

    def test_crash_during_physical_create_table_removes_orphaned_file(self):
        """Crash after physical table file creation during CREATE TABLE: recovery removes orphaned table file."""
        self.sql("BEGIN;")
        self.sql("CREATE TABLE phantom_t (id INTEGER PRIMARY KEY);")
        phantom_file = os.path.join(self.dir, "college", "tables", "phantom_t.json")
        self.assertTrue(os.path.isfile(phantom_file))

        # Simulate crash before commit
        self.engine.close()

        # On restart, recovery must remove orphaned table file and restore catalog
        engine2 = Engine(self.dir)
        engine2.execute("USE college;")
        self.assertNotIn("phantom_t", [r[0] for r in engine2.execute("SHOW TABLES;").rows])
        self.assertFalse(os.path.isfile(phantom_file))

    def test_crash_during_physical_drop_table_restores_data_and_indexes(self):
        """Crash after physical DROP TABLE before commit: recovery restores table file, rows, and indexes."""
        self.sql("CREATE INDEX idx_age ON students (age);")
        self.sql("BEGIN;")
        self.sql("DROP TABLE students;")
        tbl_file = os.path.join(self.dir, "college", "tables", "students.json")
        self.assertFalse(os.path.isfile(tbl_file))

        # Simulate crash before commit
        self.engine.close()

        # On restart, recovery must restore students table and its index
        engine2 = Engine(self.dir)
        engine2.execute("USE college;")
        self.assertTrue(os.path.isfile(tbl_file))
        rows = engine2.execute("SELECT id, name FROM students ORDER BY id;").rows
        self.assertEqual(len(rows), 4)
        # Verify index scan works
        res = engine2.execute("SELECT name FROM students WHERE age = 19;")
        self.assertEqual(res.rows, [("Asha",)])
        self.assertEqual(res.stats["access_path"], "index scan (idx_age)")

    def test_crash_during_physical_create_index_removes_orphaned_file(self):
        """Crash after physical index creation before commit: recovery removes orphaned index file."""
        self.sql("BEGIN;")
        self.sql("CREATE INDEX idx_crash ON students (name);")
        idx_file = os.path.join(self.dir, "college", "indexes", "idx_crash.json")
        self.assertTrue(os.path.isfile(idx_file))

        # Simulate crash before commit
        self.engine.close()

        # On restart, recovery removes orphaned index file and catalog entry
        engine2 = Engine(self.dir)
        engine2.execute("USE college;")
        self.assertFalse(os.path.isfile(idx_file))
        indexes = engine2.catalog.list_indexes("college", "students")
        self.assertNotIn("idx_crash", indexes)

    def test_crash_during_physical_drop_index_restores_index(self):
        """Crash after physical DROP INDEX before commit: recovery restores index file and scan ability."""
        self.sql("CREATE INDEX idx_age ON students (age);")
        self.sql("BEGIN;")
        self.sql("DROP INDEX idx_age;")
        idx_file = os.path.join(self.dir, "college", "indexes", "idx_age.json")
        self.assertFalse(os.path.isfile(idx_file))

        # Simulate crash before commit
        self.engine.close()

        # On restart, recovery restores index file and metadata
        engine2 = Engine(self.dir)
        engine2.execute("USE college;")
        self.assertTrue(os.path.isfile(idx_file))
        indexes = engine2.catalog.list_indexes("college", "students")
        self.assertIn("idx_age", indexes)

    def test_crash_during_commit_marker_persistence_rolls_back_on_restart(self):
        """Failure persisting the durable commit marker during COMMIT leaves tx uncommitted; recovery rolls back."""
        self.sql("BEGIN;")
        self.sql("INSERT INTO students VALUES (85, 'UncommittedDueToMarkerFailure', 33);")

        # Inject failure during atomic_write_json when COMMIT attempts to write marker
        with patch("minidb.transactions.atomic_write_json", side_effect=OSError("Disk write failed during commit marker")):
            with self.assertRaises(StorageError):
                self.sql("COMMIT;")

        # Journal is still ACTIVE on disk; simulate crash/restart
        self.engine.close()

        engine2 = Engine(self.dir)
        engine2.execute("USE college;")
        res = engine2.execute("SELECT name FROM students WHERE id = 85;").rows
        self.assertEqual(res, [])

    def test_crash_during_rollback_restoration_preserves_journal_for_retry(self):
        """Failure during rollback restoration preserves journal on disk so subsequent recovery can retry."""
        self.sql("BEGIN;")
        self.sql("INSERT INTO students VALUES (95, 'RollbackFail', 29);")
        journal_path = os.path.join(self.dir, "college", ".journal.json")

        # Inject failure during rollback restoration
        with patch.object(self.engine.storage, "save", side_effect=OSError("Disk error during rollback")):
            with self.assertRaises(StorageError):
                self.sql("ROLLBACK;")

        # Journal must NOT be deleted when rollback fails!
        self.assertTrue(os.path.isfile(journal_path))

        # Simulate crash of engine
        self.engine.close()

        # Subsequent engine restart successfully recovers using the preserved journal
        engine2 = Engine(self.dir)
        engine2.execute("USE college;")
        res = engine2.execute("SELECT name FROM students WHERE id = 95;").rows
        self.assertEqual(res, [])
        self.assertFalse(os.path.isfile(journal_path))

    def test_crash_during_recovery_itself_preserves_journal_and_recovers_later(self):
        """Failure during startup crash recovery raises StorageError, preserves journal, and recovers on next start."""
        self.sql("BEGIN;")
        self.sql("INSERT INTO students VALUES (96, 'RecoveryFail', 31);")
        journal_path = os.path.join(self.dir, "college", ".journal.json")

        # Simulate crash of active engine
        self.engine.close()

        # First restart attempts recovery, but storage.save fails
        from minidb.storage import StorageManager
        with patch.object(StorageManager, "save", side_effect=OSError("Disk full during recovery")):
            with self.assertRaises(StorageError):
                Engine(self.dir)

        # Journal must be preserved!
        self.assertTrue(os.path.isfile(journal_path))

        # Once condition clears, next startup recovers cleanly
        engine3 = Engine(self.dir)
        engine3.execute("USE college;")
        res = engine3.execute("SELECT name FROM students WHERE id = 96;").rows
        self.assertEqual(res, [])
        self.assertFalse(os.path.isfile(journal_path))

    def test_corrupt_journal_file_raises_storage_error(self):
        """Corrupt journal file must raise StorageError during operations instead of being ignored."""
        journal_path = os.path.join(self.dir, "college", ".journal.json")
        self.sql("BEGIN;")
        with open(journal_path, "w", encoding="utf-8") as f:
            f.write("corrupted json data {{{")

        with self.assertRaises(StorageError):
            self.sql("INSERT INTO students VALUES (99, 'CorruptJournal', 30);")

    def test_recovery_respects_flock_held_by_live_process(self):
        """Recovery cannot acquire or delete a lock file currently held under fcntl flock by a live process."""
        lock_file = os.path.join(self.dir, "college", ".lock")
        os.makedirs(os.path.dirname(lock_file), exist_ok=True)

        import fcntl
        fd = os.open(lock_file, os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.write(fd, b"pid=99999;time=0;owner=active\n")

        from minidb.transactions import TransactionManager

        with patch.object(TransactionManager, "_is_process_alive", return_value=False):
            engine2 = Engine(self.dir)

        # Lock file MUST remain untouched while flock is held
        self.assertTrue(os.path.isfile(lock_file))

        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    def test_safe_unlink_locked_file_prevents_unlinking_replaced_inode(self):
        """_safe_unlink_locked_file must verify device and inode and refuse to unlink a replaced file."""
        from pathlib import Path
        from minidb.transactions import TransactionManager

        test_lock = Path(self.dir) / "college" / ".lock_test"
        test_lock.write_text("initial_inode\n", encoding="utf-8")

        fd_old = os.open(str(test_lock), os.O_RDWR)

        # Replace the file on disk with a new inode (different file)
        test_lock.unlink()
        test_lock.write_text("new_inode\n", encoding="utf-8")

        # Calling _safe_unlink_locked_file with the old fd must NOT unlink the new file!
        result = TransactionManager._safe_unlink_locked_file(test_lock, fd_old)
        self.assertFalse(result)
        self.assertTrue(test_lock.is_file())
        self.assertEqual(test_lock.read_text(encoding="utf-8"), "new_inode\n")

        os.close(fd_old)
        test_lock.unlink()

    def test_guard_serializes_unlink_against_replacement(self):
        """While one participant holds the guard, a competing unlink/replace must wait,
        so verify-then-unlink cannot interleave with a replacement."""
        import threading
        import time
        from pathlib import Path
        from minidb.transactions import TransactionManager

        lock = Path(self.dir) / "college" / ".lock_guard_test"
        lock.write_text("owner\n", encoding="utf-8")
        fd = os.open(str(lock), os.O_RDWR)
        finished = threading.Event()

        def competitor():
            TransactionManager._safe_unlink_locked_file(lock, fd)
            finished.set()

        try:
            with TransactionManager._lock_guard(lock):
                t = threading.Thread(target=competitor)
                t.start()
                time.sleep(0.2)
                # Competitor is blocked on the guard: nothing was unlinked.
                self.assertFalse(finished.is_set())
                self.assertTrue(lock.exists())
                # Guard holder swaps the file; competitor must then see a different inode.
                lock.unlink()
                lock.write_text("replacement\n", encoding="utf-8")
            t.join(5)
            self.assertTrue(finished.is_set())
            self.assertEqual(lock.read_text(encoding="utf-8"), "replacement\n")
        finally:
            os.close(fd)
            if lock.exists():
                lock.unlink()

    def test_concurrent_processes_never_share_lock_with_stale_cleanup(self):
        """Many processes race to acquire/release (some starting from a stale lock);
        at most one may ever hold the lock, and none may unlink another's lock."""
        import subprocess
        import sys
        import textwrap

        db_dir = os.path.join(self.dir, "college")
        lock_file = os.path.join(db_dir, ".lock")
        marker = os.path.join(db_dir, "holders.txt")
        with open(lock_file, "w", encoding="utf-8") as f:
            f.write("pid=2999999;time=0;owner=dead\n")  # stale lock from a dead pid

        worker = textwrap.dedent("""
            import os, sys
            sys.path.insert(0, sys.argv[1])
            from minidb.transactions import TransactionManager
            from minidb.errors import MiniDBError
            tm = TransactionManager(sys.argv[2])
            marker = sys.argv[3]
            violations = 0
            for _ in range(150):
                try:
                    tm.acquire_lock("college")
                except MiniDBError:
                    continue
                fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)  # fails if another holder
                os.close(fd)
                os.unlink(marker)
                if not os.path.isfile(os.path.join(sys.argv[2], "college", ".lock")):
                    violations += 1
                tm.release_lock("college")
            print(violations)
        """)
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        procs = [
            subprocess.Popen([sys.executable, "-I", "-c", worker, root, self.dir, marker],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for _ in range(6)
        ]
        for p in procs:
            out, err = p.communicate(timeout=60)
            self.assertEqual(p.returncode, 0, err)  # O_EXCL on marker failing => double holder
            self.assertEqual(out.strip(), "0")


if __name__ == "__main__":
    unittest.main()


