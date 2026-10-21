"""Bounded workspace reads shared by stateful and stateless commands."""

import os
import stat
from .common import FlintError, MAX_FILE

DENIED = {".git", ".flint", ".aws", ".ssh", ".codex", ".claude"}


def clean_path(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > 1024 or "\\" in name:
        raise FlintError("use a short, relative POSIX path")
    try:
        name.encode("utf-8")
    except UnicodeEncodeError as error:
        raise FlintError("path must contain valid Unicode characters") from error
    if name.startswith("/") or any(c in name for c in ("\x00", "\n", "\r")):
        raise FlintError("invalid relative path")
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise FlintError("path traversal or empty path component")
    for p in parts:
        folded = p.casefold()
        if folded in DENIED or folded == ".env" or folded.startswith(".env.") or folded.endswith((".pem", ".key")):
            raise FlintError("private or internal path refused")
    return name


def file_bytes(root, name: str) -> bytes:
    """Read regular files without following symlinks in any component."""
    name = clean_path(name)
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    directory = os.open(root, flags | os.O_DIRECTORY)
    try:
        parts = name.split("/")
        for part in parts[:-1]:
            child = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(parts[-1], flags | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE:
                raise FlintError("only regular files up to 512 KiB are supported")
            data = stream.read(MAX_FILE + 1)
            after = os.fstat(stream.fileno())
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if len(data) > MAX_FILE or any(getattr(before, f) != getattr(after, f) for f in fields):
            raise FlintError("file changed during read or exceeds limit; retry")
        return data
    except OSError as error:
        raise FlintError(f"cannot safely read {name}: {error.strerror}") from error
    finally:
        os.close(directory)

