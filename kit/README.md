# ohw-kit

Shared, reusable Python building blocks for the `ohw` homework projects.

The kit owns the machinery that homeworks keep re-implementing by hand:

- **LLM access** — one httpx-based client for a local Ollama service
  (`/api/chat`, optional JSON mode), with declarative error types.
- **Input reading** — an *extensible* registry of directory readers
  (`{extension -> reader}`) so a homework can read `.txt`, `.md`, `.pdf`, …
  by registering a reader, without touching the kit.
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