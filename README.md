# aeo

Local-CLI AEO measurement. Two arms — knowledge vs search — for Claude Code, Codex, and Grok.

Given a brand, aliases, competitors, and a query list, run the same question on each engine with search forced off and with search allowed. Record whether the brand is mentioned, whether the model actually searched, the exact search tool-call strings, and which vendor names already appeared in those queries.

This is not Gemini grounding or AI Overviews. It measures local coding-agent CLIs.

See [METHODOLOGY.md](METHODOLOGY.md). Schemas: [aeo-cli-config-v1](schemas/aeo-cli-config-v1.json), [aeo-cli-evidence-v1](schemas/aeo-cli-evidence-v1.json). Portable skill: [skills/aeo/SKILL.md](skills/aeo/SKILL.md).

## Install

Python 3.11+. Stdlib only. `jsonschema` is optional for tests.

```bash
pip install -e .
# or, from a checkout:
PYTHONPATH=src python3 -m aeo --help
```

## Run

```bash
python3 -m aeo init --from-example xerj --out aeo.config.json
python3 -m aeo run --config aeo.config.json --engine all --arm both
python3 -m aeo run --config aeo.config.json --prompt "What's the best way to search through a folder of files by content?"
python3 -m aeo run --config aeo.config.json --engine claude --arm knowledge --dry-run
python3 -m aeo report examples/xerj/aeo-data/example-run.json
```

`--arm knowledge|search|both` (default `both`). `--engine claude|codex|grok|all`. `--dry-run` prints the exact CLI command and exits.

Evidence is append-only: each run writes a new `aeo-data/runs/<run_id>.json`. Raw samples are the source of truth; rates on a document are views.

Default `samples_per_arm` is 1. Local CLIs are slow. Use `--samples N` when you want jitter.

Never add the brand, rust, or extra stack words to the user prompt. The runner only appends:

`Recommend existing tools or products if relevant. Do not write, edit, or execute files.`

## Raw CLI flags

Use these directly if the wrapper is blocked. Do not pass `--bare` to Claude (it skips keychain).

**Claude knowledge** — `claude -p` with `--tools ""`:

```bash
claude -p --tools "" --output-format json -- "PROMPT"
```

**Claude search** — search tools plus a settings file that empties hooks (`src/aeo/data/claude-empty-hooks.json`):

```bash
claude -p \
  --tools WebSearch,WebFetch \
  --allowedTools WebSearch,WebFetch \
  --permission-mode bypassPermissions \
  --settings src/aeo/data/claude-empty-hooks.json \
  --output-format stream-json --verbose -- "PROMPT"
```

**Grok knowledge:**

```bash
grok -p --disable-web-search -- "PROMPT"
```

**Grok search** — search on by default. `--output-format json --verbatim` (not streaming-json):

```bash
grok -p --output-format json --verbatim -- "PROMPT"
```

**Codex knowledge** — no `--enable standalone_web_search`:

```bash
codex exec --ephemeral --skip-git-repo-check --sandbox read-only -- "PROMPT"
```

**Codex search:**

```bash
codex exec --ephemeral --skip-git-repo-check --sandbox read-only \
  --json --enable standalone_web_search -- "PROMPT"
```

## Example

[examples/xerj](examples/xerj) is the first workspace (XERJ), not a hard-coded only-brand. Walkthrough of the fixture: [How to read a run](METHODOLOGY.md#how-to-read-a-run).

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
# optional:
pip install -e '.[test]'
```

## License

MIT. Copyright ProbeLabs.
