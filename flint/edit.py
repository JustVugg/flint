"""Revision-bound, recoverable single-file edits without retransmitting old text.

The caller supplies new bytes and LF-line coordinates or direct Python symbols
in a stored full source. No dependency inference or execution. Writes use optimistic revision checks and
atomic replacement; advisory locks coordinate Flint writers, not hostile ones.
"""
import ast
import fcntl
import json
import os
import stat
import uuid

from .common import FlintError, MAX_FILE, digest
from .files import clean_path
from .text import MAX_PLAN, MAX_LINES, parse_plan, validate_source

MAX_EDITS = 64


def splice(data, edits):
    """Apply [1-based start, deleted LF-line count, literal UTF-8 text] triples."""
    validate_source(data)
    if not isinstance(edits, list) or not 1 <= len(edits) <= MAX_EDITS:
        raise FlintError('edit plan needs 1..64 splices')
    rows = data.split(b'\n')
    lines = [row + b'\n' for row in rows[:-1]] + ([rows[-1]] if rows[-1] else [])
    normalized = []
    total = len(data)
    for item in edits:
        if not isinstance(item, list) or len(item) != 3:
            raise FlintError('each edit is [start, delete_count, new_text]')
        start, count, text = item
        if (type(start) is not int or type(count) is not int or not isinstance(text, str)
                or not 1 <= start <= len(lines)+1 or not 0 <= count <= len(lines)-start+1):
            raise FlintError('invalid edit coordinates or text')
        try:
            content = text.encode('utf-8')
        except UnicodeError as error:
            raise FlintError('new text must contain valid Unicode') from error
        if b'\0' in content:
            raise FlintError('NUL bytes are not supported in text edits')
        total += len(content) - sum(map(len, lines[start-1:start-1+count]))
        normalized.append((start-1, count, content))
    if total > MAX_FILE or sum(len(r[2]) for r in normalized) > MAX_FILE:
        raise FlintError('edited content exceeds 512 KiB')
    normalized.sort(key=lambda r: r[0])
    output, cursor, last = [], 0, -1
    for start, count, content in normalized:
        if start < cursor or start == last:
            raise FlintError('overlapping or duplicate-start edits')
        output.extend(lines[cursor:start]); output.append(content)
        cursor, last = start+count, start
    output.extend(lines[cursor:])
    return b''.join(output)


def resolve_edits(data, edits, path):
    """Translate explicit Python definition targets to original LF-line edits."""
    if not isinstance(edits, list) or not 1 <= len(edits) <= MAX_EDITS:
        raise FlintError('edit plan needs 1..64 edits')
    if not any(isinstance(item, dict) for item in edits):
        return edits
    if not path.endswith('.py'):
        raise FlintError('symbol edits require Python .py source')
    from .survey import _index, definition
    index = _index(data)
    result = []
    for item in edits:
        if not isinstance(item, dict):
            result.append(item); continue
        if (not {'symbol','text'} <= set(item) or set(item)-{'symbol','text','where'}
                or not isinstance(item['text'], str)):
            raise FlintError('symbol edit needs symbol, text and optional where')
        where = item.get('where', 'replace')
        if where not in ('replace','before','after'):
            raise FlintError('symbol where must be replace, before or after')
        entry = definition(index,item['symbol'])
        start, end = entry['start'], entry['end']
        result.append([end+1 if where == 'after' else start,
                       end-start+1 if where == 'replace' else 0,item['text']])
    return result


def _read_at(directory, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_FILE:
            raise FlintError('edit target must be a bounded regular file with one link')
        data = stream.read(MAX_FILE+1)
        after = os.fstat(stream.fileno())
    if len(data) > MAX_FILE or _identity(before) != _identity(after):
        raise FlintError('target changed while reading')
    return data, after


def _identity(info):
    return tuple(getattr(info, k) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns','st_mode','st_nlink'))


def _replace(root, path, before, after):
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_DIRECTORY
    directory = os.open(root, flags)
    temporary = None
    try:
        parts = path.split('/')
        for part in parts[:-1]:
            child = os.open(part, flags, dir_fd=directory)
            os.close(directory); directory = child
        # Same-parent Flint writers serialize on a stable directory inode.
        fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current, original = _read_at(directory, parts[-1])
        if current == after:
            return 'already-applied' if before != after else 'unchanged'
        if current != before:
            raise FlintError('stale edit base; source changed, read current content before retrying')
        if not original.st_mode & 0o222 or original.st_mode & 0o7000:
            raise FlintError('read-only or special-mode target refused')
        temporary = '.flint-edit-' + uuid.uuid4().hex
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(after)
            stream.flush()
            os.fchmod(stream.fileno(), stat.S_IMODE(original.st_mode))
            os.fsync(stream.fileno())
        # Catch changes since reading, including rename/replacement or chmod.
        latest, info = _read_at(directory, parts[-1])
        if latest != before or _identity(info) != _identity(original):
            raise FlintError('target changed before commit; edit refused')
        os.replace(temporary, parts[-1], src_dir_fd=directory, dst_dir_fd=directory)
        temporary = None
        return 'applied'
    except OSError as error:
        raise FlintError(f'cannot safely edit {path}: {error.strerror}') from error
    finally:
        if temporary is not None:
            try: os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError: pass
        os.close(directory)


def prepare_edit(store, plan):
    """Resolve one original revision and all proposed edits without a write."""
    if not isinstance(plan, dict) or set(plan) != {'path','base','edits'}:
        raise FlintError('edit plan must contain exactly path, base and edits')
    path = clean_path(plan['path'])
    if not isinstance(plan['base'], str):
        raise FlintError('base must be a stored SHA-256 or s: reference')
    before = store.retrieve(plan['base'])
    after = splice(before, resolve_edits(before, plan['edits'], path))
    return path, before, after


def validate_edit(path, after):
    validation = 'text-only'
    if path.endswith('.py'):
        if len(after) > 65536:
            raise FlintError('Python edit syntax check supports <=64 KiB')
        try:
            ast.parse(after.decode('utf-8'))
        except (SyntaxError, ValueError, RecursionError, MemoryError) as error:
            raise FlintError('edited Python does not pass bounded syntax parsing') from error
        validation = 'python-syntax'
    return validation


def commit_edit(store, path, before, after, count, validation):
    # Ensure recovery and the receipt can be represented before any file write.
    with store.db:
        old = store._save(before); new = store._save(after)
    base_ref, result_ref = store.short_handle(old), store.short_handle(new)
    status = _replace(store.root, path, before, after)
    return {'status':status,'path':path,'base':base_ref,'result':result_ref,
            'edits':count,'validation':validation,
            'before_sha256':digest(before),'after_sha256':digest(after)}


def apply_edit(store, plan):
    path, before, after = prepare_edit(store, plan)
    validation = validate_edit(path, after)
    return commit_edit(store, path, before, after, len(plan['edits']), validation)


def render_edit(result):
    return (f"EDIT {json.dumps(result['path'],ensure_ascii=False)} {result['base']} -> {result['result']} "
            f"{result['status']} edits={result['edits']} {result['validation']}\n")
