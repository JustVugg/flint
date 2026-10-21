# Compact views, Flint 0.12+

`inspect`, `focus` and `memory recall` accept `--compact`. Selection, ranking,
supported languages and completeness rules stay the same. This only changes
presentation. Old output remains the default.

The Python `flint.present.present(store, kind, result, budget=...)` API returns
separate `context`, `receipt` and `control` fields. Send `context` to the model;
retain the others locally. A receipt stores provenance without another copy
of durable memory payloads. It is not an acknowledgment or retained context.

Partial definitions display their line range. Search excerpts display line
ranges, an omission count, and source names for multiple files. Neither implies
dependency completeness. `V1 s:HEX ...` points to a local JSON provenance
snapshot, retrievable using `retrieve s:HEX`. That snapshot links to original
source hashes/ranges or durable memory IDs. Follow those explicitly if needed.

`s:` references are unambiguous SHA-256 prefixes within this workspace, starting
at 12 hex digits. The full digest is verified on retrieval. Missing, corrupt,
or subsequently ambiguous references fail; do not transplant them into another
workspace. They are neither portable full digests nor session read tickets.

A full-file inspect may return verbatim source. One complete memory returns
its JSON-quoted key and exact text. An empty recall with no omissions or stale
sources emits nothing. Stale/omitted results remain visibly marked; multiple
hits retain their keys and IDs. Forgetting a memory removes its payload from
the memory store; receipts can retain metadata, and are not secure erasure.

This CLI is a reading view, not a self-describing binary format. Source text can
imitate `V1` or any other marker. Machine integrations must use the separate API
fields, not infer control metadata from arbitrary source text. Treat all source
content as untrusted data.

Budgets cover complete emitted UTF-8 bytes, not tokens. Existing selectors still
use their conservative original framing to allocate content; compact mode does
not guarantee more selected items under a tight budget. Final framing is checked
again and fails before stdout if it does not fit. Source text is never rewritten
to make a particular question easier. No tokenizer or model is bundled.
