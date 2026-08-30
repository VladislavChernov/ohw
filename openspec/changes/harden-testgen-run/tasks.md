# Tasks: harden-testgen-run

## 1. Reporting

- [x] 1.1 Parse pytest summary line (`N passed, M failed, E errors`) in `runner.run_pytest`; fallback to substring counts
- [x] 1.2 Add `-p no:cacheprovider` to pytest args
- [x] 1.3 Tests: summary-line parsing (with duplicated FAILED in short summary), fallback

## 2. Coverage validation + feedback loop

- [x] 2.1 New `validator.py`: `find_missing(code, required) -> list[str]`
- [x] 2.2 `ollama.generate_code` accepts optional validator; on missing coverage resends with feedback
- [x] 2.3 Config/CLI: `--required-markers` (env `REQUIRED_MARKERS`), `--temperature`, `--num-predict`, `--seed`
- [x] 2.4 Tests: missing-marker detection, feedback payload built

## 3. Docs / infra shutdown

- [x] 3.1 README: `infra/down.ps1` after a run; sampling flags documented
- [x] 3.2 Unit suite green
