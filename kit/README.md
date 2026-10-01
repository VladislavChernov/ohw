# ohw-kit

> `kit/` holds two independent things: this Python package, and
> [`harness/`](#harness--the-opencode-workflow-harness) — the opencode workflow
> harness published next to it. They share the folder, not the purpose: the
> package is shared *code*, the harness is shared *process*.

Shared, reusable Python building blocks for the `ohw` homework projects.

The kit owns the machinery that homeworks keep re-implementing by hand:

- **LLM access** — one httpx-based client for a local Ollama service
  (`/api/chat`, optional JSON mode), with declarative error types.
- **Input reading** — an *extensible* registry of directory readers
  (`{extension -> reader}`) so a homework can read `.txt`, `.md`, `.pdf`, …
  by registering a reader, without touching the kit.
- **JSON replies** — extract strict JSON from a model reply (markdown fences
  tolerated) with feedback-ready error messages, plus the shared
  `ValidationResult(ok, issues)` shape used by feedback loops.
- **Expect-check evaluation** — reference evaluator for a declarative check
  DSL (`eq / len_eq / contains / fields_eq / type`) over a JSONPath subset,
  no external dependencies; results are report-ready.
- **Rendering** — optional helpers to wrap a model reply in a Markdown
  document.

The kit intentionally does **not** fix a project's output contract: how the
model's reply is turned into a deliverable (Markdown report, executable
`test_*.py`, JSON, …) is the homework's own decision. The kit hands back a
plain string.

## Install into a homework

The kit is published inside the `ohw` monorepo under `kit/`. A homework
depends on it as an editable/local package:

```bash
# with uv (recommended)
uv add ../kit

# or pip
pip install -e ../kit
```

Both resolve the `kit/` directory next to the homework.

> Alternative for containers: clone `ohw` and point the dependency at
> `git+https://github.com/VladislavChernov/ohw.git#subdirectory=kit`.

## Usage

### LLM access

```python
from ohw_kit.ollama_client import OllamaClient

client = OllamaClient(base_url="http://localhost:11434", model="qwen2.5:7b-instruct")
reply = client.chat(user="…", system="…", json_mode=True)
```

A transport can be injected for tests:

```python
client = OllamaClient(..., transport=httpx.MockTransport(handler))
```

### Reading an input directory

```python
from ohw_kit.io import load_input

docs = load_input(Path("input"))       # -> list[InputFile]
doc.content, doc.path, doc.extension
```

Built-in readers: `.txt`, `.md`. Add your own (`pypdf` — for `.pdf`, …):

```python
from ohw_kit.io import register_reader

@register_reader(".pdf")
def read_pdf(path: Path) -> str:
    ...  # return extracted text
```

### Rendering (optional)

```python
from ohw_kit.render import render_markdown

markdown = render_markdown(reply, source_name="auth.md")
```

## Development

```bash
uv sync             # install deps incl. dev group
uv run ruff check   # lint
uv run mypy .       # types
uv run pytest       # tests
```

## Layout

```
pyproject.toml      # package metadata: name ohw-kit, module ohw_kit
src/ohw_kit/        # the library
tests/              # unit tests (httpx.MockTransport, no live Ollama)
```

Tests use mocked HTTP, so the suite runs without a running Ollama service.

---

## `harness/` — the opencode workflow harness

**What this is.** `kit/harness/README.md` describes the workflow harness the AI
assistant follows on these homeworks: spec-first changes as OpenSpec bundles,
verification before a checkbox is ticked, and an adversarial read-only reviewer
that runs over the diff before a commit.

**Only the description is published, not the harness.** The harness itself is
opencode configuration — two skills, a reviewer subagent, and a `/review`
command — and it stays machine-local. The split is deliberate: a public
repository should carry the *process* so it can be reviewed in a diff, while the
copy opencode actually loads is local tooling. Before 2026-10-01 the harness was
local-only and undocumented, which meant its conventions were reviewable on one
machine and drifted silently, and the same reviewer logic had to be re-entered
per project instead of fixed once.

**What it is not.** Not part of any homework's deliverable. It shares no code
with the `ohw_kit` package and installing it adds no dependency.

**Install into a homework.** The canon is local, at `D:\Otus\harness\.opencode`.
Copy it once into the homework folder (not the kit folder — opencode resolves
config from the opened project), then restart opencode there:

```powershell
# from the homework dir, e.g. D:\Otus\Dz4
Copy-Item D:\Otus\harness\.opencode .opencode -Recurse
```

**Both files are dated snapshots.** This description is the harness **as of
2026-10-01** and is not updated automatically. If a convention changes, it
changes in the canon first, and `kit/harness/README.md` is republished
separately — so the two going out of sync is a visible diff rather than a silent
drift.