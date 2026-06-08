# AGENTS.md — typebench

typebench is a neutral, reproducible benchmark of Python type-checker performance
(mypy, pyright, pyrefly, ty). Measurement is delegated to `hyperfine` (wall-time)
and, later, cgroup v2 (peak memory + CPU-time). Results are versioned JSON; the
README and GH Pages are rendered views. Design spec:
`docs/superpowers/specs/2026-06-07-typebench-design.md`.

**Status:** Plan 4 (memory · threads · calibration) — `taskset -c 0` 1-core affinity
floor, cgroup v2 peak memory + CPU-time + OOM under a transient `systemd-run --scope`,
and a fixed calibration baseline. Builds on Plan 3 (corpus + envman + preflight). `RunResult`
is **v2**. Renderer Plan 5; CI/bump Plan 6.

## Golden rule: this is a measurement tool

The only product is trust in the numbers. Every rule below serves that. When in
doubt, prefer the honest, conservative, reproducible choice over the convenient one.

## Layout

- `src/typebench/` — the package (src layout, hatchling):
  - `taxonomy.py` — pydantic-free on-disk enums (`ResultClass`, `ThreadMode`,
    `FailurePhase`). **Stays stdlib-only** (see Measurement fidelity).
  - `models.py` — pydantic schemas (`RunResult`, `TimingStats`, `EnvFingerprint`),
    `ConfigDict(extra="forbid")`.
  - `env.py` — environment fingerprint.
  - `corpus.py` — `CorpusProject`, `SizeBucket`, `load_suite` (corpus as data;
    dir-segment exclude validation; optional checked-in constraints lock).
  - `counting.py` — `count_first_party`, the neutral throughput denominator (§8;
    physical-LOC, file count is the scc-independent denominator).
  - `envman.py` — `prepare_project`: clone@SHA / uv venv / install (pinned to the
    constraints lock) / freeze+verify / count, behind a fingerprinted cache that
    rebuilds on stale config and cleans up partial failures. The only subprocess
    surface besides the wrapper.
  - `preflight.py` — `preflight_project`: probes the four tools, records the
    self-reported-vs-canonical divergence, and gates readiness on mis-scope
    (self < canonical) while flagging over-report for the renderer (§8/§12/§191).
  - `wrapper.py` — `RawRun`, `run_command`, `classify_default`, and the CLI used as
    hyperfine's per-run command.
  - `timing.py` — hyperfine pass + `parse_hyperfine_json`.
  - `collector.py` — `run_single`, the probe→time pipeline that assembles one `RunResult`.
  - `measure.py` — resource pass (spec §5.5): cgroup v2 peak memory + CPU-time +
    OOM under a transient `systemd-run --scope`. Pydantic-free (runs as a scoped
    child); capability-gated with a timing-only fallback on mac/CI.
  - `calibration.py` — fixed dep-free CPU workload (`calib-pyloop-v1`) timed per run
    for VM-to-VM trend normalization (spec §5.7). Pydantic-free import.
  - `cli.py` — Typer app (`typebench run`).
  - `_fake_checker.py` — in-package controllable fake checker (ships in the wheel)
    that the stub drives.
  - `adapters/base.py` — `Adapter` Protocol, `ParallelismCap`, `default_classify`,
    `coerce_count`.
  - `adapters/stub.py` — `StubAdapter`.
- `tests/` — pytest.
- `docs/superpowers/{specs,plans}/` — design spec and phase plans.

## Quality gates — the floor before "done"

uv-managed; there is no `make`. Run, in order:

```bash
uv run ruff format
uv run ruff check
uv run pyrefly check
uv run pytest
```

`uv sync` sets up the env. Pre-commit runs `ruff check --fix`, `ruff format`, and
`pyrefly check` (strict) on commit; never bypass with `--no-verify`.

## Conventions

- **Python 3.12+.** Type-annotate every function, tests included.
- **pyrefly `preset = "strict"`** — typebench dogfoods a checker it benchmarks. Keep
  it at 0 errors. If a suppression is ever unavoidable, use `# pyrefly: ignore[<kind>]`
  with a reason — never `# type: ignore`.
- **Ruff**, line length 100, double quotes, the rule set in `pyproject.toml`
  (`E,W,F,I,N,UP,B,C4,SIM,PTH,RET,ARG,TID,TC,PL,RUF`). Keep findings at zero.
  Per-file ignores exist for tests and adapter Protocol args — don't widen casually.
- **`pathlib.Path`**, not `os.path`.
- **Pydantic v2**, `ConfigDict(extra="forbid")` on every model — unknown on-disk
  fields must fail loudly.
- **`subprocess` list-form only, never `shell=True`.** Commands handed to hyperfine
  as a string go through `shlex.join`. No hardcoded secrets; no `eval`/`exec`/`pickle`
  of untrusted data.
- No commented-out code; `git log` remembers.
- **Fully synchronous.** No asyncio in the engine spine.

## Domain invariants (do not violate)

- **Honesty by construction.** The schema must never claim a methodology that wasn't
  run. `thread_mode_enforced` stays `False` until CPU affinity is actually applied
  (Plan 4). `failure_phase` records whether a failure came from the probe or a flaky
  timed run so `real_exit_code` can't be misread.
- **Record every failure, never drop one.** The failure taxonomy (`clean`,
  `diagnostics`, `failed{env|crash|timeout|oom}`) is the contract (spec §7). A dropped
  failure silently biases the benchmark. `run_command` never raises — timeout, signal
  death, and missing-binary all become a recorded `RawRun`.
- **Measurement fidelity.** The wrapper is hyperfine's per-run command, so everything
  it imports runs on every timed measurement. Keep the wrapper (and `taxonomy.py`)
  free of heavy imports — pydantic on the measured path adds constant overhead that
  biases comparative ratios. Import enums from `taxonomy`, not `models`.
- **Benchmark isolation.** On timeout, kill the whole process group
  (`start_new_session` + `killpg`), not just the direct child, or stragglers
  contaminate later runs.

## Testing

- Names: `test_<unit>_<scenario>_<expected_behavior>`.
- **No pytest markers are registered.** Gate environment-specific tests with
  `@pytest.mark.skipif(shutil.which("hyperfine") is None, ...)` (timing) and
  `@pytest.mark.skipif(os.name != "posix", ...)` (signals / process groups).
- Test the Typer CLI with `typer.testing.CliRunner`; stub boundaries (`run_timing`,
  `shutil.which`) with `monkeypatch.setattr`.
- Drive every taxonomy class deterministically through `StubAdapter` + `_fake_checker`
  — no real checker required.
- Round-trip pydantic models through JSON and assert `extra="forbid"`.

## Scope discipline by plan

Plan 1 is the engine spine. Do **not** add real-checker, cgroup, corpus, or renderer
code unless the task is that plan. The adapter Protocol is pinned to its final-ish
shape; methods not yet exercised (`install`, `parallelism_cap`) are deliberately
deferred — don't delete them, don't build behavior behind them early.

Plan 3 adds corpus/envman/preflight. Plan 4 adds the `taskset -c 0` 1-core affinity
floor, the cgroup resource pass (`measure.py`), and the calibration baseline
(`calibration.py`) — `RunResult` is now **v2** (MemoryStats/CalibrationStats +
cpu_time_s/parallel_efficiency/hard_cap/cap_mechanism). The measured path
(`wrapper.py`, `taxonomy.py`, `measure.py`, `calibration.py`) stays pydantic-free;
affinity is a uniform collector-level `taskset` prefix, never per-adapter. Do NOT add
the results envelope, the renderer, or bump automation — those are Plans 5-6. Do NOT
change `taxonomy.py` values or further enrich `RunResult`; the lock-manifest
enrichment is Plan 5.

## Commits

Conventional Commits, **required scope**, atomic, body explains the *why*. Scopes in
use: `scaffold, models, taxonomy, env, wrapper, timing, adapters, collector, cli,
e2e, ruff, plan, spec, docs, engine, corpus, envman, preflight, counting`. **No
AI/assistant attribution** in commit
messages or PR bodies — commits read as the author's own work. See the
`git-conventions` skill.

## Skills

Engineering skills live under `.agents/skills/`. Invoke the relevant one before working:

- **coding-guidance-python** — every Python edit or review (contracts, typing,
  security, module boundaries).
- **test-driven-development** — failing test first; Prove-It for bugs.
- **code-review-and-quality** — five-axis review before merge.
- **git-conventions** — commit, branch, and PR conventions.

## Ask first

- Changing a quality gate (ruff rule set, pyrefly preset, line length) or relaxing a
  per-file ignore.
- Changing the on-disk schema (`RunResult` fields, taxonomy string values) — it's a
  stability contract.
- Adding a runtime dependency.
- Anything that puts a heavy import on the measured (wrapper) path.
