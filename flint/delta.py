"""Bounded exact line patches against an acknowledged source version.

Single-splice compatibility plus unique-line/LIS anchors for separated edits.
No quadratic edit matrix, parser, or claim of globally optimal compression.
"""

from bisect import bisect_left
from .common import FlintError, MAX_FILE, digest

MAX_LINES = 20000
MAX_SPLICES = 64
PATCH_FORMAT = 'line-patch/1'


def _lines(data):
    pieces = data.split(b'\n')
    return [piece+b'\n' for piece in pieces[:-1]] + ([pieces[-1]] if pieces[-1] else [])


def make_delta(before, after):
    if max(len(before),len(after)) > MAX_FILE:
        raise FlintError('delta sources exceed 512 KiB')
    try:
        before.decode('utf-8'); after.decode('utf-8')
    except UnicodeError as error:
        raise FlintError('delta sources must be UTF-8') from error
    if max(before.count(b'\n'),after.count(b'\n')) >= MAX_LINES:
        return None
    old, new = _lines(before), _lines(after)
    prefix = 0
    while prefix < min(len(old),len(new)) and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while (suffix < min(len(old),len(new))-prefix
           and old[len(old)-suffix-1] == new[len(new)-suffix-1]):
        suffix += 1
    content = b''.join(new[prefix:len(new)-suffix]).decode('utf-8')
    result = {'base':digest(before),'handle':digest(after),'file_bytes':len(after),
              'start_line':prefix+1,'remove_lines':len(old)-prefix-suffix,
              'insert_lines':len(new)-prefix-suffix,'content':content}
    if apply_delta(before,result) != after:
        raise FlintError('delta reconstruction invariant failed')
    return result


def render_splice(splice):
    """Framing shared by the wire renderer and exact byte-cost selection."""
    content = splice['content']
    return (f"SPLICE start={splice['start_line']} remove={splice['remove_lines']} "
            f"insert={splice['insert_lines']} payload_bytes={len(content.encode())}\n"
            + content + '\n')


def _unique_positions(lines):
    positions = {}
    for index, line in enumerate(lines):
        positions[line] = -1 if line in positions else index
    return positions


def _anchors(old, new):
    """One pass of patience-style unique-line anchors, O(n log n)."""
    left, right = _unique_positions(old), _unique_positions(new)
    pairs = [(left[line], index) for index, line in enumerate(new)
             if left.get(line, -1) >= 0 and right[line] >= 0]
    tails, tail_ids, previous = [], [], []
    for index, (old_index, _) in enumerate(pairs):
        position = bisect_left(tails, old_index)
        previous.append(tail_ids[position-1] if position else -1)
        if position == len(tails):
            tails.append(old_index); tail_ids.append(index)
        else:
            tails[position] = old_index; tail_ids[position] = index
    result = []
    index = tail_ids[-1] if tail_ids else -1
    while index >= 0:
        result.append(pairs[index]); index = previous[index]
    return result[::-1]


def _gap(old, new, a, b, c, d):
    while a < b and c < d and old[a] == new[c]:
        a += 1; c += 1
    while a < b and c < d and old[b-1] == new[d-1]:
        b -= 1; d -= 1
    if a == b and c == d:
        return None
    return {'start_line':a+1, 'remove_lines':b-a, 'insert_lines':d-c,
            'content':b''.join(new[c:d]).decode('utf-8')}


def make_patch(before, after):
    """At most 64 original-coordinate splices; None on budget/no split benefit.

Anchors are exact unique lines, not hashes or semantic guesses. Missing useful
anchors is a normal fallback. Nearby splices coalesce by complete wire bytes.
"""
    if max(len(before), len(after)) > MAX_FILE:
        raise FlintError('delta sources exceed 512 KiB')
    try:
        before.decode('utf-8'); after.decode('utf-8')
    except UnicodeError as error:
        raise FlintError('delta sources must be UTF-8') from error
    if max(before.count(b'\n'), after.count(b'\n')) >= MAX_LINES:
        return None
    old, new = _lines(before), _lines(after)
    patches, a, c = [], 0, 0
    for b, d in [*_anchors(old, new), (len(old), len(new))]:
        patch = _gap(old, new, a, b, c, d)
        if patch is not None:
            patches.append(patch)
            if len(patches) > MAX_SPLICES:
                return None
        a, c = b+1, d+1
    if len(patches) < 2:
        return None
    merged = []
    for patch in patches:
        if merged:
            last = merged[-1]
            end = last['start_line']-1+last['remove_lines']
            gap_end = patch['start_line']-1
            gap_bytes = sum(map(len, old[end:gap_end]))
            # A long unchanged gap cannot pay for the extra splice header.
            if gap_bytes <= 256:
                candidate = {'start_line':last['start_line'],
                    'remove_lines':gap_end+patch['remove_lines']-(last['start_line']-1),
                    'insert_lines':last['insert_lines']+gap_end-end+patch['insert_lines'],
                    'content':last['content']+b''.join(old[end:gap_end]).decode()+patch['content']}
                if len(render_splice(candidate).encode()) <= len((render_splice(last)+render_splice(patch)).encode()):
                    merged[-1] = candidate
                    continue
        merged.append(patch)
    if len(merged) < 2:
        return None
    result = {'format':PATCH_FORMAT, 'base':digest(before), 'handle':digest(after),
              'file_bytes':len(after), 'splices':merged}
    if apply_delta(before, result) != after:
        raise FlintError('patch reconstruction invariant failed')
    return result


def apply_delta(before, delta):
    if len(before) > MAX_FILE or before.count(b'\n') >= MAX_LINES:
        raise FlintError('delta baseline exceeds limits')
    if not isinstance(delta, dict):
        raise FlintError('delta must be an object')
    try:
        before.decode('utf-8')
    except UnicodeError as error:
        raise FlintError('delta baseline must be UTF-8') from error
    if digest(before) != delta.get('base'):
        raise FlintError('delta baseline hash mismatch')
    total = delta.get('file_bytes')
    if type(total) is not int or not 0 <= total <= MAX_FILE:
        raise FlintError('delta output size exceeds limits')
    if 'splices' in delta:
        patches = delta['splices']
        if (delta.get('format') != PATCH_FORMAT or not isinstance(patches, list)
                or not 1 <= len(patches) <= MAX_SPLICES
                or any(key in delta for key in ('start_line','remove_lines','insert_lines','content'))):
            raise FlintError('invalid multi-splice envelope')
    else:
        if 'format' in delta:
            raise FlintError('unknown delta format')
        patches = [delta]
    old = _lines(before)
    chunks, cursor, previous_start, size, count = [], 0, -1, 0, 0
    def append(lines):
        nonlocal size, count
        if not lines:
            return
        if chunks and not chunks[-1].endswith(b'\n'):
            raise FlintError('delta splice lacks a source newline')
        size += sum(map(len, lines)); count += len(lines)
        if size > MAX_FILE or count > MAX_LINES:
            raise FlintError('delta output exceeds limits')
        chunks.extend(lines)
    for patch in patches:
        if not isinstance(patch, dict):
            raise FlintError('delta splice must be an object')
        fields = [patch.get(name) for name in ('start_line','remove_lines','insert_lines')]
        if any(type(value) is not int for value in fields):
            raise FlintError('delta counts must be integers')
        start, remove, insert = fields
        if not (1 <= start <= MAX_LINES+1 and 0 <= remove <= MAX_LINES and 0 <= insert <= MAX_LINES):
            raise FlintError('delta counts exceed limits')
        index = start-1
        if index < cursor or index <= previous_start or index > len(old) or remove > len(old)-index:
            raise FlintError('delta splices overlap or use invalid source positions')
        content = patch.get('content')
        if not isinstance(content,str) or len(content) > MAX_FILE:
            raise FlintError('delta content must be bounded UTF-8 text')
        try:
            data = content.encode('utf-8')
        except UnicodeError as error:
            raise FlintError('invalid delta Unicode') from error
        if len(data) > MAX_FILE or data.count(b'\n') >= MAX_LINES:
            raise FlintError('delta content exceeds limits')
        new = _lines(data)
        if len(new) != insert:
            raise FlintError('delta insertion length mismatch')
        append(old[cursor:index]); append(new)
        cursor, previous_start = index+remove, index
    append(old[cursor:])
    if size != total:
        raise FlintError('delta output size mismatch')
    output = b''.join(chunks)
    if digest(output) != delta.get('handle'):
        raise FlintError('delta reconstructed hash mismatch')
    return output
