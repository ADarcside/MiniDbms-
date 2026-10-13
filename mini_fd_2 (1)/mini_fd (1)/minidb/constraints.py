"""Central correctness rules: type coercion, NOT NULL, primary-key uniqueness.
Used by INSERT and UPDATE alike so neither can bypass a rule."""
from .errors import ConstraintError


def coerce(col, value):
    if value is None:
        if col.not_null or col.primary_key:
            raise ConstraintError(f"Column '{col.name}' cannot be NULL")
        return None
    t = col.type
    if t == "INTEGER" and isinstance(value, int) and not isinstance(value, bool):
        return value
    if t == "FLOAT" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if t == "TEXT" and isinstance(value, str):
        return value
    if t == "BOOLEAN" and isinstance(value, bool):
        return value
    raise ConstraintError(f"Column '{col.name}' expects {t}, got {value!r}")


def check_primary_key_unique(schema, rows):
    pk = schema.primary_key
    if pk is None:
        return
    seen = set()
    for row in rows:
        key = row["values"][pk]
        if key in seen:
            raise ConstraintError(f"Duplicate primary key {key!r} for column '{pk}'")
        seen.add(key)
