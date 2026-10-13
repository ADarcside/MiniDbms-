"""Index manager supporting Hash Index and B+ Tree Index with atomic persistence."""
import os
from pathlib import Path
from typing import Any, List, Optional, Tuple

from .catalog import validate_name
from .errors import SemanticError, StorageError
from .fileio import atomic_write_json, read_json


def _compare_keys(a: Any, b: Any) -> int:
    """Compare keys with SQL NULL ordering (NULL is smaller than any non-NULL)."""
    if a is None and b is None:
        return 0
    if a is None:
        return -1
    if b is None:
        return 1
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


class HashIndex:
    """Hash Index: Average O(1) equality lookup."""

    def __init__(self, name: str, table: str, column: str):
        self.name = name
        self.table = table
        self.column = column
        self.type = "HASH"
        self._entries: dict = {}  # key -> list[rid]

    def insert(self, key: Any, rid: int) -> None:
        if key not in self._entries:
            self._entries[key] = []
        if rid not in self._entries[key]:
            self._entries[key].append(rid)

    def delete(self, key: Any, rid: int) -> None:
        if key in self._entries:
            self._entries[key] = [r for r in self._entries[key] if r != rid]
            if not self._entries[key]:
                del self._entries[key]

    def lookup(self, key: Any) -> List[int]:
        return list(self._entries.get(key, []))

    def serialize(self) -> dict:
        return {
            "version": 1,
            "name": self.name,
            "table": self.table,
            "column": self.column,
            "type": self.type,
            "entries": [{"key": k, "rids": v} for k, v in self._entries.items()],
        }

    @classmethod
    def deserialize(cls, data: dict) -> "HashIndex":
        idx = cls(data["name"], data["table"], data["column"])
        for item in data.get("entries", []):
            idx._entries[item["key"]] = list(item["rids"])
        return idx


class BPlusTreeNode:
    def __init__(self, is_leaf: bool = False):
        self.is_leaf = is_leaf
        self.keys: list = []
        self.children: list = []  # if is_leaf: list[list[int]] (rids), else list[BPlusTreeNode]
        self.next: Optional["BPlusTreeNode"] = None


class BPlusTreeIndex:
    """B+ Tree Index: O(log n) search, linked leaf traversal for range scans."""

    def __init__(self, name: str, table: str, column: str, order: int = 4):
        self.name = name
        self.table = table
        self.column = column
        self.type = "BTREE"
        self.order = max(3, order)
        self.root = BPlusTreeNode(is_leaf=True)

    def _find_leaf(self, key: Any) -> BPlusTreeNode:
        node = self.root
        while not node.is_leaf:
            i = 0
            while i < len(node.keys) and _compare_keys(key, node.keys[i]) >= 0:
                i += 1
            node = node.children[i]
        return node

    def lookup(self, key: Any) -> List[int]:
        leaf = self._find_leaf(key)
        for i, k in enumerate(leaf.keys):
            if _compare_keys(k, key) == 0:
                return list(leaf.children[i])
        return []

    def range(self, lo: Any = None, hi: Any = None,
              inc_lo: bool = True, inc_hi: bool = True) -> List[int]:
        """Range scan returning all rids where lo <= key <= hi."""
        results: List[int] = []
        if lo is None:
            # Start from the leftmost leaf
            leaf = self.root
            while not leaf.is_leaf:
                leaf = leaf.children[0]
        else:
            leaf = self._find_leaf(lo)

        done = False
        while leaf and not done:
            for k, rids in zip(leaf.keys, leaf.children):
                if lo is not None:
                    cmp_lo = _compare_keys(k, lo)
                    if (cmp_lo < 0) or (cmp_lo == 0 and not inc_lo):
                        continue
                if hi is not None:
                    cmp_hi = _compare_keys(k, hi)
                    if (cmp_hi > 0) or (cmp_hi == 0 and not inc_hi):
                        done = True
                        break
                results.extend(rids)
            leaf = leaf.next
        return results

    def insert(self, key: Any, rid: int) -> None:
        leaf = self._find_leaf(key)
        # Check if key already exists in leaf
        for i, k in enumerate(leaf.keys):
            if _compare_keys(k, key) == 0:
                if rid not in leaf.children[i]:
                    leaf.children[i].append(rid)
                return

        # Insert sorted
        idx = 0
        while idx < len(leaf.keys) and _compare_keys(key, leaf.keys[idx]) > 0:
            idx += 1
        leaf.keys.insert(idx, key)
        leaf.children.insert(idx, [rid])

        if len(leaf.keys) >= self.order:
            self._split_leaf(leaf)

    def _split_leaf(self, leaf: BPlusTreeNode) -> None:
        mid = len(leaf.keys) // 2
        new_leaf = BPlusTreeNode(is_leaf=True)
        new_leaf.keys = leaf.keys[mid:]
        new_leaf.children = leaf.children[mid:]
        new_leaf.next = leaf.next

        leaf.keys = leaf.keys[:mid]
        leaf.children = leaf.children[:mid]
        leaf.next = new_leaf

        promote_key = new_leaf.keys[0]
        if leaf is self.root:
            new_root = BPlusTreeNode(is_leaf=False)
            new_root.keys = [promote_key]
            new_root.children = [leaf, new_leaf]
            self.root = new_root
        else:
            self._insert_into_parent(leaf, promote_key, new_leaf)

    def _insert_into_parent(self, left: BPlusTreeNode, key: Any, right: BPlusTreeNode) -> None:
        parent = self._find_parent(self.root, left)
        if parent is None:
            new_root = BPlusTreeNode(is_leaf=False)
            new_root.keys = [key]
            new_root.children = [left, right]
            self.root = new_root
            return

        idx = 0
        while idx < len(parent.keys) and _compare_keys(key, parent.keys[idx]) > 0:
            idx += 1
        parent.keys.insert(idx, key)
        parent.children.insert(idx + 1, right)

        if len(parent.keys) >= self.order:
            self._split_internal(parent)

    def _find_parent(self, curr: BPlusTreeNode, target: BPlusTreeNode) -> Optional[BPlusTreeNode]:
        if curr.is_leaf or not curr.children:
            return None
        for child in curr.children:
            if child is target:
                return curr
            p = self._find_parent(child, target)
            if p:
                return p
        return None

    def _split_internal(self, node: BPlusTreeNode) -> None:
        mid = len(node.keys) // 2
        promote_key = node.keys[mid]

        new_node = BPlusTreeNode(is_leaf=False)
        new_node.keys = node.keys[mid + 1:]
        new_node.children = node.children[mid + 1:]

        node.keys = node.keys[:mid]
        node.children = node.children[:mid + 1]

        if node is self.root:
            new_root = BPlusTreeNode(is_leaf=False)
            new_root.keys = [promote_key]
            new_root.children = [node, new_node]
            self.root = new_root
        else:
            self._insert_into_parent(node, promote_key, new_node)

    def delete(self, key: Any, rid: int) -> None:
        leaf = self._find_leaf(key)
        for i, k in enumerate(leaf.keys):
            if _compare_keys(k, key) == 0:
                leaf.children[i] = [r for r in leaf.children[i] if r != rid]
                if not leaf.children[i]:
                    leaf.keys.pop(i)
                    leaf.children.pop(i)
                return

    def all_entries(self) -> List[Tuple[Any, List[int]]]:
        entries: List[Tuple[Any, List[int]]] = []
        leaf = self.root
        while not leaf.is_leaf:
            leaf = leaf.children[0]
        while leaf:
            for k, rids in zip(leaf.keys, leaf.children):
                entries.append((k, list(rids)))
            leaf = leaf.next
        return entries

    def serialize(self) -> dict:
        return {
            "version": 1,
            "name": self.name,
            "table": self.table,
            "column": self.column,
            "type": self.type,
            "entries": [{"key": k, "rids": v} for k, v in self.all_entries()],
        }

    @classmethod
    def deserialize(cls, data: dict) -> "BPlusTreeIndex":
        idx = cls(data["name"], data["table"], data["column"])
        for item in data.get("entries", []):
            k = item["key"]
            for rid in item["rids"]:
                idx.insert(k, rid)
        return idx


class IndexManager:
    """Coordinates persistence, caching, and lookups for all table indexes."""

    def __init__(self, data_dir: str, catalog):
        self.root = Path(data_dir)
        self.catalog = catalog
        self._cache: dict = {}

    def _dir(self, db: str) -> Path:
        p = self.root / validate_name(db, "database name") / "indexes"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _path(self, db: str, name: str) -> Path:
        return self._dir(db) / (validate_name(name, "index name") + ".json")

    def create_index(self, db: str, table: str, name: str, column: str,
                     index_type: str, storage) -> Any:
        idx_type = index_type.upper()
        if idx_type not in ("HASH", "BTREE"):
            raise SemanticError(f"Unsupported index type '{index_type}' (use HASH or BTREE)")

        self.catalog.create_index(db, name, table, column, idx_type)

        idx = BPlusTreeIndex(name, table, column) if idx_type == "BTREE" else HashIndex(name, table, column)
        # Populate from existing rows
        for rid, values in storage.scan(db, table):
            idx.insert(values.get(column), rid)

        atomic_write_json(self._path(db, name), idx.serialize())
        self._cache[(db, name)] = idx
        return idx

    def drop_index(self, db: str, name: str) -> None:
        self.catalog.drop_index(db, name)
        p = self._path(db, name)
        if p.is_file():
            try:
                p.unlink()
            except OSError:
                pass
        self._cache.pop((db, name), None)

    def load_index(self, db: str, name: str) -> Any:
        key = (db, name)
        if key in self._cache:
            return self._cache[key]

        meta = self.catalog.get_index(db, name)
        p = self._path(db, name)
        if not p.is_file():
            # If index file is missing, reconstruct it
            raise StorageError(f"Missing index file for index '{name}'")
        data = read_json(p)
        idx_type = meta.get("type", "HASH")
        if idx_type == "BTREE":
            idx = BPlusTreeIndex.deserialize(data)
        else:
            idx = HashIndex.deserialize(data)
        self._cache[key] = idx
        return idx

    def get_index_for_column(self, db: str, table: str, column: str) -> Optional[Tuple[str, Any, dict]]:
        indexes = self.catalog.list_indexes(db, table)
        for name, meta in indexes.items():
            if meta["column"] == column:
                return name, self.load_index(db, name), meta
        return None

    def on_insert(self, db: str, table: str, rid: int, values: dict) -> None:
        indexes = self.catalog.list_indexes(db, table)
        for name, meta in indexes.items():
            idx = self.load_index(db, name)
            idx.insert(values.get(meta["column"]), rid)
            atomic_write_json(self._path(db, name), idx.serialize())

    def on_update(self, db: str, table: str, rid: int, old_values: dict, new_values: dict) -> None:
        indexes = self.catalog.list_indexes(db, table)
        for name, meta in indexes.items():
            idx = self.load_index(db, name)
            col = meta["column"]
            idx.delete(old_values.get(col), rid)
            idx.insert(new_values.get(col), rid)
            atomic_write_json(self._path(db, name), idx.serialize())

    def on_delete(self, db: str, table: str, rid: int, old_values: dict) -> None:
        indexes = self.catalog.list_indexes(db, table)
        for name, meta in indexes.items():
            idx = self.load_index(db, name)
            idx.delete(old_values.get(meta["column"]), rid)
            atomic_write_json(self._path(db, name), idx.serialize())

    def rebuild_for_table(self, db: str, table: str, storage) -> None:
        indexes = self.catalog.list_indexes(db, table)
        for name, meta in indexes.items():
            idx_type = meta.get("type", "HASH")
            idx = BPlusTreeIndex(name, table, meta["column"]) if idx_type == "BTREE" else HashIndex(name, table, meta["column"])
            for rid, values in storage.scan(db, table):
                idx.insert(values.get(meta["column"]), rid)
            atomic_write_json(self._path(db, name), idx.serialize())
            self._cache[(db, name)] = idx

    def drop_table_indexes(self, db: str, table: str) -> None:
        idx_dir = self._dir(db)
        if idx_dir.is_dir():
            for p in list(idx_dir.glob("*.json")):
                try:
                    data = read_json(p)
                    if data.get("table") == table:
                        p.unlink()
                        self._cache.pop((db, p.stem), None)
                except Exception:
                    pass

