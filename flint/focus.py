"""Literal search with exact source excerpts, full snapshots and a byte budget."""

import json

from .store import FlintError, digest

MAX_FOCUS_FILES = 16
MAX_LINES = 20000
MAX_WINDOWS = 256


def _lines(text):
    pieces = text.split("\n")
    return [line + "\n" for line in pieces[:-1]] + ([pieces[-1]] if pieces[-1] else [])


def render_focus(result):
    text = f"FOCUS literal={json.dumps(result['query'], ensure_ascii=False)}\n"
    text += "EXCERPTS ONLY; context may be reduced. Recover full originals with retrieve HASH.\n"
    for source in result["sources"]:
        text += f"SOURCE {source['path']} sha256={source['handle']} bytes={source['file_bytes']}\n"
    for block in result["blocks"]:
        text += f"\n--- {block['path']}:{block['start']}:{block['end']} ---\n" + block["content"]
        if not block["content"].endswith("\n"):
            text += "\n"
    text += f"\nMATCHED_LINES={result['matched_lines']} INCLUDED={result['included_matches']} OMITTED={result['matched_lines'] - result['included_matches']}\n"
    return text


def focus(store, paths, query, *, budget=8192, context=2):
    if not paths or len(paths) > MAX_FOCUS_FILES or len(paths) != len(set(paths)):
        raise FlintError("focus requires 1..16 distinct explicit files")
    if not query or len(query) > 128 or any(c in query for c in "\r\n\0"):
        raise FlintError("literal query must contain 1..128 characters on one line")
    if not 512 <= budget <= 65536 or not 0 <= context <= 10:
        raise FlintError("budget must be 512..65536 bytes; context must be 0..10 lines")
    result = {"query": query, "sources": [], "blocks": [], "matched_lines": 0, "included_matches": 0}
    originals = []
    candidates = []
    for path in paths:
        data = store.file_bytes(path)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise FlintError("focus supports UTF-8 text only") from error
        lines = _lines(text)
        if len(lines) > MAX_LINES:
            raise FlintError("focus supports at most 20000 lines per file")
        matches = [i for i, line in enumerate(lines) if query in line]
        result["matched_lines"] += len(matches)
        result["sources"].append({"path": path, "handle": digest(data), "file_bytes": len(data)})
        originals.append(data)
        # Keep candidate metadata bounded, even for one matching line in every
        # other line. Matches beyond the candidate cap are counted as omitted.
        windows = []
        for index in matches:
            start, end = max(0, index - context), min(len(lines), index + context + 1)
            if windows and start <= windows[-1][1]:
                windows[-1][1] = max(windows[-1][1], end)
                windows[-1][2] += 1
            elif len(windows) + len(candidates) < MAX_WINDOWS:
                windows.append([start, end, 1])
        for start, end, count in windows:
            candidates.append({"path": path, "start": start + 1, "end": end,
                               "content": "".join(lines[start:end]), "matches": count})
    if len(render_focus(result).encode()) > budget:
        raise FlintError("budget cannot fit source references; increase it or select fewer files")
    fallback_attempts = 0
    for candidate in candidates:
        result["blocks"].append(candidate)
        result["included_matches"] += candidate["matches"]
        if len(render_focus(result).encode()) > budget:
            result["blocks"].pop()
            result["included_matches"] -= candidate["matches"]
            # A long merged run must not hide every matching line merely
            # because the whole run does not fit. Try exact matching lines,
            # merging adjacent ones. Never cut a source line or duplicate hits.
            for offset, line in enumerate(_lines(candidate["content"])):
                if query not in line or fallback_attempts >= MAX_WINDOWS:
                    continue
                fallback_attempts += 1
                number = candidate["start"] + offset
                previous = result["blocks"][-1] if result["blocks"] else None
                merge = previous and previous["path"] == candidate["path"] and previous["end"] + 1 == number
                block = {"path": candidate["path"], "start": number, "end": number,
                         "content": line, "matches": 1}
                if merge:
                    block.update(start=previous["start"], content=previous["content"] + line,
                                 matches=previous["matches"] + 1)
                    result["blocks"][-1] = block
                else:
                    result["blocks"].append(block)
                result["included_matches"] += 1
                if len(render_focus(result).encode()) > budget:
                    result["included_matches"] -= 1
                    if merge:
                        result["blocks"][-1] = previous
                    else:
                        result["blocks"].pop()
    # Storage failures roll back all snapshots and do not emit unusable handles.
    with store.db:
        for data in originals:
            store._save(data)
    result["output_bytes"] = len(render_focus(result).encode())
    return result
