"""CLI shell: input, output formatting and session state only. All SQL goes through Engine."""
import os

from .engine import Engine
from .errors import MiniDBError

VERSION = "MiniDBMS 0.1.0"
HELP = """Commands:
  help      show this message
  version   show version
  clear     clear the screen
  exit      quit (also: quit)

SQL (end each statement with ';'):
  CREATE DATABASE n; DROP DATABASE n; SHOW DATABASES; USE n;
  CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT NOT NULL, age INTEGER);
  DROP TABLE t; SHOW TABLES; DESCRIBE t;
  CREATE [HASH|BTREE] INDEX idx ON t (col); DROP INDEX idx;
  INSERT INTO t [(cols)] VALUES (...), (...);
  SELECT cols|* FROM t [JOIN t2 ON t.c1 = t2.c2] [WHERE expr] [ORDER BY col [ASC|DESC]] [LIMIT n];
  UPDATE t SET col = value [, ...] [WHERE expr];
  DELETE FROM t [WHERE expr];
  EXPLAIN <statement>;
  BEGIN; COMMIT; ROLLBACK;"""


def format_value(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    return str(v)


def format_result(result):
    if not result.columns:
        return result.message
    cells = [[format_value(v) for v in row] for row in result.rows]
    widths = [max([len(c)] + [len(r[i]) for r in cells]) for i, c in enumerate(result.columns)]
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    def line(vals):
        return "| " + " | ".join(v.ljust(w) for v, w in zip(vals, widths)) + " |"

    out = [sep, line(result.columns), sep] + [line(r) for r in cells] + [sep]
    footer = f"{len(cells)} row(s)"
    if "elapsed_ms" in result.stats:
        footer += f" ({result.stats['elapsed_ms']:.2f} ms, {result.stats['rows_examined']} examined)"
    return "\n".join(out + [footer])


class Shell:
    def __init__(self, engine, input_fn=input, output_fn=print):
        self.engine, self.input, self.output = engine, input_fn, output_fn
        self.session = engine.new_session()

    def prompt(self, continuing):
        if continuing:
            return "     ...> "
        return f"minidb({self.session.db})> " if self.session.db else "minidb> "

    def run(self):
        self.output(f"{VERSION} - type 'help' for commands, 'exit' to quit.")
        buffer = ""
        while True:
            try:
                line = self.input(self.prompt(bool(buffer)))
            except EOFError:
                break
            except KeyboardInterrupt:
                self.output("")
                buffer = ""
                continue
            stripped = line.strip()
            if not buffer:
                cmd = stripped.rstrip(";").lower()
                if cmd in ("exit", "quit"):
                    break
                if cmd == "help":
                    self.output(HELP); continue
                if cmd == "version":
                    self.output(VERSION); continue
                if cmd == "clear":
                    os.system("cls" if os.name == "nt" else "clear"); continue
                if not stripped:
                    continue
            buffer += line + "\n"
            if stripped.endswith(";"):
                self.run_sql(buffer)
                buffer = ""
        self.output("Bye.")

    def run_sql(self, sql):
        try:
            for result in self.engine.execute_many(sql, self.session):
                self.output(format_result(result))
        except MiniDBError as e:
            self.output(f"Error: {e}")


def main(data_dir="data"):
    Shell(Engine(data_dir)).run()
