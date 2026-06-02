# Lint baseline — nighttrade

Captured on 2026-06-02 as the starting point. Burn these down
as we touch the relevant files. Do NOT do a mass cleanup PR.

## Ruff counts

```
427	UP006 	non-pep585-annotation
119	UP045 	non-pep604-annotation-optional
 91	UP035 	deprecated-import
  4	SIM105	suppressible-exception
  2	E402  	module-import-not-at-top-of-file
  2	E741  	ambiguous-variable-name
  1	B007  	unused-loop-control-variable
  1	B017  	assert-raises-exception
  1	SIM103	needless-bool
Found 648 errors.
No fixes available (549 hidden fixes can be enabled with the `--unsafe-fixes` option).
```

## Mypy counts

45 errors total

## Run locally

```
python3 -m ruff check src/ tests/
python3 -m black --check src/ tests/
python3 -m mypy src/nighttrade
```
