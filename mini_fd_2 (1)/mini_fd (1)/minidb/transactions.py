"""Transaction manager: commit, rollback, recovery, and single-writer lock.

Coordinates multi-statement transactions using a write-ahead undo log (journal),
durable commit markers, DDL/DML snapshot rollback, atomic locking, and crash recovery.
"""
import os
import time
import weakref
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

try:
    import fcntl
except ImportError:
    fcntl = None

from .catalog import validate_name
from .errors import MiniDBError, SemanticError, StorageError
from .fileio import atomic_write_json, read_json


class TransactionManager:
    """Manages transaction lifecycle (begin, commit, rollback), locks, and crash recovery."""

    # Tracks active TransactionManager instances within this process using weak references.
    _ACTIVE_MANAGERS: weakref.WeakValueDictionary = weakref.WeakValueDictionary()
    # Tracks locks currently held by active TransactionManager instances within this process.
    _HELD_LOCKS: dict[Path, str] = {}

    def __init__(self, data_dir: str):
        self.root = Path(data_dir)
        self.manager_id = f"tm_{id(self)}_{time.time()}"
        TransactionManager._ACTIVE_MANAGERS[self.manager_id] = self
        self._lock_fds: dict[str, int] = {}

    def close(self) -> None:
        """Close this transaction manager and release in-memory tracking without modifying disk."""
        TransactionManager._ACTIVE_MANAGERS.pop(self.manager_id, None)
        to_remove = [p for p, owner in list(TransactionManager._HELD_LOCKS.items()) if owner == self.manager_id]
        for p in to_remove:
            TransactionManager._HELD_LOCKS.pop(p, None)
        for db, fd in list(self._lock_fds.items()):
            if fcntl:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                os.close(fd)
            except OSError:
                pass
        self._lock_fds.clear()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _db_dir(self, db: str) -> Path:
        return self.root / validate_name(db, "database name")

    def _lock_file(self, db: str) -> Path:
        return self._db_dir(db) / ".lock"

    def _journal_file(self, db: str) -> Path:
        return self._db_dir(db) / ".journal.json"

    @staticmethod
    def _read_lock_pid(lock_path: Path) -> Optional[int]:
        """Read the PID from a lock file. Returns None if unreadable."""
        info = TransactionManager._read_lock_info(lock_path)
        return info["pid"] if info else None

    @staticmethod
    def _read_lock_info(lock_path: Path) -> Optional[dict]:
        """Read metadata (pid, owner, time) from a lock file.
        Returns None if missing, unreadable, or missing a valid positive pid."""
        try:
            content = lock_path.read_text(encoding="utf-8").strip()
            if not content:
                return None
            info = {}
            for part in content.split(";"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    info[k.strip()] = v.strip()
            if "pid" not in info:
                return None
            pid = int(info["pid"])
            if pid <= 0:
                return None
            info["pid"] = pid
            return info
        except (OSError, ValueError):
            return None

    @staticmethod
    def _is_process_alive(pid: int) -> bool:
        """Check whether a process with the given PID is still running."""
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)  # signal 0: existence check, no actual signal sent
            return True
        except ProcessLookupError:
            return False  # no such process
        except PermissionError:
            return True  # process exists but we lack permission
        except OSError:
            return False

    @classmethod
    def _is_lock_held_in_process(cls, lock_path: Path) -> bool:
        """Check whether lock is held by an active engine instance in the current process."""
        try:
            resolved = lock_path.resolve()
            owner = cls._HELD_LOCKS.get(resolved)
            if not owner:
                return False
            # Check if the manager that acquired this lock is still alive in memory
            if owner not in cls._ACTIVE_MANAGERS:
                cls._HELD_LOCKS.pop(resolved, None)
                return False
            return lock_path.is_file()
        except OSError:
            return False

    def _is_lock_stale(self, lock_path: Path) -> bool:
        """Return True if the lock file is stale (owning process is dead or abandoned in same process).
        Unreadable lock files are NOT treated as stale (fail-safe)."""
        info = self._read_lock_info(lock_path)
        if info is None:
            return False  # unreadable lock is treated as unknown/corrupt, NOT stale
        pid = info["pid"]
        if pid == os.getpid():
            # In the same process: only stale if NOT held by an active engine/transaction manager
            return not self._is_lock_held_in_process(lock_path)
        return not self._is_process_alive(pid)

    @staticmethod
    @contextmanager
    def _lock_guard(lock_path: Path):
        """Hold the per-database guard mutex for the duration of the block.

        Ownership protocol for the ``.lock`` file
        -----------------------------------------
        ``<db>/.lock.guard`` is a permanent file that is never unlinked.  An
        exclusive blocking ``flock`` on it is a short-lived mutex that every
        participant must hold while it creates, replaces, inspects-for-removal
        or unlinks ``.lock``.  Because nobody can create or replace ``.lock``
        without holding the guard, the pathname cannot change between "verify
        this is the file I own" and "unlink it": the check and the unlink are
        one critical section, not two racing syscalls.

        Lock ordering is always guard first, then the non-blocking ``flock`` on
        ``.lock`` itself, so the two can never deadlock.  The guard is not
        re-entrant: code already inside the block must use
        ``_unlink_if_same_inode`` rather than ``_safe_unlink_locked_file``.
        Without ``fcntl`` (non-POSIX) the guard degrades to a no-op."""
        guard_path = lock_path.with_name(lock_path.name + ".guard")
        gfd = os.open(str(guard_path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            if fcntl:
                fcntl.flock(gfd, fcntl.LOCK_EX)
            yield
        finally:
            if fcntl:
                try:
                    fcntl.flock(gfd, fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                os.close(gfd)
            except OSError:
                pass

    @staticmethod
    def _unlink_if_same_inode(path: Path, fd: int) -> bool:
        """Unlink *path* only if it is still the inode open as *fd*.

        CALLER MUST HOLD ``_lock_guard`` for this path.  Under the guard no
        protocol participant can replace the path between the stat and the
        unlink, so this is race-free; the inode comparison remains as a
        defence against foreign (non-protocol) interference."""
        try:
            fd_stat = os.fstat(fd)
            path_stat = os.stat(path)
            if fd_stat.st_ino == path_stat.st_ino and fd_stat.st_dev == path_stat.st_dev:
                path.unlink()
                return True
            return False
        except OSError:
            return False

    @staticmethod
    def _safe_unlink_locked_file(path: Path, fd: int) -> bool:
        """Unlink a lock file only if *path* still refers to the same inode as *fd*,
        atomically with respect to every other lock operation (takes the guard)."""
        with TransactionManager._lock_guard(path):
            return TransactionManager._unlink_if_same_inode(path, fd)

    def acquire_lock(self, db: str, owner_id: str = None) -> None:
        """Atomically acquires the single-writer lock using OS exclusive creation and kernel flock.

        The whole acquisition (create, stale detection, stale removal, re-create)
        runs inside the per-database guard, see ``_lock_guard``."""
        lock = self._lock_file(db)
        lock.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_guard(lock):
            self._acquire_lock_guarded(db, lock, owner_id or self.manager_id)

    def _acquire_lock_guarded(self, db: str, lock: Path, owner_id: str) -> None:
        """Body of acquire_lock; caller holds the guard."""
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR

        try:
            fd = os.open(str(lock), flags)
        except FileExistsError:
            if self._is_lock_held_in_process(lock):
                raise MiniDBError(f"Database '{db}' is currently locked by another transaction/writer")

            fd_stale = None
            try:
                try:
                    fd_stale = os.open(str(lock), os.O_RDWR)
                except OSError as e:
                    raise MiniDBError(f"Database '{db}' is currently locked by another transaction/writer") from e

                if fcntl:
                    try:
                        fcntl.flock(fd_stale, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except (BlockingIOError, OSError) as e:
                        raise MiniDBError(f"Database '{db}' is currently locked by another transaction/writer") from e

                info = self._read_lock_info(lock)
                if info is None or self._is_process_alive(info["pid"]):
                    raise MiniDBError(f"Database '{db}' is currently locked by another transaction/writer")

                # Stale lock confirmed under exclusive flock: safe unlink matching inode only
                TransactionManager._unlink_if_same_inode(lock, fd_stale)
            finally:
                if fd_stale is not None:
                    if fcntl:
                        try:
                            fcntl.flock(fd_stale, fcntl.LOCK_UN)
                        except OSError:
                            pass
                    try:
                        os.close(fd_stale)
                    except OSError:
                        pass

            try:
                fd = os.open(str(lock), flags)
            except FileExistsError:
                raise MiniDBError(f"Database '{db}' is currently locked by another transaction/writer")
            except OSError as e:
                raise MiniDBError(f"Failed to acquire lock for database '{db}': {e}") from e
        except OSError as e:
            raise MiniDBError(f"Failed to acquire lock for database '{db}': {e}") from e

        if fcntl:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError) as e:
                os.close(fd)
                raise MiniDBError(f"Database '{db}' is currently locked by another transaction/writer") from e

        try:
            content = f"pid={os.getpid()};time={time.time()};owner={owner_id}\n".encode("utf-8")
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, content)
            self._lock_fds[db] = fd
        except Exception:
            if fcntl:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                TransactionManager._unlink_if_same_inode(lock, fd)
            finally:
                os.close(fd)
            raise

        try:
            TransactionManager._HELD_LOCKS[lock.resolve()] = owner_id
        except OSError:
            pass

    def release_lock(self, db: str) -> None:
        lock = self._lock_file(db)
        try:
            TransactionManager._HELD_LOCKS.pop(lock.resolve(), None)
        except OSError:
            pass

        fd = self._lock_fds.pop(db, None)
        try:
            guard = self._lock_guard(lock)
            guard.__enter__()
        except OSError:
            guard = None  # db directory gone; nothing left to unlink
        try:
            if fd is not None:
                if guard is not None:
                    TransactionManager._unlink_if_same_inode(lock, fd)
            elif guard is not None and lock.is_file():
                try:
                    lock.unlink()
                except OSError:
                    pass
        finally:
            if fd is not None:
                if fcntl:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    except OSError:
                        pass
                try:
                    os.close(fd)
                except OSError:
                    pass
            if guard is not None:
                guard.__exit__(None, None, None)

    def is_locked(self, db: str) -> bool:
        return self._lock_file(db).is_file()

    @contextmanager
    def write_lock(self, db: str, session):
        """Acquires lock for a single write operation if not already in an active transaction."""
        if getattr(session, "in_transaction", False):
            yield
        else:
            self.acquire_lock(db)
            try:
                yield
            finally:
                self.release_lock(db)

    def begin(self, db: str, session) -> None:
        if getattr(session, "in_transaction", False):
            raise SemanticError("Transaction already in progress")

        self.acquire_lock(db)
        try:
            catalog_path = self._db_dir(db) / "catalog.json"
            catalog_snapshot = read_json(catalog_path) if catalog_path.is_file() else {}
            journal_data = {
                "version": 1,
                "status": "ACTIVE",
                "db": db,
                "catalog": catalog_snapshot,
                "tables": {},          # table_name -> original_table_json
                "indexes": {},         # index_name -> original_index_json
                "created_tables": [],  # table names created in this transaction
                "created_indexes": [], # index names created in this transaction
            }
            atomic_write_json(self._journal_file(db), journal_data)
        except Exception:
            self.release_lock(db)
            raise
        session.in_transaction = True
        session.tx_db = db

    def _load_journal(self, db: str) -> Optional[dict]:
        """Load the journal for a database.

        Returns None if the journal file does not exist on disk.
        Raises StorageError if the file exists but is corrupt or unreadable,
        because losing the journal mid-transaction means subsequent operations
        would proceed without rollback protection."""
        j_path = self._journal_file(db)
        if not j_path.is_file():
            return None
        # read_json raises StorageError on corrupt/unreadable files — propagate it
        return read_json(j_path)

    def record_table_pre_image(self, db: str, table: str, storage) -> None:
        """Write-ahead snapshot table data before DML modification if in active transaction."""
        journal = self._load_journal(db)
        if not journal or journal.get("status") != "ACTIVE":
            return
        if table not in journal["tables"] and table not in journal["created_tables"]:
            table_path = storage._path(db, table)
            if table_path.is_file():
                journal["tables"][table] = read_json(table_path)
                atomic_write_json(self._journal_file(db), journal)

    def record_table_create_intent(self, db: str, table: str) -> None:
        """Write-ahead journal intent to create table before physical creation."""
        journal = self._load_journal(db)
        if not journal or journal.get("status") != "ACTIVE":
            return
        if table not in journal["created_tables"]:
            journal["created_tables"].append(table)
            atomic_write_json(self._journal_file(db), journal)

    def record_table_drop_intent(self, db: str, table: str, storage, index_manager) -> None:
        """Write-ahead snapshot table and its indexes before physical drop."""
        journal = self._load_journal(db)
        if not journal or journal.get("status") != "ACTIVE":
            return
        # If table was created in this transaction, remove it from created_tables
        if table in journal["created_tables"]:
            journal["created_tables"].remove(table)
        elif table not in journal["tables"]:
            table_path = storage._path(db, table)
            if table_path.is_file():
                journal["tables"][table] = read_json(table_path)

        # Snapshot all indexes on this table
        idx_dir = index_manager._dir(db)
        if idx_dir.is_dir():
            for p in idx_dir.glob("*.json"):
                try:
                    data = read_json(p)
                    if data.get("table") == table and p.stem not in journal["indexes"]:
                        journal["indexes"][p.stem] = data
                except Exception:
                    pass

        atomic_write_json(self._journal_file(db), journal)

    def record_index_create_intent(self, db: str, index_name: str) -> None:
        """Write-ahead journal intent to create index before physical creation."""
        journal = self._load_journal(db)
        if not journal or journal.get("status") != "ACTIVE":
            return
        if index_name not in journal["created_indexes"]:
            journal["created_indexes"].append(index_name)
            atomic_write_json(self._journal_file(db), journal)

    def record_index_drop_intent(self, db: str, index_name: str, index_manager) -> None:
        """Write-ahead snapshot index file before physical drop."""
        journal = self._load_journal(db)
        if not journal or journal.get("status") != "ACTIVE":
            return
        if index_name in journal["created_indexes"]:
            journal["created_indexes"].remove(index_name)
        elif index_name not in journal["indexes"]:
            idx_path = index_manager._path(db, index_name)
            if idx_path.is_file():
                journal["indexes"][index_name] = read_json(idx_path)
        atomic_write_json(self._journal_file(db), journal)

    def commit(self, db: str, session) -> None:
        if not getattr(session, "in_transaction", False):
            raise SemanticError("No active transaction to commit")

        journal_file = self._journal_file(db)
        if journal_file.is_file():
            # Durable commit point: mark status = COMMITTED before unlinking
            try:
                j_data = read_json(journal_file)
                j_data["status"] = "COMMITTED"
                atomic_write_json(journal_file, j_data)
            except Exception as e:
                raise StorageError(f"Failed to record durable commit marker: {e}") from e

            try:
                journal_file.unlink()
            except OSError:
                pass

        self.release_lock(db)
        session.in_transaction = False
        session.tx_db = None

    def rollback(self, db: str, session, catalog, storage, index_manager) -> None:
        if not getattr(session, "in_transaction", False):
            raise SemanticError("No active transaction to rollback")

        journal_path = self._journal_file(db)
        if journal_path.is_file():
            journal = read_json(journal_path)
            # If _apply_rollback fails, it raises StorageError and journal is preserved for retry/recovery
            try:
                self._apply_rollback(db, journal, catalog, storage, index_manager)
            except StorageError:
                raise
            except Exception as e:
                raise StorageError(f"Rollback failed during state restoration: {e}") from e

            try:
                journal_path.unlink()
            except OSError as e:
                raise StorageError(f"Rollback succeeded but failed to delete journal: {e}") from e

        self.release_lock(db)
        session.in_transaction = False
        session.tx_db = None

    def _apply_rollback(self, db: str, journal: dict, catalog, storage, index_manager) -> None:
        """Revert catalog, table files, and index files to pre-transaction state.
        Fails safely and propagates StorageError on any deletion or restoration error."""
        # 1. Restore catalog definition
        if "catalog" in journal and journal["catalog"] is not None:
            try:
                catalog._save(db, journal["catalog"])
            except Exception as e:
                raise StorageError(f"Rollback failed: could not restore catalog for '{db}': {e}") from e

        # 2. Remove files of tables created in this transaction
        for table_name in journal.get("created_tables", []):
            tbl_p = storage._path(db, table_name)
            if tbl_p.is_file():
                try:
                    tbl_p.unlink()
                except OSError as e:
                    raise StorageError(f"Rollback failed: could not remove created table file '{tbl_p.name}': {e}") from e

        # 3. Restore pre-images of modified or dropped tables
        for table_name, original_data in journal.get("tables", {}).items():
            try:
                if original_data is not None:
                    storage.save(db, table_name, original_data)
                else:
                    storage.drop_table(db, table_name)
            except Exception as e:
                raise StorageError(f"Rollback failed: could not restore table '{table_name}': {e}") from e

        # 4. Remove files of indexes created in this transaction
        for idx_name in journal.get("created_indexes", []):
            idx_p = index_manager._path(db, idx_name)
            if idx_p.is_file():
                try:
                    idx_p.unlink()
                except OSError as e:
                    raise StorageError(f"Rollback failed: could not remove created index file '{idx_p.name}': {e}") from e

        # 5. Restore dropped indexes
        for idx_name, idx_data in journal.get("indexes", {}).items():
            if idx_data is not None:
                atomic_write_json(index_manager._path(db, idx_name), idx_data)
            else:
                idx_p = index_manager._path(db, idx_name)
                if idx_p.is_file():
                    try:
                        idx_p.unlink()
                    except OSError as e:
                        raise StorageError(f"Rollback failed: could not remove dropped index file '{idx_p.name}': {e}") from e

        # 6. Clear index manager in-memory cache and re-sync indexes with restored catalog
        index_manager._cache.clear()
        restored_tables = catalog.list_tables(db)
        for tbl in restored_tables:
            index_manager.rebuild_for_table(db, tbl, storage)

    def recover(self, catalog, storage, index_manager) -> None:
        """Startup crash recovery: rolls back uncommitted transactions using kernel-backed exclusive ownership."""
        if not self.root.is_dir():
            return

        for db_dir in self.root.iterdir():
            if not db_dir.is_dir():
                continue
            db = db_dir.name
            lock_path = db_dir / ".lock"
            journal_path = db_dir / ".journal.json"

            if not journal_path.is_file() and not lock_path.is_file():
                continue

            recovery_fd = None
            if lock_path.is_file():
                with self._lock_guard(lock_path):
                    info = self._read_lock_info(lock_path)
                    if info is None:
                        raise StorageError(
                            f"Crash recovery failed for database '{db}': unreadable or corrupt lock file; explicit intervention required"
                        )

                    if not self._is_lock_stale(lock_path):
                        continue

                    try:
                        recovery_fd = os.open(str(lock_path), os.O_RDWR)
                    except OSError:
                        continue

                    if fcntl:
                        try:
                            fcntl.flock(recovery_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except (BlockingIOError, OSError):
                            os.close(recovery_fd)
                            continue

                    info_after = self._read_lock_info(lock_path)
                    if info_after != info or not self._is_lock_stale(lock_path):
                        if fcntl:
                            try:
                                fcntl.flock(recovery_fd, fcntl.LOCK_UN)
                            except OSError:
                                pass
                        os.close(recovery_fd)
                        continue
            else:
                if journal_path.is_file():
                    try:
                        self.acquire_lock(db)
                    except MiniDBError:
                        continue

            # Exclusive ownership established throughout recovery
            try:
                if journal_path.is_file():
                    journal = read_json(journal_path)
                    if journal.get("status") == "COMMITTED":
                        try:
                            journal_path.unlink()
                        except OSError:
                            pass
                    else:
                        self._apply_rollback(db, journal, catalog, storage, index_manager)
                        journal_path.unlink()
            except Exception as e:
                raise StorageError(f"Crash recovery failed for database '{db}': {e}") from e
            finally:
                if recovery_fd is not None:
                    try:
                        TransactionManager._safe_unlink_locked_file(lock_path, recovery_fd)
                    finally:
                        if fcntl:
                            try:
                                fcntl.flock(recovery_fd, fcntl.LOCK_UN)
                            except OSError:
                                pass
                        try:
                            os.close(recovery_fd)
                        except OSError:
                            pass
                else:
                    self.release_lock(db)
