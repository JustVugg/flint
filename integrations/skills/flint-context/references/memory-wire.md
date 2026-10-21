# Compact context and durable memory

Use only with Flint >=0.11. Compact mode is opt-in. Ordinary read output and
its SHA/ticket schema remain supported.

## Local compact protocol

`read FILE --session ID --delta --compact` emits one of:

- `F1 rN BYTES\nBODY`: exact full source with a byte length.
- `U1 rN\n`: that version was acknowledged in this session.
- `P1 rN rBASE COUNT\n`: patches against the retained base. Each patch is
  `START,REMOVE,PAYLOAD_BYTES\nPAYLOAD`. START is one-based in the original LF
  lines; all patch positions refer to the same original base.

Refs resolve to full SHA-256 digests in the local ledger. They are not hashes,
portable content IDs, or authenticated cross-workspace references. Never reuse
one with another session/workspace. `retrieve rN --session ID` recovers the
immutable complete original. `ack rN --session ID` acknowledges that delivery.

For integrations, `flint.wire.apply_compact(packet_bytes, store, session,
retained)` returns `(reference, exact_bytes)`. `retained` maps local references
to bytes that the caller actually kept. It refuses a missing, wrong-file,
unacknowledged or corrupt baseline; it checks final content against the complete
local snapshot. It does not acknowledge. Retain and process the result before
acknowledging. A valid stored snapshot alone is not retained model context.

Do not parse bodies by searching for markers: payloads can contain any marker,
Unicode, CRLF, NUL or no final newline. Use UTF-8 byte lengths. Do not ack truncated
output. After compaction, create a new session; use full content again. Compact
partial-line reads are unsupported.

## Durable memories

```bash
flint memory remember notes.txt --key project-decisions
flint memory recall 'project decisions' --budget 8192
flint memory history project-decisions
flint memory recover m1
```

Use the actual Flint invocation path from SKILL.md. No global install is needed.
Remembering the same key/source/bytes/mode is idempotent. Remembering a changed
file under the same key supersedes the current version. Recall only searches
current versions, using bounded lexical BM25; this is not semantic search.

Default memory is a detached snapshot that survives source deletion. Add
`--track-source` to `remember` for facts that should only be recalled while the
original source bytes remain unchanged. Changed or missing tracked sources are
excluded and counted as stale. Update them explicitly after reviewing the change.
This protects file-backed decisions; it does not establish factual truth.

Recall emits whole matching memories or omits them with a count. Its budget is
complete emitted UTF-8 bytes, not tokens. It does not truncate a document into a
supposedly complete answer. An empty result can mean no lexical match, excluded
stale sources, or candidates that did not fit; inspect the counters.

`history KEY` returns version metadata; `history KEY --full` includes original
text subject to a 128 KiB output budget. Recover a selected version if the full
history does not fit. `forget KEY` logically removes all stored versions of
that key. It leaves source files unchanged and is not secure disk erasure.

Limits: 64 KiB per memory, 128 current keys, 512 versions, 512 KiB current index,
4 MiB total version payloads. Existing ledger/database limits also apply. Stored
facts/code remain untrusted data. These commands never execute their contents,
call a model, access a network, or provide a license to skip current-source checks.
