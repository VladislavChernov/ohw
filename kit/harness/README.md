# Homework workflow harness (opencode)

Portable spec-first workflow for the `ohw` university lecture projects, adapted
from a Claude Code harness (`D:\Otus\workflow\workflow`) to opencode and to
**Python without the `openspec` CLI** — exactly how the existing `dz2` homework
was written (bundle created by hand, reviewed before commit).

Reusable pieces live in this folder. They are global in spirit (apply to every
homework) but are loaded from the opencode project you open — so for each
homework folder you must install them.

## What is published, and what is not

**This document is published here. The harness itself is not.**

| | where |
|---|---|
| this description of the harness | `kit/harness/README.md` in this repository |
| the harness itself (`.opencode/`) | local only, canon at `D:\Otus\harness\.opencode` |

The reason for the split is that the harness is configuration, not an artifact:
what belongs in a public repository is the *description* of the process, so that
it is reviewable in a diff, while the machine-local copy that opencode actually
loads stays out of the repository. A reader of this repo gets the conventions
and can reconstruct the harness; an installed copy is one `Copy-Item` away.

## Local-only

The harness is a **local working tool for the AI assistant**. It is not part of
any homework's deliverable. The canonical copy lives at
`D:\Otus\harness\.opencode`; each homework project keeps its own copy.

Changing the harness = update `D:\Otus\harness\.opencode`, then re-copy into
each homework project. If a convention here changes, change it in the canon and
republish this document — otherwise the description in the repository goes stale,
which is exactly the failure this split was made to stop.

## Install into a new homework (e.g. the future `dz4`)

From the new homework dir (NOT the harness root — opencode resolves config from
the opened project folder):

```powershell
# from the homework dir, e.g. D:\Otus\Dz4
Copy-Item D:\Otus\harness\.opencode .opencode -Recurse
```

Restart opencode in that folder. Skills appear by name, `/review` runs the
adversarial self-review on the current diff.

> No `openspec` CLI is required: the skills build `openspec/changes/<name>/`
> files by hand, mirroring the existing `dz2/openspec/changes/*`.

## What's in the harness

| Path | Type | Purpose |
|---|---|---|
| `skills/openspec-propose/SKILL.md` | skill | Create a change bundle (`proposal.md` + `tasks.md`, optionally `design.md`/`spec.md`) by hand, no CLI |
| `skills/openspec-apply/SKILL.md` | skill | Implement tasks from a bundle, verify each with pytest/ruff/mypy, tick checkboxes |
| `agent/reviewer.md` | subagent | Adversarial, read-only, evidence-first reviewer (Python variant of Claude Code's `self-review`). Never edits code. |
| `command/review.md` | command | `/review` — run the reviewer over the current diff and summarize |

## Conventions baked in

### Workflow conventions

- **Spec-first.** Changes start as an OpenSpec bundle; the code is the *what*,
  the proposal is the *why*.
- **Verify before you tick.** A task is done when its check passes
  (`pytest`/`ruff`/`mypy`), not when the code "looks right".
- **Self-review before commit, not after.** `/review` runs the adversarial
  reviewer on the diff and resolves confirmed `[BUG]`s before committing.
- **Read-only reviewer.** `reviewer` never edits or pushes; fixes land through the
  main agent, then are re-verified and re-reviewed.
- **Never cite a document you have not read.** When a claim is about what the
  project *specifies* (an invariant, an ADR, a convention), open that file and
  quote or paraphrase it. Inferring the documented position from function names,
  code shape, or memory is not evidence, and it has already produced a wrong
  diagnosis: a helper named `_identity_key` was read as "the canonical key *is*
  the identity", while the governing invariant said the opposite. A plausible
  explanation for a behaviour is not grounds to call it a defect — read the spec
  first, then decide whether there is anything to fix at all.
- **A decision that failed verification is corrected in the record first.** When a
  reproduction disproves an accepted decision, amend the ADR before writing the
  fix, and treat the amendment as part of the change rather than a follow-up.
  "Fix the docs or fix the code" is not a question to ask: the answer is the
  docs, because the code is supposed to implement the decision. A question the
  harness or an ADR already answers means the rule was read and not applied —
  cite the source instead of asking. In this project the standing instruction was
  `UTF8Encoding $false` for every path; it was right for repository files and
  wrong for scripts, and it corrupted a file before anyone noticed, because the
  incorrect rule was never re-examined against what actually reads the file.
- **A list of un-enforced rules is a hazard map, not a backlog.** When a gate,
  audit, or review reports which documented rules have no test, read each entry as
  a place where docs and code may already disagree silently. An audit line saying
  "invariant X not enforced" is a warning about the present, not a task for later.
- **Before framing an open design question, read what the code already does.** In
  code with any history, behaviour is *already* a decision — often made implicitly
  and often badly. The question is rarely "what should we do?"; it is usually "do
  we change what is already being done, and why was it done that way?". Three from
  this project, all of which were proposed before the code was read:
  - a wrapper thought to be broken turned out to have a known argument-passing bug;
  - the plan for per-stage timings had to account for `ts` already being overwritten
    on every stage transition, which invalidates the obvious approach;
  - the "what to do with an unresolvable relation" question got three proposed
    options, while the code already implemented the most destructive of them behind
    `optional_failure=True`, erasing the document's entire LLM layer.
  Read the surrounding error handling and fallbacks, not just the happy path — the
  third one was missed precisely because the "drop" option lived in an `except`
  branch nobody had opened.
- **Write the criterion before the run.** An artifact with no pre-declared "what are
  we testing, what counts as success" is a log, not evidence — in a month nobody can
  tell what it was for. Do not let a run be promoted to a committed artifact without
  it; if the criterion was only formulated after seeing the first failure, say so in
  the artifact, that is a method error worth recording.
- **`n = 1` is not a result.** One document, one run, one model is enough to *reject*
  a hypothesis and not enough to accept it. Twice on the same day a conclusion drawn
  from a single document was overturned by a larger one: a prompt rule that took
  unresolved relation endpoints from 12 to 0 on a small document left 4 unresolved
  on a document four times larger. Never tune or sign off prompt text on one sample.
- **A hypothesis that failed on the second material is written down as disproved,
  not dropped.** The list of what is excluded is worth more than the list of what
  was built.
- **Never invent a convention inside a prompt example.** A model that is not
  fine-tuned copies examples verbatim, and this propagates all the way down: an
  example written as `REQ-2: хранение контекста в памяти` made the model declare
  entities with real identifiers (`DEDUP_AUTO`) while writing relation endpoints in
  the invented scheme, so nothing resolved. Use only formats that really occur in the
  ontology or the corpus. A prohibition does not help either — mentioning `REQ-N:`
  even in a "never do this" rule implants it; state the property instead ("no
  numbering or prefixes on the name").
- **Prefer a guard test to a note.** When an error is of the "easy to make, easy to
  miss" kind, close it with a test rather than a paragraph. The invented-prefix
  mistake above is now a regex over every profile, which makes the whole class
  impossible to reintroduce.

### Test and environment boundaries

- **Test-first, no ad-hoc verification.** Every bug fix and behavior change must
  first be represented by a named regression test in the repository. Inline
  Python snippets, `python -c` probes, ad hoc shell fixtures, and one-off tests
  are not acceptance evidence. Exploratory diagnostics may help investigate a
  failure, but they must be converted into a stored test before the fix is
  reported as verified.
- **Host owns source control and file operations.** Read and edit source files,
  inspect diffs, and run Git on the host. The dev container is for running
  code, tests, linters, type checkers, and application debugging only; do not
  run Git or edit files inside it.
- **One persistent dev container.** Use the existing dev image with
  `--pull=never`, mount the host workspace, start one long-lived container, and
  use `docker exec` for test and tool runs. Do not create an ephemeral container
  for each test, rebuild the image on every change, or pull images implicitly.
   Keep the container alive during an autonomous test → fix → rerun loop (and
   equivalent implementation loops). Stop the existing container when that
   logical execution stage ends; start it again only when the next stage needs
   container execution. Subagents inherit this lifecycle and must not leave a
   separate container running.
- **Dev container is for tests only, and only while tests are planned.** It exists
  to run `pytest`/`ruff`/`mypy` and to debug failures. Once the gate is green and
  no further test, lint or typecheck run is planned, stop it — do not leave it
  running "just in case". Rationale: on a 16 GB host the dev container competes
  for RAM with the eval/demo stacks (Neo4j heap + pagecache, the embeddings
  service with torch, the LLM), and an idle container is pure overhead. Concretely:
  the dev container is started for a gate run and stopped right after the gate
  turns green; it is started again for the next fix cycle. Long-running stages
  (eval, demo, e2e) run with the dev container stopped. If a run needs both, the
  dev container is the one that gets stopped.
- **A counter that cannot measure must say `unknown`, not `0`.** Absence of data and
  absence of a defect are different results, and an instrument obliged to tell them
  apart explicitly. Measured 2026-09-30 in `Dz4`: five incidents in a row where a
  measurement produced a confident zero where the honest answer was "not measured",
  and each zero was plausible enough to survive two reports —
  (1) an instrument that read names from more fields than the validator and reported
  `0 unresolvable` where there were 12; (2) `@(ConvertFrom-Json)` silently collapsing
  to nothing; (3) a loop collapsing records; (4) `mutual = 0` produced by a predicate
  that could never be true; (5) a run with an empty exchange log, reported as if it
  had been measured; (6) a *missing* document scoring as `UNGROUNDED`, accusing the
  model of inventing names on data the instrument never had — the second pole of the
  same error as `mutual = 0`, one inventing absence of a defect, the other inventing
  a defect out of absence. Not one instrument ever said "I don't know". Concrete rules:
  - an unverifiable absence is `unknown`/`attributable: false`, never `0`, and never
    an absent file — an empty result and a missing file must not look alike;
  - if an instrument reads a *different* set of inputs than the code under test
    (fields, defaults, normalization), it is the same bug: import the shared
    constant and log it in the verdict;
  - every silent-zero counter needs a `seen_in_raw` line checked by hand against the
    raw data;
  - a claim that cannot be re-run because one of its conditions has since changed is
    not a testable claim; write the weaker one that can, and say in the ADR that it is
    weaker and what it no longer covers.
- **State the denominator next to the number, and check that it is the right unit.**
  Measured 2026-09-30 in `Dz4`: a defect rate computed over *relations* instead of
  *endpoint slots* roughly doubled — `12/5` instead of `12/12`, `4/5` instead of
  `4/10` — because every relation has two endpoints and in that run all six lost both.
  The numerator was right, so the error survived re-running the count. Emit the
  denominator as a field and compute the ratio from it, not from a number a reader
  supplied from memory.
- **Form a contract as the positive invariant, not as a frozen list.** "the field the
  ontology calls canonical must resolve endpoints" survives any edit to the field list;
  "`id` must not be in the list" fails the first legitimate change and teaches the team
  to work around the test. The negative claim belongs in the ADR prose; the test guards
  the positive one.
- **Open decision first, code second.** While a design decision is still open, the
  deliverable is an analysis, not an implementation, and a green gate is not
  evidence that the chosen path is right — it certifies code against its tests,
  never the premise. Wait for the decision, then implement, then gate.
- **Pass host Git metadata explicitly.** Before container checks, compute the
  short commit on the host with `git rev-parse --short HEAD` and pass it as
  `RUN_CODE_COMMIT` to `docker exec -e RUN_CODE_COMMIT=<hash>`. The container
  must not invoke Git or guess the host revision.
- **Check what is free before choosing a lane.** When a stand has just produced a
  bug, decide cheap vs expensive by fact, not by assumption:
  - env changes and anything bind-mounted into the container need no rebuild;
  - confirm the mount before promising a free experiment:
    `docker inspect --format '{{json .Mounts}}' <container>`, and prove the edit is
    visible by comparing line counts on host and in container;
  - a change to code inside the image costs one service rebuild — models and
    embeddings stay in volumes, so this is still far cheaper than the full stack;
  - anything touching schema, storage or migrations belongs to the dev loop with a
    full gate.
  An experiment on a warm stand answers in minutes and costs several times less than
  reasoning from logs afterwards — but only if the criterion was written down first.

### Project lifecycle conventions

- **One homework, one folder.** `D:\Otus\DzN` is a single homework assignment —
  a self-contained project with its own git repository. No homework mixes
  several unrelated subprojects.
- **New subproject = separate announcement.** If the user wants to create
  another standalone project (like `light_llm_engine`) inside or alongside a
  `DzN`, the agent MUST NOT start silently. The user announces it explicitly;
  the agent asks for and confirms the project **name** before scaffolding.
- **Publish via the monorepo.** Published homework lives in the `ohw` monorepo
  on GitHub under `dz1/`, `dz2/`, ... via subtree. The harness itself is never
  pushed.

### Shared components (ohw_kit)

- **Reuse only through the kit, never by copying.** `D:\Otus\kit` (published in
  the `ohw` monorepo as `kit/`, package `ohw-kit` / import `ohw_kit`) is the
  single home for cross-homework machinery: Ollama access (`OllamaClient`),
  input reading (`load_input`), optional Markdown rendering (`render_markdown`).
  A new homework that needs any of these adds `../kit` as a dependency
  (`uv add ../kit`) and imports from `ohw_kit`; it does NOT extract code from
  past homework folders.
- **Historical homeworks are frozen monuments.** `Dz1`/`Dz2`/`Dz3`/
  `light_llm_engine` are working reference implementations that stay as they
  are. Do not refactor them and do not copy their code into a new homework;
  shared functionality is consolidated in `ohw_kit`.
- **No fixed output contract.** The kit hands back a plain string. How the
  model's reply becomes a deliverable — Markdown report (`Dz2`), executable
  `test_*.py` that is run and verified (`Dz3/simple`), JSON, ... — is the
  homework's own decision. Do not force a shared output shape.
- **Input reading is extensible.** `ohw_kit.io` is a registry
  `{extension -> reader}`; adding a new file format (e.g. `.pdf` via pypdf)
  means registering a reader in the homework, not editing the kit. The kit
  ships `.txt`/`.md` readers; PDF support must be real text extraction, not a
  placeholder.
- **When in doubt, ask.** If a homework needs cross-cutting machinery that is
  neither in `ohw_kit` nor clearly homework-specific, propose adding it to the
  kit and confirm with the user before scaffolding bespoke code.

## Environment hints (Windows host + Docker) — read before running checks

These were hard-won lessons from the `dz4` sessions (Sept 2026). If pytest/ruff/
mypy "won't run", it is almost always the environment, not the code. Follow
these patterns instead of re-discovering them:

### 1. Host toolchain does not exist — verify inside the dev container
There is no `uv`/`pytest`/`ruff`/`mypy` on the Windows host. The toolchain
lives in the existing `ohw/dz4-dev:0.1.0` image. Mount the project read-write,
start one long-lived container, and never rebuild or pull it for an edit:

```powershell
docker run -d --pull=never --name ohw-dz4-dev -v D:/Otus/ohw/Dz4/prototype:/work -w /work ohw/dz4-dev:0.1.0 bash -lc "tail -f /dev/null"
docker exec -e RUN_CODE_COMMIT=<host-hash> ohw-dz4-dev bash -lc "uv run --no-sync pytest -q"
```

Run the project's stored tests and tool commands with `docker exec`. Set
`PYTHONPATH` to the mounted source path when the image's application path is
stale; do not install packages or rebuild the image during a normal edit loop.

### 2. PowerShell quoting is the main failure mode
- `&&` is not a PowerShell separator → parse error. Use `;`.
- `head`, `cat -A`, `sed` are not host commands — they only exist inside WSL or
  the container.
- Nested quotes inside `powershell -Command "..."` get mangled. **Do not fight
  it**: instead of `bash -c 'long; inline; script'`, run the script from a file
  that is already inside the mounted volume (`bash /work/reports/check.sh`),
  or use `Start-Process` with an **array** ArgumentList.
- `Start-Process -ArgumentList` does NOT quote elements with spaces. A
  `bash -c 'export …; pytest …'` element arrives inside the container as the
  single word `export`. Fix: wrap the whole script in inner double quotes as
  one ArgumentList element: `'bash','-c',"<script>"`.
- Long runs: launch detached with `Start-Process … -RedirectStandardOutput
  <log> -RedirectStandardError <err>`, then sleep/poll and read the log. A
  foreground command that "loses" its shell integration may be killed.

### 3. WSL: docker is NOT integrated into the default distro
`wsl -e bash -lc "docker …"` fails (no docker CLI/daemon socket there). Docker
CLI works from the PowerShell host. WSL IS useful for file inspection/repair
(paths `/mnt/d/...`, python3, sed, file).

### 4. Never create shell scripts with the editor tool or PowerShell heredocs
Both corrupted `.sh` files (lost characters, CRLF, `check.sh` even became a
*directory* once). The ONLY reliable way to write a script file:

```powershell
wsl -e bash -lc 'echo -e "#!/bin/bash\ncd /work || exit 2\n…" > /mnt/d/Otus/ohw/Dz4/prototype/reports/check.sh'
```

Then verify with `file <path>` (must say `ASCII text`, not `CRLF`/`data`) and
`sed -i "s/\r$//" <path>` if needed. Same for any file with Cyrillic that a
bash script will consume.

### 5. Debug loop for "container exits immediately"
`docker run -d --name X …` → `docker inspect X --format
'STATUS={{.State.Status}} EXIT={{.State.ExitCode}}'` → `docker logs X` → fix
the script (usually line endings or quoting), `docker rm -f X`, retry.

### 6. Non-ASCII text: one write path, then verify
Any file holding Cyrillic goes through exactly one path, and the result is
checked before moving on:

```powershell
[System.IO.File]::WriteAllText($p, $text, (New-Object System.Text.UTF8Encoding $false))
$t = [System.IO.File]::ReadAllText($p, [Text.Encoding]::UTF8)
"FFFD=$([regex]::Matches($t, [char]0xFFFD).Count) q=$([regex]::Matches($t, '\?{4,}').Count)"
```

Never `Set-Content`, `Out-File`, `>` redirection, or heredocs for non-ASCII text —
they mangle it, and the damage only surfaces after a commit. `FFFD == 0` and
no run of four or more `?` is the acceptance check; a regex replace used as a
"quick fix" is how broken bytes get committed twice.

**A BOM is not universally required — it depends on who reads the file.** The write
path above is right for repository files and wrong for scripts, and measured
2026-09-30:

| path | BOM | why |
|---|---|---|
| repository files (`.md`, `.py`, `.yaml`) | **no, and do not add one** | verified across 7 files: none carries a BOM; `.gitattributes` sets `eol=lf` |
| a `.ps1` that PowerShell 5.1 executes and that contains non-ASCII literals | **yes** | without it the file is decoded as ANSI: a Cyrillic literal turns into mojibake, and the mangled bytes can also break parsing outright (`TerminatorExpectedAtEndOfString`) |
| a file written and read via `WriteAllText` / `ReadAllText` | irrelevant | `ReadAllText` detects UTF-8 itself; verified both ways |
| `Get-Content`, `Out-File`, `>` | irrelevant | not a BOM question: they need an explicit `-Encoding UTF8` or they use ANSI |

The robust way to avoid the question in throwaway scripts is to keep them ASCII
only and build non-ASCII from `[char]` codes, so the file's own encoding cannot
matter. Also check `FFFD` in the `.ps1` itself, not only in its output.

**`FFFD == 0` is necessary and not sufficient.** A Cyrillic literal in a BOM-less
`.ps1` is decoded as ANSI, and the resulting mojibake is then written into the
target as *valid* UTF-8 — so there is no replacement character to find and the
`FFFD` check passes on a corrupted file. Measured 2026-09-30: a repair script with
one Cyrillic heading in it silently wrote `## 5. С л о ж н ы е   с л у ч а и`
as data. Two cheap additions close the gap:
- after a scripted edit, read a few changed lines back and look at them, not just at counters;
- treat a run of characters in `À-ÿ` inside a Russian document as suspect; `grep` for it with
  a class, e.g. `[À-ÿ]{2,}`.

### 7. `Set-Content` writes CRLF, which breaks shell scripts
A CRLF line ending turns `tail -40` into `option used in invalid context`. Write
`.sh` files with the editor tool (LF), never with `Set-Content`. Symptom of the
same class: an option number and its argument appearing split apart.

**The editor tool also produced CRLF here**, on a file that was LF before and
that `.gitattributes` pins to `eol=lf`, so `Set-Content` is not the only source.
After editing a tracked file, compare the counts: `([regex]::Matches($t,"`r`n")).Count`
against `([regex]::Matches($t,"(?<!`r)`n")).Count`. One of them must be zero.

### 8. Inside a container, silence is not evidence
- `grep` for non-ASCII matches nothing because of the container locale. That does
  not mean the file is unchanged — compare line counts or byte size on host and in
  container instead.
- A service port is not necessarily published. Do not guess it: read
  `/proc/net/tcp` for `LISTEN` (`st == 0A`) and try what is really there.
- `docker stop`, never `down -v`: volumes hold downloaded models and run
  artifacts, and re-fetching them costs far more than the whole experiment.
- Never pass non-ASCII as a command-line argument into a container. Write a
  `.sh` and `docker cp` it.
- Never `-Recurse` through a directory containing `node_modules`: the output runs
  to tens of thousands of lines and drowns the result. Filter by name or use
  `-Depth`.
