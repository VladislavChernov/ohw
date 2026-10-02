# ohw addendum

Rules that hold only in the `ohw` university projects. `install.ps1` does **not** install these
into a project; read them when working inside `ohw` or one of its homeworks.

## Layout

- One homework, one folder. `D:\Otus\ohw\DzN` is a single assignment, a self-contained project with
  its own git repository. No homework mixes several unrelated subprojects.
- Published homework lives in the `ohw` monorepo on GitHub under `dz1/`, `dz2/`, `dz3/`, ... via
  subtree. A harness is never pushed.
- **Homeworks are frozen once submitted.** `dz1`/`dz2`/`dz3` and `light_llm_engine` are working
  reference implementations that stay as they are. Do not refactor them, do not install tooling
  into them, and do not copy their code into a new homework. Shared functionality is consolidated
  in `ohw_kit`.
- Standalone working copies also exist at `D:\Otus\DzN`. The two locations are not the same thing:
  only the monorepo copy is in git, and documentation that names a homework path should say which
  one it means.

## Shared components

- **Reuse only through the kit, never by copying.** `D:\Otus\kit` (published as `kit/`, package
  `ohw-kit`, imported as `ohw_kit`) is the single home for cross-homework machinery: Ollama
  access, input reading, Markdown rendering. A new homework adds `../kit` as a dependency rather
  than extracting code from past homeworks.
- The kit hands back a plain string. How a model's reply becomes a deliverable - a Markdown
  report, a verified `test_*.py`, JSON - is the homework's own decision. Do not force a shared
  output shape.
- Input reading is a registry `{extension -> reader}`; adding a file format means registering it
  in the homework, not editing the kit. Every reader must read real text; a placeholder is not a
  reader.
- When in doubt, ask: if something is needed across homeworks and is neither in `ohw_kit` nor
  clearly homework-specific, propose adding it to the kit and confirm before scaffolding bespoke
  code.

## Starting a new subproject

A new standalone project inside or alongside a homework requires an explicit announcement. Ask for
and confirm the **name** before scaffolding anything.

## Tooling conventions accepted here

These are chosen, not required, and they are the reason the global rules above are phrased the
way they are:

- `uv` for environment and dependency management; no `uv`/`pytest`/`ruff`/`mypy` on the host.
- Tests live in the repository and are run in the dev container. Inline snippets, `python -c`
  probes and one-off fixtures are not acceptance evidence. Exploratory diagnostics are fine, but a
  fix is not reported as verified until the diagnostic has been turned into a stored test.
- Specs live in `openspec/changes/<name>/` as hand-written bundles. No `openspec` CLI.
- Evaluations live under `prototype/infra/eval/`; the golden set is a data file reviewed by a
  human, and the acceptance criterion is fixed before any run that informs it.
