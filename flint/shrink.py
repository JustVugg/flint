"""Conservative English prose compression with exact originals and explicit pins.

Inspired by Caveman's protected-segment prose compressor (Julius Brussee, 2026).
This is a changed Python implementation, not its JavaScript runtime. Attribution
and the upstream Apache-2.0 license are in vendor/caveman-shrink/. Unlike that
component, this preserves whole lines with lexical risk markers, never removes
hedges or intent leaders, and uses slices rather than injectable sentinels.
These lexical checks do not prove semantic equivalence.
"""

import json
import re

from .store import FlintError, MAX_FILE, digest

# Deliberately broad: sacrificing compression is preferable for these lines.
RISK = re.compile(
    r"\b(?:not|no|never|none|cannot|can|could|may|might|must|shall|should|will|"
    r"would|perhaps|maybe|potentially|probably|possibly|seems?|appears?|think|"
    r"if|unless|except|only|always|until|before|after|without|at least|at most|"
    r"minimum|maximum|exactly|just|sure|certainly|all|every|any|either|neither|"
    r"required|optional|warning|error|fail\w*|limit\w*)\b|n['’]t\b|\d|[<>=]",
    re.IGNORECASE,
)
ARTICLE = re.compile(r"(?<![\w-])(?:the|The|an|An|a)[ \t]+(?=[a-z])")
COURTESY = re.compile(r"^(?:Please[,!]?|please[,!]?|Kindly|kindly)[ \t]+")
# No rewriting inside quotations, inline code, paths, URLs or identifiers.
LITERAL = re.compile(
    r'`[^`\r\n]*`|"(?:\\.|[^"\\])*"|(?<!\w)\'(?:\\.|[^\'\\])*\'(?!\w)'
    r"|https?://\S+|\S*[/\\]\S*|\b\w*[_]\w*\b|\b\w+(?:\.\w+)+\b"
)
STRUCTURED = re.compile(r"^[ \t]*(?:[\[{]|[\"']?\w+[\"']?[ \t]*[:=]|[-+][ \t]|\|)")
FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})")


def _prose_line(line):
    """Edit only gaps; protected bytes never enter a replacement algorithm."""
    output, cursor = [], 0
    for match in LITERAL.finditer(line):
        gap = line[cursor:match.start()]
        if cursor == 0:
            gap = COURTESY.sub("", gap)
        output.extend((ARTICLE.sub("", gap), match.group()))
        cursor = match.end()
    gap = line[cursor:]
    if cursor == 0:
        gap = COURTESY.sub("", gap)
    output.append(ARTICLE.sub("", gap))
    return "".join(output)


def compress_prose(text, *, preserve=()):
    if not isinstance(text, str):
        raise FlintError("prose must be text")
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_FILE:
        raise FlintError("prose exceeds 512 KiB")
    if len(preserve) > 32 or any(not isinstance(p, str) or not p or len(p) > 256 or
                                any(c in p for c in "\r\n\0") for p in preserve):
        raise FlintError("at most 32 nonempty single-line pins of <=256 characters")
    if any(p not in text for p in preserve):
        raise FlintError("a requested preserve literal is absent from the source")
    # English-only rules must not be applied to arbitrary languages or binary
    # controls. Keep the whole input unchanged; do not attempt language guessing.
    if any(ord(c) > 127 or (ord(c) < 32 and c not in "\r\n\t") for c in text):
        return {"content": text, "changed_lines": 0, "protected_lines": 0,
                "reason": "non_ascii_or_control_passthrough"}
    output, changed, protected = [], 0, 0
    fence = None
    for line in text.splitlines(keepends=True):
        marker = FENCE.match(line)
        in_fence = fence is not None
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not line[marker.end():].strip():
                fence = None
        if (in_fence or marker or RISK.search(line) or STRUCTURED.match(line)
                or line.startswith(("    ", "\t")) or any(p in line for p in preserve)
                or line.count('"') % 2 or line.count("'") % 2 or '`' in line):
            output.append(line)
            protected += 1
            continue
        reduced = _prose_line(line)
        output.append(reduced)
        changed += reduced != line
    candidate = "".join(output)
    # Defense in depth: a pin is an exact fragment, not a fuzzy semantic match.
    if len(candidate.encode()) > len(encoded) or any(candidate.count(p) != text.count(p) for p in preserve):
        return {"content": text, "changed_lines": 0, "protected_lines": protected,
                "reason": "invariant_fallback"}
    return {"content": candidate, "changed_lines": changed, "protected_lines": protected,
            "reason": "compressed" if candidate != text else "unchanged"}


def shrink_file(store, path, *, prose=False, preserve=()):
    data = store.file_bytes(path)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise FlintError("shrink supports UTF-8 text only") from error
    result = compress_prose(text, preserve=preserve) if prose else {
        "content": text, "changed_lines": 0, "protected_lines": 0, "reason": "verbatim"}
    if preserve and not prose:
        raise FlintError("preserve is only used with explicit --prose")
    with store.db:
        store._save(data)
    result.update({"path": path, "original": digest(data), "input_bytes": len(data),
                   "output_bytes": len(result["content"].encode()),
                   "output_sha256": digest(result["content"].encode())})
    return result


def render_shrink(result, original, *, report=False):
    """Count all stdout/stderr bytes; default to original if framing costs more."""
    if digest(original) != result["original"]:
        raise FlintError("render source does not match the stored original")
    metadata = json.dumps({key: value for key, value in result.items() if key != "content"},
                          ensure_ascii=False) + "\n"
    if not report and len(result["content"].encode()) + len(metadata.encode()) >= len(original):
        return original.decode("utf-8"), ""
    return result["content"], metadata
