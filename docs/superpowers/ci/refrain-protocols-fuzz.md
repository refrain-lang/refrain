# refrain-protocols: fuzz CI (Increment 0 handoff)

Wire `refrain fuzz` into the refrain-protocols repo CI. This file is the
ready-to-commit workflow + invocation; open it as a separate PR in
refrain-protocols once a refrain version exposing batch mode is installable.

## Workflow (`.github/workflows/fuzz.yml`)

```yaml
name: fuzz
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
jobs:
  fuzz:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - name: Install refrain (pin to a version with batch fuzz)
        run: |
          python -m pip install --upgrade pip
          pip install "refrain[eval]>=0.12"   # the version that ships batch fuzz
      - name: Fuzz the protocol library (gate on violations only)
        run: refrain fuzz protocols lib drafts --max-scenarios 8
```

## Notes

- No `--library` flag is needed: refrain-protocols has self-contained protocols
  under its own `protocols/`, `lib/`, and `drafts/` layout (no cross-repo
  extends references).
- Gate is violations-only; skips are reported (coverage line in the log), not
  failed. A skip means the fuzzer cannot exercise that protocol yet — it does
  not indicate a defect.
- Bump the `--max-scenarios` cap and the version pin as coverage and runtime
  evolve.
- As later increments unlock additional protocol shapes (center/bandwidth
  bandpass, complex reward conditions, etc.), the `coverage: fuzzed N / total M`
  line in the CI log will rise automatically — no workflow change needed.
