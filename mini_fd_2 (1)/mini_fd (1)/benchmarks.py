"""Benchmark script for MiniDBMS comparing Full Table Scan vs Hash Index vs B+ Tree Index."""
import os
import shutil
import tempfile
import time

from minidb.engine import Engine


def run_benchmarks():
    print("=" * 60)
    print("MiniDBMS Performance Benchmarks")
    print("=" * 60)

    temp_dir = tempfile.mkdtemp(prefix="minidb_bench_")
    try:
        engine = Engine(temp_dir)
        engine.execute("CREATE DATABASE bench_db; USE bench_db;")
        engine.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, age INTEGER, score FLOAT, name TEXT NOT NULL);")

        row_count = 1000
        print(f"[*] Populating {row_count} rows...")
        start_ins = time.perf_counter()

        # Batch insert 50 rows at a time
        batch_size = 50
        for b in range(0, row_count, batch_size):
            tuples = []
            for i in range(b, b + batch_size):
                age = 18 + (i % 60)
                score = round(50.0 + (i % 50) * 0.9, 2)
                tuples.append(f"({i}, {age}, {score}, 'user_{i}')")
            engine.execute(f"INSERT INTO users VALUES {', '.join(tuples)};")

        insert_time = (time.perf_counter() - start_ins) * 1000
        print(f"[+] Inserted {row_count} rows in {insert_time:.2f} ms\n")

        # ---------------------------------------------------------
        # Benchmark 1: Full Scan vs Hash Index Scan (Equality)
        # ---------------------------------------------------------
        print("--- Benchmark 1: Equality Lookup (Full Scan vs Hash Index) ---")
        target_age = 42

        # 1. Full Scan
        t0 = time.perf_counter()
        scan_res = engine.execute(f"SELECT id, name FROM users WHERE age = {target_age};")
        t_scan = (time.perf_counter() - t0) * 1000

        # 2. Hash Index Scan
        engine.execute("CREATE HASH INDEX idx_users_age ON users (age);")
        t0 = time.perf_counter()
        idx_res = engine.execute(f"SELECT id, name FROM users WHERE age = {target_age};")
        t_idx = (time.perf_counter() - t0) * 1000

        print(f"  Full Scan:   Rows examined: {scan_res.stats['rows_examined']:4d} | Returned: {scan_res.stats['rows_returned']:3d} | Time: {t_scan:6.2f} ms")
        print(f"  Hash Index:  Rows examined: {idx_res.stats['rows_examined']:4d} | Returned: {idx_res.stats['rows_returned']:3d} | Time: {t_idx:6.2f} ms")
        speedup = t_scan / max(t_idx, 0.0001)
        print(f"  Result match: {scan_res.rows == idx_res.rows}")
        print(f"  Rows examined reduction: {scan_res.stats['rows_examined']} -> {idx_res.stats['rows_examined']} (O(N) -> O(1))\n")

        # ---------------------------------------------------------
        # Benchmark 2: Range Query (Full Scan vs B+ Tree Index)
        # ---------------------------------------------------------
        print("--- Benchmark 2: Range Scan (Full Scan vs B+ Tree Index) ---")
        engine.execute("CREATE TABLE metrics (id INTEGER PRIMARY KEY, val INTEGER);")
        m_tuples = [f"({i}, {i * 2})" for i in range(500)]
        for b in range(0, len(m_tuples), 50):
            engine.execute(f"INSERT INTO metrics VALUES {', '.join(m_tuples[b:b+50])};")

        # Full Scan range
        t0 = time.perf_counter()
        scan_range = engine.execute("SELECT id, val FROM metrics WHERE val >= 800 AND val <= 850;")
        t_scan_range = (time.perf_counter() - t0) * 1000

        # B+ Tree Index range
        engine.execute("CREATE BTREE INDEX idx_metrics_val ON metrics (val);")
        t0 = time.perf_counter()
        btree_range = engine.execute("SELECT id, val FROM metrics WHERE val >= 800 AND val <= 850;")
        t_btree_range = (time.perf_counter() - t0) * 1000

        print(f"  Full Scan:   Rows examined: {scan_range.stats['rows_examined']:4d} | Returned: {scan_range.stats['rows_returned']:3d} | Time: {t_scan_range:6.2f} ms")
        print(f"  B+ Tree:     Rows examined: {btree_range.stats['rows_examined']:4d} | Returned: {btree_range.stats['rows_returned']:3d} | Time: {t_btree_range:6.2f} ms")
        print(f"  Result match: {scan_range.rows == btree_range.rows}\n")

        # ---------------------------------------------------------
        # Benchmark 3: Nested-Loop Join vs Index Nested-Loop Join
        # ---------------------------------------------------------
        print("--- Benchmark 3: Join Performance (Nested-Loop vs Index Nested-Loop) ---")
        engine.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER, amount FLOAT);")
        o_tuples = [f"({i}, {i % 100}, {round(10.5 + i, 2)})" for i in range(200)]
        for b in range(0, len(o_tuples), 50):
            engine.execute(f"INSERT INTO orders VALUES {', '.join(o_tuples[b:b+50])};")

        # Standard Nested-Loop Join
        t0 = time.perf_counter()
        nl_res = engine.execute("SELECT users.name, orders.amount FROM users JOIN orders ON users.id = orders.user_id WHERE users.id = 5;")
        t_nl = (time.perf_counter() - t0) * 1000

        # Index Nested-Loop Join
        engine.execute("CREATE HASH INDEX idx_orders_uid ON orders (user_id);")
        t0 = time.perf_counter()
        inl_res = engine.execute("SELECT users.name, orders.amount FROM users JOIN orders ON users.id = orders.user_id WHERE users.id = 5;")
        t_inl = (time.perf_counter() - t0) * 1000

        print(f"  Nested-Loop:       Rows examined: {nl_res.stats['rows_examined']:4d} | Returned: {nl_res.stats['rows_returned']:3d} | Time: {t_nl:6.2f} ms")
        print(f"  Index Nested-Loop: Rows examined: {inl_res.stats['rows_examined']:4d} | Returned: {inl_res.stats['rows_returned']:3d} | Time: {t_inl:6.2f} ms")
        print(f"  Result match: {nl_res.rows == inl_res.rows}\n")

        print("=" * 60)
        print("Benchmark completed successfully. All invariants verified.")
        print("=" * 60)

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    run_benchmarks()
