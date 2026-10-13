"""Atomic JSON persistence helpers (temp file + fsync + os.replace)."""
import json
import os
from pathlib import Path

from .errors import StorageError


def atomic_write_json(path, obj):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise StorageError(f"Could not write {path.name}: {e}") from e
    _fsync_dir(path.parent)


def _fsync_dir(directory):
    try:  # persists the rename on POSIX; not supported on Windows
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as e:
        raise StorageError(f"Missing file: {Path(path).name}") from e
    except (json.JSONDecodeError, OSError) as e:
        raise StorageError(f"Corrupt or unreadable file {Path(path).name}: {e}") from e


def cleanup_tmp(root):
    """Remove leftover *.tmp files from interrupted writes."""
    for p in Path(root).rglob("*.tmp"):
        try:
            p.unlink()
        except OSError:
            pass
