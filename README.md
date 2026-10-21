<img src="assets/brand/flint-horizontal.png" width="760" alt="Flint">

**English** | [Italiano](README.it.md)

**Compact context. Keep the information.**

Flint gives AI agents tools to compress code, JSON and text, keep useful context,
remember decisions and recover original content. Focused reads and edits tied to
file versions turn that context into changes the agent can apply safely.

It works at the agent-tool layer: connect the CLI or JSON interface to your
workflow, independently of your model or provider. **Python 3.11+ · Linux / WSL ·
standard library core · no model or server required.**

**[Install Flint](#install) · [Download the ready-to-run file](https://github.com/JustVugg/flint/raw/refs/heads/main/dist/flint.pyz)**

## See it work

![Flint compresses JSON, restores its data, edits code and recalls a decision](assets/demo/flint-demo.gif)

The GIF runs real commands on the [included examples](examples/). It shows:

1. **Compress context:** 12 JSON records, already minified, go from **1,226 to
   499 bytes**, including the format report — **59.3% fewer bytes in this example**.
2. **Restore the information:** all 12 records and every JSON value are recovered.
3. **Read and edit code:** open the exact source, then send just the changed line.
4. **Carry decisions forward:** save a decision and recall it in another call.

These are byte counts for this example, not model token counts or a general
savings guarantee. [Captured commands and output](assets/demo/recording.json).
In the GIF, `flint` abbreviates `python3 -S dist/flint.pyz`.

## What Flint does

| Capability | How it helps an agent |
|---|---|
| Reversible compression | Represent repeated code, JSON and text more compactly, with explicit decoding. |
| Exact recovery | Retrieve stored originals and code ranges when the full content is needed. |
| Focused context | Read complete Python definitions, line ranges or text matches. |
| Context packing | Assemble selected sources and declared dependencies within a byte budget. |
| Persistent memory | Store decisions, search them and preserve previous versions. |
| Source freshness | Exclude memories linked to changed or missing source files. |
| Revision-bound edits | Apply a change only to the file version the agent read. |
| Incremental rereads | Return changes against a complete, acknowledged source version. |

Reversible JSON codecs preserve data values; code/text codecs can reconstruct
exact content. Source views select what to show, while stored originals remain
recoverable. These are different operations: a selected excerpt is not the
entire file. Optional prose rewriting is separate from reversible encoding.

## Install

**Python 3.11+ on Linux or WSL. No build, pip packages or model downloads.**

[Download the ZIP](https://github.com/JustVugg/flint/archive/refs/heads/main.zip),
extract it, and run this from the extracted folder:

```bash
python3 install.py
```

Choose Codex, Claude Code, both, or CLI only. The installer copies the ready-made
package and configures the selected agents' user skills. It preserves existing
settings and refuses to replace unrelated commands or edited skills.

Prefer one file? [Download flint.pyz](https://github.com/JustVugg/flint/raw/refs/heads/main/dist/flint.pyz)
and run `python3 flint.pyz install`. The skill and licenses are included.

The command is installed to `~/.local/bin/flint`. If that directory is not on
your PATH, the installer prints the exact `export PATH=...` line to run and add
to your shell profile. Agent skills include their own launcher and do not depend
on that PATH change. Restart your agent to discover the skill, then ask it to
use Flint for your project.

```bash
flint doctor
flint --workspace /path/to/project work open src/example.py
```

`doctor` checks the installation, skill files, compression, recovery, editing
and memory in a temporary workspace. It makes no model requests and does not
change your project. Skill installation makes Flint available to the agent;
it does not automatically intercept every tool call or change your model.

### Configure, update or remove

```bash
# Select an agent without the interactive menu.
python3 install.py --agent codex

# Add a skill for just one project; use --agent claude for Claude Code.
flint setup --agent codex --project /path/to/project

# Another agent that supports SKILL.md: choose its skills directory.
flint setup --skill-dir /path/to/agent/skills

# Inspect changes or diagnose an installation.
python3 install.py --agent both --dry-run
flint doctor --json

# Remove Flint and its unchanged skills; preserve project files and memories.
flint uninstall --yes
```

Update by downloading the new ZIP or `.pyz` and running the installer again.
Registered skills update with it. Flint refuses to overwrite edited managed
files: save your edits separately and restore the original before retrying.
`--prefix /path` selects a
different installation directory. User-scope setup respects `CODEX_HOME` and
`CLAUDE_CONFIG_DIR`; project-scope setup uses `.agents/skills` for Codex and
`.claude/skills` for Claude Code. Custom skill directories are also supported.

To try Flint without installing anything, from this repository run:

```bash
python3 -S dist/flint.pyz --workspace . work open examples/hello.py
```

The examples below use the portable package; installed users can replace
`python3 -S dist/flint.pyz` with `flint`.

### Compress and restore JSON

```bash
python3 -S dist/flint.pyz encode examples/records.json --format columns --report > packed.txt 2> packed.meta.json
python3 -S dist/flint.pyz decode packed.txt --format json-columns
```

This example uses `json-columns`: recurring fields, constant values and numeric
sequences share a compact representation. For other inputs, use the format
reported in `packed.meta.json` when decoding. `encode --format auto` selects
among supported candidates by bytes. Code/text COPY and template formats are
explicit options; [codec documentation](integrations/skills/flint-context/references/advanced-context.md).

### Edit only what changed

```bash
python3 -S dist/flint.pyz --workspace . work open examples/hello.py
```

The result includes a ticket, such as `W1 e1 source`. Save this plan as
`edit.json` to replace the second line of the example:

```json
{"edits":[[2,1,"    return f'Hi, {name}!'\n"]]}
```

```bash
python3 -S dist/flint.pyz --workspace . work edit e1 edit.json
```

Use the ticket returned by your own call; `e1` is an example. Flint refuses the
write if the source has changed. Successful edits return a new ticket, and
`work show TICKET` retrieves its content. A Python proposal with invalid syntax
can remain available for correction while the original file stays intact.

For agents sending JSON:

```bash
printf '%s\n' '{"op":"open","path":"examples/hello.py"}' |
  python3 -S dist/flint.pyz --workspace . work call
```

[JSON interface and revision tickets](integrations/skills/flint-context/references/work-tickets.md).

### Remember a decision

```bash
python3 -S dist/flint.pyz --workspace . memory remember examples/decisions.txt --key project-decisions
python3 -S dist/flint.pyz --workspace . memory recall 'project decisions' --budget 8192 --compact
python3 -S dist/flint.pyz --workspace . memory history project-decisions
```

Writing a changed note under the same key updates the current memory and retains
its history. `memory recover REFERENCE` retrieves an original. Search is lexical
and returns complete memories within the byte budget.

Add `--track-source` when a memory should only remain current while its source
file is unchanged. Changed or missing sources are reported as stale and excluded
from current recall. Read the new source before updating the memory.

[Memory and sessions](integrations/skills/flint-context/references/memory-wire.md).

### Read the relevant code

```bash
python3 -S dist/flint.pyz --workspace . map examples/hello.py
python3 -S dist/flint.pyz --workspace . inspect examples/hello.py greet --editable
```

Selections retain exact source. Budgets are UTF-8 bytes. An insufficient budget
is reported instead of silently returning an incomplete selection. After a
context reset, start a new session and recover the source needed for the task.

## Faster in 0.18

The JSON path loads the editor only when needed and reduces CLI startup work,
while keeping responses and revision checks unchanged.

| JSON operation on the same Python file | 0.17 | 0.18 | Less time |
|---|---:|---:|---:|
| Open a file | 62.12 ms | **45.64 ms** | **26.5%** |
| Recover ticket content | 66.97 ms | **48.65 ms** | **27.4%** |
| Apply an edit | 103.58 ms | **84.95 ms** | **18.0%** |

Medians of 12 runs per version and operation, on the same Linux system and
Python interpreter. Across three inputs, observed reductions were **26–30% for
JSON reads/recovery** and **17–22% for edits**. These are tool timings; overall
agent time and token usage depend on the task and integration.

## Integrate and build

The [Flint Context skill](integrations/skills/flint-context/SKILL.md) explains
when to read, compress, remember, recover and edit. Agents can also call the
CLI or JSON interface directly. The agent controls which operations to use.

Workspace state is stored in `.flint/`, excluded from Git. References belong
to that workspace. Optional codecs and the C COPY/1 engine are documented in
[advanced operations](integrations/skills/flint-context/references/advanced-context.md).

- [Python engine](flint/), [CLI](bin/flint) and [ready-to-run package](dist/flint.pyz).
- [Source hashes and package manifest](dist/manifest.json).
- [Optional C engine](native/flint_copy.c).
- [Logo, font and brand assets](assets/preview.html).

Rebuild the package without downloading dependencies:

```bash
python3 tools/package.py
```

## License

Flint is licensed under the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE)
for attribution. Both files are included in the ready-to-run package.

Third-party notices and licenses are retained in `vendor/` and in the package.
Micro 5 includes its [OFL license](assets/fonts/OFL.txt).
