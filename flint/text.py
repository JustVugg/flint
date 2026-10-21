"""Bounded text and JSON checks shared by readers and editors."""

import json

from .common import FlintError, MAX_FILE

MAX_PLAN = 1024 * 1024
MAX_LINES = 24000


def parse_plan(raw):
    if len(raw) > MAX_PLAN:
        raise FlintError('edit plan exceeds 1 MiB')

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise FlintError('duplicate edit plan key')
            result[key] = value
        return result

    try:
        text = raw.decode('utf-8') if isinstance(raw, bytes) else raw
        return json.loads(text, object_pairs_hook=pairs)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise FlintError('edit plan must be bounded UTF-8 JSON') from error


def validate_source(data):
    """Validate without constructing a no-op splice or copying every line."""
    if len(data) > MAX_FILE or data.count(b'\n') > MAX_LINES:
        raise FlintError('edit source exceeds byte/line cap')
    try:
        data.decode('utf-8')
    except UnicodeError as error:
        raise FlintError('editing requires UTF-8 source') from error
    if b'\0' in data:
        raise FlintError('NUL bytes are not supported in text edits')
