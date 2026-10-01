---
description: Run the adversarial self-review agent over the current diff/staged changes and summarize its findings.
agent: build
---

Run the `reviewer` subagent over the current change in this homework directory and act on its findings.

1. Determine the diff to review:
   - reviewed changes (staged or worktree) versus `HEAD`
   - if the user gives a change name, also read `openspec/changes/<name>/{proposal,design,spec,tasks}.md` for context
2. Invoke the `reviewer` subagent with: the diff scope, the optional change name, and this homework directory (the `ohw` subfolder that is the project root).
3. Present its structured verdict to the user.
4. If the verdict is REQUEST-CHANGES or lists `[BUG]`s the user wants fixed, implement the fixes (confirm with the user first), re-run the checklist (`uv run pytest -q`, `uv run ruff check .`, `uv run mypy src`), and re-review the delta with `reviewer`.

Never let the `reviewer` agent edit code itself — it is read-only; you (or the user) apply fixes, then re-verify.
