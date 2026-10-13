"""Lexer: SQL text -> list[Token] with line/column positions."""
from dataclasses import dataclass

from .errors import LexError

KEYWORDS = {
    "CREATE", "DROP", "DATABASE", "DATABASES", "TABLE", "TABLES", "SHOW", "USE", "DESCRIBE",
    "INSERT", "INTO", "VALUES", "SELECT", "FROM", "WHERE", "UPDATE", "SET", "DELETE",
    "ORDER", "BY", "ASC", "DESC", "LIMIT", "AND", "OR", "NOT", "IS", "NULL", "TRUE", "FALSE",
    "PRIMARY", "KEY", "INTEGER", "INT", "FLOAT", "TEXT", "BOOLEAN",
    "INDEX", "JOIN", "INNER", "ON", "EXPLAIN", "BEGIN", "COMMIT", "ROLLBACK", "TRANSACTION", "HASH", "BTREE",
}



@dataclass(frozen=True)
class Token:
    kind: str  # KEYWORD IDENT INT FLOAT STRING OP PUNCT EOF
    value: object
    line: int
    col: int


def tokenize(text):
    tokens, i, n = [], 0, len(text)
    line, col = 1, 1
    while i < n:
        c = text[i]
        if c == "\n":
            i += 1; line += 1; col = 1
            continue
        if c.isspace():
            i += 1; col += 1
            continue
        if text.startswith("--", i):  # comment to end of line
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            word = text[i:j]
            if word.upper() in KEYWORDS:
                tokens.append(Token("KEYWORD", word.upper(), line, col))
            else:
                tokens.append(Token("IDENT", word.lower(), line, col))
            col += j - i; i = j
            continue
        if c.isdigit():
            j = i
            while j < n and text[j].isdigit():
                j += 1
            is_float = False
            if j + 1 < n and text[j] == "." and text[j + 1].isdigit():
                is_float = True
                j += 1
                while j < n and text[j].isdigit():
                    j += 1
            raw = text[i:j]
            tokens.append(Token("FLOAT" if is_float else "INT",
                                float(raw) if is_float else int(raw), line, col))
            col += j - i; i = j
            continue
        if c == "'":
            j, buf = i + 1, []
            while True:
                if j >= n or text[j] == "\n":
                    raise LexError(f"Unterminated string at line {line}, column {col}")
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":  # escaped quote
                        buf.append("'"); j += 2
                        continue
                    break
                buf.append(text[j]); j += 1
            tokens.append(Token("STRING", "".join(buf), line, col))
            col += j + 1 - i; i = j + 1
            continue
        two = text[i:i + 2]
        if two in ("!=", "<>", "<=", ">="):
            tokens.append(Token("OP", "!=" if two == "<>" else two, line, col))
            i += 2; col += 2
            continue
        if c in "=<>-":
            tokens.append(Token("OP", c, line, col))
            i += 1; col += 1
            continue
        if c in "(),;*.":
            tokens.append(Token("PUNCT", c, line, col))
            i += 1; col += 1
            continue
        raise LexError(f"Unexpected character {c!r} at line {line}, column {col}")
    tokens.append(Token("EOF", None, line, col))
    return tokens
