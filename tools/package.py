"""Build a local, reproducible stdlib-only zipapp; no dependencies or network."""

import hashlib
import json
import importlib.util
import marshal
from pathlib import Path
import struct
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def main():
    destination=ROOT/'dist';destination.mkdir(exist_ok=True)
    sources={p.relative_to(ROOT).as_posix():p.read_bytes() for p in sorted((ROOT/'flint').glob('*.py'))}
    hashes={name:hashlib.sha256(data).hexdigest() for name,data in sources.items()}
    sources['__main__.py']=b'from flint.__main__ import main\nraise SystemExit(main())\n'
    # Matching interpreters may load compiled code directly; other Python minor
    # versions fall back to the included source. Hash-based pyc, no host paths.
    for name,data in list(sources.items()):
        code=compile(data,name,'exec',dont_inherit=True)
        header=importlib.util.MAGIC_NUMBER+struct.pack('<I',1)+importlib.util.source_hash(data)
        sources[name+'c']=header+marshal.dumps(code)
    for component in ('caveman-toon', 'caveman-shrink'):
        for name in ('LICENSE', 'NOTICE', 'UPSTREAM-NOTICE', 'provenance.json'):
            path = ROOT / 'vendor' / component / name
            if path.is_file():
                sources['licenses/' + component + '/' + name] = path.read_bytes()
    for name in ('LICENSE', 'NOTICE'):
        sources[name] = (ROOT / name).read_bytes()
    with zipfile.ZipFile(destination/'flint.pyz','w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for name,data in sorted(sources.items()):
            info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,data)
    with zipfile.ZipFile(destination/'flint.pyz') as archive:
        if archive.testzip() is not None:
            raise RuntimeError('invalid package')
    manifest={'source_sha256':hashes,'artifact_sha256':hashlib.sha256((destination/'flint.pyz').read_bytes()).hexdigest(),
              'artifact_bytes':(destination/'flint.pyz').stat().st_size,'python_required':'3.11+',
              'license':'Apache-2.0',
              'license_sha256':{name:hashlib.sha256(sources[name]).hexdigest() for name in ('LICENSE', 'NOTICE')},
              'method':'source zipapp with standard-library imports; no embedded interpreter or external dependencies'}
    (destination/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))


if __name__=='__main__': main()
