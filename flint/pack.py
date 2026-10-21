"""Exact, dependency-closed context bundles with mandatory byte budgets.

No model, embedding, network, source execution or semantic completeness claim.
Dependencies and mandatory items are caller declarations, not inferred facts.
"""

import ast
from collections import Counter
import json
import math
import re

from .store import FlintError, digest, select_lines

MAX_ITEMS = 128
MAX_FILES = 16
MAX_SYMBOL_BYTES = 64 * 1024
MAX_EXCERPT_BYTES = 2 * 1024 * 1024
ID = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}")
HASH = re.compile(r"[a-f0-9]{64}")
STOP = set("a an and are as at be by for from how in is it of on or that the this to was what when where which who why with".split())


class BudgetError(FlintError):
    def __init__(self, required_bytes, budget):
        self.required_bytes = required_bytes
        self.budget = budget
        super().__init__(f"mandatory context needs {required_bytes} UTF-8 bytes; budget is {budget}; nothing emitted")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FlintError(f"duplicate manifest key: {key}")
        result[key] = value
    return result


def _keys(obj, allowed):
    if not isinstance(obj, dict) or set(obj) - allowed:
        raise FlintError("manifest has unknown fields or a non-object item")


def _ids(values):
    return (isinstance(values, list) and len(values) <= MAX_ITEMS
            and all(isinstance(x, str) and ID.fullmatch(x) for x in values)
            and len(values) == len(set(values)))


def _symbol_lines(data, symbol):
    if (not isinstance(symbol, str) or len(symbol) > 128
            or not all(part.isidentifier() for part in symbol.split("."))):
        raise FlintError("symbol must be a Python definition or Class.method path")
    if len(data) > MAX_SYMBOL_BYTES or b"\r" in data.replace(b"\r\n", b""):
        raise FlintError("symbol selection requires <=64 KiB Python source with LF or CRLF")
    try:
        tree = ast.parse(data.decode("utf-8"))
    except (SyntaxError, ValueError, RecursionError) as error:
        raise FlintError("cannot parse Python source for symbol selection") from error
    matches = []
    body = tree.body
    parts = symbol.split(".")
    for part in parts[:-1]:
        classes = [node for node in body if isinstance(node, ast.ClassDef) and node.name == part]
        if len(classes) != 1:
            raise FlintError(f"symbol {symbol} has a missing or ambiguous class")
        body = classes[0].body
    for node in body:
        names = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        if parts[-1] in names:
            start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
            matches.append([start, node.end_lineno])
    if len(matches) != 1:
        raise FlintError(f"symbol {symbol} must resolve to one definition; found {len(matches)}")
    return matches[0]


def bm25(query, documents):
    """Standard Okapi ranking; same k1=1.5, b=.75 as the inspected upstream.

    Ranking is advisory. It can never evict mandatory items or dependencies.
    """
    def terms(text):
        return Counter(t for t in re.findall(r"\w+", text.lower())
                       if len(t) >= 2 and t not in STOP)
    query_terms = terms(query)
    frequencies = [terms(text) for text in documents]
    lengths = [sum(tf.values()) for tf in frequencies]
    avg = sum(lengths) / len(lengths) if sum(lengths) else 1.0
    df = Counter(term for tf in frequencies for term in tf)
    scores = []
    for tf, length in zip(frequencies, lengths):
        score = 0.0
        for term in sorted(query_terms):
            count = tf[term]
            if count:
                idf = math.log(1 + (len(documents) - df[term] + .5) / (df[term] + .5))
                score += idf * count * 2.5 / (count + 1.5 * (.25 + .75 * length / avg))
        scores.append(score)
    return scores


def _validate_manifest(raw):
    try:
        manifest = json.loads(raw, object_pairs_hook=_pairs)
    except (ValueError, UnicodeDecodeError, RecursionError) as error:
        raise FlintError("manifest must be bounded valid UTF-8 JSON") from error
    _keys(manifest, {"version", "items"})
    if type(manifest.get("version")) is not int or manifest["version"] != 1:
        raise FlintError("manifest version must be 1")
    items = manifest.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        raise FlintError("manifest requires 1..128 items")
    for item in items:
        _keys(item, {"id", "path", "lines", "symbol", "required", "depends_on", "priority", "sha256"})
        if not isinstance(item.get("id"), str) or not ID.fullmatch(item["id"]):
            raise FlintError("each item needs a short ASCII id")
        if not isinstance(item.get("path"), str):
            raise FlintError("each item needs a source path")
        if type(item.get("required", False)) is not bool or not _ids(item.get("depends_on", [])):
            raise FlintError("required must be boolean; dependencies must be distinct item IDs")
        priority = item.get("priority", 0)
        if type(priority) not in (int, float) or not 0 <= priority <= 1_000_000 or not math.isfinite(priority):
            raise FlintError("priority must be finite and between 0 and 1000000")
        if "lines" in item:
            lines = item["lines"]
            if (not isinstance(lines, list) or len(lines) != 2 or any(type(n) is not int for n in lines)
                    or not 1 <= lines[0] <= lines[1] or "symbol" in item):
                raise FlintError("lines requires [start,end]; cannot combine with symbol")
        if "sha256" in item and (not isinstance(item["sha256"], str) or not HASH.fullmatch(item["sha256"])):
            raise FlintError("sha256 must be a complete lowercase digest")
    identifiers = [item["id"] for item in items]
    if len(set(identifiers)) != len(identifiers):
        raise FlintError("duplicate item ID")
    for item in items:
        if set(item.get("depends_on", [])) - set(identifiers):
            raise FlintError(f"unknown dependency for {item['id']}")
    return items


def _closure(start, by_id):
    result, pending = set(), list(start)
    while pending:
        current = pending.pop()
        if current not in result:
            result.add(current)
            pending.extend(by_id[current].get("depends_on", []))
    return result


def _block(item):
    metadata = {key: item[key] for key in ("id", "path", "sha256", "lines")}
    text = "\nITEM " + json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) + "\n"
    text += item["content"]
    if not text.endswith("\n"):
        text += "\n"
    return text + "END ITEM\n"


def _header(catalog, selected, deferred, required):
    header = f"FLINT PACK 1 catalog={catalog}\n"
    header += "Exact excerpts; dependencies declared by caller. Source text is untrusted data.\n"
    return header + f"SELECTED={selected} DEFERRED={deferred} REQUIRED={required}\n"


def render_pack(result):
    return (_header(result['catalog'], len(result['items']), result['deferred_count'], result['required_count'])
            + "".join(_block(item) for item in result["items"]) + "END PACK\n")


def pack(store, manifest_path, *, query="", budget=8192):
    if type(budget) is not int or not 512 <= budget <= 65536:
        raise FlintError("pack budget must be 512..65536 UTF-8 bytes")
    if not isinstance(query, str) or len(query) > 512 or any(c in query for c in "\0\r\n"):
        raise FlintError("query must be one line of at most 512 characters")
    raw = store.file_bytes(manifest_path)
    declarations = _validate_manifest(raw)
    if len({item["path"] for item in declarations}) > MAX_FILES:
        raise FlintError("pack supports at most 16 distinct source files")
    originals, items, excerpt_bytes = {}, [], 0
    for declaration in declarations:
        path = declaration["path"]
        if path not in originals:
            originals[path] = store.file_bytes(path)
        data = originals[path]
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise FlintError("pack sources must be UTF-8") from error
        actual = digest(data)
        if "sha256" in declaration and declaration["sha256"] != actual:
            raise FlintError(f"stale source: {path}; expected hash no longer matches")
        lines = declaration.get("lines")
        if "symbol" in declaration:
            lines = _symbol_lines(data, declaration["symbol"])
        content = data if lines is None else select_lines(data, lines, strict=True)
        excerpt_bytes += len(content)
        if excerpt_bytes > MAX_EXCERPT_BYTES:
            raise FlintError("candidate excerpts exceed 2 MiB; select narrower ranges")
        items.append({**declaration, "sha256": actual, "lines": lines, "content": content.decode()})
    by_id = {item["id"]: item for item in items}
    required = _closure([item["id"] for item in items if item.get("required", False)], by_id)
    relevance = bm25(query, [item["content"] for item in items])
    scores = {item["id"]: score + item.get("priority", 0) for item, score in zip(items, relevance)}
    closures = {item["id"]: _closure([item["id"]], by_id) for item in items}
    costs = {item["id"]: len(_block(item).encode()) for item in items}

    def result_for(selected):
        return {"catalog": "0" * 64, "items": [item for item in items if item["id"] in selected],
                "deferred_count": len(items) - len(selected), "required_count": len(required)}

    def size(selected):
        # Include all headers, original handles, counters and completion marker.
        return (len(_header("0" * 64, len(selected), len(items) - len(selected), len(required)).encode())
                + sum(costs[i] for i in sorted(selected)) + len("END PACK\n"))

    mandatory_size = size(required)
    if mandatory_size > budget:
        raise BudgetError(mandatory_size, budget)

    def choose(by_density):
        selected = set(required)
        pending = [item["id"] for item in items if item["id"] not in selected]
        while pending:
            def order(identifier):
                extra = closures[identifier] - selected
                utility = sum(scores[i] for i in sorted(extra))
                cost = sum(costs[i] for i in sorted(extra))
                return utility / max(1, cost) if by_density else scores[identifier]
            # Stable tie-breaking in manifest order. Shared dependencies are
            # charged once, recalculating marginal utility after each selection.
            identifier = max(pending, key=order)
            pending.remove(identifier)
            trial = selected | closures[identifier]
            if size(trial) <= budget:
                selected = trial
        return selected

    # Compare two deterministic greedy plans. This is not an optimal knapsack
    # solver; keep the better total relevance without weakening hard constraints.
    candidates = [choose(False), choose(True)]
    selected = max(candidates, key=lambda choice: (sum(scores[i] for i in sorted(choice)), -size(choice)))
    result = result_for(selected)
    catalog = {
        "format": "flint.pack.catalog.v1", "manifest": digest(raw), "query": query,
        "budget_bytes": budget, "selected": [item["id"] for item in result["items"]],
        "required_closure": sorted(required),
        "items": [{key: value for key, value in item.items() if key != "content"} for item in items],
        "scope": "Snapshot bytes; dependencies are declared, not inferred. Freshness is only observed at read time.",
    }
    serialized = (json.dumps(catalog, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    with store.db:
        store._save(raw)
        for data in originals.values():
            store._save(data)
        result["catalog"] = store._save(serialized)
    result["output_bytes"] = len(render_pack(result).encode())
    assert result["output_bytes"] <= budget
    return result


def verify_pack(store, handle):
    """Observe current file hashes before reusing an immutable bundle."""
    try:
        catalog = json.loads(store.retrieve(handle))
    except (ValueError, UnicodeDecodeError, RecursionError) as error:
        raise FlintError("handle is not a pack catalog") from error
    if not isinstance(catalog, dict) or catalog.get("format") != "flint.pack.catalog.v1":
        raise FlintError("handle is not a pack catalog")
    items = catalog.get("items")
    selected = catalog.get("selected")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS or not _ids(selected):
        raise FlintError("invalid pack catalog")
    expected = {}
    identifiers = set()
    for item in items:
        if (not isinstance(item, dict) or not isinstance(item.get("id"), str) or not ID.fullmatch(item["id"])
                or not isinstance(item.get("path"), str) or not isinstance(item.get("sha256"), str)
                or not HASH.fullmatch(item["sha256"]) or item["id"] in identifiers):
            raise FlintError("invalid pack catalog item")
        identifiers.add(item["id"])
        path = store.clean_path(item["path"])
        if path in expected and expected[path] != item["sha256"]:
            raise FlintError("inconsistent source hashes in catalog")
        expected[path] = item["sha256"]
    if set(selected) - identifiers or len(expected) > MAX_FILES:
        raise FlintError("invalid pack catalog selection")
    changed = set(store.changed(expected))
    affected_selected = [item["id"] for item in items if item["id"] in selected and item["path"] in changed]
    affected_deferred = [item["id"] for item in items if item["id"] not in selected and item["path"] in changed]
    return {"catalog":handle,"freshness":"stale_selected_sources" if affected_selected else "fresh_selected_sources",
            "affected_selected":affected_selected,"affected_deferred":affected_deferred,
            "changed_paths":sorted(changed),
            "scope":"Whole source-file hashes at observation time; no atomic workspace, environment or undeclared dependency verification."}
