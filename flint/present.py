"""One compact presentation layer for exact snippets and durable memories.

No project names, query rewrites, ranking changes, or semantic compression.
Detailed provenance is retained locally; essential incompleteness stays visible.
API callers get separate context and control fields. CLI context is for reading,
not automatic format detection: literal source can imitate any display marker.
"""
import json

from .common import FlintError


def _metadata(value):
    if isinstance(value, dict):
        return {k: _metadata(v) for k, v in value.items() if k != 'content'}
    if isinstance(value, list):
        return [_metadata(v) for v in value]
    return value


def present(store, kind, result, *, budget=8192):
    """Render already-selected data; do not broaden selection or claim truth.

    A complete single memory keeps its key and exact text. Clean miss is empty.
    Partial snippets, omissions, stale sources and multi-hit identity stay marked.
    Receipts do not store another copy of memory text after forgetting a key.
    """
    if kind not in ('inspect', 'focus', 'memory'):
        raise FlintError('unsupported compact view kind')
    if type(budget) is not int or not 128 <= budget <= 131072:
        raise FlintError('view budget must be 128..131072 bytes')
    metadata = {'flint-view': 1, 'kind': kind, 'details': _metadata(result)}
    with store.db:
        handle = store._save(json.dumps(metadata, ensure_ascii=False, separators=(',', ':')).encode())
        receipt = store.short_handle(handle)
        if kind == 'inspect':
            if result['scope'] == 'full':
                text = result['content']
            else:
                start, end = result['lines']
                text = f'V1 {receipt} definition {start}:{end}\n'+result['content']
        elif kind == 'focus':
            omitted = result['matched_lines']-result['included_matches']
            text = f'V1 {receipt} excerpts omitted={omitted}\n'
            multi = len(result['sources']) > 1
            for block in result['blocks']:
                name = json.dumps(block['path'], ensure_ascii=False)+' ' if multi else ''
                text += f'{name}{block["start"]}:{block["end"]}\n'+block['content']
                if not block['content'].endswith('\n'):
                    text += '\n'
        else:
            hits = result['hits']; omitted = result['omitted']; stale = result['stale']
            if not omitted and not stale and len(hits) <= 1:
                text = json.dumps(hits[0]['key'],ensure_ascii=False)+'\n'+hits[0]['content'] if hits else ''
            else:
                text = f'V1 {receipt} memories omitted={omitted} stale={stale}\n'
                for hit in hits:
                    text += f'{hit["id"]} {json.dumps(hit["key"],ensure_ascii=False)}\n'+hit['content']
                    if not hit['content'].endswith('\n'):
                        text += '\n'
        if len(text.encode()) > budget:
            raise FlintError('complete compact view exceeds budget; nothing emitted')
    return {'context': text, 'receipt': receipt, 'control': metadata}
