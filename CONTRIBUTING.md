# Contributing

Thanks for helping. A few ground rules keep Night Shift trustworthy:

- **Safety first.** Changes that loosen the autonomy gate, the mimic's isolation, or token handling need a clear rationale and tests. When in doubt, the default should ask a human.
- **Deterministic rails.** Anything the agents decide by judgment that could be a command with a clear result (`HAND OFF:`, `RESULT:`, `DONE:`) should be a command.
- **Tests.** `make test` runs the suite against a small sample repository; please add a test with every change. `make lint` must pass.

## Development

```bash
make install    # .venv with Night Shift and dev tools
make test
make lint
make dashboard  # http://127.0.0.1:8000
```

Commit messages are short and plain ("Add map drafting for Django URL configs").
