"""Compact local-ledger protocol. References are scoped to workspace + session.

F1 ref bytes LF body; U1 ref LF; P1 ref base count LF splices.
Each splice is: original_start,remove,payload_bytes LF payload.
The local resolver retains full SHA-256 checks; this is not a portable codec.
"""
import re
from .common import FlintError, MAX_FILE, digest
from .delta import apply_delta, MAX_SPLICES, _lines


def render_compact(result):
    ref = result['reference']
    if result['status'] == 'unchanged':
        return f'U1 {ref}\n'
    if result['status'] == 'content':
        return f'F1 {ref} {len(result["content"].encode())}\n' + result['content']
    patches = result.get('splices', [result])
    return (f'P1 {ref} {result["base_reference"]} {len(patches)}\n' +
            ''.join(f'{p["start_line"]},{p["remove_lines"]},{len(p["content"].encode())}\n'+p['content'] for p in patches))


def apply_compact(packet, store, session, retained):
    """Validate and reconstruct against caller-retained bytes, without acking.

Never fetch a missing baseline silently from disk: persistence is not context.
"""
    if not isinstance(packet, bytes) or len(packet) > MAX_FILE+8192:
        raise FlintError('compact packet exceeds limits')
    cursor = 0
    def line():
        nonlocal cursor
        end = packet.find(b'\n', cursor, cursor+128)
        if end < 0:
            raise FlintError('invalid compact header')
        value = packet[cursor:end]; cursor = end+1
        return value
    header = line()
    match = re.fullmatch(rb'(F1|U1|P1) (r[1-9][0-9]{0,9})(?: (.*))?',header)
    if not match:
        raise FlintError('invalid compact protocol')
    mode, reference = match[1].decode(), match[2].decode()
    target = store.resolve_reference(reference, session)
    if target['lines'] is not None:
        raise FlintError('compact protocol requires full-file references')
    expected = store.retrieve(target['handle'])
    def number(value, maximum):
        if not re.fullmatch(rb'0|[1-9][0-9]{0,6}', value):
            raise FlintError('invalid compact count')
        n = int(value)
        if n > maximum:
            raise FlintError('compact count exceeds limit')
        return n
    if mode == 'F1':
        size = number(match[3] or b'', MAX_FILE)
        restored = packet[cursor:]
        if len(restored) != size:
            raise FlintError('compact content length mismatch')
        cursor = len(packet)
    else:
        base = reference
        count = 0
        if mode == 'P1':
            parts = (match[3] or b'').split(b' ')
            if len(parts) != 2 or not re.fullmatch(rb'r[1-9][0-9]{0,9}',parts[0]):
                raise FlintError('invalid compact baseline')
            base = parts[0].decode(); count = number(parts[1],MAX_SPLICES)
            if count == 0:
                raise FlintError('empty compact patch')
        elif match[3] is not None:
            raise FlintError('unexpected unchanged fields')
        origin = store.resolve_reference(base,session)
        if (not origin['acknowledged'] or origin['lines'] is not None or
                origin['path'] != target['path'] or base not in retained):
            raise FlintError('acknowledged same-file baseline must remain in caller context')
        before = retained[base]
        if not isinstance(before,bytes) or len(before)>MAX_FILE or digest(before)!=origin['handle']:
            raise FlintError('retained baseline integrity failure')
        if mode == 'U1':
            restored = before
        else:
            patches = []
            for _ in range(count):
                fields = line().split(b',')
                if len(fields)!=3:
                    raise FlintError('invalid compact splice')
                start,remove,size = (number(v,MAX_FILE) for v in fields)
                payload = packet[cursor:cursor+size]; cursor += size
                if len(payload)!=size:
                    raise FlintError('truncated compact splice')
                try:
                    text = payload.decode('utf-8')
                except UnicodeError as error:
                    raise FlintError('compact payload must be UTF-8') from error
                patches.append({'start_line':start,'remove_lines':remove,
                                'insert_lines':len(_lines(payload)),'content':text})
            restored = apply_delta(before, {'format':'line-patch/1','base':origin['handle'],
                'handle':target['handle'],'file_bytes':len(expected),'splices':patches})
    if cursor != len(packet) or restored != expected:
        raise FlintError('compact result integrity failure')
    return reference, restored
