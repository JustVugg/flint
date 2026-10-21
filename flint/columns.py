"""Lossless JSON record columns: constants, exact sequences, cycles, exceptions.

Only canonical JSON lexemes are factored; other valid inputs keep the original
lexemes via minification. No rows are sampled or dropped. This is columnar
encoding, not a claim to have invented column stores or sparse compression.
"""

from collections import Counter, namedtuple
import json
import re

from .common import FlintError, MAX_FILE

MAX_ROWS = 4096
MAX_COLUMNS = 64
MAX_TABLES = 32
MAX_NODES = 32768
RULE = "Replace data at path by rows from columns. at selects present rows; except overrides values; order permutes column indices."
NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")
INTEGER = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")


Number = namedtuple("Number", "raw")


class Unsupported(ValueError):
    pass


def dump(value):
    if isinstance(value, Number):
        if not NUMBER.fullmatch(value.raw):
            raise FlintError("invalid JSON number lexeme")
        return value.raw
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + dump(v) for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ",".join(map(dump, value)) + "]"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def load(text):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise Unsupported("duplicate object keys")
            out[key] = value
        return out
    def constant(value):
        raise ValueError("non-JSON constant")
    result = json.loads(text, parse_int=Number, parse_float=Number,
                        object_pairs_hook=pairs, parse_constant=constant)
    count = 0
    stack = [(result, 0)]
    while stack:
        value, depth = stack.pop()
        count += 1
        if depth > 64 or count > MAX_NODES:
            raise Unsupported("JSON tree exceeds bounds")
        if isinstance(value, dict):
            for key in value:
                key.encode("utf-8")
            stack.extend((v, depth + 1) for v in value.values())
        elif isinstance(value, list):
            stack.extend((v, depth + 1) for v in value)
        elif isinstance(value, str):
            value.encode("utf-8")
    return result


def integer(value, low, high):
    raw = value.raw if isinstance(value, Number) else str(value) if type(value) is int else ""
    if not INTEGER.fullmatch(raw) or len(raw) > 33:
        raise FlintError("invalid integer in column envelope")
    result = int(raw)
    if not low <= result <= high:
        raise FlintError("column integer outside limit")
    return result


def vector(values):
    """Select the shortest exact descriptor; candidates checked across ALL rows."""
    serialized = [dump(v) for v in values]
    choices = [{"values": values}]
    if len(set(serialized)) == 1:
        choices.append({"constant": values[0]})
        return min(choices, key=lambda item: len(dump(item).encode()))
    if len(values) >= 3 and all(isinstance(v, Number) and INTEGER.fullmatch(v.raw)
                                and len(v.raw) <= 32 and v.raw != "-0" for v in values):
        first, step = int(values[0].raw), int(values[1].raw) - int(values[0].raw)
        if abs(step) <= 10**9 and all(str(first + i * step) == v.raw for i, v in enumerate(values)):
            choices.append({"start": values[0], "step": step})
    if len(values) >= 3 and all(isinstance(v, str) for v in values):
        matches = [re.fullmatch(r"(.*?)([0-9]+)([^0-9]*)", v, re.DOTALL) for v in values]
        if all(matches) and max(len(m[2]) for m in matches) <= 32:
            first = int(matches[0][2])
            step = int(matches[1][2]) - first
            prefix, suffix = matches[0][1], matches[0][3]
            width = len(matches[0][2]) if matches[0][2].startswith('0') and len(matches[0][2]) > 1 else 0
            if (abs(step) <= 10**9 and first + (len(values)-1)*step >= 0 and
                    all(prefix + str(first + i * step).zfill(width) + suffix == v for i, v in enumerate(values))):
                spec = {"prefix": prefix, "start": first, "step": step}
                if suffix:
                    spec["suffix"] = suffix
                if width:
                    spec["width"] = width
                choices.append(spec)
    if len(set(serialized)) == len(values):
        # With all distinct values a cycle needs every literal plus exception
        # indices: it cannot improve on the plain value vector. Avoid building
        # eight impossible candidates, while retaining exact sequences.
        return min(choices, key=lambda item: len(dump(item).encode()))
    # Mode per residue captures cycles with sparse anomalies. This never
    # approximates: exceptions record every value differing from the cycle.
    for period in range(1, min(8, len(values) // 2) + 1):
        common = [Counter(serialized[i::period]).most_common(1)[0][0] for i in range(period)]
        by_text = dict(zip(serialized, values))
        pattern = [by_text[v] for v in common]
        exceptions = {str(i): value for i, value in enumerate(values) if serialized[i] != common[i % period]}
        candidate = {"constant": pattern[0]} if period == 1 else {"cycle": pattern}
        if exceptions:
            candidate["except"] = exceptions
        choices.append(candidate)
    return min(choices, key=lambda item: len(dump(item).encode()))


def table(rows, path):
    if not 3 <= len(rows) <= MAX_ROWS or any(not isinstance(row, dict) or not row for row in rows):
        return None
    fields = list(dict.fromkeys(key for row in rows for key in row))
    if len(fields) > MAX_COLUMNS:
        return None
    positions = {key: i for i, key in enumerate(fields)}
    order = {str(i): [positions[key] for key in row] for i, row in enumerate(rows)
             if list(row) != [key for key in fields if key in row]}
    columns = {}
    for key in fields:
        present = [i for i, row in enumerate(rows) if key in row]
        description = vector([rows[i][key] for i in present])
        if len(present) != len(rows):
            description = {"at": present, **description}
        columns[key] = description
    result = {"path": path, "rows": len(rows), "columns": columns}
    if order:
        result["order"] = order
    return result if len(dump(result).encode()) < len(dump(rows).encode()) else None


def encode_columns(text):
    from .compact import minify_json
    minimal = minify_json(text)
    try:
        source = load(minimal)
        # This gate keeps escaped key/value spellings and lexical number forms
        # exact. No float conversion or silently merged duplicate keys.
        if dump(source) != minimal:
            return minimal, "json-whitespace"
    except (ValueError, RecursionError, UnicodeError):
        return minimal, "json-whitespace"
    tables = []

    def visit(value, path):
        if isinstance(value, list):
            proposed = table(value, path) if len(tables) < MAX_TABLES else None
            if proposed is not None:
                tables.append(proposed)
                return None
            return [visit(v, path + [i]) for i, v in enumerate(value)]
        if isinstance(value, dict):
            return {key: visit(v, path + [key]) for key, v in value.items()}
        return value

    data = visit(source, [])
    if not tables:
        return minimal, "json-whitespace"
    candidate = dump({"flint": "columns/1", "expand": RULE, "data": data, "tables": tables})
    if len(candidate.encode()) >= len(minimal.encode()):
        return minimal, "json-whitespace"
    if decode_columns(candidate) != minimal:
        raise FlintError("column reconstruction invariant failed")
    return candidate, "json-columns"


def decode_columns(text):
    from .compact import checked_text
    checked_text(text)
    try:
        envelope = load(text)
    except (ValueError, RecursionError, UnicodeError) as error:
        raise FlintError("invalid column envelope") from error
    if (not isinstance(envelope, dict) or set(envelope) != {"flint", "expand", "data", "tables"}
            or envelope["flint"] != "columns/1" or envelope["expand"] != RULE):
        raise FlintError("unknown column envelope")
    data, tables = envelope["data"], envelope["tables"]
    if not isinstance(tables, list) or not 1 <= len(tables) <= MAX_TABLES:
        raise FlintError("invalid column table count")
    # Estimate expansion bytes BEFORE constructing row objects. Count repeated
    # values in full, so an envelope cannot turn a short constant into a bomb.
    remaining = MAX_FILE - len(dump(data).encode())
    total_cells = 0
    seen_paths = []
    for record in tables:
        if (not isinstance(record, dict) or not {"path", "rows", "columns"} <= set(record)
                or set(record) - {"path", "rows", "columns", "order"}):
            raise FlintError("invalid column table")
        count = integer(record["rows"], 1, MAX_ROWS)
        path, columns = record["path"], record["columns"]
        if not isinstance(path, list) or len(path) > 64:
            raise FlintError("invalid table path")
        path = [p if isinstance(p, str) else integer(p, 0, MAX_ROWS * 8) for p in path]
        if any(path[:len(other)] == other or other[:len(path)] == path for other in seen_paths):
            raise FlintError("overlapping table paths")
        seen_paths.append(path)
        if not isinstance(columns, dict) or not 1 <= len(columns) <= MAX_COLUMNS:
            raise FlintError("invalid columns")
        expanded = []
        remaining -= count * 3 + 2
        for key, spec in columns.items():
            if not isinstance(spec, dict):
                raise FlintError("invalid column descriptor")
            present = spec.get("at")
            if present is None:
                if "at" in spec:
                    raise FlintError("null presence indices")
                present = list(range(count))
            else:
                if not isinstance(present, list) or len(present) > count:
                    raise FlintError("invalid presence indices")
                present = [integer(i, 0, count - 1) for i in present]
                if not present or present != sorted(set(present)):
                    raise FlintError("presence indices must be nonempty, sorted and unique")
            size = len(present)
            total_cells += size
            if total_cells > MAX_NODES:
                raise FlintError("too many decoded cells")
            fields = set(spec) - {"at", "except"}
            if fields == {"values"}:
                values = spec["values"]
                if not isinstance(values, list) or len(values) != size:
                    raise FlintError("column value count mismatch")
            elif fields == {"constant"}:
                values = [spec["constant"]] * size
            elif fields == {"start", "step"}:
                start = integer(spec["start"], -(10**32), 10**32)
                step = integer(spec["step"], -(10**9), 10**9)
                values = [Number(str(start + i * step)) for i in range(size)]
            elif {"prefix", "start", "step"} <= fields and not fields - {"prefix", "start", "step", "suffix", "width"}:
                prefix, suffix = spec["prefix"], spec.get("suffix", "")
                if not isinstance(prefix, str) or not isinstance(suffix, str):
                    raise FlintError("invalid string sequence")
                start = integer(spec["start"], 0, 10**32)
                step = integer(spec["step"], -(10**9), 10**9)
                width = integer(spec.get("width", 0), 0, 32)
                if start + (size - 1) * step < 0:
                    raise FlintError("negative string sequence")
                # Refuse oversized prefixes before generating repeated strings.
                if size * (len(prefix.encode()) + len(suffix.encode()) + 1) > remaining:
                    raise FlintError("string sequence exceeds output cap")
                values = [prefix + str(start + i * step).zfill(width) + suffix for i in range(size)]
            elif fields == {"cycle"}:
                cycle = spec["cycle"]
                if not isinstance(cycle, list) or not 1 <= len(cycle) <= 8:
                    raise FlintError("invalid column cycle")
                values = [cycle[i % len(cycle)] for i in range(size)]
            else:
                raise FlintError("unknown column descriptor")
            overrides = spec.get("except", {})
            if not isinstance(overrides, dict) or len(overrides) > size:
                raise FlintError("invalid column exceptions")
            values = list(values)
            for index, value in overrides.items():
                if not re.fullmatch(r"0|[1-9][0-9]{0,3}", index) or int(index) >= size:
                    raise FlintError("invalid exception index")
                values[int(index)] = value
            key_cost = len(json.dumps(key, ensure_ascii=False).encode()) + 2
            for value in values:
                remaining -= key_cost + len(dump(value).encode())
                if remaining < 0:
                    raise FlintError("decoded JSON exceeds 512 KiB")
            expanded.append((key, present, values))
        rows = [{} for _ in range(count)]
        for key, present, values in expanded:
            for index, value in zip(present, values):
                rows[index][key] = value
        orders = record.get("order", {})
        if not isinstance(orders, dict) or len(orders) > count:
            raise FlintError("invalid property order channel")
        keys = list(columns)
        for index, order in orders.items():
            if not re.fullmatch(r"0|[1-9][0-9]{0,3}", index) or int(index) >= count or not isinstance(order, list):
                raise FlintError("invalid row order")
            order = [integer(i, 0, len(keys)-1) for i in order]
            row = rows[int(index)]
            if len(order) != len(row) or len(set(order)) != len(order) or set(keys[i] for i in order) != set(row):
                raise FlintError("row order is not a property permutation")
            rows[int(index)] = {keys[i]: row[keys[i]] for i in order}
        try:
            if not path:
                if data is not None:
                    raise FlintError("table must replace null placeholder")
                data = rows
            else:
                parent = data
                for part in path[:-1]:
                    if isinstance(parent, list) != (type(part) is int):
                        raise FlintError("table path type mismatch")
                    parent = parent[part]
                part = path[-1]
                if isinstance(parent, list) != (type(part) is int) or parent[part] is not None:
                    raise FlintError("table must replace null placeholder")
                parent[part] = rows
        except (TypeError, IndexError, KeyError) as error:
            raise FlintError("table path missing") from error
    result = dump(data)
    checked_text(result)
    return result
