"""Lossless code tables: readable block templates, raw bindings, explicit order.

Textual factoring, not a parser, AST transform or executable macro language.
Every block is split only at LF line boundaries; operators and whitespace are
literal. Parameters contain whole lexemes, never offsets into another string.
"""

import json
import re

from .common import FlintError, MAX_FILE
from .compact import checked_text

HEADER = ('FLINT CODE/2: {{#n}} raw args; T=id:lines template; R=id:rows (source-line TAB args; J=JSON row); '
          'S then +start:lines literals.\n')
MAX_WINDOWS = 32768
MAX_PROPOSALS = 512
MAX_TEMPLATES = 128
MAX_PARAMETERS = 8
MAX_ROWS = 1024
MAX_LINES = 20000
HOLE = re.compile(r'\{\{#(0|[1-7])\}\}')
MARKER = '{{#'
# Protect common control words across languages. This is deliberately a union,
# not a claim to recognize every language's grammar or reserved words.
KEYWORDS = set('''and as assert async await begin break case catch class const
continue def default defer del delete do done elif else elseif end enum except
export extends false False final finally fn for foreach from func function go
goto if implements import in include instanceof interface is lambda let loop
match mod module namespace new nil None not null of or package pass private
protected public raise range repeat return select static struct super switch
then this throw throws trait true True try type typeof union until use using
var void volatile when where while with yield fi esac done unless rescue ensure
elsif unless require local echo exit set fi select return pub mut impl dyn unsafe
self Self extern crate ref move box select insert update into values where join
having group order limit offset union intersect except distinct exists then'''.split())
KEYWORDS = {word.casefold() for word in KEYWORDS}
TOKEN = re.compile(
    r'"(?:[^"\\\r\n]|\\[^\r\n])*"|\'(?:[^\'\\\r\n]|\\[^\r\n])*\'|'
    r'`(?:[^`\\\r\n]|\\[^\r\n])*`|'
    r'(?<!\w)[A-Za-z_][A-Za-z_0-9]*(?!\w)|'
    r'(?<![\w.])(?:0[xX][0-9a-fA-F]+|[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)(?![\w.])')


def physical_lines(text):
    parts = text.split('\n')
    return [part + '\n' for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def _shape(block):
    if (not block.endswith('\n') or not 1 <= block.count('\n') <= 64
            or len(block.encode()) > 4096 or MARKER in block):
        return None
    parts, values, kinds, cursor = [], [], [], 0
    for match in TOKEN.finditer(block):
        value = match.group()
        if value.casefold() in KEYWORDS or len(value.encode()) > 256:
            continue
        parts.append(block[cursor:match.start()]); values.append(value)
        kinds.append('s' if value[0] in '\'"`' else 'n' if value[0].isdigit() else 'i')
        cursor = match.end()
        if len(values) > 512:
            return None
    parts.append(block[cursor:])
    return (tuple(parts), tuple(kinds)), values


def _definition(identifier, template, rows):
    return (f'T={identifier}:{template.count(chr(10))}\n' + template
            + f'R={identifier}:{len(rows)}\n'
            + ''.join(('J'+json.dumps(row,ensure_ascii=False,separators=(',',':'))
                       if any('\t' in value for value in row[1:])
                       else '\t'.join([str(row[0]),*row[1:]]))+'\n' for row in rows))


def encode_code(text):
    checked_text(text)
    lines = physical_lines(text)
    if len(lines) > MAX_LINES:
        return text
    shapes = [_shape(line) for line in lines]
    sizes = [0]
    for line in lines:
        sizes.append(sizes[-1] + len(line.encode()))
    # Shared per-line shapes keep sliding windows bounded without duplicating
    # source strings for every candidate. Limits cap memory and search work.
    groups = {}
    considered = 0
    for width in (1, 2, 3, 4, 6, 8, 12, 16):
        for start in range(len(lines)-width+1):
            if considered >= MAX_WINDOWS:
                break
            considered += 1
            size = sizes[start+width]-sizes[start]
            if not 24 <= size <= 4096:
                continue
            chunk = shapes[start:start+width]
            if any(shape is None for shape in chunk):
                continue
            key = tuple(shape[0] for shape in chunk)
            groups.setdefault(key, []).append(start)
    proposals = []
    for key, starts in groups.items():
        if len(starts) < 2:
            continue
        width = len(key)
        # This upper bound ranks candidates; actual tables and conflicts are
        # charged below. It is a bounded greedy grammar, not an optimal solver.
        upper = sum(sizes[i+width]-sizes[i] for i in starts[1:])
        proposals.append((upper, width, starts))
    proposals.sort(key=lambda item: item[0], reverse=True)
    definitions, uses, occupied = [], {}, [False]*len(lines)
    for _, width, starts in proposals[:MAX_PROPOSALS]:
        if len(definitions) >= MAX_TEMPLATES:
            break
        available, end = [], -1
        for start in starts:
            if start >= end and not any(occupied[start:start+width]):
                available.append(start); end = start+width
        if not 2 <= len(available) <= MAX_ROWS:
            continue
        members = []
        parts = None
        for start in available:
            literal_parts, values = [''], []
            for shape in shapes[start:start+width]:
                (line_parts, _), line_values = shape
                literal_parts[-1] += line_parts[0]
                literal_parts.extend(line_parts[1:]); values.extend(line_values)
            parts = literal_parts
            members.append((start, values))
        if len(parts) > 513:
            continue
        columns, components = [], []
        for position in range(len(parts) - 1):
            components.append(parts[position])
            vector = tuple(values[position] for _, values in members)
            if len(set(vector)) == 1:
                components.append(vector[0])
            else:
                if vector not in columns:
                    columns.append(vector)
                components.append('{{#' + str(columns.index(vector)) + '}}')
        if len(columns) > MAX_PARAMETERS:
            continue
        components.append(parts[-1]); template = ''.join(components)
        rows = [(start+1,*(column[member_index] for column in columns))
                for member_index,(start,_) in enumerate(members)]
        if len(rows) > MAX_ROWS:
            continue
        identifier = len(definitions)
        definition = _definition(identifier, template, rows)
        # Cover extra literal framing at the boundaries, in addition to rows.
        original = sum(sizes[index+width]-sizes[index] for index, _ in members)
        if len(definition.encode()) + 32 >= original:
            continue
        definitions.append(definition)
        for index, _ in members:
            uses[index] = width
            occupied[index:index+width] = [True]*width
    if not definitions:
        return text
    layout, pending = [], []
    literal_start = 0
    def flush():
        if pending:
            literal = ''.join(pending)
            layout.append(f'+{literal_start+1}:{len(physical_lines(literal))}\n' + literal)
            pending.clear()
    index = 0
    while index < len(lines):
        if index in uses:
            flush(); index += uses[index]
        else:
            if not pending:
                literal_start = index
            pending.append(lines[index]); index += 1
    flush()
    output = HEADER + ''.join(definitions) + 'S\n' + ''.join(layout)
    if len(output.encode()) >= len(text.encode()):
        return text
    if decode_code(output) != text:
        raise FlintError('code table reconstruction invariant failed')
    return output


def decode_code(text):
    checked_text(text)
    if not text.startswith(HEADER):
        raise FlintError('unknown code table header')
    wire = physical_lines(text[len(HEADER):]); cursor = 0
    definitions = 0
    output, size, line_count = [], 0, 0
    def take():
        nonlocal cursor
        if cursor >= len(wire):
            raise FlintError('truncated code table')
        line = wire[cursor]; cursor += 1
        return line
    def count(value, maximum, *, zero=False):
        if (not value.isascii() or not value.isdigit() or len(value) > 5
                or (len(value) > 1 and value.startswith('0'))):
            raise FlintError('invalid code table count')
        number = int(value)
        if not int(not zero) <= number <= maximum:
            raise FlintError('code table count outside limits')
        return number
    def pair(line, prefix, max_first, max_second, *, second_zero=False):
        if not line.startswith(prefix) or not line.endswith('\n'):
            raise FlintError('invalid code table record')
        values = line[len(prefix):-1].split(':')
        if len(values) != 2:
            raise FlintError('invalid code table pair')
        return count(values[0], max_first, zero=True), count(values[1], max_second, zero=second_zero)
    def literal(n):
        nonlocal cursor
        if n > len(wire) - cursor:
            raise FlintError('truncated literal lines')
        value = ''.join(wire[cursor:cursor+n]); cursor += n
        return value
    def add(start, value):
        nonlocal size,line_count
        size += len(value.encode()); n = len(physical_lines(value)); line_count += n
        if size > MAX_FILE or line_count > MAX_LINES or start+n-1 > MAX_LINES:
            raise FlintError('decoded code exceeds limits')
        output.append((start,n,value))
    while True:
        record = take()
        if record == 'S\n':
            break
        identifier, n = pair(record, 'T=', MAX_TEMPLATES - 1, 64)
        if identifier != definitions:
            raise FlintError('template IDs must be sequential')
        template = literal(n)
        if not template.endswith('\n') or len(template.encode()) > 8192:
            raise FlintError('invalid or excessive template')
        matches = list(HOLE.finditer(template))
        if MARKER in HOLE.sub('', template):
            raise FlintError('invalid template parameter')
        indices = {int(m.group(1)) for m in matches}
        width = max(indices) + 1 if indices else 0
        if indices != set(range(width)):
            raise FlintError('template parameters must be dense')
        row_id, row_count = pair(take(), 'R=', MAX_TEMPLATES - 1, MAX_ROWS)
        if row_id != identifier:
            raise FlintError('bindings refer to wrong template')
        for _ in range(row_count):
            record = take()
            try:
                row = json.loads(record[1:]) if record.startswith('J') else record.rstrip('\n').split('\t')
            except (ValueError, RecursionError) as error:
                raise FlintError('invalid code bindings JSON') from error
            if not isinstance(row,list) or len(row) != width+1 or not record.endswith('\n'):
                raise FlintError('invalid code bindings')
            if record.startswith('J'):
                if type(row[0]) is not int:
                    raise FlintError('invalid binding source line')
                start = count(str(row[0]),MAX_LINES)
            else:
                start = count(row[0],MAX_LINES)
            args = row[1:]
            if any(not isinstance(v,str) or '\n' in v or '\r' in v or MARKER in v for v in args):
                raise FlintError('invalid code bindings')
            try:
                if any(len(v.encode()) > 256 for v in args):
                    raise FlintError('code binding exceeds limit')
            except UnicodeError as error:
                raise FlintError('invalid Unicode code binding') from error
            expected = len(template.encode())
            for match in matches:
                expected += len(args[int(match.group(1))].encode()) - len(match.group())
            if expected > MAX_FILE - size:
                raise FlintError('decoded code exceeds 512 KiB')
            value = HOLE.sub(lambda m: args[int(m.group(1))],template)
            add(start,value)
        definitions += 1
    if not definitions:
        raise FlintError('code table must define a template')
    while cursor < len(wire):
        record = take()
        start,n = pair(record,'+',MAX_LINES,MAX_LINES)
        if start == 0:
            raise FlintError('source lines start at one')
        add(start,literal(n))
    output.sort(key=lambda item:item[0])
    next_line = 1
    for index,(start,n,value) in enumerate(output):
        if start != next_line or (index < len(output)-1 and not value.endswith('\n')):
            raise FlintError('code source ranges overlap, have gaps or lack a newline')
        next_line += n
    return ''.join(value for _,_,value in output)
