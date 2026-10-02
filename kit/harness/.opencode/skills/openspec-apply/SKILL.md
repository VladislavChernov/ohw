---
name: openspec-apply
description: Implement tasks from an OpenSpec change in a homework assignment. Use when the user wants to implement, continue, or finish a change whose bundle lives in openspec/changes/<name>/. Reads proposal.md + tasks.md, works through the checkbox list, and verifies with the project's test/lint toolchain.
---

# Apply an OpenSpec change (no CLI)

Work through the tasks of an existing spec bundle in the current homework directory. Verify each task with the project's real toolchain before ticking it — never mark done without proof.

## Steps

1. **Select the change**: if the user didn't name it, list `openspec/changes/` and pick (ask if ambiguous; auto-select if only one active change).

2. **Read context first**:
   - `<change>/proposal.md` — what & why
   - `<change>/design.md` (if present) — how
   - `<change>/spec.md` (if present) — detailed behavior requirements
   - `<change>/tasks.md` — the checkbox list to work through

3. **Check the project's toolchain** before touching code. Convention across the `ohw` homework dirs (Python + uv):
   - tests: `uv run pytest -q`
   - lint: `uv run ruff check .`
   - types: `uv run mypy src` (if a `src` layout exists)
   If a homework uses a different stack (e.g. `dz1` torch), adapt: read its README/requirements for the right commands.

4. **Implement tasks one at a time**:
   - State which task you're on (e.g. "task 2/5: ...").
   - Make minimal, focused changes for that task only.
   - After each task, run the relevant check (targeted test, lint on changed files).
   - Tick `- [x]` in tasks.md immediately after verifying.

5. **On completion**: run the full verification (pytest + ruff + mypy), then report:
   - N/M tasks complete
   - verification results
   - suggest running the self-review agent (`reviewer`) before committing

## Guardrails

- Always read proposal + tasks before implementing.
- Keep changes scoped to the current task; don't refactor unrelated code.
- If a task is ambiguous or implementation reveals a design flaw, pause and propose updating the spec bundle (don't guess).
- Tick a task only after it's verified — a task is done when its check passes, not when the code "looks right".
- Never commit unless the user explicitly asks.
