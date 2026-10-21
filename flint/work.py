"""Small immutable edit tickets; exact source and guards stay in the local store.

Tickets bind a path, original target and proposed content. They are not read
acknowledgments. No automatic repair, source execution or cross-workspace IDs.
"""
import json
import re

from .common import FlintError
from .files import clean_path
from .store import select_lines
from .text import parse_plan, validate_source

FORMAT = 'flint-work/1'


def _ticket(store, path, before, candidate):
    # INTEGER PRIMARY KEY AUTOINCREMENT stays stable across VACUUM/deletions.
    store.db.execute('CREATE TABLE IF NOT EXISTS edit_tickets('
                     'id INTEGER PRIMARY KEY AUTOINCREMENT, hash TEXT UNIQUE NOT NULL '
                     'REFERENCES blobs(hash))')
    record = {'format':FORMAT, 'path':path, 'base':store._save(before),
              'candidate':store._save(candidate)}
    blob = (json.dumps(record, ensure_ascii=False, separators=(',',':'))+'\n').encode()
    handle = store._save(blob)
    existing = store.db.execute('SELECT id FROM edit_tickets WHERE hash=?',(handle,)).fetchone()
    if existing:
        return 'e'+str(existing[0])
    store._room('edit_tickets')
    cursor = store.db.execute('INSERT INTO edit_tickets(hash) VALUES (?)',(handle,))
    return 'e'+str(cursor.lastrowid)


def _load(store, reference):
    if not isinstance(reference, str) or not re.fullmatch(r'e[1-9][0-9]{0,9}', reference):
        raise FlintError('work reference must be a workspace-local eN ticket')
    if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='edit_tickets' AND type='table'").fetchone():
        raise FlintError('unknown work ticket in this workspace')
    row = store.db.execute('SELECT hash FROM edit_tickets WHERE id=?',(int(reference[1:]),)).fetchone()
    if row is None:
        raise FlintError('unknown work ticket in this workspace')
    record = parse_plan(store.retrieve(row[0]))
    if (not isinstance(record, dict) or set(record) != {'format','path','base','candidate'}
            or record['format'] != FORMAT):
        raise FlintError('invalid work ticket record')
    record['path'] = clean_path(record['path'])
    for key in ('base','candidate'):
        if not isinstance(record[key],str) or not re.fullmatch('[a-f0-9]{64}',record[key]):
            raise FlintError('invalid work source digest')
    before, candidate = (store.retrieve(record[k]) for k in ('base','candidate'))
    # Validate stored records as data too; the database is not a trust boundary.
    for data in (before,candidate):
        validate_source(data)
    return record, before, candidate


def _view(reference, data, scope, budget):
    if type(budget) is not int or not 128 <= budget <= 131072:
        raise FlintError('work view budget must be 128..131072 UTF-8 bytes')
    text=f'W1 {reference} {scope}\n'+data.decode()
    if len(text.encode()) > budget:
        raise FlintError('complete work view exceeds budget; nothing emitted')
    return text


def open_work(store, path, *, budget=8192):
    path=clean_path(path);data=store.file_bytes(path)
    validate_source(data)
    with store.db:
        reference=_ticket(store,path,data,data)
        return _view(reference,data,'source',budget)


def show_work(store, reference, *, lines=None, budget=8192):
    _,_,candidate=_load(store,reference)
    data=candidate if lines is None else select_lines(candidate,lines,strict=True)
    return _view(reference,data,'candidate' if lines is None else f'candidate {lines[0]}:{lines[1]}',budget)


def info_work(store, reference):
    return _load(store,reference)[0]


def edit_work(store, reference, plan):
    from .edit import splice, resolve_edits, validate_edit, commit_edit, _replace

    if not isinstance(plan,dict) or set(plan)!={'edits'}:
        raise FlintError('work edit plan must contain exactly edits')
    record,before,candidate=_load(store,reference);path=record['path']
    after=splice(candidate,resolve_edits(candidate,plan['edits'],path))
    validate_source(after)
    try:
        validation=validate_edit(path,after)
    except FlintError as error:
        syntax=error.__cause__
        if not isinstance(syntax,SyntaxError):
            raise
        _replace(store.root,path,before,before)
        with store.db:
            ticket=_ticket(store,path,before,after)
        return {'status':'rejected','reference':ticket,'line':syntax.lineno,
                'column':syntax.offset,'error':syntax.msg[:160]}
    # Reserve snapshots and the next ticket before touching the target.
    with store.db:
        ticket=_ticket(store,path,after,after)
    result=commit_edit(store,path,before,after,len(plan['edits']),validation)
    return {'status':result['status'],'reference':ticket}


def render_work(result):
    text=f"W1 {result['reference']} {result['status']}"
    if result['status']=='rejected':
        text+=f" {result['line']}:{result['column']} "+json.dumps(result['error'],ensure_ascii=False)
    return text+'\n'


def dispatch_work(store, request):
    """Native compact JSON transport; operation and all arguments are explicit."""
    if not isinstance(request,dict) or not isinstance(request.get('op'),str):
        raise FlintError('work call requires a JSON object with op')
    op=request['op']
    schemas={'open':({'op','path'},{'budget'}),
             'edit':({'op','ref','edits'},set()),
             'show':({'op','ref'},{'lines','budget'}),
             'info':({'op','ref'},set())}
    if op not in schemas:
        raise FlintError('unknown work operation')
    required,optional=schemas[op]
    if not required <= set(request) or set(request)-required-optional:
        raise FlintError('invalid work operation fields')
    if op=='open':
        return open_work(store,request['path'],budget=request.get('budget',8192)),0
    if op=='show':
        lines=request.get('lines')
        if lines is not None and (not isinstance(lines,list) or len(lines)!=2):
            raise FlintError('work lines must be [start,end]')
        return show_work(store,request['ref'],lines=lines,budget=request.get('budget',8192)),0
    if op=='info':
        return json.dumps(info_work(store,request['ref']),ensure_ascii=False,separators=(',',':'))+'\n',0
    result=edit_work(store,request['ref'],{'edits':request['edits']})
    return render_work(result),3 if result['status']=='rejected' else 0
