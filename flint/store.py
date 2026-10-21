"""A bounded local ledger. It never executes commands or makes network calls."""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from pathlib import Path

from .common import FlintError, MAX_FILE, digest
from .files import clean_path, file_bytes, DENIED

MAX_CONTENT = 16 * 1024 * 1024
MAX_DB = 32 * 1024 * 1024
MAX_INPUTS = 256
MAX_RECORDS = 4096


def select_lines(data: bytes, lines, *, strict=False) -> bytes:
    start, stop = lines
    if type(start) is not int or type(stop) is not int or not 1 <= start <= stop:
        raise FlintError("line range must satisfy 1 <= start <= end")
    parts = data.split(b"\n")
    chunks = [part + b"\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])
    if start > len(chunks) or (strict and stop > len(chunks)):
        raise FlintError("line range exceeds source")
    return b"".join(chunks[start - 1:stop])


class Store:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise FlintError("workspace must be a directory")
        state = self.root / ".flint"
        if state.is_symlink():
            raise FlintError("state directory must not be a symlink")
        state.mkdir(mode=0o700, exist_ok=True)
        for name in ("ledger.sqlite3", "ledger.sqlite3-journal", "ledger.sqlite3-wal", "ledger.sqlite3-shm"):
            path = state / name
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise FlintError("unsafe state file")
            if path.exists() and path.stat().st_size > MAX_DB:
                raise FlintError("state exceeds storage cap")
        self.db = sqlite3.connect(state / "ledger.sqlite3", timeout=1)
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA max_page_count=8192")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, created REAL);
            CREATE TABLE IF NOT EXISTS blobs(hash TEXT PRIMARY KEY, data BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS deliveries(
                session TEXT, path TEXT, hash TEXT, start INTEGER, stop INTEGER,
                PRIMARY KEY(session, path, hash, start, stop),
                FOREIGN KEY(session) REFERENCES sessions(id),
                FOREIGN KEY(hash) REFERENCES blobs(hash));
            CREATE TABLE IF NOT EXISTS evidence(
                id TEXT PRIMARY KEY, label TEXT, manifest TEXT,
                status TEXT, exit_code INTEGER, result_hash TEXT,
                FOREIGN KEY(result_hash) REFERENCES blobs(hash));
        """)
        # Old deliveries must remain unacknowledged after upgrading: they are
        # evidence of a request, not proof its output survived tool truncation.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(deliveries)")}
            if "ticket" not in columns:
                self.db.execute("ALTER TABLE deliveries ADD COLUMN ticket TEXT")
            if "acknowledged" not in columns:
                self.db.execute("ALTER TABLE deliveries ADD COLUMN acknowledged INTEGER NOT NULL DEFAULT 0")
            self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS delivery_ticket ON deliveries(ticket)")

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _room(self, table: str):
        if self.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] >= MAX_RECORDS:
            raise FlintError("record cap reached; use a new workspace or archive local state")

    def session(self) -> str:
        with self.db:
            self._room("sessions")
            identifier = uuid.uuid4().hex
            self.db.execute("INSERT INTO sessions VALUES (?,?)", (identifier, time.time()))
        return identifier

    def _session_exists(self, identifier: str):
        if not self.db.execute("SELECT 1 FROM sessions WHERE id=?", (identifier,)).fetchone():
            raise FlintError("unknown session; create one with `flint session`")

    clean_path = staticmethod(clean_path)

    def file_bytes(self, name: str) -> bytes:
        return file_bytes(self.root, name)

    def _save(self, data: bytes) -> str:
        if len(data) > MAX_FILE:
            raise FlintError("snapshot exceeds 512 KiB")
        handle = digest(data)
        old = self.db.execute("SELECT data FROM blobs WHERE hash=?", (handle,)).fetchone()
        if old:
            if bytes(old[0]) != data:
                raise FlintError("snapshot integrity failure")
            return handle
        used = self.db.execute("SELECT coalesce(sum(length(data)), 0) FROM blobs").fetchone()[0]
        if used + len(data) > MAX_CONTENT:
            raise FlintError("snapshot budget exhausted; no content has been suppressed")
        self._room("blobs")
        self.db.execute("INSERT INTO blobs VALUES (?,?)", (handle, data))
        return handle

    def short_handle(self, handle: str) -> str:
        """Unambiguous workspace-local SHA prefix; not a read receipt."""
        self.retrieve(handle)
        for width in range(12, 65, 4):
            prefix = handle[:width]
            rows = self.db.execute('SELECT hash FROM blobs WHERE hash LIKE ? LIMIT 2', (prefix+'%',)).fetchall()
            if len(rows) == 1:
                return 's:'+prefix
        raise FlintError('cannot allocate unambiguous snapshot reference')

    def retrieve(self, handle: str, *, lines=None) -> bytes:
        if isinstance(handle, str) and handle.startswith('s:'):
            prefix = handle[2:]
            if not re.fullmatch(r'[a-f0-9]{12,64}', prefix):
                raise FlintError('invalid short snapshot reference')
            rows = self.db.execute('SELECT hash FROM blobs WHERE hash LIKE ? LIMIT 2', (prefix+'%',)).fetchall()
            if len(rows) != 1:
                raise FlintError('missing or ambiguous short snapshot reference')
            handle = rows[0][0]
        if not re.fullmatch(r"[a-f0-9]{64}", handle):
            raise FlintError("handle must be a complete SHA-256 digest")
        row = self.db.execute("SELECT data FROM blobs WHERE hash=?", (handle,)).fetchone()
        if row is None:
            raise FlintError("snapshot not found")
        data = bytes(row[0])
        if digest(data) != handle:
            raise FlintError("snapshot integrity failure")
        return data if lines is None else select_lines(data, lines, strict=True)

    def read(self, name: str, session: str, *, full=False, lines=None, delta=False, compact=False) -> dict:
        if compact and lines is not None:
            raise FlintError('compact reads require full-file scope')
        if compact:
            from .wire import render_compact as render
        else:
            render = render_read
        if delta and lines is not None:
            raise FlintError('delta reads require complete-file scope; omit --lines')
        self._session_exists(session)
        data = self.file_bytes(name)
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise FlintError("read supports UTF-8 text only") from error
        start, stop = (0, 0) if lines is None else lines
        selected = data
        if lines is not None:
            selected = select_lines(data, lines)
        with self.db:
            handle = self._save(data)
            key = (session, name, handle, start, stop)
            seen = self.db.execute("SELECT ticket,acknowledged FROM deliveries WHERE session=? AND path=? AND hash=? AND start=? AND stop=?", key).fetchone()
            ticket = seen[0] if seen and seen[0] else uuid.uuid4().hex
            if not seen:
                self._room("deliveries")
                self.db.execute("INSERT INTO deliveries(session,path,hash,start,stop,ticket) VALUES (?,?,?,?,?,?)", key + (ticket,))
            elif not seen[0]:
                self.db.execute("UPDATE deliveries SET ticket=? WHERE session=? AND path=? AND hash=? AND start=? AND stop=?", (ticket,) + key)
        result = {"status": "content", "path": name, "handle": handle, "file_bytes": len(data),
                  "lines": None if lines is None else [start, stop], "content": selected.decode("utf-8"),
                  "ticket": ticket, "acknowledged": bool(seen and seen[1])}
        if compact:
            row = self.db.execute('SELECT rowid FROM deliveries WHERE ticket=? AND session=?',(ticket,session)).fetchone()
            result['reference'] = 'r'+str(row[0])
        if seen and seen[1] and not full and len(selected) > 256:
            receipt = dict(result, status="unchanged", content=None)
            # A receipt can cost more than a tiny file. Keep the content then.
            if len(render(receipt).encode()) < len(render(result).encode()):
                return receipt
        if delta and not full:
            previous = self.db.execute(
                'SELECT hash,rowid FROM deliveries WHERE session=? AND path=? AND start=0 AND stop=0 '
                'AND acknowledged=1 AND hash!=? ORDER BY rowid DESC LIMIT 1',
                (session,name,handle)).fetchone()
            if previous:
                from .delta import make_delta, make_patch
                baseline = self.retrieve(previous[0])
                best, size = result, len(render(result).encode())
                for change in (make_delta(baseline,data), make_patch(baseline,data)):
                    if change is None:
                        continue
                    candidate = dict(result, **change, status='delta')
                    if compact:
                        candidate['base_reference'] = 'r'+str(previous[1])
                    if 'splices' in change:
                        candidate.pop('content')
                    cost = len(render(candidate).encode())
                    if cost < size:
                        best, size = candidate, cost
                return best
        return result

    def resolve_reference(self, reference: str, session: str) -> dict:
        self._session_exists(session)
        if not isinstance(reference,str) or not re.fullmatch(r'r[1-9][0-9]{0,9}',reference):
            raise FlintError('invalid local reference')
        row = self.db.execute('SELECT path,hash,start,stop,ticket,acknowledged FROM deliveries WHERE rowid=? AND session=?',
                              (int(reference[1:]),session)).fetchone()
        if row is None:
            raise FlintError('reference not found in this session')
        self.retrieve(row[1])
        return {'path':row[0],'handle':row[1],'lines':None if row[2:4]==(0,0) else list(row[2:4]),
                'ticket':row[4],'acknowledged':bool(row[5])}

    def acknowledge(self, ticket: str, session: str) -> dict:
        self._session_exists(session)
        if ticket.startswith('r'):
            ticket = self.resolve_reference(ticket,session)['ticket']
        row = self.db.execute("SELECT hash FROM deliveries WHERE ticket=? AND session=?", (ticket, session)).fetchone()
        if row is None:
            raise FlintError("unknown delivery in this session")
        self.retrieve(row[0])
        with self.db:
            self.db.execute("UPDATE deliveries SET acknowledged=1 WHERE ticket=? AND session=?", (ticket, session))
        return {"status": "acknowledged", "ticket": ticket}

    def manifest(self, paths: list[str]) -> dict:
        if not paths or len(paths) > MAX_INPUTS or len(paths) != len(set(paths)):
            raise FlintError("declare between 1 and 256 distinct input files")
        return {p: digest(self.file_bytes(p)) for p in sorted(paths)}

    def changed(self, manifest: dict) -> list[str]:
        changed = []
        for path, expected in manifest.items():
            try:
                actual = digest(self.file_bytes(path))
            except FlintError:
                actual = None
            if actual != expected:
                changed.append(path)
        return changed

    def begin(self, label: str, paths: list[str]) -> str:
        """Capture declared inputs before an external check. Does not run it."""
        if not label.strip() or len(label) > 256:
            raise FlintError("label must contain 1..256 characters")
        manifest = self.manifest(paths)
        identifier = uuid.uuid4().hex
        with self.db:
            self._room("evidence")
            self.db.execute("INSERT INTO evidence VALUES (?,?,?,'pending',NULL,NULL)",
                            (identifier, label, json.dumps(manifest, sort_keys=True)))
        return identifier

    def _evidence(self, identifier: str):
        row = self.db.execute("SELECT label,manifest,status,exit_code,result_hash FROM evidence WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise FlintError("unknown evidence ID")
        return row

    def finish(self, identifier: str, exit_code: int, result_path: str) -> dict:
        row = self._evidence(identifier)
        if row[2] != "pending":
            raise FlintError("evidence already finalized; create a new check")
        if not isinstance(exit_code, int) or not -255 <= exit_code <= 255:
            raise FlintError("invalid exit code")
        result = self.file_bytes(result_path)
        changed = self.changed(json.loads(row[1]))
        status = "changed_during_check" if changed else ("passed" if exit_code == 0 else "failed")
        with self.db:
            handle = self._save(result)
            updated = self.db.execute("UPDATE evidence SET status=?,exit_code=?,result_hash=? WHERE id=? AND status='pending'",
                                      (status, exit_code, handle, identifier))
            if updated.rowcount != 1:
                raise FlintError("another caller already finalized this check")
        return self.evidence(identifier)

    def evidence(self, identifier: str) -> dict:
        label, serialized, status, exit_code, result = self._evidence(identifier)
        manifest = json.loads(serialized)
        changed = self.changed(manifest)
        # Corruption must not yield a fresh success receipt.
        if result is not None:
            self.retrieve(result)
        freshness = "pending" if status == "pending" else "stale" if changed or status == "changed_during_check" else "fresh_for_declared_inputs"
        return {"id": identifier, "label": label, "recorded_status": status,
                "freshness": freshness, "changed_inputs": changed,
                "input_count": len(manifest), "exit_code": exit_code, "result_handle": result,
                "scope": "declared file bytes only; result supplied by caller; environment and undeclared inputs not verified"}


def render_read(result: dict) -> str:
    location = result["path"]
    if result["lines"]:
        location += ":" + ":".join(map(str, result["lines"]))
    if result["status"] == "unchanged":
        return f"UNCHANGED {location} sha256={result['handle']} bytes={result['file_bytes']}\nAcknowledged in this session. Need contents? Use read --full or retrieve HANDLE.\n"
    if result['status'] == 'delta':
        if 'splices' in result:
            from .delta import render_splice
            return (f"PATCH/1 {location} base={result['base']} sha256={result['handle']} bytes={result['file_bytes']}\n"
                    + f"{len(result['splices'])} splices, positions in original baseline lines; apply together.\n"
                    + ''.join(render_splice(patch) for patch in result['splices'])
                    + f"END PATCH ticket={result['ticket']} (ack only with retained baseline and all changes)\n")
        return (f"DELTA {location} base={result['base']} sha256={result['handle']} bytes={result['file_bytes']}\n"
                f"SPLICE start={result['start_line']} remove={result['remove_lines']} insert={result['insert_lines']} payload_bytes={len(result['content'].encode())}\n"
                + result['content']
                + f"\nEND DELTA ticket={result['ticket']} (ack only with retained baseline and complete change)\n")
    return f"CONTENT {location} sha256={result['handle']} bytes={result['file_bytes']}\n" + result["content"] + f"\nEND CONTENT ticket={result['ticket']} (ack only after complete receipt)\n"
