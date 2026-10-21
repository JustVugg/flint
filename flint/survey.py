"""Python source navigation: bounded index and whole-definition excerpts.

No execution, dependency inference, source rewriting, or full-file read receipt.
"""
import ast
import json

from .common import FlintError, digest
from .store import select_lines

MAX_SOURCE = 64 * 1024
MAX_NODES = 12000
MAX_SYMBOLS = 512
KINDS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _budget(value):
    if type(value) is not int or not 128 <= value <= 128 * 1024:
        raise FlintError('navigation budget must be 128..131072 UTF-8 bytes')


def _index(data):
    if len(data) > MAX_SOURCE or b'\r' in data.replace(b'\r\n', b''):
        raise FlintError('Python navigation requires <=64 KiB source with LF or CRLF')
    try:
        tree = ast.parse(data.decode('utf-8'))
    except (UnicodeError, SyntaxError, ValueError, RecursionError) as error:
        raise FlintError('cannot parse Python source for navigation') from error
    for count, _ in enumerate(ast.walk(tree), 1):
        if count > MAX_NODES:
            raise FlintError('Python navigation AST node limit exceeded')
    found, pending = [], [('', tree.body)]
    while pending:
        prefix, body = pending.pop()
        for node in body:
            if not isinstance(node, KINDS):
                continue
            name = prefix + node.name
            if len(name) > 256 or len(found) >= MAX_SYMBOLS:
                raise FlintError('Python navigation symbol limit exceeded')
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            kind = 'class' if isinstance(node, ast.ClassDef) else 'async-def' if isinstance(node, ast.AsyncFunctionDef) else 'def'
            found.append({'name':name, 'kind':kind, 'start':start, 'end':node.end_lineno})
            if isinstance(node, ast.ClassDef):
                pending.append((name+'.', node.body))
    return sorted(found, key=lambda item:(item['start'], item['name']))


def render_map(result):
    rows = ''.join(f"{r['kind']} {json.dumps(r['name'],ensure_ascii=False)} {r['start']}:{r['end']}\n" for r in result['symbols'])
    return (f"MAP/1 source={json.dumps(result['source'],ensure_ascii=False)} sha256={result['handle']} language=python\n"
            + rows
            + f"END MAP shown={len(result['symbols'])}/{result['total_symbols']}; direct module/class definitions only. Bodies and dependencies omitted; retrieve HASH for full source.\n")


def source_map(store, path, *, budget=4096):
    _budget(budget)
    data = store.file_bytes(path)
    entries = _index(data)
    result = {'source':path, 'handle':digest(data), 'total_symbols':len(entries), 'symbols':[]}
    if len(render_map(result).encode()) > budget:
        raise FlintError('navigation budget cannot fit map metadata; nothing emitted')
    for entry in entries:
        result['symbols'].append(entry)
        if len(render_map(result).encode()) > budget:
            result['symbols'].pop()
    with store.db:
        store._save(data)
    return result


def render_inspect(result):
    start,end = result['lines']
    return (f"INSPECT/1 source={json.dumps(result['source'],ensure_ascii=False)} sha256={result['handle']} "
            f"symbol={json.dumps(result['symbol'],ensure_ascii=False)} lines={start}:{end} scope={result['scope']}\n"
            + result['content']
            + f"\nEND INSPECT omitted_lines={result['omitted_lines']}; dependencies not inferred.\n")


def definition(index, symbol):
    """Resolve one direct definition, refusing ambiguous owning classes too."""
    if (not isinstance(symbol,str) or len(symbol) > 256
            or not all(part.isidentifier() for part in symbol.split('.'))):
        raise FlintError('symbol must be a direct Python definition or Class.method path')
    parts = symbol.split('.')
    for stop in range(1,len(parts)):
        owner = '.'.join(parts[:stop])
        if sum(entry['name'] == owner and entry['kind'] == 'class' for entry in index) != 1:
            raise FlintError('symbol has a missing or ambiguous owning class')
    matches = [entry for entry in index if entry['name'] == symbol]
    if len(matches) != 1:
        raise FlintError(f'symbol must resolve to exactly one direct definition; found {len(matches)}')
    return matches[0]


def render_editable(store, result, *, budget=8192):
    """Explicit edit-ready view: reference identifies full source, not metadata."""
    _budget(budget)
    ref = store.short_handle(result['handle'])
    start, end = result['lines']
    text = (f'E1 {ref} {json.dumps(result["source"],ensure_ascii=False)} '
            f'{json.dumps(result["symbol"],ensure_ascii=False)} {result["scope"]} {start}:{end}\n'
            + result['content'])
    if len(text.encode()) > budget:
        raise FlintError('complete editable view exceeds budget; nothing emitted')
    return text


def inspect_source(store, source, symbol, *, snapshot=False, budget=8192):
    _budget(budget)
    data = store.retrieve(source) if snapshot else store.file_bytes(source)
    entry = definition(_index(data), symbol)
    count = data.count(b'\n') + bool(data and not data.endswith(b'\n'))
    start,end = entry['start'],entry['end']
    result = {'source':'snapshot' if snapshot else source, 'handle':digest(data),
              'symbol':symbol, 'lines':[start,end], 'scope':'definition',
              'content':select_lines(data,(start,end),strict=True).decode(),
              'omitted_lines':count-(end-start+1)}
    full = dict(result, lines=[1,count], scope='full', content=data.decode(), omitted_lines=0)
    if len(render_inspect(full).encode()) < len(render_inspect(result).encode()):
        result = full
    size = len(render_inspect(result).encode())
    if size > budget:
        raise FlintError(f'complete definition needs {size} UTF-8 bytes; budget is {budget}; nothing emitted')
    with store.db:
        store._save(data)
    return result


def render_batch(result, *, editable=False):
    """Lengths delimit literal source; line ranges never imply an omitted gap."""
    header = (f'{"E2" if editable else "I2"} {result["reference"]} '
              f'{json.dumps(result["source"],ensure_ascii=False)} '
              f'scope={result["scope"]} omitted={result["omitted_lines"]}\n')
    return header + ''.join(
        f'@{block["lines"][0]}:{block["lines"][1]} bytes={len(block["content"].encode())}\n'
        + block['content'] + '\n' for block in result['blocks'])


def inspect_many(store, source, symbols, *, snapshot=False, budget=8192, editable=False):
    """Read all requested definitions once, or a complete UTF-8 file with [].

    All requested names must resolve. No inferred dependencies; candidate choice
    measures complete emitted UTF-8 bytes, not model tokens.
    """
    _budget(budget)
    if not isinstance(symbols, (list, tuple)) or len(symbols) > 32:
        raise FlintError('batch inspection requires a list of at most 32 symbols')
    data = store.retrieve(source) if snapshot else store.file_bytes(source)
    try:
        text = data.decode('utf-8')
    except UnicodeError as error:
        raise FlintError('inspection source must be UTF-8') from error
    count = data.count(b'\n') + bool(data and not data.endswith(b'\n'))
    common = {'source': 'snapshot' if snapshot else source, 'handle': digest(data)}
    full = dict(common, scope='full', omitted_lines=0,
                blocks=[{'lines': [1, count], 'content': text}])
    selected = full
    if symbols:
        index = _index(data)
        entries = [definition(index, symbol) for symbol in symbols]
        spans = []
        for entry in sorted(entries, key=lambda item: (item['start'], item['end'])):
            start, end = entry['start'], entry['end']
            if spans and start <= spans[-1][1] + 1:
                spans[-1][1] = max(spans[-1][1], end)
            else:
                spans.append([start, end])
        selected = dict(common, scope='selection',
                        omitted_lines=count-sum(end-start+1 for start, end in spans),
                        blocks=[{'lines': [start, end],
                                 'content': select_lines(data, (start, end), strict=True).decode()}
                                for start, end in spans])
    with store.db:
        store._save(data)
        reference = store.short_handle(common['handle'])
        full['reference'] = selected['reference'] = reference
        result = min((full, selected), key=lambda item: len(render_batch(item, editable=editable).encode()))
        if len(render_batch(result, editable=editable).encode()) > budget:
            raise FlintError('complete inspection exceeds budget; nothing emitted')
    return result
