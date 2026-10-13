# MiniDBMS SQL Subset & Language Grammar

MiniDBMS implements a subset of standard SQL tailored for relational DBMS operations. Keywords are case-insensitive. Identifiers are normalized to lowercase and follow `[a-z_][a-z0-9_]{0,63}`. Statements terminate with `;`.

A whole script is lexed and parsed before execution begins; any syntax error stops execution before any statement runs. Single-line comments begin with `--`.

---

## 1. Supported Statements

### Database Management
```sql
CREATE DATABASE <db_name>;
DROP DATABASE <db_name>;
SHOW DATABASES;
USE <db_name>;
```

### Schema & Table Management
```sql
CREATE TABLE <table_name> (
    <col_name> <TYPE> [PRIMARY KEY] [NOT NULL],
    ...
);
DROP TABLE <table_name>;
SHOW TABLES;
DESCRIBE <table_name>;
```
*Supported Types:* `INTEGER` (or `INT`), `FLOAT`, `TEXT`, `BOOLEAN`.

### Index Management
```sql
CREATE [HASH | BTREE] INDEX <index_name> ON <table_name> (<column_name>);
DROP INDEX <index_name>;
```
- `HASH`: Fast equality lookup ($O(1)$).
- `BTREE`: Balanced tree supporting both equality and range scans ($O(\log n)$).

### Data Manipulation Language (DML)
```sql
-- INSERT
INSERT INTO <table_name> [(col1, col2, ...)] VALUES (val1, val2, ...), (...);

-- SELECT (Single Table or JOIN)
SELECT <col1, col2, ... | *>
FROM <table_name>
[INNER JOIN <table_name2> ON <table1.col> = <table2.col>]
[WHERE <expression>]
[ORDER BY <col> [ASC | DESC], ...]
[LIMIT <count>];

-- UPDATE
UPDATE <table_name>
SET <col1> = <val1> [, <col2> = <val2>, ...]
[WHERE <expression>];

-- DELETE
DELETE FROM <table_name>
[WHERE <expression>];
```

### Query Execution Plan (EXPLAIN)
```sql
EXPLAIN <statement>;
```
Displays the operator plan (Table Scan, Index Scan, Filter, Join Scan, Sort, Limit, Projection).

### Transaction Control
```sql
BEGIN [TRANSACTION];
COMMIT [TRANSACTION];
ROLLBACK [TRANSACTION];
```

---

## 2. Expressions & Operators

- **Precedence (lowest to highest):**
  1. `OR`
  2. `AND`
  3. `NOT`
  4. Comparisons (`=`, `!=`, `<>`, `<`, `<=`, `>`, `>=`)
  5. Primary expressions: Columns (e.g. `col` or `table.col`), Literals, Parentheses `(...)`, `col IS [NOT] NULL`
- **Literals:**
  - Integers: `42`, `-10`
  - Floating point: `3.14`, `-0.5`
  - Strings: `'hello'` (escape single quotes with `''`, e.g., `'it''s'`)
  - Booleans: `TRUE`, `FALSE`
  - Nulls: `NULL`

---

## 3. Database Semantics & Correctness Rules

- **Three-Valued Logic (NULL):**
  Comparisons with `NULL` evaluate to Unknown (`None`) and never evaluate to True. Use `IS NULL` or `IS NOT NULL`. In `ORDER BY ASC`, `NULL` values sort first.
- **Type Safety & Implicit Coercion:**
  Only `INTEGER` to `FLOAT` promotion is permitted. Type mismatches raise `ConstraintError`. Comparing incompatible types in WHERE raises `SemanticError`.
- **Primary Key & NOT NULL:**
  At most one `PRIMARY KEY` is supported per table; primary key implies `NOT NULL` and must be unique. Uniqueness is enforced on both `INSERT` and `UPDATE`.
- **Single-Writer Concurrency:**
  Database operations acquire a `.lock` file to prevent concurrent conflicting writes.
- **Statement & Transaction Atomicity:**
  A failing multi-row statement leaves data unchanged. In multi-statement transactions, `ROLLBACK` or abnormal crash recovery reverts all changes made during the transaction.
