---
name: openspec-propose
description: Propose a new OpenSpec change for a homework assignment. Use when the user wants to start a new feature/artifact and needs a spec-first plan (proposal + tasks) before any code. Creates openspec/changes/<slug>/ with proposal.md and tasks.md by hand (no CLI).
---

# Propose an OpenSpec change (no CLI)

Create a spec-driven change bundle for the current homework directory (a subfolder of the `ohw` monorepo, e.g. `dz2`). There is no `openspec` CLI here — build the files by hand, exactly like the existing changes in `dz2/openspec/changes/<name>/`.

## Steps

1. **Derive a kebab-case change name** from what the user wants. E.g. "add artifact types" → `add-artifact-types`.

2. **Where to write**: `<current-homework-dir>/openspec/changes/<name>/`. The homework dir is the subfolder of `ohw` that contains the project source (e.g. `dz2`). If the user didn't say, ask or infer from context.

3. **If a change with that name already exists**, check `openspec status`-style knowledge: ask whether to continue it or make a new one. Do not silently overwrite.

4. **Create `proposal.md`**. Follow this structure (mirrors `dz2/openspec/changes/add-artifact-types/proposal.md`):
   - `# Proposal: <title>` (RU)
   - `## Почему` — problem/motivation
   - `## Что делаем` — bullet list of concrete changes
   - `## Спека` — spec coverage (what behavior this affects, any requirement deltas)
   - `## Проверка` — how to verify it works (manual steps, commands)

5. **Create `tasks.md`** with a checkbox list, one line per concrete task:
   ```markdown
   # Задачи

   - [ ] task 1
   - [ ] task 2
   ```
   Tasks must be small, verifiable, and ordered for implementation. Where relevant, reference business-requirement ids (BR-N) used in the assignment.

6. **Create `design.md` and `spec.md`** only if the change is non-trivial (multiple files / behavior changes). For simple changes a `proposal.md` + `tasks.md` is enough (see `add-docker-compose` which has only `proposal.md`).

7. **Summarize**: change path, what was created, and suggest next step: review it (self-review agent) or start implementing.

## Guardrails

- Keep it spec-first: the *why* and *what* come before code.
- Write identifiers/paths in English, prose in Russian.
- Track progress by ticking `- [x]` in tasks.md as tasks complete (during apply).
- Do not create code files in this skill — only the spec bundle.
