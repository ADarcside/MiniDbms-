"""Storage manager: persistent records and slotted-page storage architecture.

Stage S1: One JSON file per table with atomic rewrite.
Stage S3: Slotted pages, page headers, slot directories, and buffer pool abstraction.
"""
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .catalog import validate_name
from .errors import StorageError
from .fileio import atomic_write_json, read_json

PAGE_SIZE = 4096  # 4 KB default page size


class Slot:
    """Directory slot pointing to a record within a page."""
    def __init__(self, offset: int, length: int, is_deleted: bool = False):
        self.offset = offset
        self.length = length
        self.is_deleted = is_deleted


class Page:
    """Fixed-size slotted page: Page Header + Slot Directory + Records."""

    def __init__(self, page_id: int, size: int = PAGE_SIZE):
        self.page_id = page_id
        self.size = size
        self.slots: List[Slot] = []
        self.records: Dict[int, Tuple[int, dict]] = {}  # slot_idx -> (rid, values)
        self.free_space_offset = size

    @property
    def record_count(self) -> int:
        return sum(1 for s in self.slots if not s.is_deleted)

    def can_fit(self, estimated_bytes: int) -> bool:
        # Header (64 bytes) + slots (8 bytes each) + record bytes
        used = 64 + len(self.slots) * 8 + (self.size - self.free_space_offset)
        return (used + estimated_bytes + 8) <= self.size

    def insert_record(self, rid: int, values: dict) -> Optional[int]:
        import json
        payload_len = len(json.dumps(values).encode("utf-8"))
        if not self.can_fit(payload_len):
            return None
        self.free_space_offset -= payload_len
        slot_idx = len(self.slots)
        self.slots.append(Slot(self.free_space_offset, payload_len))
        self.records[slot_idx] = (rid, values)
        return slot_idx

    def get_record(self, slot_idx: int) -> Optional[Tuple[int, dict]]:
        if slot_idx < len(self.slots) and not self.slots[slot_idx].is_deleted:
            return self.records.get(slot_idx)
        return None

    def delete_record(self, slot_idx: int) -> bool:
        if slot_idx < len(self.slots):
            self.slots[slot_idx].is_deleted = True
            self.records.pop(slot_idx, None)
            return True
        return False


class BufferPool:
    """Buffer pool managing in-memory page frames with LRU eviction."""

    def __init__(self, capacity: int = 16):
        self.capacity = capacity
        self.pages: Dict[int, Page] = {}
        self.dirty: Dict[int, bool] = {}
        self.lru_order: List[int] = []

    def get_page(self, page_id: int) -> Optional[Page]:
        if page_id in self.pages:
            self.lru_order.remove(page_id)
            self.lru_order.append(page_id)
            return self.pages[page_id]
        return None

    def put_page(self, page: Page, is_dirty: bool = False) -> None:
        if page.page_id in self.pages:
            self.lru_order.remove(page.page_id)
        elif len(self.pages) >= self.capacity:
            evict_id = self.lru_order.pop(0)
            self.pages.pop(evict_id, None)
            self.dirty.pop(evict_id, None)

        self.pages[page.page_id] = page
        self.dirty[page.page_id] = is_dirty
        self.lru_order.append(page.page_id)


class StorageManager:
    """Persistent storage manager: hides file paths and exposes record CRUD."""

    def __init__(self, root: str):
        self.root = Path(root)
        self.buffer_pool = BufferPool()

    def _path(self, db: str, table: str) -> Path:
        return self.root / validate_name(db, "database name") / "tables" / \
            (validate_name(table, "table name") + ".json")

    def create_table(self, db: str, table: str) -> None:
        path = self._path(db, table)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, {"version": 1, "next_rid": 1, "rows": []})

    def drop_table(self, db: str, table: str) -> None:
        try:
            self._path(db, table).unlink()
        except FileNotFoundError:
            pass

    def load(self, db: str, table: str) -> dict:
        return read_json(self._path(db, table))

    def save(self, db: str, table: str, data: dict) -> None:
        atomic_write_json(self._path(db, table), data)

    def scan(self, db: str, table: str):
        """Iterate over all records in the table yielding (rid, values)."""
        data = self.load(db, table)
        for row in data.get("rows", []):
            yield row["rid"], row["values"]

    def get_record(self, db: str, table: str, target_rid: int) -> Optional[dict]:
        """Fetch a specific record by rid."""
        for rid, values in self.scan(db, table):
            if rid == target_rid:
                return values
        return None

    def insert(self, db: str, table: str, values: dict) -> int:
        """Insert a single row, returning its assigned rid."""
        data = self.load(db, table)
        rid = data["next_rid"]
        data["rows"].append({"rid": rid, "values": values})
        data["next_rid"] = rid + 1
        self.save(db, table, data)
        return rid

    def update(self, db: str, table: str, target_rid: int, new_values: dict) -> bool:
        """Update a row by rid."""
        data = self.load(db, table)
        found = False
        for row in data["rows"]:
            if row["rid"] == target_rid:
                row["values"] = new_values
                found = True
                break
        if found:
            self.save(db, table, data)
        return found

    def delete(self, db: str, table: str, target_rid: int) -> bool:
        """Delete a row by rid."""
        data = self.load(db, table)
        original_len = len(data["rows"])
        data["rows"] = [r for r in data["rows"] if r["rid"] != target_rid]
        if len(data["rows"]) != original_len:
            self.save(db, table, data)
            return True
        return False
