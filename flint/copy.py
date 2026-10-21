"""Bounded exact LZ-style references; readable literals, deterministic decoder.

This is an existing compression idea, not a new semantic summarizer. Keep the
format explicit at decode time: literal input may itself begin with the header.
The optional native accelerator implements this same algorithm and wire format.
"""

from .common import FlintError, MAX_FILE
from .compact import checked_text

HEADER = b'FLINT COPY/1: @distance,length@ copies prior decoded bytes; @@ means @.\n'
MIN_MATCH = 48
PROBES = 8
SLOTS = 65536


def _hash(data, i):
    value = int.from_bytes(data[i:i+4], 'little')
    return ((value * 2654435761) & 0xffffffff) >> 16


def _boundary(data, i):
    return i == len(data) or data[i] & 0xc0 != 0x80


def encode_copy(text):
    checked_text(text)
    data = text.encode()
    if len(data) < len(HEADER) + MIN_MATCH:
        return text
    # Arrays keep the worst-case memory bounded; no Python int per byte index.
    from array import array
    heads = array('i', [-1]) * SLOTS
    previous = array('i', [-1]) * len(data)
    output = bytearray(HEADER)

    def insert(i):
        if i + 4 <= len(data) and _boundary(data, i):
            key = _hash(data, i)
            previous[i] = heads[key]
            heads[key] = i

    i = 0
    while i < len(data):
        best, length = -1, 0
        if i + MIN_MATCH <= len(data) and _boundary(data, i):
            candidate = heads[_hash(data, i)]
            for _ in range(PROBES):
                if candidate < 0:
                    break
                n = 0
                # Total work is bounded by fixed probes and the consumed match.
                while i + n < len(data) and data[candidate+n] == data[i+n]:
                    n += 1
                while n and not _boundary(data, i+n):
                    n -= 1
                if n > length:
                    best, length = candidate, n
                candidate = previous[candidate]
        if length >= MIN_MATCH:
            output.extend(f'@{i-best},{length}@'.encode())
            stop = i + length
            while i < stop:
                insert(i)
                i += 1
        else:
            insert(i)
            output.append(data[i])
            if data[i] == 64:
                output.append(64)
            i += 1
    if len(output) >= len(data):
        return text
    result = output.decode()
    if decode_copy(result) != text:
        raise FlintError('copy reconstruction invariant failed')
    return result


def decode_copy(text):
    checked_text(text)
    data = text.encode()
    if not data.startswith(HEADER):
        raise FlintError('unknown copy header')
    output = bytearray()
    i = len(HEADER)
    while i < len(data):
        if data[i] != 64:
            output.append(data[i]); i += 1
        elif i + 1 < len(data) and data[i+1] == 64:
            output.append(64); i += 2
        else:
            i += 1
            values = []
            for delimiter in (44, 64):
                start = i
                while i < len(data) and 48 <= data[i] <= 57 and i-start < 6:
                    i += 1
                if i == start or i >= len(data) or data[i] != delimiter or data[start] == 48:
                    raise FlintError('invalid copy reference')
                values.append(int(data[start:i])); i += 1
            distance, count = values
            if distance > len(output) or count > MAX_FILE-len(output):
                raise FlintError('copy reference exceeds source or output cap')
            # Standard overlapping LZ copying, bounded before allocating.
            period = output[-distance:]
            output.extend((period * ((count + distance-1)//distance))[:count])
        if len(output) > MAX_FILE:
            raise FlintError('decoded copy exceeds 512 KiB')
    try:
        return output.decode('utf-8')
    except UnicodeDecodeError as error:
        raise FlintError('copy does not reconstruct valid UTF-8') from error
