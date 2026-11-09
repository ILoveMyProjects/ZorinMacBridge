# Tests

All regression and source-invariant tests live in this directory.

Run the complete suite from the repository root:

```bash
scripts/run-tests.sh
```

The runner exports the repository root through `PYTHONPATH`, so tests can import the application modules without duplicating path bootstrapping in every file.
