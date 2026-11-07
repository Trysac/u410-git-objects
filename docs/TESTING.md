# Testing

## Running

```sh
uv run pytest --cov=git_objects   # tests with coverage report
uv run python scripts/check.py      # every gate, as CI and the factory run it
```

## Policy

- Branch coverage of `src/git_objects` must stay at or above 85 %; the gate fails below it.
- Tests exercise the public API (`import git_objects`) and assert every observable effect:
  return values, raised exceptions and their messages.
- Each bug fix starts with a failing test that reproduces it.
- Tests are deterministic: no network, no wall clock, no dependence on test order.

## Tiers

- Local tier (every gate run, coverage measured here): unit and property tests, plus conformance on small
  fixtures kept in the repository.
- CI tier: conformance that needs an external tool or a download. Mark them `@pytest.mark.conformance`; `scripts/check.py` deselects them locally
  (unless `FABRICA_CI=1`). The CI `conformance` job sets `FABRICA_CI=1` and runs the full suite. Tools are installed
  by the optional `ci/conformance-setup.sh` (bash, CI only; verify every download against a pinned SHA-256), and
  tagged tests skip cleanly when their tool is missing.

## Layout

`tests/test_<module>.py` mirrors each public module. Shared fixtures go in `tests/conftest.py`.
