<!-- PR title: Conventional Commit format with a required scope, e.g. "fix(collector): record timing-phase failure instead of crashing the run" -->

## Description

<!-- What changes and why — the problem, then the approach. Link related issues with "Closes #123". -->

## Type of Change

- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature (non-breaking change that adds functionality)
- [ ] Breaking change (CLI flags, exit codes, on-disk schema, or taxonomy strings)
- [ ] Refactoring (no behavior change)
- [ ] Documentation update
- [ ] CI / tooling

## Quality Gate

- [ ] `uv run ruff format` leaves the tree unchanged
- [ ] `uv run ruff check` passes with zero findings
- [ ] `uv run pyrefly check` passes (strict preset, zero errors)
- [ ] `uv run pytest` passes
- [ ] New behavior has tests; environment-specific tests carry the right `skipif` guard

## Benchmark Integrity

typebench's only product is trust in the numbers — these apply to any change touching the engine, adapters, or schema. Check each or mark N/A in the description.

- [ ] Measured path stays lightweight: no heavy imports (pydantic etc.) added to `engine/wrapper`, `engine/measure`, `engine/calibration`, or `contracts/taxonomy`
- [ ] Every failure path still produces a recorded result — nothing raises out of the measured pipeline or drops a run
- [ ] The record claims nothing the engine didn't actually run (honesty flags, `failure_phase`, enforcement fields stay truthful)
- [ ] On-disk schema (`RunResult` fields, taxonomy string values) is unchanged — or the change was discussed and approved first

## Ask-First Boundaries

<!-- If this PR touches a quality-gate config, the on-disk schema, runtime dependencies, or measured-path imports, link the discussion where it was approved. -->

- [ ] No ask-first boundary crossed, or the approval is linked above

## Testing

<!-- How you verified the change. For pipeline behavior, note how it's driven through StubAdapter + the fake checker. For suite/corpus changes, include the command you ran and the key numbers. -->
