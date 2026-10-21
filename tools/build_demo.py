"""Render a small terminal GIF from real Flint CLI calls, without network access."""
import json
import os
from pathlib import Path
import re
import resource
import shutil
import subprocess
import sys
import tempfile
import textwrap

ROOT = Path(__file__).resolve().parents[1]


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (128 * 1024**2, 128 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (5, 6))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def main():
    resource.setrlimit(resource.RLIMIT_AS, (128 * 1024**2, 128 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (30, 35))
    os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:1])
    from PIL import Image, ImageDraw, ImageFont

    out = ROOT / 'assets/demo'
    out.mkdir(exist_ok=True)
    records = []
    scenes = []
    with tempfile.TemporaryDirectory(prefix='flint-demo-', dir='/tmp') as tmp:
        base = Path(tmp)
        (base / 'examples').mkdir()
        for name in ('hello.py', 'decisions.txt', 'records.json'):
            shutil.copyfile(ROOT / 'examples' / name, base / 'examples' / name)
        shutil.copyfile(ROOT / 'dist/flint.pyz', base / 'flint.pyz')

        def run(args, stdin=''):
            p = subprocess.run([sys.executable, '-S', str(base / 'flint.pyz'),
                                '--workspace', str(base), *args], input=stdin,
                               text=True, capture_output=True, timeout=8,
                               cwd=base, preexec_fn=limits,
                               env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
            record = {'args': args, 'stdin': stdin, 'stdout': p.stdout,
                      'stderr': p.stderr, 'exit_code': p.returncode}
            records.append(record)
            if p.returncode:
                raise RuntimeError(record)
            return p

        encoded = run(['encode', 'examples/records.json', '--format', 'columns', '--report'])
        meta = json.loads(encoded.stderr)
        (base / 'packed.txt').write_text(encoded.stdout)
        original = (base / 'examples/records.json').read_bytes()
        restored = run(['decode', 'packed.txt', '--format', meta['format']])
        data = json.loads(original)
        assert json.loads(restored.stdout) == data
        payload_bytes = len(encoded.stdout.encode())
        report_bytes = len(encoded.stderr.encode())
        total = payload_bytes + report_bytes
        saving = 100 * (1 - total / len(original))
        metrics = {'input_bytes': len(original), 'encoded_payload_bytes': payload_bytes,
                   'format_report_bytes': report_bytes, 'total_output_bytes': total,
                   'reduction_including_report_percent': round(saving, 2),
                   'records_restored': len(data), 'json_values_preserved': True,
                   'input_already_minified': True,
                   'scope': 'Illustrative JSON example; bytes, not model tokens or general savings.'}
        scenes.append(('01 / COMPRESS CONTEXT', 'Same JSON data. Less repeated structure.', [
            ('hint', f'Input: {len(data)} records, already minified, {len(original):,} bytes'),
            ('command', '$ flint encode examples/records.json --format columns --report'),
            ('output', encoded.stdout),
            ('success', f'{len(original):,} B -> {total:,} B including the format report'),
            ('success', f'{saving:.1f}% fewer bytes for this example'),
        ]))
        scenes.append(('02 / RESTORE INFORMATION', 'Compact representation. Recoverable data.', [
            ('command', '$ flint decode packed.txt --format json-columns'),
            ('hint', 'Decoded preview (first record):'),
            ('output', json.dumps(data[0], indent=2)),
            ('success', f'All {len(data)} records restored; every JSON value matches.'),
        ]))
        opened = run(['work', 'open', 'examples/hello.py'])
        ticket = re.match(r'W1 (e\d+) source', opened.stdout).group(1)
        scenes.append(('03 / READ THE RIGHT SOURCE', 'Exact code with a revision ticket.', [
            ('command', '$ flint work open examples/hello.py'),
            ('output', opened.stdout),
            ('hint', 'The ticket binds the edit to the version just read.'),
        ]))
        plan = {'op': 'edit', 'ref': ticket, 'edits': [[2, 1, '    return f"Hi, {name}!"\n']]}
        edited = run(['work', 'call'], json.dumps(plan))
        new_ticket = re.match(r'W1 (e\d+) applied', edited.stdout).group(1)
        shown = run(['work', 'show', new_ticket])
        scenes.append(('04 / SEND ONLY THE CHANGE', 'Keep the original. Edit the checked version.', [
            ('command', '$ flint work call'),
            ('hint', 'JSON input:'), ('output', json.dumps(plan)),
            ('output', edited.stdout),
            ('command', f'$ flint work show {new_ticket}'), ('output', shown.stdout),
        ]))
        remembered = run(['memory', 'remember', 'examples/decisions.txt', '--key', 'project-decisions'])
        recalled = run(['memory', 'recall', 'project decisions', '--budget', '4096', '--compact'])
        assert (base / 'examples/decisions.txt').read_text() in recalled.stdout
        scenes.append(('05 / KEEP DECISIONS', 'Persistent memory for the next context.', [
            ('command', '$ flint memory remember examples/decisions.txt --key project-decisions'),
            ('output', remembered.stdout),
            ('command', "$ flint memory recall 'project decisions' --compact"),
            ('output', recalled.stdout),
        ]))

    mono = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf', 16)
    normal = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 20)
    title = ImageFont.truetype(str(ROOT / 'assets/fonts/Micro5-Regular.ttf'), 54)
    small = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 13)
    colors = {'command': '#a5c9ff', 'output': '#e6e8ed', 'hint': '#929aa9', 'success': '#91e5bb'}
    frames, durations = [], []
    for section, subtitle, blocks in scenes:
        lines = []
        for kind, content in blocks:
            for line in content.rstrip('\n').splitlines():
                lines.extend((kind, s) for s in (textwrap.wrap(line, width=83,
                    replace_whitespace=False, drop_whitespace=False) or ['']))
            lines.append(('hint', ''))
        if len(lines) > 19:
            raise RuntimeError(f'Demo scene does not fit: {section}: {len(lines)} lines')
        # Two compact frames per scene: command appears, then its real result.
        for partial, duration in ((True, 500), (False, 4300)):
            im = Image.new('RGB', (960, 650), '#101318')
            draw = ImageDraw.Draw(im)
            draw.text((28, 11), 'flint', font=title, fill='#ffffff')
            draw.text((175, 27), section, font=small, fill='#91e5bb')
            draw.text((28, 78), subtitle, font=normal, fill='#ffffff')
            draw.rounded_rectangle((20, 122, 940, 603), radius=12, fill='#181e27', outline='#2d3541')
            y = 140
            displayed = lines[:2] if partial else lines
            for kind, line in displayed:
                draw.text((36, y), line, font=mono, fill=colors[kind])
                y += 23
            draw.text((28, 620), 'Real CLI output  |  flint = python3 -S dist/flint.pyz  |  bytes are not token counts',
                      font=small, fill='#929aa9')
            frames.append(im.convert('P', palette=Image.Palette.ADAPTIVE, colors=48))
            durations.append(duration)
    frames[0].save(out / 'flint-demo.gif', save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, optimize=False, disposal=2)
    frames[1].convert('RGB').save(out / 'flint-demo.png')
    (out / 'recording.json').write_text(json.dumps({'metrics': metrics, 'calls': records}, indent=2) + '\n')
    print(json.dumps({'metrics': metrics, 'gif_bytes': (out / 'flint-demo.gif').stat().st_size,
                      'frames': len(frames)}, indent=2))


if __name__ == '__main__':
    main()
