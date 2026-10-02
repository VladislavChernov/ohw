# Global working rules

These rules apply to any project. `install.ps1` copies this file verbatim into a project's
`AGENTS.md` between marker comments; `check.ps1` reports drift. Edit here, never there.

Two rules above all:

1. **Never cite a document you have not read.** When a claim is about what a project
   *specifies* - an invariant, an ADR, a convention - open that file and quote or paraphrase it.
   Inferring the documented position from function names, code shape or memory is not evidence,
   and it has already produced a wrong diagnosis: a helper named `_identity_key` was read as
   "the canonical key *is* the identity", while the governing invariant said the opposite. A
   plausible explanation for a behaviour is not grounds to call it a defect. Read the spec
   first, then decide whether there is anything to fix at all.
2. **Read what the code already does before framing the question.** In code with any history,
   behaviour is *already* a decision - often made implicitly and often badly. The question is
   rarely "what should we do?"; it is usually "do we change what is already being done, and why
   was it done that way?".

## Decision and record

**Spec-first.** Changes start as a written proposal or bundle; the code is the *what*, the
proposal is the *why*.

**A decision that failed verification is corrected in the record first.** When a reproduction
disproves an accepted decision, amend it before writing the fix, and treat the amendment as part
of the change rather than a follow-up. "Fix the docs or fix the code" is not a question to ask:
the answer is the docs, because the code is supposed to implement the decision. A question an ADR
already answers means the rule was read and not applied - cite the source instead of asking.

**Open decision first, code second.** While a design decision is still open, the deliverable is
an analysis, not an implementation, and a green gate is not evidence that the chosen path is
right - it certifies code against its tests, never the premise. Wait for the decision, then
implement, then gate.

**A list of un-enforced rules is a hazard map, not a backlog.** When a gate, audit or review
reports which documented rules have no test, read each entry as a place where docs and code may
already disagree silently.

**Write the criterion before the run.** An artifact with no pre-declared "what are we testing, what
counts as success" is a log, not evidence. If the criterion was only formulated after seeing the
first failure, say so in the artifact - that is a method error worth recording.

## Measurement

**A counter that cannot measure must say `unknown`, not `0`.** Absence of data and absence of a
defect are different results. Six incidents in a row in one project produced a confident zero
where the honest answer was "not measured", and each zero was plausible enough to survive two
reports. Concrete rules:

- an unverifiable absence is `unknown` / `attributable: false`, never `0`, and never a missing
  file - an empty result and a missing file must not look alike;
- if an instrument reads a *different* set of inputs than the code under test (fields, defaults,
  normalization), that is the same bug: import the shared constant and log the disagreement;
- every silent-zero counter needs a hand-checked comparison against the raw data;
- a claim that can no longer be re-run because a condition has changed is not a testable claim;
  write the weaker one that can, and say in the record that it is weaker and what it no longer
  covers.

**State the denominator next to the number, and check that it is the right unit.** A defect rate
computed over *relations* instead of *endpoint slots* roughly doubled, because every relation has
two endpoints. The numerator was right, so the error survived re-running the count. Emit the
denominator as a field and compute the ratio from it.

**`n = 1` is not a result.** One document, one run, one model is enough to *reject* a hypothesis
and not enough to accept it. Never tune or sign off prompt or threshold values on one sample.

**A hypothesis that failed on the second material is written down as disproved, not dropped.**
The list of what is excluded is worth more than the list of what was built.

**Choose size against risk, not convenience.** Sample where error is plausible, and cover the
smallest stratum at least once. Never pin a sample to a convenient count: a hard-coded size
invites "update the number because it failed", which is how a stale figure survives two reports.
Assert the *rule* and *anchors* (concrete identifiers) instead.

## Code and review

**Form a contract as the positive invariant, not as a frozen list.** "the field the ontology calls
canonical must resolve endpoints" survives any edit to the field list; "`id` must not be in the
list" fails the first legitimate change and teaches the team to work around the test. The negative
claim belongs in the prose; the guard test protects the positive one.

**Never hard-code what provably lives.** Words produced by a model are data, not constants. A
threshold, a size or a count is fixed before the result, never chosen after seeing it. A field
name like `EVENT` reads as an event rather than as a vertex name and loses its meaning.

**A regression test must build what the model builds.** If a failure came from a model's output,
the test must use the shape of that output. Identifiers and flags set by the code are not
sufficient. A whole class of bugs survived a large test suite because no test ever committed a
relation the way the pipeline assembles it.

**Fix the class, not the instance.** Having found one failure, look for the rest of that class
and close them with one decision, or the second instance returns later.

**Silence is worse than failure.** Any failure must be visible in three places: log, run report,
counter. "Looks like success" is a forbidden outcome even when the code did run.

**Prefer a guard test to a note.** When an error is easy to make and easy to miss, close it with a
test rather than a paragraph.

**Self-review before commit, not after.** Run the adversarial reviewer over the diff and resolve
confirmed bugs before committing. A read-only reviewer never edits or pushes; fixes land through
the main agent, then are re-verified and re-reviewed.

**Verify before you tick.** A task is done when its check passes, not when the change looks right.
On a repo with a defined gate, all of the gate runs before commit - and a green gate never
excuses a missing record, because a green gate cannot see documents that do not exist.

## Environment, when a container is used

**Host owns source control and file operations.** Read and edit files and run Git on the host.
The container runs code, tests, linters and type checkers.

**One persistent dev container.** Mount the host workspace, keep one long-lived container, and
use `exec` for test and tool runs. Do not build a throwaway container per test or rebuild the image
on every edit. Stop it when the stage that needed it ends.

**A dev container competes with the real stack for memory.** On a 16 GB host it must not run at the
same time as the evaluation or demo stack. Long-running stages run with it stopped; if a run needs
both, the dev container is the one that goes.

**Pass host Git metadata explicitly.** Compute the revision on the host and pass it into the
container; the container must not invoke Git or guess it.

**Do not assume an edit is visible before testing it.** Confirm the mount, then prove the change
is present in the container by comparing content, not by assuming.

**PowerShell quoting and encodings are the usual failure mode, not the code.** Use `;` rather
than `&&`; do not fight nested quotes by inlining a long script into `bash -c` - put the script in
a file and run the file. Write non-ASCII files through exactly one path and verify the result by
reading it back. A BOM requirement depends on who reads the file, and a run of replacement
characters in a document is a signal that a repair script wrote mojibake as data.
