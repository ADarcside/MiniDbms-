# MiniDBMS

A command-line relational database management system built from scratch in Python (using only the Python standard library). MiniDBMS implements the internal layers of a real DBMS: lexical analysis, recursive-descent parsing, AST validation, rule-based query planning, indexing (Hash and B+ Tree), nested-loop joins, transaction management with write-ahead undo logs, crash recovery, and JSON-backed persistent storage (a slotted-page storage layer exists only as a separately tested prototype).

---

## Quickstart

### Launch the Interactive CLI Shell
```bash
python main.py [--data-dir data]
```

### Run the Automated Test Suite (91 tests, zero dependencies)
```bash
python -m unittest discover -s tests -t .
```

### Run Performance Benchmarks
```bash
python benchmarks.py
```

---

## Execution Pipeline

```
SQL -> Lexer -> Parser (AST) -> Validator -> Planner/Optimizer -> Executor -> Storage / Indexes -> ResultSet -> CLI Formatter
```

| Module | Responsibility |
| --- | --- |
| `minidb/engine.py` | Facade: `Engine.execute(sql, session)`; the single entry point for frontends |
| `minidb/cli.py` | Terminal interactive shell, multi-line buffer, result and table formatting |
| `minidb/lexer.py` | Token stream with line/column tracking |
| `minidb/parser.py` | Recursive-descent parser producing typed AST nodes |
| `minidb/ast_nodes.py` | Dataclass AST definitions for statements and expressions |
| `minidb/validator.py` | Semantic analysis, column resolution, static type checks against catalog |
| `minidb/catalog.py` | Catalog manager: schemas, constraints, and index metadata (`catalog.json`) |
| `minidb/storage.py` | Storage manager: active JSON-backed table files (atomic replace); also holds the slotted-page `Page` and `BufferPool` prototype, not used for active persistence |
| `minidb/indexes.py` | Index manager: Hash Index ($O(1)$) and B+ Tree Index ($O(\log n)$) with atomic persistence |
| `minidb/transactions.py` | Transaction manager: `BEGIN`, `COMMIT`, `ROLLBACK`, single-writer lock, startup crash recovery |
| `minidb/constraints.py` | Central correctness rules: type checking, `NOT NULL`, and `PRIMARY KEY` uniqueness |
| `minidb/fileio.py` | Atomic persistence protocol: temp file + `fsync` + `os.replace` |
| `minidb/errors.py` | Typed error hierarchy (`LexError`, `ParseError`, `SemanticError`, `ConstraintError`, `StorageError`) |

---

## Supported SQL Features

- **Databases:** `CREATE DATABASE`, `DROP DATABASE`, `SHOW DATABASES`, `USE`
- **Schemas:** `CREATE TABLE`, `DROP TABLE`, `SHOW TABLES`, `DESCRIBE`
- **Data (CRUD):** `INSERT`, `SELECT`, `UPDATE`, `DELETE`
- **Indexes:** `CREATE [HASH|BTREE] INDEX idx ON t (col);`, `DROP INDEX idx;`
- **Joins:** `SELECT ... FROM t1 [INNER] JOIN t2 ON t1.id = t2.ref_id ...` (Nested-Loop & Index Nested-Loop)
- **Transactions:** `BEGIN [TRANSACTION];`, `COMMIT;`, `ROLLBACK;`
- **Query Plan:** `EXPLAIN <query>;`
- **Filtering & Logic:** Comparisons (`= != < <= > >=`), `AND`, `OR`, `NOT`, `IS [NOT] NULL`, parentheses
- **Ordering & Pagination:** `ORDER BY col [ASC|DESC]`, `LIMIT n`

---

## Project Status & Implementation Notes

MiniDBMS implements a custom SQL processing pipeline, catalog manager, JSON-backed storage engine, query executor, hash and B+ tree indexes, joins, and transaction-management mechanisms. The automated test suite contains 91 passing tests covering implemented features, failure scenarios, and multi-file consistency:

- **Phase 0 (Foundations):** Typed error hierarchy, engine facade, test harness.
- **Phase 1 (CLI Shell):** Command loop, formatted result tables, multi-line SQL input.
- **Phase 2 (Database Management):** Database creation, deletion, discovery, session state.
- **Phase 3 (Tables & Catalog):** Column types, NOT NULL, primary keys, schema persistence.
- **Phase 4 (Persistent CRUD):** Statement atomicity, type coercion, full scan.
- **Phase 5 (Lexer & Parser):** SQL subset parser, operator precedence, typed AST.
- **Phase 6 (Indexes & Planning):** Hash index and B+ tree index, automatic maintenance, index scans, and failure atomicity.
- **Phase 7 (Advanced Execution):** Nested-loop join, index nested-loop join, `EXPLAIN` statement.
- **Phase 8 (Transactions & Recovery):** Write-ahead undo log, durable commit markers, full DDL (`CREATE/DROP TABLE`, `CREATE/DROP INDEX`) and DML rollback, single-writer locking across all writes, crash recovery with database ownership verification, PID-based live process protection, active vs. abandoned in-process lock discrimination, safe handling of unreadable locks, and targeted crash-injection tests across persistence boundaries (journal replacement, physical DDL modifications, commit-marker persistence, rollback restoration, and startup recovery).
- **Phase 9 (Page-Based Storage Prototype):** The slotted-page (`Page`, slot directory) and buffer-pool (`BufferPool` with LRU eviction) components have been prototyped and tested separately in `tests/test_storage_pages.py`. Active persistent storage is managed via JSON table files with atomic replacement per blueprint design staging.
- **Phase 10 (Documentation & Release):** Architecture document, SQL reference, benchmarks.

---

## Documentation

- [docs/architecture.md](docs/architecture.md) — Comprehensive technical reference, locking contracts, and complexity table.
- [docs/sql_subset.md](docs/sql_subset.md) — Detailed grammar and semantic specifications.

