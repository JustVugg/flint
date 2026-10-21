"""Shared contracts without storage or command-line initialization."""

import hashlib

MAX_FILE = 512 * 1024


class FlintError(Exception):
    """A refused operation; no compact answer should replace this error."""


def digest(data):
    return hashlib.sha256(data).hexdigest()
