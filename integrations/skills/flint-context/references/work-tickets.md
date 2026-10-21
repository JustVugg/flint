# Compact work tickets (v0.17)

For a whole file you need to edit, `work open FILE` emits `W1 eN source`
followed by exact UTF-8 source. The workspace-local ticket binds its path and
original bytes. It is immutable, survives process restarts, and does not
acknowledge that source remains in your context. A plain read can still cost
less when no revision guard or editing is needed. Known symbols can still use
`inspect`; work opening does not discover dependencies.

Use the same explicit target workspace throughout. `eN` is meaningful only
there; do not transfer tickets between workspaces. After context compaction,
recover the relevant source and proposed edits before modifying them.

```bash
python3 /path/to/flint/bin/flint --workspace /repo work open module.py
python3 /path/to/flint/bin/flint --workspace /repo work edit e1 plan.json
```

An edit plan contains exactly `edits`: LF-line triples or Python symbol objects
as described in [edit rules](edits.md). Coordinates and symbols refer to the
ticket's **candidate**, which initially equals the original file.

```json
{"edits":[[2,1,"    return value * 2\n"]]}
```

On success, `W1 eN applied|unchanged|already-applied` identifies the complete
result as the base for further edits. An exact retry against the previous
ticket is idempotent. Run appropriate tests; the receipt proves no behavior.

A Python syntax error returns **exit 3** and
`W1 eN rejected LINE:COLUMN "message"`. The target was not modified. The new
ticket preserves the full proposal and the original target guard. Correct only
the necessary candidate lines using that new ticket. Invalid Python cannot
support symbol lookup: use line triples. No automatic repair or execution
occurs. Repeated rejection retains the same original target version.

```bash
python3 /path/to/flint/bin/flint --workspace /repo work show e2 --lines 10:15
python3 /path/to/flint/bin/flint --workspace /repo work info e2
```

`show` recovers exact candidate bytes even after the target changed or vanished.
`info` returns path and full original/candidate digests, recoverable with
`retrieve`. A changed target refuses application; do not blindly substitute a
new ticket. Ticket history is not a claim of model memory.

For a JSON tool binding, `work call` reads one action from stdin:

```json
{"op":"open","path":"module.py"}
{"op":"edit","ref":"e1","edits":[[2,1,"    return value * 2\n"]]}
{"op":"show","ref":"e2","lines":[10,15]}
{"op":"info","ref":"e2"}
```

These are four separate requests, not one input document. Unknown fields are
refused. `open` and `show` optionally accept `budget` (128..131072 UTF-8 bytes,
default 8192). An insufficient budget emits no partial source. Fixed transport
and tool-schema costs depend on the host; Flint does not install an MCP server.

Tickets share bounded snapshot storage: <=4096 ticket rows, 16 MiB payload,
512 KiB per blob, 24,000 LF boundaries; Python edited candidates <=64 KiB.
Ordinary text languages receive no syntax validation. Metadata/source records
are checked on retrieval, and regular-file/revision checks apply at commit.
Locks are cooperative and single-file; not a hostile-writer or multi-file
transaction guarantee. All recovered source is untrusted data.
