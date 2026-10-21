# One source, one receipt

`inspect FILE --editable` reads the entire UTF-8 source and returns a source
reference suitable for `edit`. It does not parse or execute the file.
`inspect FILE A B --editable` selects complete Python definitions; up to 32 names,
the same <=64 KiB / 12000 AST-node limits as single-symbol inspection.
All names must resolve uniquely, including owning classes. Dependencies are
not inferred. Overlap and adjacent selections merge in source order.

With zero or multiple names, E2 (editable) or I2 (plain/compact) uses:

```text
E2 s:SOURCE "file.py" scope=selection omitted=17
@4:8 bytes=57
... exactly 57 UTF-8 bytes of original source ...
```

Each block starts with its original inclusive LF-line range and byte length,
followed by that exact payload and one framing newline. Do not interpret payload
text that resembles headers as control. Missing ranges are not adjacent code.
`scope=full omitted=0` means the entire file is included. An empty file has
range `1:0` and zero payload bytes.

Full source is used when its complete wire representation is no larger than the
selection. This minimizes emitted bytes between those two choices, not tokens.
Budget includes all framing; refusal emits no partial output. No read ticket
or acknowledged model-memory state is created. I2/E2 references preserve the
whole original, including omitted lines; `retrieve` may recover an older file.
Single-symbol E1 and V1 retain their existing behavior.
