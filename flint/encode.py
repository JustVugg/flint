"""Stateless, complete views for callers that do not need original whitespace.

JSON values, number lexemes and row order survive; duplicate keys use lexical
minification. Selection minimizes UTF-8 bytes, NOT model tokens. Formats are
explicit at decode time: literal JSON might itself resemble a Flint envelope.
"""

from .common import FlintError
from .compact import compact_text, minify_json, checked_text, decode_lines


def _choose(candidates, cost):
    def score(item):
        size = len(item[0].encode())
        value = size if cost is None else cost(item[0])
        if type(value) is not int or value < 0:
            raise FlintError('cost function must return a nonnegative integer')
        return value, size
    unique = list({item[0]:item for item in reversed(candidates)}.values())
    return min(unique, key=score)


def encode_text(text, *, format='auto', cost=None):
    """Select by bytes, or by a caller-supplied deterministic nonnegative cost.

    A model tokenizer can supply ``cost=lambda s: len(tokenizer.encode(s))``.
    No model or tokenizer is loaded here. The guarantee is only relative to
    this invocation's generated candidates under that supplied cost function.
    It does not claim superiority over arbitrary codecs or downstream agents.
    """
    checked_text(text)
    if format == 'code':
        from .codebook import encode_code
        candidate = encode_code(text)
        return _choose([(text, 'verbatim'), (candidate, 'code-table' if candidate != text else 'verbatim')], cost)
    if format == 'toon':
        from .toon import encode_toon
        try:
            return encode_toon(text), 'toon'
        except (ValueError, RecursionError, UnicodeError) as error:
            raise FlintError('input outside supported TOON subset') from error
    if format == 'copy':
        from .copy import encode_copy
        candidate = encode_copy(text)
        return candidate, 'copy' if candidate != text else 'verbatim'
    if format != 'auto':
        return compact_text(text, format=format)
    candidates = [(text, 'verbatim')]
    try:
        minimal = minify_json(text)
    except FlintError:
        candidates.append(compact_text(text, format='lines'))
        # Experimental code tables and byte COPY require explicit selection.
        # A byte saving alone is not evidence of a better model-facing view.
    else:
        candidates.append((minimal, 'json-whitespace'))
        # Tiny JSON cannot amortize the explicit column decoding instructions.
        if len(minimal.encode()) > 256:
            from .columns import encode_columns
            candidates.append(encode_columns(text))
        try:
            from .toon import encode_toon
            candidates.append((encode_toon(text), 'toon'))
        except (ValueError, RecursionError, UnicodeError):
            pass
    return _choose(candidates, cost)


def decode_text(text, *, format):
    checked_text(text)
    if format == 'verbatim':
        return text
    if format == 'json-whitespace':
        return minify_json(text)
    if format == 'line-templates':
        return decode_lines(text)
    if format == 'copy':
        from .copy import decode_copy
        return decode_copy(text)
    if format == 'code-table':
        from .codebook import decode_code
        return decode_code(text)
    if format == 'json-columns':
        from .columns import decode_columns
        return decode_columns(text)
    if format == 'toon':
        from .toon import decode_toon
        return decode_toon(text)
    raise FlintError('unknown encoded format')
