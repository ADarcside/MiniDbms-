"""Catalog manager: databases, table schemas and constraints (catalog.json)."""
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .ast_nodes import ColumnDef
from .errors import SemanticError
from .fileio import atomic_write_json, read_json

NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")


def validate_name(name, what="name"):
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise SemanticError(
            f"Invalid {what} {name!r}: use lowercase letters, digits and underscores "
            f"(max 64 chars, not starting with a digit)")
    return name


@dataclass
class TableSchema:
    name: str
    columns: list  # list[ColumnDef]

    @property
    def primary_key(self) -> Optional[str]:
        for c in self.columns:
            if c.primary_key:
                return c.name
        return None

    def column_names(self):
        return [c.name for c in self.columns]

    def column(self, name):
        for c in self.columns:
            if c.name == name:
                return c
        raise SemanticError(f"Unknown column '{name}' in table '{self.name}'")

    def to_dict(self):
        return {"columns": [
            {"name": c.name, "type": c.type, "not_null": c.not_null, "primary_key": c.primary_key}
            for c in self.columns]}

    @classmethod
    def from_dict(cls, name, d):
        cols = [ColumnDef(c["name"], c["type"], c["not_null"], c["primary_key"])
                for c in d["columns"]]
        return cls(name, cols)


class Catalog:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- databases ----
    def db_path(self, name):
        return self.root / validate_name(name, "database name")

    def database_exists(self, name):
        return (self.db_path(name) / "catalog.json").is_file()

    def list_databases(self):
        return sorted(p.name for p in self.root.iterdir()
                      if p.is_dir() and (p / "catalog.json").is_file())

    def create_database(self, name):
        path = self.db_path(name)
        if self.database_exists(name):
            raise SemanticError(f"Database '{name}' already exists")
        (path / "tables").mkdir(parents=True, exist_ok=True)
        (path / "indexes").mkdir(parents=True, exist_ok=True)
        atomic_write_json(path / "catalog.json", {"version": 1, "tables": {}, "indexes": {}})

    def drop_database(self, name):
        if not self.database_exists(name):
            raise SemanticError(f"Database '{name}' does not exist")
        shutil.rmtree(self.db_path(name))

    # ---- tables ----
    def _load(self, db):
        data = read_json(self.db_path(db) / "catalog.json")
        data.setdefault("indexes", {})
        return data

    def _save(self, db, data):
        atomic_write_json(self.db_path(db) / "catalog.json", data)

    def list_tables(self, db):
        return sorted(self._load(db)["tables"])

    def table_exists(self, db, name):
        return name in self._load(db)["tables"]

    def get_table(self, db, name):
        tables = self._load(db)["tables"]
        if name not in tables:
            raise SemanticError(f"Table '{name}' does not exist in database '{db}'")
        return TableSchema.from_dict(name, tables[name])

    def create_table(self, db, schema):
        data = self._load(db)
        if schema.name in data["tables"]:
            raise SemanticError(f"Table '{schema.name}' already exists")
        data["tables"][schema.name] = schema.to_dict()
        self._save(db, data)

    def drop_table(self, db, name):
        data = self._load(db)
        if name not in data["tables"]:
            raise SemanticError(f"Table '{name}' does not exist in database '{db}'")
        del data["tables"][name]
        if "indexes" in data:
            data["indexes"] = {k: v for k, v in data["indexes"].items() if v["table"] != name}
        self._save(db, data)

    # ---- indexes ----
    def list_indexes(self, db, table=None):
        indexes = self._load(db).get("indexes", {})
        if table is not None:
            return {k: v for k, v in indexes.items() if v["table"] == table}
        return indexes

    def index_exists(self, db, name):
        return name in self._load(db).get("indexes", {})

    def get_index(self, db, name):
        indexes = self._load(db).get("indexes", {})
        if name not in indexes:
            raise SemanticError(f"Index '{name}' does not exist in database '{db}'")
        return indexes[name]

    def create_index(self, db, name, table, column, index_type="HASH"):
        validate_name(name, "index name")
        data = self._load(db)
        indexes = data.setdefault("indexes", {})
        if name in indexes:
            raise SemanticError(f"Index '{name}' already exists in database '{db}'")
        schema = self.get_table(db, table)
        schema.column(column)
        indexes[name] = {"table": table, "column": column, "type": index_type.upper()}
        self._save(db, data)

    def drop_index(self, db, name):
        data = self._load(db)
        indexes = data.get("indexes", {})
        if name not in indexes:
            raise SemanticError(f"Index '{name}' does not exist in database '{db}'")
        del indexes[name]
        self._save(db, data)

