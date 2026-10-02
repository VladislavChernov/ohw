# Workflow harness

Portable working rules and opencode artifacts for AI-assisted development. Applies to any
project; the `ohw` specifics live in `rules/adopted/`.

**This folder is the canon.** Until 2026-10-02 this folder published only a *description* of the
harness and kept the tool machine-local, on the reasoning that configuration does not belong in a
public repository. That reasoning was wrong in a way only measurement could show.

## Why the decision changed

The description was written, reviewed and merged - and then never loaded by anything. No opencode
artifact referenced it, no setting imported it, and no project had an `AGENTS.md`. The rules were
present and unreachable. Two measured consequences:

- in one project a session could not find the rules, wrote "fix the record before the code" into a
  second file, and then violated that rule twice in the same session;
- a homework's private copy of the description had fallen **176 lines** behind, missing every
  convention learned since, and nothing reported it.

A description nobody reads is not enforcement, and a snapshot that drifts silently is worse than an
honest gap: it looks authoritative while being out of date. So the tool is published, the rules go
into the one file opencode loads on its own, and a guard reports drift.

## Layout

| Path | Purpose |
|---|---|
| `rules/global.md` | **The canonical working rules.** Installed into projects verbatim. Edit here. |
| `rules/adopted/ohw.md` | Rules that hold only in the `ohw` projects. Read, never installed. |
| `.opencode/agent/reviewer.md` | Read-only adversarial reviewer. Never edits, never pushes. |
| `.opencode/command/review.md` | `/review` - run the reviewer over the current diff. |
| `.opencode/skills/*/SKILL.md` | `openspec-propose`, `openspec-apply` - spec bundles without a CLI. |
| `install.ps1` | Installs artifacts and rules into a project. Idempotent. |
| `check.ps1` | Verifies the install exists and the rules are in sync. Exit 1 on drift. |

## Install

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Project D:\some\project
```

`Bypass` is needed because the default execution policy on this host blocks `.ps1`.

- `.opencode/` is copied verbatim. Existing subdirectories are kept unless `-Force`, so a project
  that customised an artifact does not lose it silently.
- The rules are **embedded** into the project's `AGENTS.md` between marker comments, not
  referenced by path. A path reference would be a cleaner single source of truth, but it depends on
  the agent choosing to follow it - and the entire point is that an unread rules file is not a
  rules file. Embedding is safe only because `check.ps1` compares the block against
  `rules/global.md`, which turns duplication into something detected rather than something that
  rots.

The install never touches anything below the closing marker: that region is the project's own rules
and survives every reinstall.

## Check

```powershell
powershell -ExecutionPolicy Bypass -File .\check.ps1 -Project D:\some\project
```

Reports missing artifacts, a missing or stale rules block, and replacement characters in
`AGENTS.md` (the signature of a repair script that wrote mojibake as data).

Run it before committing. Drift is worth catching on the day it is introduced, not months later,
which is how the 176-line gap survived in the first place.

## Editing the rules

1. Edit `rules/global.md`.
2. Re-run `install.ps1` for every project that has the harness.
3. Run `check.ps1` to confirm.

Never edit the block inside a project's `AGENTS.md`: the next install overwrites it, and
project-specific material belongs below the marker for exactly that reason.

## Scope

Not part of any homework's deliverable, and shares no code with the `ohw-kit` package - installing
it adds no dependency. Homeworks already submitted are **not** retrofitted: they are frozen
artifacts, and adding tooling to them changes history for no benefit.

## Environment notes

The Windows-host and Docker lessons accumulated while building these projects are summarised in
`rules/global.md` under "Environment, when a container is used". They are deliberately kept short
there. The long-form narrative of how each one was learned stays in the project that learned it,
because a rule is worth keeping exactly as long as its reason is - and the reasons here are long.
