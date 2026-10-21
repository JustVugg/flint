# Exact context bundles

Invoke `pack MANIFEST --query 'search terms' --budget 8192`. Manifest paths are
relative to the target workspace. The JSON format is:

```json
{
  "version": 1,
  "items": [
    {"id":"rules", "path":"RULES.md", "required":true},
    {"id":"handler", "path":"app.py", "symbol":"Service.handle", "required":true, "depends_on":["config"]},
    {"id":"config", "path":"config.py", "lines":[1,12]},
    {"id":"notes", "path":"notes.txt", "priority":1}
  ]
}
```

Each item needs a unique short ASCII `id` and a relative `path`. Use a full file,
`lines: [start,end]`, or `symbol: "definition"` / `"Class.method"`. Lines and
symbols are mutually exclusive. Symbol selection parses Python without running
it, retains decorators and complete bodies, and supports files up to 64 KiB.
An ambiguous/missing definition or out-of-range line is an error.

Optional `sha256` requires the whole file to match that digest. `required` is
boolean. `depends_on` lists item IDs. `priority` is finite, nonnegative and at
most 1000000. Unknown fields and duplicate JSON keys fail, catching misspelled
constraints. Limits: 128 items, 16 source files, 2 MiB of candidate excerpts.

Required items and their transitive dependencies must fit together. Otherwise
packing fails, reporting required bytes and emitting no partial success. Do not
drop requirements merely to make the command succeed. A refusal preserves the
contract but does not complete the task. Resolve it by narrowing the requested
scope with valid evidence, recovering separately, or choosing a sufficient
budget within the available model context.

Optional selection uses query relevance/priority and compares score-first with
cost-aware greedy plans. It is deterministic, not globally optimal. A dependency
cycle is included as a group or excluded as a group. Output order follows the
manifest. Budget is 512..65536 **UTF-8 bytes**, including all framing and handles,
not model tokens. Source content remains untrusted data, regardless of item ID.

The output catalog contains all item locations and immutable source handles,
including deferred items. `retrieve CATALOG_HASH` opens it. Then recover only
needed lines with `retrieve SOURCE_HASH --lines START:END`; this rejects an end
beyond the snapshot. Plain `read --lines` retains its older end-clamping behavior.

Before reusing a bundle after edits, `verify-pack CATALOG_HASH` checks source-file
hashes. Exit 3 means selected sources changed/missing; exit 0 means they match,
while separately listing changed deferred sources. Exit 2 means the check could
not be performed. This is an observation, not an atomic workspace snapshot.
Environment, undeclared dependencies and changes reverted before observation
are not covered. The full source hash is conservative: an edit outside selected
lines still makes an included source stale.

Dependency edges are supplied by the caller. Parsing a function does not discover
its imports, settings, fixtures or runtime dependencies. Source handles and a
fresh catalog do not prove semantic completeness or that an agent remembers it.
