import json
import sys

from .common import FlintError
from . import __version__


def line_range(value):
    try:
        start, stop = map(int, value.split(":"))
        if not 1 <= start <= stop:
            raise ValueError
        return start, stop
    except ValueError as error:
        import argparse
        raise argparse.ArgumentTypeError("expected START:END with 1 <= START <= END") from error


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        argv = ['--help']
    # Exact canonical forms only. Everything else, including help, option
    # abbreviations and malformed flags, keeps argparse's original behavior.
    workspace = None
    if argv == ['work', 'call']:
        workspace = '.'
    elif len(argv) == 4 and argv[0] == '--workspace' and argv[2:] == ['work', 'call']:
        if argv[1] and not argv[1].startswith('-'):
            workspace = argv[1]
    elif len(argv) == 3 and argv[0].startswith('--workspace=') and argv[1:] == ['work', 'call']:
        workspace = argv[0].split('=', 1)[1]
    if workspace is not None:
        import sqlite3
        from .store import Store
        try:
            with Store(workspace) as store:
                return _call_work(store)
        except (FlintError, OSError, sqlite3.Error) as error:
            print(f"Flint refused: {error}", file=sys.stderr)
            return 2
    return _general_main(argv)


def _call_work(store):
    from .text import MAX_PLAN, parse_plan
    from .work import dispatch_work
    text, status = dispatch_work(store, parse_plan(sys.stdin.buffer.read(MAX_PLAN + 1)))
    print(text, end='')
    return status


def _general_main(argv):
    import argparse
    parser = argparse.ArgumentParser(description="Flint: compact context, recoverable sources and memory for AI agents")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--version", action="version", version=f"Flint {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    _add_setup_parsers(sub)
    sub.add_parser("session", help="new receipt scope; also use after compaction")
    read = sub.add_parser("read")
    read.add_argument("path")
    read.add_argument("--session", required=True)
    read.add_argument("--full", action="store_true")
    read.add_argument("--delta", action="store_true", help="exact changed lines only when a full baseline is acknowledged in this session")
    read.add_argument("--lines", type=line_range)
    read.add_argument("--json", action="store_true")
    read.add_argument('--compact',action='store_true',help='short local references; requires workspace/session ledger and compact protocol')
    ack = sub.add_parser("ack", help="confirm a complete read is retained in this session")
    ack.add_argument("ticket")
    ack.add_argument("--session", required=True)
    edit = sub.add_parser('edit', help='apply revision-bound LF-line or Python-symbol edits; both versions remain recoverable')
    edit.add_argument('plan', nargs='?', default='-', help='JSON plan path, or - for stdin: {path,base,edits}; edits are [start,count,text] or {symbol,text,where?}')
    draft_mode = edit.add_mutually_exclusive_group()
    draft_mode.add_argument('--keep-draft', action='store_true', help='retain syntactically rejected Python candidate without writing target; exit 3')
    draft_mode.add_argument('--resume', metavar='DRAFT', help='repair stored draft with {edits:[[start,count,text]]} against candidate LF lines')
    work = sub.add_parser('work', help='exact source and recoverable edits through short immutable local tickets')
    work_commands = work.add_subparsers(dest='work_command', required=True)
    work_commands.add_parser('call', help='compact JSON action on stdin: open/edit/show/info')
    work_open = work_commands.add_parser('open')
    work_open.add_argument('path')
    work_open.add_argument('--budget', type=int, default=8192)
    work_show = work_commands.add_parser('show')
    work_show.add_argument('reference')
    work_show.add_argument('--lines', type=line_range)
    work_show.add_argument('--budget', type=int, default=8192)
    work_info = work_commands.add_parser('info')
    work_info.add_argument('reference')
    work_edit = work_commands.add_parser('edit')
    work_edit.add_argument('reference')
    work_edit.add_argument('plan', nargs='?', default='-')
    memory=sub.add_parser('memory',help='durable source-linked memory with current-only recall')
    memory_sub=memory.add_subparsers(dest='memory_command',required=True)
    remember=memory_sub.add_parser('remember')
    remember.add_argument('path');remember.add_argument('--key',required=True)
    remember.add_argument('--track-source',action='store_true',help='exclude recall if this source changes or disappears; default is a durable detached snapshot')
    recall=memory_sub.add_parser('recall')
    recall.add_argument('query');recall.add_argument('--limit',type=int,default=5)
    recall.add_argument('--budget',type=int,default=8192,help='complete emitted UTF-8 bytes')
    recall.add_argument('--compact',action='store_true',help='compact context; full provenance stays in local view receipt')
    history=memory_sub.add_parser('history');history.add_argument('key')
    history.add_argument('--full',action='store_true');history.add_argument('--budget',type=int,default=131072)
    forget=memory_sub.add_parser('forget');forget.add_argument('key')
    recover=memory_sub.add_parser('recover');recover.add_argument('reference')
    focused = sub.add_parser("focus", help="literal search with exact excerpts and recoverable originals")
    focused.add_argument("query")
    focused.add_argument("paths", nargs="+")
    focused.add_argument("--budget", type=int, default=8192, help="maximum emitted UTF-8 bytes")
    focused.add_argument("--context", type=int, default=2)
    focused.add_argument('--compact',action='store_true',help='compact excerpts with local provenance receipt')
    survey = sub.add_parser("map", help="bounded Python definition index; bodies remain in an immutable snapshot")
    survey.add_argument("path")
    survey.add_argument("--budget", type=int, default=4096)
    inspect = sub.add_parser("inspect", help="exact UTF-8 file or complete Python definitions; no inferred dependencies")
    inspect.add_argument("source")
    inspect.add_argument("symbol", nargs="*", help="zero: whole file; one or more: Python definitions")
    inspect.add_argument("--snapshot", action="store_true", help="source is an immutable SHA-256 handle instead of a path")
    inspect.add_argument("--budget", type=int, default=8192)
    inspect_view = inspect.add_mutually_exclusive_group()
    inspect_view.add_argument('--compact',action='store_true',help='compact exact definition with local provenance receipt')
    inspect_view.add_argument('--editable',action='store_true',help='exact definition with full-source base reference for edit; no read acknowledgment')
    shrink = sub.add_parser("shrink", help="conservative prose view; original remains recoverable")
    shrink.add_argument("path")
    shrink.add_argument("--prose", action="store_true", help="explicitly allow English prose edits")
    shrink.add_argument("--preserve", action="append", default=[], help="keep every line containing this exact literal")
    shrink.add_argument("--report", action="store_true", help="show candidate plus metadata even when total output grows")
    compact = sub.add_parser("compact", help="keep all JSON values or reversible line templates")
    compact.add_argument("path")
    compact.add_argument("--format", choices=("auto", "json", "lines", "columns"), default="auto")
    compact.add_argument("--report", action="store_true", help="show candidate even if recovery metadata adds bytes")
    encode = sub.add_parser("encode", help="stateless complete view; JSON whitespace may change; no snapshot")
    encode.add_argument("path")
    encode.add_argument("--format", choices=("auto", "json", "lines", "columns", "toon", "copy", "code"), default="auto")
    encode.add_argument("--report", action="store_true", help="write format and sizes to stderr; counts as additional output")
    code = sub.add_parser("code", help="exact original by default; experimental code tables require --templates")
    code.add_argument("path")
    code.add_argument('--templates',action='store_true',help='opt into experimental byte-selected code tables; may increase tokens')
    code.add_argument("--report", action="store_true", help="emit diagnostic sizes even if they add bytes")
    decode = sub.add_parser("decode", help="stateless reconstruction; explicit format avoids literal-envelope ambiguity")
    decode.add_argument("path")
    decode.add_argument("--format", required=True, choices=("verbatim", "json-whitespace", "line-templates", "json-columns", "toon", "copy", "code-table"))
    decode.add_argument("--lines", type=line_range, help="return exact LF-based lines from reconstructed content")
    expand = sub.add_parser("expand", help="decode a Flint line-template envelope to exact source text")
    expand.add_argument("path")
    retrieve = sub.add_parser("retrieve", help="write exact original snapshot bytes to stdout")
    retrieve.add_argument("handle")
    retrieve.add_argument('--session',help='required when handle is a compact local r-reference')
    retrieve.add_argument("--lines", type=line_range, help="exact LF-based range from immutable original")
    bundle = sub.add_parser("pack", help="exact context with mandatory items and declared dependencies")
    bundle.add_argument("manifest")
    bundle.add_argument("--query", default="")
    bundle.add_argument("--budget", type=int, default=8192)
    verify = sub.add_parser("verify-pack", help="check whether selected bundle sources changed")
    verify.add_argument("catalog")
    begin = sub.add_parser("begin", help="record declared inputs before a check")
    begin.add_argument("label")
    begin.add_argument("inputs", nargs="+")
    finish = sub.add_parser("finish", help="attach caller-reported outcome after a check")
    finish.add_argument("id")
    finish.add_argument("--exit-code", type=int, required=True)
    finish.add_argument("--log", required=True)
    evidence = sub.add_parser("evidence", help="recheck the declared input hashes")
    evidence.add_argument("id")
    args = parser.parse_args(argv)
    storage_errors = ()
    try:
        if args.command in ('install', 'setup', 'doctor', 'uninstall'):
            from .onboarding import run
            return run(args)
        if args.command in ("encode", "decode", "code"):
            from pathlib import Path
            from .files import file_bytes
            from .encode import encode_text, decode_text
            data = file_bytes(Path(args.workspace).resolve(strict=True), args.path)
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as error:
                raise FlintError("encode/decode support UTF-8 only") from error
            if args.command in ("encode", "code"):
                if args.command == 'code' and not args.templates:
                    content, method = text, 'verbatim'
                else:
                    content, method = encode_text(text, format='code' if args.command == 'code' else args.format)
                if args.report:
                    print(json.dumps({"format": method, "input_bytes": len(data), "output_bytes": len(content.encode())}, separators=(",", ":")), file=sys.stderr)
                elif args.command == 'code' and method != 'verbatim':
                    metadata = json.dumps({'format': method}, separators=(',', ':')) + '\n'
                    if len(content.encode()) + len(metadata.encode()) >= len(data):
                        content = text
                    else:
                        print(metadata, end='', file=sys.stderr)
            else:
                content = decode_text(text, format=args.format)
                if args.lines:
                    from .store import select_lines
                    content = select_lines(content.encode(), args.lines, strict=True).decode()
            print(content, end="")
            return 0
        import sqlite3
        from .store import Store, render_read
        storage_errors = (sqlite3.Error,)
        with Store(args.workspace) as store:
            if args.command == 'work':
                from .work import open_work, show_work, info_work, edit_work, render_work, dispatch_work
                if args.work_command == 'call':
                    return _call_work(store)
                elif args.work_command == 'open':
                    print(open_work(store,args.path,budget=args.budget),end='')
                elif args.work_command == 'show':
                    print(show_work(store,args.reference,lines=args.lines,budget=args.budget),end='')
                elif args.work_command == 'info':
                    print(json.dumps(info_work(store,args.reference),ensure_ascii=False,separators=(',',':')))
                else:
                    from .text import MAX_PLAN, parse_plan
                    raw = sys.stdin.buffer.read(MAX_PLAN+1) if args.plan == '-' else store.file_bytes(args.plan)
                    result=edit_work(store,args.reference,parse_plan(raw))
                    print(render_work(result),end='')
                    if result['status']=='rejected':
                        return 3
            elif args.command == 'edit':
                from .edit import MAX_PLAN, parse_plan, apply_edit, render_edit
                raw = sys.stdin.buffer.read(MAX_PLAN+1) if args.plan == '-' else store.file_bytes(args.plan)
                plan = parse_plan(raw)
                if args.keep_draft or args.resume:
                    from .drafts import apply_with_draft, render_draft
                    result = apply_with_draft(store, plan, resume=args.resume)
                    if result['status'] == 'draft':
                        print(render_draft(result), end='')
                        return 3
                else:
                    result = apply_edit(store, plan)
                print(render_edit(result), end='')
            elif args.command == 'memory':
                from .memory import Memory,render_recall
                memory=Memory(store)
                if args.memory_command=='remember':print(memory.remember(args.path,args.key,tracked=args.track_source))
                elif args.memory_command=='recall':
                    result=memory.recall(args.query,limit=args.limit,budget=args.budget)
                    from .present import present
                    text=present(store,'memory',result,budget=args.budget)['context'] if args.compact else render_recall(result)
                    print(text,end='')
                elif args.memory_command=='history':
                    if not 128<=args.budget<=131072:raise FlintError('history budget must be 128..131072 bytes')
                    history=json.dumps(memory.history(args.key,full=args.full),ensure_ascii=False,separators=(',',':'))+'\n'
                    if len(history.encode())>args.budget:raise FlintError('complete history exceeds budget; use metadata and recover selected versions')
                    print(history,end='')
                elif args.memory_command=='forget':print('FORGOTTEN' if memory.forget(args.key) else 'NOT FOUND')
                else:sys.stdout.buffer.write(memory.recover(args.reference))
            elif args.command == "session":
                print(store.session())
            elif args.command == "read":
                result = store.read(args.path, args.session, full=args.full, lines=args.lines, delta=args.delta,compact=args.compact)
                from .wire import render_compact
                print(json.dumps(result, ensure_ascii=False) if args.json else (render_compact(result) if args.compact else render_read(result)), end="\n" if args.json else "")
            elif args.command == "retrieve":
                handle=args.handle
                if handle.startswith('r'):
                    if not args.session:raise FlintError('local reference requires --session')
                    handle=store.resolve_reference(handle,args.session)['handle']
                sys.stdout.buffer.write(store.retrieve(handle, lines=args.lines))
            elif args.command == "ack":
                result = store.acknowledge(args.ticket, args.session)
                print("ACK " + result["ticket"])
            elif args.command == "focus":
                from .focus import focus, render_focus
                from .present import present
                result=focus(store, args.paths, args.query, budget=args.budget, context=args.context)
                print(present(store,'focus',result,budget=args.budget)['context'] if args.compact else render_focus(result), end="")
            elif args.command == 'map':
                from .survey import source_map, render_map
                print(render_map(source_map(store,args.path,budget=args.budget)),end='')
            elif args.command == 'inspect':
                from .survey import inspect_source, render_inspect, render_editable, inspect_many, render_batch
                from .present import present
                if len(args.symbol) != 1:
                    result=inspect_many(store,args.source,args.symbol,snapshot=args.snapshot,
                                        budget=args.budget,editable=args.editable)
                    text=render_batch(result,editable=args.editable)
                else:
                    result=inspect_source(store,args.source,args.symbol[0],snapshot=args.snapshot,budget=args.budget)
                    text = (render_editable(store,result,budget=args.budget) if args.editable else
                            present(store,'inspect',result,budget=args.budget)['context'] if args.compact else render_inspect(result))
                print(text,end='')
            elif args.command == "shrink":
                from .shrink import shrink_file, render_shrink
                result = shrink_file(store, args.path, prose=args.prose, preserve=args.preserve)
                content, metadata = render_shrink(result, store.retrieve(result["original"]), report=args.report)
                print(content, end="")
                print(metadata, end="", file=sys.stderr)
            elif args.command == "compact":
                from .compact import compact_file, render_compact
                result = compact_file(store, args.path, format=args.format)
                content, metadata = render_compact(result, store.retrieve(result["original"]), report=args.report)
                print(content, end="")
                print(metadata, end="", file=sys.stderr)
            elif args.command == "expand":
                from .compact import expand_text
                try:
                    text = store.file_bytes(args.path).decode("utf-8")
                except UnicodeDecodeError as error:
                    raise FlintError("expand supports UTF-8 only") from error
                print(expand_text(text), end="")
            elif args.command == "pack":
                from .pack import pack, render_pack
                print(render_pack(pack(store, args.manifest, query=args.query, budget=args.budget)), end="")
            elif args.command == "verify-pack":
                from .pack import verify_pack
                result = verify_pack(store, args.catalog)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 3 if result["freshness"] == "stale_selected_sources" else 0
            elif args.command == "begin":
                print(store.begin(args.label, args.inputs))
            elif args.command == "finish":
                print(json.dumps(store.finish(args.id, args.exit_code, args.log), indent=2))
            else:
                print(json.dumps(store.evidence(args.id), indent=2))
        return 0
    except (FlintError, OSError, *storage_errors) as error:
        print(f"Flint refused: {error}", file=sys.stderr)
        return 2


def _add_setup_parsers(sub):
    for name, description in (('install', 'install the ready-to-run CLI and optional agent skills'),
                              ('setup', 'connect the installed CLI to your agent'),
                              ('doctor', 'check installation, skills and engine'),
                              ('uninstall', 'remove unchanged installation files; keep project memories')):
        parser = sub.add_parser(name, help=description, description=description)
        parser.add_argument('--prefix', help='installation prefix (default: ~/.local or current installation)')
        if name in ('install', 'setup'):
            parser.add_argument('--agent', choices=('codex', 'claude', 'both', 'none'), help='agent to configure; interactive choice when omitted')
            parser.add_argument('--project', metavar='DIR', help='install skills for this existing project instead of user scope')
            parser.add_argument('--skill-dir', metavar='DIR', help='custom skills parent for another compatible agent')
        if name != 'doctor':
            parser.add_argument('--dry-run', action='store_true', help='show planned changes without writing files')
        if name == 'doctor':
            parser.add_argument('--json', action='store_true', help='machine-readable check results')
        if name == 'uninstall':
            parser.add_argument('--yes', action='store_true', help='confirm removal of unchanged owned files')


if __name__ == "__main__":
    raise SystemExit(main())
