# AGENTS.md — typebench

typebench is a neutral, reproducible benchmark of Python type-checker performance
(mypy, pyright, pyrefly, ty). Measurement is delegated to `hyperfine` (wall-time)
and cgroup v2 (peak memory + CPU-time). Results are versioned JSON; the README and
GH Pages are rendered views.

`ResultsEnvelope` wraps the sharded `(project × tool × thread-mode)` matrix; the
renderer emits the README table + `trends.json` (per-CPU-model calibration anchor).
`RunResult` records lock-manifest scalars, tokei code-LOC, and `cores`. `ThreadMode`
is `constrained` / `all-cores`; the constrained track is parameterized by
`--cores N` (default 1 = single-threaded, opt-in multithreading,
`taskset -c 0..N-1`). mypy ≥ 2.0 cold-parallel via `--num-workers` (fresh per-run
cache; result-equivalent to single-process).

## Golden rule: this is a measurement tool

The only product is trust in the numbers. Every rule below serves that. When in
doubt, prefer the honest, conservative, reproducible choice over the convenient one.

## Layout

- `src/typebench/` — the package (src layout, hatchling):
  - `cli.py` — Typer app (`run`, `suite`, `render`, `preflight`; `--cores`).
  - `contracts/` — shared vocabulary, no internal deps.
    - `contracts/models` — pydantic schemas (`RunResult`, `ResultsEnvelope`,
      `TimingStats`, `EnvFingerprint`, `PreparedProject`), `ConfigDict(extra="forbid")`.
    - `contracts/taxonomy` — pydantic-free on-disk enums (`ResultClass`, `ThreadMode`,
      `FailurePhase`). **Stays stdlib-only** (see Measurement fidelity).
    - `contracts/config` — `NormalizedConfig` (incl. `cores`) + `config_hash` (the
      reproducibility hash; `cores` deliberately excluded from it).
  - `engine/` — produces one `RunResult`.
    - `engine/wrapper` — `RawRun`, `run_command`, `classify_default`,
      `classify_with_map`, `universal_failure_prefix`, and the CLI used as
      hyperfine's per-run command via `python -m typebench.engine.wrapper`.
    - `engine/timing` — hyperfine pass + `parse_hyperfine_json`.
    - `engine/measure` — cgroup v2 resource pass: peak memory + CPU-time + OOM under a
      transient `systemd-run --scope`. Pydantic-free (runs as a scoped child);
      capability-gated with a timing-only fallback on mac/CI. Invoked as
      `python -m typebench.engine.measure`.
    - `engine/calibration` — fixed dep-free CPU workload (`calib-pyloop-v1`) timed per
      run for VM-to-VM trend normalization. Pydantic-free import.
    - `engine/env` — environment fingerprint.
    - `engine/collector` — `run_single` + `RunManifest`, the probe→time pipeline that
      assembles one `RunResult` (stamps lock-manifest scalars; scales the `taskset`
      affinity pin to `config.cores`).
  - `adapters/` — the only checker-specific surface.
    - `adapters/base` — `Adapter` Protocol, `ParallelismCap`, `default_classify`,
      `coerce_count`.
    - `adapters/_support` — `probe_version` + `confirm_clean` shared adapter helpers.
    - `adapters/mypy`, `adapters/pyright`, `adapters/pyrefly`, `adapters/ty`,
      `adapters/stub` — checker adapters and `StubAdapter`.
  - `corpus/` — what gets benchmarked.
    - `corpus/catalog` — `CorpusProject`, `SizeBucket`, `load_suite`,
      `load_suite_version` (corpus as data; dir-segment exclude validation; optional
      checked-in lock).
    - `corpus/counting` — `count_first_party` (physical-LOC denominator) +
      `count_code_loc` (tokei reconciled code-LOC, the headline throughput
      denominator).
    - `corpus/envman` — `prepare_project`: clone@SHA / uv venv / install (pinned to
      the constraints lock) / freeze+verify / count, behind a fingerprinted cache that
      rebuilds on stale config and cleans up partial failures. The only subprocess
      surface besides the measured wrapper.
  - `suite/` — orchestration, gating, rendering.
    - `suite/runner` — `run_suite` + `SuiteCell`, the sharded
      `(project × tool × thread-mode)` matrix → `ResultsEnvelope`; excluded cells
      become visible FAILED_ENV records ("didn't compete", never silently absent).
    - `suite/preflight` — `preflight_project`: probes the four tools, records the
      self-reported-vs-canonical divergence, and gates readiness on mis-scope
      (self < canonical) while flagging over-report for the renderer.
    - `suite/renderer` — `render_readme` (latest envelope → README table) +
      `build_trends` (full history → `trends.json`, per-CPU-model calibration anchor).
  - `_internal/` — private support code.
    - `_internal/fake_checker` — in-package controllable fake checker (ships in the
      wheel) that the stub drives via `python -m typebench._internal.fake_checker`.
  - Dependency layering: `contracts <- engine <- {adapters, corpus} <- suite <- cli`.
- `tests/` — pytest.

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

## Generated verification artifacts

`results/` and `preflight/` are local verification outputs by default. Do not
commit files from these directories, and never `git add -f` them, unless the user
explicitly asks to publish/version that specific result artifact.

For verification runs, report the command, key metrics, and sanity checks in the
assistant response. Commit rendered/source artifacts only when they are part of the
requested change and not ignored by default.

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
  by the collector. `failure_phase` records whether a failure came from the probe or
  a flaky timed run so `real_exit_code` can't be misread.
- **Record every failure, never drop one.** The failure taxonomy (`clean`,
  `diagnostics`, `failed{env|crash|timeout|oom}`) is the contract. A dropped failure
  silently biases the benchmark. `run_command` never raises — timeout, signal death,
  and missing-binary all become a recorded `RawRun`.
- **Measurement fidelity.** The wrapper is hyperfine's per-run command, so everything
  it imports runs on every timed measurement. Keep `typebench.engine.wrapper` (and
  `typebench.contracts.taxonomy`) pydantic-free and otherwise free of heavy imports —
  pydantic on the measured path adds constant overhead that biases comparative ratios.
  Import enums from `contracts.taxonomy`, not `contracts.models`.
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
- Drive every taxonomy class deterministically through `StubAdapter` +
  `python -m typebench._internal.fake_checker` — no real checker required.
- Round-trip pydantic models through JSON and assert `extra="forbid"`.

## Scope discipline

The adapter Protocol is pinned to its final-ish shape; methods not yet exercised
(`install`, `parallelism_cap`) are deliberately deferred — don't delete them, don't
build behavior behind them early.

## Commits

Conventional Commits, **required scope**, atomic, body explains the *why*. Scopes in
use: `scaffold, contracts, models, taxonomy, config, engine, wrapper, timing, measure,
calibration, env, collector, adapters, corpus, catalog, counting, envman, suite,
preflight, renderer, cli, docs, skills, ci, ruff`. **No AI/assistant attribution** in commit
messages or PR bodies — commits read as the author's own work. See the
`git-conventions` skill.

Before switching branches or merging, check `git status --short`. If unrelated
local edits exist, stash only those paths with a descriptive message, perform the
branch operation, then pop the stash and confirm the restored status.

## Executing tasks with codex

`codex exec` is the per-task executor: hand it ONE well-scoped task (implement a
plan task, or review a diff), then the orchestrating agent reviews the result and
owns the commit. It runs in its own sandbox — treat it as a junior pair, not an
autonomous committer.

**Invocation (implementation task):**

```bash
UV_CACHE_DIR=/tmp/uv-cache codex exec -s workspace-write \
  -c model_reasoning_effort=high "<single-task prompt>" </dev/null
```

**Invocation (review — no writes, faster):** swap `-s workspace-write` for
`-s read-only`. Add `--skip-git-repo-check` if codex balks at the repo state.

**Non-negotiable pitfalls (each one cost real time):**

- **Always redirect `</dev/null`.** `codex exec` reads *additional* prompt input
  from stdin and **hangs forever** ("Reading additional input from stdin...") when
  stdin is an open pipe/tty with no EOF. This is the #1 way a codex run silently
  stalls.
- **Never pipe codex through `| tail`/`| head`.** Those buffer until EOF, so the
  output file stays empty until codex fully exits — indistinguishable from a hang.
  Redirect to a file (`> /tmp/codex.log 2>&1`) and read that, or let the task
  framework capture it.
- **codex cannot `git commit`** — the sandbox mounts `.git` read-only. codex
  implements + (optionally) self-tests; the orchestrating agent runs the full gate
  and commits. Never ask codex to commit.
- **Scope to one task.** A good codex prompt states the task, the relevant context,
  the acceptance check, and "be terse / list only real issues" for reviews. It is
  not a planner — give it the plan.

**The loop:** dispatch codex on the task → read its diff and output → run the full
quality gate yourself (`ruff`, `pyrefly`, `pytest`) → commit. If codex flags an
in-scope problem in its own work, fix before committing; if it raises a real issue
the orchestrator missed (it happens — e.g. an honesty-flag mismatch the human
reviewer passed), treat it as a finding and address it.

## Skills

Engineering skills live under `.agents/skills/`. Invoke the relevant one before working:

- **coding-guidance-python** — every Python edit or review (contracts, typing,
  security, module boundaries).
- **test-driven-development** — failing test first; Prove-It for bugs.
- **code-review-and-quality** — five-axis review before merge.
- **improve-architecture** — architecture-friction review and refactor plan documents.
- **git-conventions** — commit, branch, and PR conventions.

## Ask first

- Changing a quality gate (ruff rule set, pyrefly preset, line length) or relaxing a
  per-file ignore.
- Changing the on-disk schema (`RunResult` fields, taxonomy string values) — it's a
  stability contract.
- Adding a runtime dependency.
- Anything that puts a heavy import on the measured wrapper path.
