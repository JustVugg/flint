#!/usr/bin/env python3
"""Install the bundled Flint CLI; no compiler, pip or network required."""
import hashlib
import json
import os
from pathlib import Path
import sys


def main():
    if sys.version_info < (3, 11):
        raise SystemExit('Flint requires Python 3.11 or newer.')
    root = Path(__file__).resolve().parent
    package = root / 'dist/flint.pyz'
    try:
        manifest = json.loads((root / 'dist/manifest.json').read_text())
        if package.stat().st_size > 4 * 1024 * 1024:
            raise ValueError('oversized package')
        if hashlib.sha256(package.read_bytes()).hexdigest() != manifest['artifact_sha256']:
            raise ValueError('package checksum mismatch')
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit(f'Flint package is missing or damaged: {error}') from error
    os.execv(sys.executable, [sys.executable, '-S', str(package), 'install', *sys.argv[1:]])


if __name__ == '__main__':
    main()
