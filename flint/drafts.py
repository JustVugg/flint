"""Retain rejected Python candidates so a caller can repair only changed lines.

Explicit local drafts, no automatic correction or execution. Every retry keeps
the original target revision as its write guard, not the rejected candidate.
"""
import json
import re

from .common import FlintError, digest
from .edit import prepare_edit, validate_edit, commit_edit, splice, parse_plan, _replace
from .files import clean_path

FORMAT = 'flint-edit-draft/1'


def load_draft(store, reference):
    if not isinstance(reference, str):
        raise FlintError('draft reference must be a stored digest or s: reference')
    record = parse_plan(store.retrieve(reference))
    if (not isinstance(record, dict) or set(record) != {'format','path','base','candidate'}
            or record['format'] != FORMAT):
        raise FlintError('reference is not an edit draft')
    path = clean_path(record['path'])
    if not path.endswith('.py'):
        raise FlintError('edit drafts require Python .py source')
    for name in ('base','candidate'):
        if not isinstance(record[name], str) or not re.fullmatch('[a-f0-9]{64}', record[name]):
            raise FlintError('invalid draft source digest')
    before = store.retrieve(record['base'])
    candidate = store.retrieve(record['candidate'])
    if len(candidate) > 65536:
        raise FlintError('Python draft exceeds 64 KiB')
    splice(before, [[1,0,'']])  # Enforce original text/line caps even on a forged record.
    return path, before, candidate


def apply_with_draft(store, plan, *, resume=None):
    if resume is None:
        path, before, after = prepare_edit(store, plan)
    else:
        if not isinstance(plan, dict) or set(plan) != {'edits'}:
            raise FlintError('draft correction must contain exactly edits')
        path, before, candidate = load_draft(store, resume)
        # Invalid syntax cannot provide a reliable symbol index. Coordinates
        # refer to LF lines of the immutable candidate, not the original file.
        after = splice(candidate, plan['edits'])
    try:
        validation = validate_edit(path, after)
    except FlintError as error:
        syntax = error.__cause__
        if not isinstance(syntax, SyntaxError):
            raise
        # Check current target safely without writing. A later resume still
        # performs the full optimistic revision check at commit.
        _replace(store.root, path, before, before)
        record = {'format':FORMAT, 'path':path,
                  'base':digest(before), 'candidate':digest(after)}
        encoded = (json.dumps(record, ensure_ascii=False, separators=(',',':'))+'\n').encode()
        with store.db:
            store._save(before); store._save(after)
            ref = store.short_handle(store._save(encoded))
            candidate_ref = store.short_handle(record['candidate'])
        # Quoted, bounded diagnostic is untrusted source, not a command. The
        # full candidate remains recoverable if this single line is too short.
        line = (syntax.text or '').rstrip('\r\n')
        return {'status':'draft', 'path':path, 'reference':ref,
                'candidate':candidate_ref, 'line':syntax.lineno, 'column':syntax.offset,
                'error':syntax.msg[:160], 'excerpt':line[:160],
                'excerpt_truncated':len(line)>160, 'validation':'python-syntax-failed'}
    return commit_edit(store, path, before, after, len(plan['edits']), validation)


def render_draft(result):
    return (f"DRAFT {result['reference']} candidate={result['candidate']} "
            f"not-applied {json.dumps(result['path'],ensure_ascii=False)}\n"
            + json.dumps({k:result[k] for k in
                ('line','column','error','excerpt','excerpt_truncated')},
                ensure_ascii=False,separators=(',',':')) + '\n')
