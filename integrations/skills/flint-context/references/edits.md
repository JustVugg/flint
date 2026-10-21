# Revision-bound edits

Use for modifications already authorized by the user, after reading the
affected source and its dependencies. `inspect FILE SYMBOL --editable` emits
`E1 s:SOURCE "path" "symbol" scope start:end`, then exact source. The reference
identifies the full stored file even when the view shows only a definition.
Plain `inspect`, `map` and `read` also expose full-source hashes usable as bases.
Compact `V1` receipts from `inspect --compact` or `focus --compact` identify
metadata, NOT source bytes; do not use those receipts as edit bases. A snapshot
is not evidence that you have read omitted content.

For a known Python definition, avoid counting lines or copying old bodies:

```json
{"path":"module.py","base":"s:0123456789ab","edits":[{"symbol":"Service.run","text":"    def run(self):\n        return 42\n"}]}
```

Each object has exactly `symbol`, `text`, and optionally `where` (`replace`
by default, or `before` / `after`). Replacement covers the entire decorated
definition in the original base. Insertion is before its first decorator or
after its last body line. Leading/trailing standalone comments are outside
the definition. New code is literal: supply complete indentation, decorators
and newlines yourself. Empty text deletes the definition, subject to syntax
validation of the resulting file. Class/method names, async definitions and
nested classes are supported; nested functions and conditional definitions
are not indexed. Ambiguous definitions or owning classes fail. This selects
syntax, not runtime bindings, call sites or dependencies.

Use symbols for whole definitions. A tiny change inside a large function is
usually cheaper as a small ordinary patch or LF-line edit. The symbol editor
does not claim to minimize tokens or infer a better edit automatically.

`python3 /path/to/flint/bin/flint --workspace /repo edit plan.json` or `edit -`
with JSON on stdin accepts exactly:

```json
{"path":"module.py","base":"s:0123456789ab","edits":[[10,2,"    return result\n"],[30,0,"# insert before original line 30\n"]]}
```

Symbol objects and line triples can be mixed in one plan. Each triple is
`[start, delete_count, new_text]`: 1-based LF-line position in
the ORIGINAL base, number of complete lines to remove, and literal replacement
text. All splices refer to the same base, even when earlier ones add lines.
Insertion uses delete_count=0. End-of-file insertion uses line_count+1; supply
a leading newline yourself when the old file has no final newline. Newline
bytes are not normalized: use `\r\n` in new text when preserving CRLF style.
No indentation or language-specific rewrite is inferred.

The command requires the current file to match the whole stored base before
writing. A stale source is refused: read its current version and reconsider
coordinates instead of blindly retrying with a new hash. A retry of the same
plan returns `already-applied` when current bytes already equal the complete
intended result. It does not duplicate insertions. Multiple overlapping edits
or two edits at the same position are refused.

Receipt:

```text
EDIT "module.py" s:OLD -> s:NEW applied edits=2 python-syntax
```

Both references recover exact full files with `retrieve REF`. The new reference
can be the base of the next edit. It does not acknowledge a read session or
prove that you retain the whole result in context. Syntax validation for `.py`
does not execute code or prove behavior; other extensions report `text-only`.

Writes replace one file atomically and preserve permission bits. Regular
existing files only; links, private paths, overlaps and invalid UTF-8 are
refused. Advisory directory locks coordinate Flint writers; other writers can
ignore them. Revision checks are optimistic, not an OS compare-and-swap
guarantee against a hostile concurrent writer. Extended metadata/ACL preservation
and multi-file transactions are not promised.

Limits: 64 splices, 512 KiB source/result/replacement text, 24,000 LF boundaries in the original,
1 MiB stdin plan, Python result <=64 KiB for syntax parsing. Symbol resolution
also uses the navigation caps: <=64 KiB original, <=12,000 AST nodes and
<=512 direct definitions. File-based plans
use the normal 512 KiB file-read cap. Refused syntax, storage-cap or stale-base
checks do not modify the target. Original/result snapshots may remain in the
ledger after a refused file write. This is an opt-in editor, not a background
hook or a token-optimal automatic selector.

## Repair a rejected candidate (v0.16)

Use `edit --keep-draft plan.json` when preserving a substantial proposed Python
edit may avoid resending it after a syntax error. Normal successful edits have
the same receipt. A syntax failure returns **exit 3**, a `DRAFT` reference,
the candidate reference, and a bounded diagnostic. **The target was not
modified.** The diagnostic is quoted source data; a truncated excerpt is
explicitly marked. Other failures (stale file, bad plan, resource/storage caps)
remain refusals, not repairable drafts.

Correct only the necessary LF lines of that candidate:

```json
{"edits":[[26,1,"    \"\"\"Corrected docstring.\"\"\"\n"]]}
```

```bash
python3 /path/to/flint/bin/flint --workspace /repo edit correction.json --resume s:DRAFT
```

Resume accepts exactly `edits` with line triples. Positions refer to the stored
candidate, **not the live target or the original source**. Symbol lookup cannot
reliably parse invalid syntax. A valid correction applies the entire repaired
candidate only if the target still matches the original revision. An identical
retry is idempotent. Another syntax failure produces a new immutable draft,
still guarded by the original target version. Do not repeatedly retry stale
sources; read current content and reconsider the change.

`retrieve s:CANDIDATE --lines START:END` reads exact candidate ranges if needed.
The DRAFT reference identifies metadata and is not a source edit base. After
context loss, recover the relevant source and proposed changes before editing;
a stored draft does not establish that the agent still remembers them.

Candidates are never executed or automatically repaired. Python syntax is
checked, not behavior. Run appropriate tests after a successful commit.
Keep the ordinary editing path for small changes when cheaper; draft references
and diagnostics add tokens to failures and can cost more than a small retry.
Drafts share the existing local snapshot/record caps, with candidate <=64 KiB.
No separate eviction, network service, or unbounded retry loop is introduced.
