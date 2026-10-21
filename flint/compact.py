"""Reversible line templates and lexically exact JSON whitespace removal.

No row sampling, prose rewriting, numeric parsing, code execution or inference.
Template decoding is a byte contract, not proof that an LLM understands a table.
"""

import json
import re

from .common import FlintError, MAX_FILE, digest

RULE = "Interleave template t with columns v for n rows. String columns repeat; arrays index rows; {start,step} means zero-padded start+i*step. String blocks copy verbatim."
DIGITS = re.compile(r"([0-9]+)")
MAX_LINES = 20000
MAX_TEMPLATES = 512


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def checked_text(text):
    if not isinstance(text, str):
        raise FlintError("compact input must be UTF-8 text")
    try:
        if len(text.encode("utf-8")) > MAX_FILE:
            raise FlintError("compact input exceeds 512 KiB")
    except UnicodeEncodeError as error:
        raise FlintError("compact input must be valid UTF-8") from error


def minify_json(text):
    """Keep every non-whitespace source byte, including duplicate keys/numbers."""
    checked_text(text)
    output, quoted, escaped, depth = [], False, False, 0
    for char in text:
        if quoted:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
            output.append(char)
        elif char not in " \t\r\n":
            if char in "[{":
                depth += 1
                if depth > 64:
                    raise FlintError("JSON nesting exceeds 64 levels")
            elif char in "]}":
                depth -= 1
            output.append(char)
    # Validate the ORIGINAL: otherwise '1 2' could become valid '12'. Numeric
    # lexemes stay strings during validation, avoiding overflow and rounding.
    def invalid_constant(value):
        raise ValueError("non-JSON constant " + value)
    try:
        json.loads(text, parse_int=str, parse_float=str, parse_constant=invalid_constant)
    except (ValueError, RecursionError) as error:
        raise FlintError("invalid or excessively nested JSON") from error
    return "".join(output)


def decode_lines(text):
    """Bounded decoder; reject ambiguous or oversized envelopes before expansion."""
    checked_text(text)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate envelope key")
            result[key] = value
        return result
    try:
        envelope = json.loads(text, object_pairs_hook=unique)
    except (ValueError, RecursionError) as error:
        raise FlintError("invalid template envelope") from error
    if (not isinstance(envelope, dict) or set(envelope) != {"flint", "expand", "templates", "blocks"}
            or envelope["flint"] != "lines/1" or envelope["expand"] != RULE):
        raise FlintError("unknown template envelope")
    templates, blocks = envelope["templates"], envelope["blocks"]
    if not isinstance(templates, list) or len(templates) > MAX_TEMPLATES:
        raise FlintError("too many templates")
    if not isinstance(blocks, list) or len(blocks) > MAX_LINES:
        raise FlintError("too many blocks")
    if any(not isinstance(t, list) or not t or len(t) > 65 or
           any(not isinstance(chunk, str) for chunk in t) for t in templates):
        raise FlintError("invalid template")
    output, size, rows = [], 0, 0

    def emit(chunk):
        nonlocal size
        try:
            size += len(chunk.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise FlintError("invalid template UTF-8") from error
        if size > MAX_FILE:
            raise FlintError("decoded output exceeds 512 KiB")
        output.append(chunk)

    for block in blocks:
        if isinstance(block, str):
            emit(block)
            continue
        if not isinstance(block, dict) or set(block) != {"t", "n", "v"}:
            raise FlintError("invalid template block")
        index, count, values = block["t"], block["n"], block["v"]
        if (type(index) is not int or not 0 <= index < len(templates) or
                type(count) is not int or not 1 <= count <= MAX_LINES):
            raise FlintError("invalid template index or count")
        rows += count
        if rows > MAX_LINES:
            raise FlintError("too many expanded rows")
        chunks = templates[index]
        if not isinstance(values, list) or len(values) != len(chunks) - 1:
            raise FlintError("column/template mismatch")
        for column in values:
            if isinstance(column, str):
                continue
            if isinstance(column, dict):
                if (set(column) != {"start", "step"} or not isinstance(column["start"], str)
                        or not re.fullmatch(r"[0-9]{1,32}", column["start"])
                        or type(column["step"]) is not int or abs(column["step"]) > 10**9
                        or int(column["start"]) + (count - 1) * column["step"] < 0):
                    raise FlintError("invalid numeric sequence")
                continue
            if not isinstance(column, list) or len(column) != count or any(not isinstance(v, str) for v in column):
                raise FlintError("invalid template column")
        # Emit individual chunks, with a cumulative cap before any large join.
        for row in range(count):
            for col, chunk in enumerate(chunks):
                emit(chunk)
                if col < len(values):
                    column = values[col]
                    if isinstance(column, dict):
                        emit(str(int(column["start"]) + row * column["step"]).zfill(len(column["start"])))
                    else:
                        emit(column if isinstance(column, str) else column[row])
    return "".join(output)


def encode_lines(text):
    checked_text(text)
    # Split ONLY on LF. Preserve CRLF, tabs, ANSI, and the absent final newline.
    lines = re.findall(r"[^\n]*\n|[^\n]+$", text)
    if len(lines) > MAX_LINES:
        return text
    templates, blocks, indices, pending = [], [], {}, []

    def literal(value):
        pending.append(value)

    def flush():
        if pending:
            blocks.append("".join(pending))
            pending.clear()

    cursor = 0
    while cursor < len(lines):
        parts = DIGITS.split(lines[cursor])
        pattern, values = tuple(parts[::2]), [parts[1::2]]
        stop = cursor + 1
        while stop < len(lines):
            following = DIGITS.split(lines[stop])
            if tuple(following[::2]) != pattern:
                break
            values.append(following[1::2])
            stop += 1
        raw = "".join(lines[cursor:stop])
        if stop - cursor >= 3 and len(pattern) <= 65 and (pattern in indices or len(templates) < MAX_TEMPLATES):
            columns = [list(c) for c in zip(*values)]
            # Fold constant numeric fields back into exact literal chunks.
            # Encode arithmetic sequences only after checking EVERY source value.
            folded, varying = [pattern[0]], []
            for i, column in enumerate(columns):
                if all(value == column[0] for value in column):
                    folded[-1] += column[0] + pattern[i + 1]
                    continue
                candidate = column
                if max(map(len, column)) <= 32:
                    start, step = int(column[0]), int(column[1]) - int(column[0])
                    seq = {"start": column[0], "step": step}
                    if (abs(step) <= 10**9 and len(dumps(seq)) < len(dumps(column)) and
                            all(str(start + j * step).zfill(len(column[0])) == value for j, value in enumerate(column))):
                        candidate = seq
                varying.append(candidate)
                folded.append(pattern[i + 1])
            pattern, columns = tuple(folded), varying
            index = indices.get(pattern, len(templates))
            block = {"t": index, "n": stop - cursor, "v": columns}
            template_cost = 0 if pattern in indices else len(dumps(pattern).encode())
            if template_cost + len(dumps(block).encode()) < len(dumps(raw).encode()):
                if pattern not in indices:
                    indices[pattern] = index
                    templates.append(pattern)
                flush()
                blocks.append(block)
            else:
                literal(raw)
        else:
            literal(raw)
        cursor = stop
    flush()
    candidate = dumps({"flint": "lines/1", "expand": RULE, "templates": templates, "blocks": blocks})
    if len(candidate.encode()) >= len(text.encode()):
        return text
    if decode_lines(candidate) != text:
        raise FlintError("template round-trip invariant failed")
    return candidate


def compact_text(text, *, format="auto"):
    checked_text(text)
    if format not in ("auto", "json", "lines", "columns"):
        raise FlintError("format must be auto, json, columns or lines")
    if format == "json":
        return minify_json(text), "json-whitespace"
    if format == "columns":
        from .columns import encode_columns
        return encode_columns(text)
    if format == "auto" and text.lstrip().startswith(("{", "[")):
        try:
            from .columns import encode_columns
            return encode_columns(text)
        except FlintError:
            # A log can begin with '[build]'. Fall back to exact templates;
            # decoding returns the original, never a repaired JSON document.
            pass
    candidate = encode_lines(text)
    return candidate, "line-templates" if candidate != text else "verbatim"


def expand_text(text):
    checked_text(text)
    try:
        kind = json.loads(text).get("flint")
    except (ValueError, AttributeError, RecursionError) as error:
        raise FlintError("invalid expansion envelope") from error
    if kind == "columns/1":
        from .columns import decode_columns
        return decode_columns(text)
    return decode_lines(text)


def compact_file(store, path, *, format="auto"):
    data = store.file_bytes(path)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise FlintError("compact supports UTF-8 files only") from error
    content, method = compact_text(text, format=format)
    with store.db:
        store._save(data)
    return {"content": content, "path": path, "original": digest(data), "method": method,
            "input_bytes": len(data), "output_bytes": len(content.encode()),
            "output_sha256": digest(content.encode())}


def render_compact(result, original, *, report=False):
    """Emit only the recovery contract by default; diagnostics are opt-in.

The result object still contains sizes and the output hash for programmatic
callers. Repeating those fields in every agent message wastes context.
"""
    if digest(original) != result["original"]:
        raise FlintError("render source does not match stored original")
    keys = (key for key in result if key != "content") if report else ("original", "method")
    metadata = json.dumps({key:result[key] for key in keys}, ensure_ascii=False, separators=(",", ":")) + "\n"
    if not report and len(result["content"].encode()) + len(metadata.encode()) >= len(original):
        return original.decode(), ""
    return result["content"], metadata
