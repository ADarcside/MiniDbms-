# MiniDBMS — Architecture & Technical Reference

MiniDBMS is a relational database management system built completely from scratch in Python (using only standard library modules). It avoids wrapping SQLite or any existing engine, implementing the core primitives of database engineering: lexical analysis, recursive-descent parsing, AST validation, rule-based execution planning, indexing (Hash and B+ Tree), transaction recovery with write-ahead undo logs, single-writer locking, and atomic persistence.

---

## 1. System Architecture

MiniDBMS follows a strict layered architecture where each layer communicates with adjacent layers strictly through well-defined contracts:

```
+--------------------------------------------------------------+
| CLI SHELL           input, output formatting, session state  |
+--------------------------------------------------------------+
| SQL FRONT END       Lexer -> Parser -> AST -> Validator     |
+--------------------------------------------------------------+
| QUERY EXECUTION     Scan | Filter | Project | Sort | Join    |
| (Planner)           chooses scan vs. hash / B+ tree index    |
+--------------------------------------------------------------+
| CATALOG      |      STORAGE         |      INDEXES           |
| schemas, PK  | records, slotted-page| hash, B+ tree          |
+--------------------------------------------------------------+
| PERSISTENCE LAYER   atomic write, journal / undo, lock files |
+--------------------------------------------------------------+
TRANSACTION MANAGER   coordinates Execution + Storage + Indexes
```

### Component Contracts & Hard Rules
1. **The Parser never touches storage**: Syntax analysis only emits immutable AST nodes.
2. **The Executor never builds file paths**: Only the Storage Manager and Index Manager manage files.
3. **The Catalog stores definitions**: Schemas, column constraints, and index definitions are kept separate from table record storage.
4. **Constraints are enforced centrally**: The executor and constraint checker validate data types, `NOT NULL`, and `PRIMARY KEY` uniqueness before any write is committed.
5. **The Engine facade is the single entry point**: The CLI shell, future REST APIs, or GUI applications interact solely via `Engine.execute(sql, session)` which yields structured `ResultSet` instances.

---

## 2. SQL Frontend Pipeline

1. **Lexer (`minidb/lexer.py`)**:
   - Tokenizes input SQL into a stream of `Token(kind, value, line, col)`.
   - Recognizes keywords, identifiers (normalized to lowercase), integer/float literals, strings with escape sequences (`''`), comparison operators (`=`, `!=`, `<`, `<=`, `>`, `>=`), and punctuation (including `.` for table-qualified column references).
   - Time Complexity: $O(L)$ where $L$ is the length of SQL text.

2. **Parser (`minidb/parser.py`)**:
   - Recursive-descent parser producing typed dataclass AST nodes defined in `minidb/ast_nodes.py`.
   - Supports precedence climbing for boolean expressions (`OR` < `AND` < `NOT` < comparisons < primary literals/columns).
   - Syntax errors report precise line and column numbers.
   - A whole script is parsed before execution begins, ensuring that syntax errors abort early without executing any partial statements.

3. **Validator (`minidb/validator.py`)**:
   - Validates table and column names against the Catalog.
   - Disallows ambiguous column names in joined queries.
   - Enforces static type compatibility in expressions (e.g., comparing numbers to text raises `SemanticError`).

---

## 3. Storage and Persistence Engine

### On-Disk Directory Layout
```
data/
  <database_name>/
    catalog.json             # Schema definitions and index metadata
    .lock                    # Single-writer lock file
    .journal.json            # Transaction undo log (created during active transactions)
    tables/
      <table_name>.json      # Table rows with stable record IDs (rid)
    indexes/
      <index_name>.json      # Persisted index entries (Hash or B+ Tree)
```

### Atomic Write Protocol
Every rewrite of a table, catalog, or index file adheres to the atomic write sequence:
1. Serialize new JSON data to `<file>.tmp` in the same directory.
2. Flush and `os.fsync(f.fileno())` to guarantee data is on physical storage.
3. Replace original file atomically via `os.replace(tmp, final)`.
4. On POSIX systems, `fsync()` the parent directory.
5. On engine startup, `cleanup_tmp()` sweeps and removes any orphaned `*.tmp` files left by crashes.

### Slotted-Page Storage & Buffer Pool Prototype (`minidb/storage.py`)
- **Page (`Page`)**: Implements a standalone fixed-size (4 KB) page format prototype consisting of a page header (`page_id`, `record_count`, `free_space_offset`), a slot directory of offset/length pointers, and variable-length record slots.
- **Buffer Pool (`BufferPool`)**: In-memory frame manager with LRU cache eviction and dirty-page tracking.
- **Active Record Store**: Active record persistence uses the Stage S1 JSON format with atomic temporary-file replacement as designated in the blueprint roadmap.

---

## 4. Indexing & Query Planning (`minidb/indexes.py`)

MiniDBMS supports two indexing mechanisms:
1. **Hash Index (`HashIndex`)**:
   - In-memory hash map with atomic JSON persistence.
   - Average $O(1)$ equality lookup for queries like `WHERE col = value`.
2. **B+ Tree Index (`BPlusTreeIndex`)**:
   - Order $M=4$ balanced search tree with linked leaf nodes.
   - $O(\log n)$ search and $O(\log n + k)$ range queries for operators `<`, `<=`, `>`, `>=`.
3. **Index Consistency & Multi-File Failure Atomicity**:
   - Every `INSERT`, `UPDATE`, and `DELETE` updates the table file and all corresponding index files as a single logical transaction. If an index write fails, both the table file and all index files are rolled back to their pre-statement state.
   - Table rollbacks and crash recovery rebuild indexes to ensure the invariant: **Scan-based queries and index-based queries return identical results**.

### Rule-Based Optimizer (Planner)
- Equality predicates on indexed columns trigger an **Index Scan**, bypassing full table scans.
- Range predicates on B+ tree indexed columns trigger an **Index Range Scan**.
- Joins where the inner table has an index on the join key utilize an **Index Nested-Loop Join**, reducing join overhead from $O(n \times m)$ to $O(n \times 1)$.

---

## 5. Transactions & Recovery (`minidb/transactions.py`)

1. **Single-Writer Concurrency & Lock Ownership**:
   - Protected via `.lock` file per database directory with PID, timestamp, and ownership tracking.
   - **Guarded cleanup protocol**: a permanent `.lock.guard` file (never unlinked) carries a short-lived exclusive `flock` mutex. Creating, replacing, stale-removing, or releasing `.lock` all happen inside that guard, so verifying the inode and unlinking are one critical section and no participant can swap the file in between. Lock order is always guard, then non-blocking flock on `.lock`.
   - Enforced across all write operations (both single-statement autocommit writes and explicit multi-statement transactions).
   - **Database Ownership Verification**: Startup crash recovery verifies database ownership before inspecting or recovering active journals. If another live process or active engine in the same process holds the lock, startup does not touch, recover, or modify that database.
   - **Active vs. Abandoned Lock Discrimination**: In-process engine registries distinguish actively held locks from abandoned locks left behind by terminated/closed engines.
   - **Fail-Safe Lock Protection**: Corrupt or unreadable lock files are never deleted automatically; recovery fails safely with `StorageError`, requiring explicit intervention to prevent data corruption.
2. **Statement-Level & Multi-File Atomicity**:
   - Multi-row operations validate all rows in memory before committing to disk. A single constraint failure leaves data completely unmodified.
   - Table and index file writes are guarded so any failure rolls back all modified persistent files.
3. **Multi-Statement Transactions (`BEGIN`, `COMMIT`, `ROLLBACK`)**:
   - `BEGIN`: Creates `.journal.json` with status `"ACTIVE"`, snapshotting catalog state, table pre-images, and tracking newly created/dropped tables and indexes.
   - **DDL Rollback Support**:
     - `CREATE TABLE` inside a transaction: `ROLLBACK` removes both the catalog entry and the physical `tables/<name>.json` file.
     - `DROP TABLE` inside a transaction: `ROLLBACK` restores the catalog entry and the physical `tables/<name>.json` file with all original rows and indexes.
     - `CREATE INDEX` inside a transaction: `ROLLBACK` removes both the catalog definition and the physical `indexes/<name>.json` file.
     - `DROP INDEX` inside a transaction: `ROLLBACK` restores both the catalog definition and the physical `indexes/<name>.json` file.
   - **Durable Commit Point**:
     - `COMMIT` writes `"status": "COMMITTED"` to the journal before deleting it. If startup recovery encounters a committed journal, it does not roll back committed data.
   - `ROLLBACK`: Restores tables, catalog, and indexes to their pre-transaction snapshot and clears the journal.
4. **Crash Recovery (`recover()`)**:
   - Triggered on `Engine` initialization. Identifies uncommitted active journals under exclusive database lock and automatically rolls back partial transaction changes to restore database consistency.
   - **Safe Error Handling**: If journal restoration or recovery fails, recovery raises a `StorageError` and preserves journals for future safe recovery rather than proceeding with a corrupted database.
   - **Persistence Boundary Verification**: Verified against simulated crashes across journal replacement, physical DDL modifications, commit-marker persistence, rollback restoration, and recovery execution.


---

## 6. Correctness Rules & Complexities

| Feature | Implementation | Complexity |
| --- | --- | --- |
| Lexer | Finite-state scanning | $O(L)$ where $L$ is input length |
| Parser | Recursive descent + precedence climbing | $O(T)$ where $T$ is token count |
| Full Table Scan | Sequential iteration | $O(N)$ rows |
| Hash Index Lookup | Hash map lookup | Average $O(1)$ |
| B+ Tree Range Scan | Tree traversal + linked leaf scan | $O(\log N + k)$ |
| Sorting (`ORDER BY`) | Python Timsort multi-key | $O(N \log N)$ |
| Nested-Loop Join | Outer $\times$ Inner scan | $O(N \times M)$ |
| Index Nested-Loop Join | Outer $\times$ Inner Index Lookup | $O(N \times 1)$ |
