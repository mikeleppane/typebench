# typebench

Neutral, reproducible benchmark of Python type-checker performance
(mypy, pyright, pyrefly, ty). Methodology and decisions:
`docs/superpowers/specs/2026-06-07-typebench-design.md`.

> **Status:** engine spine (Plan 1). Real checker adapters, corpus, cgroup
> memory measurement, and rendered results land in later plans. Numbers in
> this README are auto-generated and must never be hand-edited.

## Local run (spine demo)

```bash
uv sync
uv run typebench run --tool stub --project demo --output results.json
```

Requires `hyperfine` on `PATH` for timing (otherwise the run still classifies
and records, with `timing: null`).
