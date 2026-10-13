"""Engine facade: the single entry point for any frontend (CLI today, REST/GUI later)."""
from .catalog import Catalog
from .errors import MiniDBError
from .executor import Executor, ResultSet, Session
from .fileio import cleanup_tmp
from .indexes import IndexManager
from .lexer import tokenize
from .parser import parse
from .storage import StorageManager
from .transactions import TransactionManager


class Engine:
    def __init__(self, data_dir="data"):
        self.catalog = Catalog(data_dir)
        self.storage = StorageManager(data_dir)
        self.indexes = IndexManager(data_dir, self.catalog)
        self.transactions = TransactionManager(data_dir)
        self.executor = Executor(self.catalog, self.storage, self.indexes, self.transactions)
        self.session = Session()
        cleanup_tmp(data_dir)  # recover from interrupted atomic writes
        self.transactions.recover(self.catalog, self.storage, self.indexes)  # crash recovery

    def execute_many(self, sql, session=None):
        """Lex + parse the whole script first (a syntax error runs nothing), then execute."""
        session = session or self.session
        statements = parse(tokenize(sql))
        return [self.executor.execute(s, session) for s in statements]

    def execute(self, sql, session=None):
        results = self.execute_many(sql, session)
        if not results:
            raise MiniDBError("Empty statement")
        return results[-1]

    def new_session(self):
        return Session()

    def close(self):
        """Close the engine and unregister in-memory resources."""
        if hasattr(self, "transactions") and self.transactions:
            self.transactions.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

