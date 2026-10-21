"""Bounded TOON subset interoperable with Caveman v4.0.1's default encoder.

Python adaptation of the table/scalar rules inspected in Caveman's Apache-2.0
toon_encode.go / toon_decode.go. See vendor/caveman-toon/NOTICE and LICENSE.
No key folding, tab delimiter, or heterogeneous object-array syntax. Unsupported
inputs fall back to JSON in the portfolio; no rows or values are discarded.
"""

import json
import re

from .common import FlintError, MAX_FILE
from .columns import Number, NUMBER, MAX_NODES, MAX_ROWS, MAX_COLUMNS, dump, load
from .compact import checked_text

KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*\Z")
HEADER = re.compile(r"(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)?\[(?P<n>0|[1-9][0-9]{0,3})\](?:\{(?P<fields>[^}]*)\})?:(?: (?P<values>.*))?\Z")


def scalar(value):
    return not isinstance(value, (dict, list))


def cell(value):
    if not isinstance(value, str):
        return dump(value)
    if (not value or value.strip() != value or any(c in value for c in '\n\r\t":,')
            or value[0] in '[{"-0123456789' or value in ('true', 'false', 'null')):
        return json.dumps(value, ensure_ascii=False)
    return value


def encode_toon(text):
    checked_text(text)
    value = load(text)
    lines = []

    def emit(item, key, depth):
        if depth > 64 or (key and not KEY.fullmatch(key)):
            raise ValueError('unsupported TOON key or nesting')
        indent = '  ' * depth
        if scalar(item):
            lines.append(indent + (key + ': ' if key else '') + cell(item))
        elif isinstance(item, dict):
            if not item:
                lines.append(indent + (key + ': ' if key else '') + '{}')
            else:
                if key:
                    lines.append(indent + key + ':')
                for name, child in item.items():
                    if not KEY.fullmatch(name):
                        raise ValueError('unsupported TOON key')
                    emit(child, name, depth + bool(key))
        elif len(item) > MAX_ROWS:
            raise ValueError('TOON row count exceeds limit')
        elif all(scalar(v) for v in item):
            lines.append(indent + key + '[' + str(len(item)) + ']:' +
                         (' ' + ','.join(map(cell, item)) if item else ''))
        else:
            if not item or not isinstance(item[0], dict) or not item[0]:
                raise ValueError('unsupported TOON array')
            fields = list(item[0])
            if len(fields) > MAX_COLUMNS or any(not KEY.fullmatch(k) for k in fields):
                raise ValueError('unsupported TOON columns')
            if any(not isinstance(row, dict) or list(row) != fields or
                   any(not scalar(v) for v in row.values()) for row in item):
                raise ValueError('TOON table requires uniform scalar records')
            lines.append(indent + key + '[' + str(len(item)) + ']{' + ','.join(fields) + '}:')
            lines.extend(indent + '  ' + ','.join(cell(row[k]) for k in fields) for row in item)

    emit(value, '', 0)
    result = '\n'.join(lines)
    checked_text(result)
    if load(decode_toon(result)) != value:
        raise FlintError('TOON reconstruction invariant failed')
    return result


def cells(text):
    result, start, quoted, escaped = [], 0, False, False
    for i, char in enumerate(text):
        if escaped:
            escaped = False
        elif quoted and char == '\\':
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif char == ',' and not quoted:
            result.append(text[start:i])
            start = i + 1
    if quoted or escaped:
        raise FlintError('unterminated TOON cell')
    result.append(text[start:])
    return result


def parse_cell(text):
    if not text or text.strip() != text:
        raise FlintError('invalid TOON scalar')
    if text.startswith('"'):
        try:
            value = json.loads(text)
            if not isinstance(value, str):
                raise ValueError('not a string')
            value.encode('utf-8')
            return value
        except (ValueError, UnicodeError) as error:
            raise FlintError('invalid quoted TOON cell') from error
    if text in ('true', 'false', 'null'):
        return {'true': True, 'false': False, 'null': None}[text]
    if NUMBER.fullmatch(text):
        return Number(text)
    if any(c in text for c in '\n\r\t"'):
        raise FlintError('invalid literal TOON cell')
    return text


def decode_toon(text):
    checked_text(text)
    raw = text.split('\n')
    if raw and not raw[-1]:
        raw.pop()
    lines = []
    for line in raw:
        n = len(line) - len(line.lstrip(' '))
        if n % 2 or n > 128 or not line[n:]:
            raise FlintError('invalid TOON indentation')
        lines.append((n // 2, line[n:]))
    if not lines or lines[0][0] != 0 or len(lines) > MAX_NODES:
        raise FlintError('invalid TOON document')
    nodes, remaining = 0, MAX_FILE

    def charge(size):
        nonlocal remaining
        remaining -= size
        if remaining < 0:
            raise FlintError('decoded TOON exceeds 512 KiB')

    def array(match, i, depth):
        nonlocal nodes
        count = int(match['n'])
        if count > MAX_ROWS:
            raise FlintError('TOON array exceeds limit')
        fields = match['fields']
        if fields is None:
            values = [] if match['values'] is None else [parse_cell(v) for v in cells(match['values'])]
            if len(values) != count:
                raise FlintError('TOON array count mismatch')
            nodes += count
            charge(len(dump(values).encode()))
            return values, i + 1
        keys = fields.split(',')
        if (match['values'] is not None or not 1 <= len(keys) <= MAX_COLUMNS or
                len(set(keys)) != len(keys) or any(not KEY.fullmatch(k) for k in keys) or
                i + count >= len(lines)):
            raise FlintError('invalid TOON table header or missing rows')
        nodes += count * (len(keys) + 1)
        if nodes > MAX_NODES:
            raise FlintError('TOON table exceeds node cap')
        # Bound repeated column names before constructing rows or joining JSON.
        charge(count * (2 + sum(len(json.dumps(k).encode()) + 2 for k in keys)) + 2)
        rows = []
        for j in range(i + 1, i + count + 1):
            if lines[j][0] != depth + 1:
                raise FlintError('invalid TOON table row indentation')
            values = cells(lines[j][1])
            if len(values) != len(keys):
                raise FlintError('TOON table column count mismatch')
            parsed = [parse_cell(v) for v in values]
            charge(sum(len(dump(v).encode()) for v in parsed))
            rows.append(dict(zip(keys, parsed)))
        return rows, i + count + 1

    def inline(text):
        value = {} if text == '{}' else [] if text == '[]' else parse_cell(text)
        charge(len(dump(value).encode()))
        return value

    def object_at(i, depth):
        nonlocal nodes
        out = {}
        charge(2)
        while i < len(lines) and lines[i][0] == depth:
            text = lines[i][1]
            match = HEADER.fullmatch(text)
            if match and match['key']:
                key = match['key']
                value, following = array(match, i, depth)
            else:
                head = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_.-]*):(?: (.*))?', text)
                if not head:
                    raise FlintError('unsupported TOON object syntax')
                key, rest = head.groups()
                if rest is None:
                    if i + 1 >= len(lines) or lines[i + 1][0] != depth + 1:
                        raise FlintError('missing TOON nested object')
                    value, following = object_at(i + 1, depth + 1)
                else:
                    value, following = inline(rest), i + 1
            if key in out:
                raise FlintError('duplicate TOON key')
            charge(len(json.dumps(key).encode()) + 2)
            nodes += 1
            if nodes > MAX_NODES:
                raise FlintError('TOON node cap exceeded')
            out[key] = value
            i = following
        return out, i

    match = HEADER.fullmatch(lines[0][1])
    if match and not match['key']:
        value, end = array(match, 0, 0)
    elif (match and match['key']) or re.match(r'[A-Za-z_][A-Za-z0-9_.-]*:', lines[0][1]):
        value, end = object_at(0, 0)
    else:
        value, end = inline(lines[0][1]), 1
    if end != len(lines) or nodes > MAX_NODES:
        raise FlintError('unconsumed TOON rows or node cap exceeded')
    result = dump(value)
    checked_text(result)
    return result
