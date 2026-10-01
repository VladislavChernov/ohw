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

**What this is.** `kit/harness/.opencode/` is an [opencode](https://opencode.ai)
project configuration: the workflow the AI assistant follows when working on
these homeworks. It is four files, no runtime code:

| Path | Type | Purpose |
|---|---|---|
| `skills/openspec-propose/SKILL.md` | skill | Create a change bundle (`proposal.md` + `tasks.md`, optionally `design.md`/`spec.md`) by hand |
| `skills/openspec-apply/SKILL.md` | skill | Implement a bundle's tasks, verify each one, tick the checkboxes |
| `agent/reviewer.md` | subagent | Adversarial read-only reviewer: verifies each suspicion against the real code before reporting it, never edits |
| `command/review.md` | command | `/review` — run the reviewer over the current diff |

No `openspec` CLI is required; the skills write `openspec/changes/<name>/`
files directly, which is how the bundles in this monorepo were made.

**Why it lives here.** Until 2026-10-01 the harness was local-only and never
published: the canonical copy sat at `D:\Otus\harness\.opencode` and every
homework installed its own copy from there. That had a cost worth naming — the
process conventions were reviewable only on one machine and drifted silently,
and the same reviewer logic was re-entered per project instead of being fixed
once. Publishing the canon makes it reviewable in a diff and fixes it once.

**What it is not.** Not part of any homework's deliverable. It is not the
`ohw_kit` package, it shares no code with it, and installing it into a homework
adds no dependency.

**Install into a homework.** From the homework folder (not the kit folder —
opencode resolves config from the opened project):

```powershell
Copy-Item D:\Otus\ohw\kit\harness\.opencode .opencode -Recurse
```

Restart opencode in that folder: the skills appear by name, and `/review` runs
the adversarial self-review on the current diff.

**This is a snapshot, dated.** The copy here is the harness **as of
2026-10-01** and is not updated automatically. The canonical copy remains
`D:\Otus\harness\.opencode`; changing the harness means editing the canon
first, then re-copying here and into each homework, so that the published copy
is a deliberate publication step rather than a side effect of local work.

**Known gap.** The canon also carries a conventions document
(`.opencode/README.md`, ~370 lines of process and environment rules). It is
deliberately **not** duplicated here — it is the harness's own README, and it
still states that the harness is not published to this monorepo, which is no
longer true. It stays in the canonical copy until it is rewritten for
publication. Reading it is not a prerequisite for using the harness above;
installing from this folder gives you the four files that actually drive
opencode.