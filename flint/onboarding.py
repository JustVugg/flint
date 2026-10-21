"""Offline installation and skill setup. Loaded only by onboarding commands."""

from contextlib import contextmanager, nullcontext
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import stat
import sys
import tempfile
import zipfile

from . import __version__
from .common import FlintError

APP_RECORD = '.flint-install.json'
SKILL_RECORD = '.flint-skill.json'
MAX_ARTIFACT = 4 * 1024 * 1024
MAX_SKILL = 256 * 1024


def _json(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _path(value):
    path = Path(value).expanduser().absolute()
    if any(ord(c) < 32 for c in str(path)) or '..' in path.parts:
        raise FlintError('installation paths must not contain controls or ..')
    return path


def _no_links(path):
    for part in (path, *path.parents):
        if part.is_symlink():
            raise FlintError(f'symlink in managed path: {part}')


def _read(path):
    _no_links(path)
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > MAX_ARTIFACT:
        raise FlintError(f'not a bounded regular file: {path}')
    return path.read_bytes()


def _relative(name):
    path = PurePosixPath(name)
    if (not isinstance(name, str) or not name or path.is_absolute()
            or any(p in ('..', '.') for p in path.parts) or '\\' in name
            or path.as_posix() != name):
        raise FlintError('invalid installed file record')
    return path


def package_path():
    archive = getattr(__loader__, 'archive', None)
    return Path(archive) if archive else Path(__file__).resolve().parents[1] / 'dist/flint.pyz'


def default_prefix():
    archive = package_path().absolute()
    app = archive.parent
    if app.name == 'flint' and app.parent.name == 'share' and (app / APP_RECORD).is_file():
        return app.parent.parent
    return Path.home() / '.local'


def _bundle(package):
    data = package.read_bytes() if package.is_file() and package.stat().st_size <= MAX_ARTIFACT else b''
    if not data:
        raise FlintError('ready-to-run flint.pyz is missing; use the download or rebuild tools/package.py')
    try:
        with zipfile.ZipFile(package) as archive:
            entries = archive.infolist()
            if len(entries) > 160 or sum(e.file_size for e in entries) > MAX_ARTIFACT:
                raise FlintError('package exceeds installation limits')
            if len({e.filename for e in entries}) != len(entries) or archive.testzip():
                raise FlintError('damaged package')
            version = archive.read('flint/__init__.py').decode()
            if not re.search(r'__version__\s*=\s*[\"\']' + re.escape(__version__) + r'[\"\']', version):
                raise FlintError('package version differs from running source; rebuild it first')
            skill = {}
            prefix = 'flint/data/skill/'
            for entry in entries:
                if entry.filename.startswith(prefix) and not entry.is_dir():
                    name = entry.filename[len(prefix):]
                    _relative(name)
                    skill[name] = archive.read(entry)
            if 'SKILL.md' not in skill:
                raise FlintError('package does not contain the agent skill')
            if sum(map(len, skill.values())) > MAX_SKILL:
                raise FlintError('packaged skill exceeds installation limits')
            files = {'flint.pyz': data, 'LICENSE': archive.read('LICENSE'), 'NOTICE': archive.read('NOTICE')}
            return files, skill
    except (zipfile.BadZipFile, KeyError, UnicodeError) as error:
        raise FlintError('invalid Flint package') from error


def _owned(root, marker, owner):
    """Validate all owned files before any write; extra/user-edited files refuse."""
    _no_links(root)
    if not root.exists():
        return None, {}
    if (root / marker).is_file() and (root / marker).stat().st_size > 65536:
        raise FlintError('ownership record exceeds 64 KiB')
    raw = _read(root / marker)
    if raw is None:
        raise FlintError(f'existing directory is not managed by Flint: {root}')
    try:
        record = json.loads(raw)
        if (record['kind'] != marker or record['owner'] != str(owner)
                or not isinstance(record['files'], dict) or len(record['files']) > 64):
            raise ValueError()
        required = {'flint.pyz', 'LICENSE', 'NOTICE'} if marker == APP_RECORD else {'SKILL.md', 'scripts/flint'}
        if not required.issubset(record['files']) or marker in record['files']:
            raise ValueError()
        known = {marker}
        contents = {root / marker: raw}
        total = 0
        for name, expected in record['files'].items():
            _relative(name)
            _no_links(root / name)
            if (root / name).is_file():
                total += (root / name).stat().st_size
            if total > (MAX_ARTIFACT if marker == APP_RECORD else MAX_SKILL):
                raise FlintError('managed directory exceeds installation limits')
            payload = _read(root / name)
            if payload is None or _hash(payload) != expected:
                raise FlintError(f'managed file was changed or removed; preserve it before retrying: {root / name}')
            known.add(name)
            contents[root / name] = payload
        for current, dirs, files in os.walk(root, followlinks=False):
            for name in dirs + files:
                path = Path(current) / name
                if path.is_symlink():
                    raise FlintError(f'symlink in managed directory: {path}')
            for name in files:
                if (Path(current) / name).relative_to(root).as_posix() not in known:
                    raise FlintError(f'unmanaged file in installation; leaving it intact: {Path(current) / name}')
        return record, contents
    except (KeyError, TypeError, ValueError) as error:
        raise FlintError(f'invalid ownership record: {root / marker}') from error


def _launcher(prefix):
    python = str(Path(sys.executable).resolve())
    target = str(prefix / 'share/flint/flint.pyz')
    return ('#!/bin/sh\n# Managed by Flint.\nexec ' + shlex.quote(python)
            + ' -S ' + shlex.quote(target) + ' "$@"\n').encode()


def skill_targets(agent, project=None, skill_dir=None):
    if skill_dir is not None:
        if project is not None or agent not in (None, 'none'):
            raise FlintError('--skill-dir is an alternative to --agent/--project')
        return [_path(skill_dir) / 'flint-context']
    if agent in (None, 'none'):
        return []
    base = _path(project) if project else Path.home()
    if project and not base.is_dir():
        raise FlintError('project directory must already exist')
    selected = ('codex', 'claude') if agent == 'both' else (agent,)
    targets = []
    for name in selected:
        if name == 'codex':
            directory = base / '.agents/skills' if project else _path(os.environ.get('CODEX_HOME', base / '.codex')) / 'skills'
        elif name == 'claude':
            directory = base / '.claude/skills' if project else _path(os.environ.get('CLAUDE_CONFIG_DIR', base / '.claude')) / 'skills'
        else:
            raise FlintError('unsupported agent; use --skill-dir for a custom skill location')
        targets.append(directory / 'flint-context')
    return targets


def _registered(record):
    targets = record.get('skills', []) if record else []
    if (not isinstance(targets, list) or len(targets) > 32
            or any(not isinstance(p, str) for p in targets) or len(set(targets)) != len(targets)):
        raise FlintError('invalid installed skill registry')
    result = []
    for item in targets:
        target = _path(item)
        if str(target) != item or target.name != 'flint-context':
            raise FlintError('invalid registered skill path')
        result.append(target)
    return result


def _record(root, marker, files, owner, **extra):
    result = {root / name: payload for name, payload in files.items()}
    result[root / marker] = _json({'kind': marker, 'owner': str(owner), 'version': __version__,
                                  'files': {name: _hash(data) for name, data in files.items()}, **extra})
    return result


def _mkdir(path, created):
    missing = []
    while not path.exists():
        missing.append(path)
        path = path.parent
    _no_links(path)
    for directory in reversed(missing):
        directory.mkdir()
        created.append(directory)


def _write(path, data, mode):
    fd, temporary = tempfile.mkstemp(prefix='.flint-write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _transaction(wanted, expected, executables=()):
    """Bounded writes with optimistic checks and rollback on caught I/O errors."""
    plan = {path: None for path in expected}
    plan.update(wanted)
    modes = {}
    for path in plan:
        if _read(path) != expected.get(path):
            raise FlintError(f'target changed or belongs to another program: {path}')
        modes[path] = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    applied = []
    created = []
    try:
        for path, data in sorted(plan.items(), key=lambda pair: (pair[0].name in (APP_RECORD, SKILL_RECORD), str(pair[0]))):
            if data == expected.get(path) and (path not in executables or os.access(path, os.X_OK)):
                continue
            _mkdir(path.parent, created)
            if _read(path) != expected.get(path):
                raise FlintError(f'target changed during installation: {path}')
            if data is None:
                path.unlink()
            else:
                _write(path, data, 0o755 if path in executables else 0o644)
            applied.append(path)
    except BaseException:
        for path in reversed(applied):
            if _read(path) != plan[path]:
                continue  # Never overwrite a concurrent external change during rollback.
            previous = expected.get(path)
            if previous is None:
                path.unlink()
            else:
                _write(path, previous, modes[path])
        for directory in reversed(created):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise


@contextmanager
def _lock(prefix):
    import fcntl
    directory = prefix / 'share'
    _no_links(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / '.flint-install.lock'
    _no_links(path)
    with path.open('a+b') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise FlintError('another Flint installer is running') from error
        yield


def install(prefix, targets=(), *, package=None, dry_run=False, setup_only=False):
    prefix = _path(prefix)
    app = prefix / 'share/flint'
    launcher = prefix / 'bin/flint'
    files, skill = _bundle(package or package_path())
    with nullcontext() if dry_run else _lock(prefix):
        record, expected = _owned(app, APP_RECORD, app)
        if setup_only and record is None:
            raise FlintError('Flint is not installed here; run python3 install.py first')
        if record:
            existing = _read(launcher)
            if existing is None or _hash(existing) != record.get('launcher_sha256'):
                raise FlintError(f'launcher was changed or removed: {launcher}')
            expected[launcher] = existing
        elif _read(launcher) is not None:
            raise FlintError(f'will not replace another flint command: {launcher}')
        roots = list(dict.fromkeys([*_registered(record), *(_path(p) for p in targets)]))
        if len(roots) > 32:
            raise FlintError('at most 32 installed skill locations')
        if any(a.is_relative_to(b) for a in roots for b in roots if a != b):
            raise FlintError('managed skill directories must not overlap')
        wanted = {}
        executable = _launcher(prefix)
        executables = [launcher]
        for target in roots:
            if target.name != 'flint-context' or target == app or target.is_relative_to(app) or app.is_relative_to(target):
                raise FlintError('skill directory must be named flint-context and separate from the application')
            old, content = _owned(target, SKILL_RECORD, app)
            expected.update(content)
            material = dict(skill, **{'scripts/flint': executable})
            wanted.update(_record(target, SKILL_RECORD, material, app))
            executables.append(target / 'scripts/flint')
        wanted.update(_record(app, APP_RECORD, files, app, skills=[str(p) for p in roots],
                              launcher_sha256=_hash(executable), python=str(Path(sys.executable).resolve())))
        wanted[launcher] = executable
        # Dry runs perform the same collision checks but create nothing.
        for path in wanted:
            if _read(path) != expected.get(path):
                raise FlintError(f'will not overwrite an unmanaged file: {path}')
        if not dry_run:
            _transaction(wanted, expected, executables)
        return {'version': __version__, 'prefix': str(prefix), 'command': str(launcher),
                'skills': [str(p) for p in roots], 'dry_run': dry_run}


def uninstall(prefix, *, dry_run=False):
    prefix = _path(prefix)
    app = prefix / 'share/flint'
    _no_links(app)
    if not app.exists():
        return {'removed': False, 'skills': [], 'dry_run': dry_run}
    with nullcontext() if dry_run else _lock(prefix):
        record, expected = _owned(app, APP_RECORD, app)
        if record is None:
            return {'removed': False, 'skills': [], 'dry_run': dry_run}
        launcher = prefix / 'bin/flint'
        data = _read(launcher)
        if data is None or _hash(data) != record.get('launcher_sha256'):
            raise FlintError(f'launcher changed; leaving installation intact: {launcher}')
        expected[launcher] = data
        roots = _registered(record)
        for target in roots:
            _, contents = _owned(target, SKILL_RECORD, app)
            expected.update(contents)
        if not dry_run:
            _transaction({}, expected)
            for root in [*roots, app]:
                directories = {p.parent for p in expected if p.is_relative_to(root)} | {root}
                for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
        return {'removed': True, 'skills': [str(p) for p in roots], 'dry_run': dry_run}


def doctor(prefix):
    prefix = _path(prefix)
    app = prefix / 'share/flint'
    checks = []

    def check(name, function):
        try:
            detail = function()
            checks.append({'name': name, 'status': 'ok', 'detail': detail})
        except Exception as error:
            checks.append({'name': name, 'status': 'error', 'detail': str(error)})

    def installed():
        record, _ = _owned(app, APP_RECORD, app)
        if not record:
            raise FlintError('not installed; run python3 install.py or python3 flint.pyz install')
        launcher = prefix / 'bin/flint'
        data = _read(launcher)
        if data is None or _hash(data) != record.get('launcher_sha256') or not os.access(launcher, os.X_OK):
            raise FlintError('launcher missing, changed or not executable')
        if not os.access(record.get('python', ''), os.X_OK):
            raise FlintError('installed Python interpreter is unavailable; reinstall using Python 3.11+')
        return f"Flint {record['version']}; package, licenses and launcher verified"

    def skills():
        record, _ = _owned(app, APP_RECORD, app)
        roots = _registered(record)
        for target in roots:
            item, _ = _owned(target, SKILL_RECORD, app)
            if item is None or not os.access(target / 'scripts/flint', os.X_OK):
                raise FlintError(f'skill missing or launcher not executable: {target}')
        return [str(p) for p in roots]

    def engine():
        from .encode import encode_text, decode_text
        from .store import Store
        from .memory import Memory
        from .work import open_work, edit_work
        text = '[{"x":1},{"x":2},{"x":3}]'
        packed, method = encode_text(text)
        if json.loads(decode_text(packed, format=method)) != json.loads(text):
            raise FlintError('JSON roundtrip failed')
        with tempfile.TemporaryDirectory(prefix='flint-doctor-') as tmp:
            root = Path(tmp)
            (root / 'sample.py').write_text('value = 1\n')
            (root / 'decision.txt').write_text('Keep the public interface stable.\n')
            with Store(root) as store:
                ticket = open_work(store, 'sample.py').split()[1]
                result = edit_work(store, ticket, {'edits': [[1, 1, 'value = 2\n']]})
                if result['status'] != 'applied' or (root / 'sample.py').read_text() != 'value = 2\n':
                    raise FlintError('revision-bound edit failed')
                memory = Memory(store)
                memory.remember('decision.txt', 'interface')
                if not memory.recall('interface')['hits']:
                    raise FlintError('persistent memory check failed')
        return 'compression, recovery, revision-bound edits and memory passed in a temporary workspace'

    checks.append({'name': 'python', 'status': 'ok' if sys.version_info >= (3, 11) else 'error',
                   'detail': sys.version.split()[0]})
    check('installation', installed)
    check('skills', skills)
    check('engine', engine)
    found = shutil.which('flint')
    expected = prefix / 'bin/flint'
    matches = bool(found and Path(found).resolve() == expected.resolve())
    checks.append({'name': 'PATH', 'status': 'ok' if matches else 'warning',
                   'detail': found if matches else 'Run: export PATH=' + shlex.quote(str(prefix / 'bin')) + ':"$PATH"'})
    if not any(c['name'] == 'skills' and c['status'] == 'ok' and c['detail'] for c in checks):
        checks.append({'name': 'agent', 'status': 'warning', 'detail': 'No configured skill. Run flint setup.'})
    return {'version': __version__, 'ok': all(c['status'] != 'error' for c in checks), 'checks': checks,
            'note': 'Checks verify files and the engine; restart your agent to discover the skill. No model request was made.'}


def _select_agent():
    print('Connect Flint to an agent:')
    print('  1. Codex   2. Claude Code   3. Both   4. CLI only')
    try:
        answer = input('Choose [1-4, default 4]: ').strip() or '4'
    except EOFError:
        answer = '4'
    if answer not in ('1', '2', '3', '4'):
        raise FlintError('choose 1, 2, 3 or 4; or use --agent codex|claude|both|none')
    return {'1': 'codex', '2': 'claude', '3': 'both', '4': 'none'}[answer]


def run(args):
    if sys.platform != 'linux':
        raise FlintError('the installer currently supports Linux and WSL; use WSL on Windows')
    prefix = _path(args.prefix or default_prefix())
    if args.command == 'doctor':
        result = doctor(prefix)
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            for item in result['checks']:
                print(f"{item['status'].upper():7} {item['name']}: {item['detail']}")
            print(result['note'])
        return 0 if result['ok'] else 1
    if args.command == 'uninstall':
        if not args.yes and not args.dry_run:
            if not sys.stdin.isatty() or input('Remove Flint CLI and its unchanged skills? [y/N] ').strip().lower() not in ('y', 'yes'):
                raise FlintError('nothing removed; use --dry-run to inspect or --yes to confirm')
        result = uninstall(prefix, dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
        print('Project files and .flint memories are preserved.')
        return 0
    agent = args.agent
    if agent is None and args.skill_dir is None:
        if sys.stdin.isatty() and not args.dry_run:
            agent = _select_agent()
        elif args.command == 'setup':
            raise FlintError('choose --agent codex|claude|both or --skill-dir DIR')
        else:
            agent = 'none'
    targets = skill_targets(agent, args.project, args.skill_dir)
    print(f"{'Preview' if args.dry_run else 'Install'} Flint {__version__}: {prefix / 'bin/flint'}")
    for target in targets:
        print(f'Skill: {target}')
    result = install(prefix, targets, dry_run=args.dry_run, setup_only=args.command == 'setup')
    if args.dry_run:
        print('No files changed.')
        return 0
    if shutil.which('flint') != str(prefix / 'bin/flint'):
        print('For this terminal: export PATH=' + shlex.quote(str(prefix / 'bin')) + ':"$PATH"')
        print('Add that line to your shell profile to keep the command on PATH.')
    print('Ready. Run: flint doctor')
    if result['skills']:
        print('Restart your agent. Invoke flint-context, or ask it to use Flint for this project.')
    else:
        print('Connect an agent: flint setup --agent codex  (or --agent claude)')
    return 0
