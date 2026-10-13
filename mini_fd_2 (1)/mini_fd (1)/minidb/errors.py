"""Typed errors. Every pipeline stage raises one of these; the CLI prints them without crashing."""


class MiniDBError(Exception):
    """Base class for all engine errors."""


class LexError(MiniDBError):
    pass


class ParseError(MiniDBError):
    pass


class SemanticError(MiniDBError):
    """Unknown table/column, bad names, type-incompatible expressions."""


class ConstraintError(MiniDBError):
    """NOT NULL, PRIMARY KEY and type violations on write."""


class StorageError(MiniDBError):
    """Corrupt or unreadable files."""
