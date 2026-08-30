# Proposal: harden test-run reporting, LLM feedback loop, sampling options, infra shutdown

## Why

E2E run on GPU exposed four issues:

1. Report counts were parsed by counting `"PASSED"`/`"FAILED"` substrings in
   pytest output — the `short test summary info` block duplicates `FAILED ...`
   lines, so totals were wrong (reported 7/2 while pytest said 5/1).
2. pytest tried to write `.pytest_cache` into the host-mounted `/data` volume
   owned by root; the unprivileged `app` user got `Permission denied` warnings.
3. The LLM sometimes "gets lazy": returns tests for only one resource or
   misses HTTP verbs (e.g. asserted `"posts" in response.json()` for a list
   endpoint). The pipeline accepted the first syntactically valid answer.
4. Sampling (temperature, `num_predict`, seed) was hard-coded in `ollama.py`;
   and after a run the shared ollama container keeps running with no
   documented shutdown step.

## What Changes

- `runner.py`: parse the pytest **summary line** (`N passed, M failed, E errors`)
  with a regex; substring counts only as fallback. Add `-p no:cacheprovider`
  to the pytest invocation (fixes cache permission warnings in containers).
- New `validator.py`: coverage validation of the generated code against
  required markers (default: HTTP verbs GET/POST/PUT/PATCH/DELETE; configurable).
- `ollama.py`: feedback loop — when the response is missing required coverage,
  the model is sent a follow-up prompt ("your previous answer is missing X,
  regenerate the full file including it") within the retry budget.
- `config.py` / `cli.py`: `--temperature`, `--num-predict`, `--seed`,
  `--required-markers` options (env overrides supported).
- README: documented ollama shutdown after a run (`infra/down.ps1`).

## Impact

- Affected specs: `api-test-generator`
- Affected code: `src/api_testgen/{runner,validator,ollama,config,cli}.py`, `tests/test_core.py`, README
