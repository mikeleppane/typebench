# typebench

A neutral, reproducible benchmark engine for Python type-checker performance across
**mypy**, **pyright**, **pyrefly**, and **ty**.

typebench measures cold single-shot checker runs on real Python projects. It is
built around one rule: the numbers must be credible enough for checker maintainers,
Python teams, and independent readers to rerun and audit them.

> **Status:** The engine currently supports single-project, single-checker runs and
> corpus preflight checks. Full-suite orchestration, generated result tables, and
> the public trend site are still in development. No official benchmark results are
> published yet.

---

## Contents

- [Why typebench exists](#why-typebench-exists)
- [What it measures](#what-it-measures)
- [Quick start](#quick-start)
- [Requirements](#requirements)
- [Usage](#usage)
- [Architecture](#architecture)
- [Methodology](#methodology)
- [Results](#results)
- [Contributing](#contributing)
- [License](#license)

---

## Why typebench exists

Python has several active static type checkers with different implementation
strategies and different performance profiles. Performance claims are easy to make
and hard to trust unless the workload, environment, and failure handling are
repeatable.

typebench exists to replace anecdote with measurement.

Neutrality is a design requirement:

- No winner framing or tool-favorable defaults.
- Every checker runs through the same benchmark engine.
- Checker-specific behavior is isolated behind adapters.
- Diagnostics are reported as data, not as a soundness ranking.
- Failures are recorded explicitly instead of disappearing from the dataset.

If project text reads as advocacy for one checker, that is a bug.

---

## What it measures

For each run, typebench can record:

- Wall time, delegated to [`hyperfine`](https://github.com/sharkdp/hyperfine).
- Peak cgroup memory on supported Linux systems.
- CPU-time from the same cgroup resource pass.
- Calibration timing from a fixed Python CPU workload.
- Checker result class: clean, diagnostics, environment failure, crash, timeout, or
  OOM.
- Checker-reported file and diagnostic counts when the adapter can parse them.
- Environment details such as OS, kernel, CPU model, core count, and Python version.

Current output is a structured JSON record for one `(project x checker x
thread-mode)` run. Generated result tables and long-running trend data are not
published yet.

---

## Quick start

```bash
git clone <repository-url> typebench
cd typebench
uv sync
```

Run the stub checker to verify the local CLI and JSON output:

```bash
uv run typebench run --tool stub --project demo --output results.json
```

The stub checker does not need a real project, checker binary, corpus clone, or
network access. If `hyperfine` is missing, the command still writes a JSON record
with `timing: null`.

Run the local quality gate before contributing changes:

```bash
uv run ruff format && uv run ruff check && uv run pyrefly check && uv run pytest
```

---

## Requirements

### Python dependencies

typebench is a Python 3.12+ project managed by `uv`.

Runtime dependencies:

- `pydantic>=2.6`
- `typer>=0.12`

Development dependencies are pinned through `uv.lock`.

### Quick start with mise

typebench pins its external CLI toolchain (`hyperfine`, `tokei`, `node`, `uv`)
with [`mise`](https://mise.jdx.dev) so runs are reproducible across machines. New
to mise? Start with the [getting-started guide](https://mise.jdx.dev/getting-started.html).

**1. Install mise** (skip if already installed; full options at the
[mise installation docs](https://mise.jdx.dev/installing-mise.html)):

```bash
curl https://mise.run | sh                       # official installer
echo 'eval "$(mise activate zsh)"' >> ~/.zshrc   # activate (bash: zsh -> bash, ~/.bashrc)
exec $SHELL                                      # reload the shell so shims land on PATH
```

**2. Provision the toolchain and verify:**

```bash
mise install              # installs hyperfine, tokei, node, uv at the pinned versions
uv sync                   # installs the type checkers (mypy/pyright/pyrefly/ty) from uv.lock
uv run typebench doctor   # confirm every external tool resolves at the expected version
```

`mise install` alone is not enough: the pinned `node`/`tokei` only bind once mise
is **activated** (the `mise activate` line above puts the shims on PATH).
`typebench doctor` reports the resolved versions, so a mis-activated shell is
visible immediately.

### External tools

| Tool | Role | Required? | If absent |
|------|------|-----------|-----------|
| `uv` | Project environment and corpus venv creation | Required | Setup or corpus preparation fails. |
| `git` | Clone pinned corpus projects | Required for corpus mode | Corpus preparation fails. |
| `hyperfine` | Wall-time measurement | Required for timing numbers | A record is still written, but timing fields are `null`. |
| cgroup v2 + `systemd-run --user --scope` | Peak memory, CPU-time, OOM detection | Optional, Linux-only | Resource fields are `null`; timing can still run. |
| `taskset` | One-core CPU affinity track | Optional, Linux-only | The record does not claim affinity enforcement. |
| `node` | Runtime required by pyright | Required for pyright only | pyright cannot run; other checkers are unaffected. |

macOS runs are timing-only because cgroup v2 is Linux-specific.

> `mise install` provisions `hyperfine`, `tokei`, `node`, and `uv`; `uv sync`
> provisions the four type checkers. `git` and the cgroup/systemd-run capability
> stay system-provided.

---

## Usage

Two commands are available today:

- `typebench run` measures one checker on one project.
- `typebench preflight` prepares a corpus project and probes selected checkers.

Check the live CLI with:

```bash
uv run typebench run --help
uv run typebench preflight --help
```

### Corpus run

The curated corpus lives in [`corpus/suite.toml`](corpus/suite.toml). Each entry
pins a repository SHA, source roots, Python target, install recipe, and dependency
constraints.

Run one checker against a corpus project:

```bash
uv run typebench run \
  --tool pyright \
  --corpus corpus/suite.toml \
  --corpus-project httpx \
  --thread-mode all-cores \
  --cache-root typebench-cache \
  --output results-httpx-pyright.json
```

In corpus mode, typebench derives source roots, excludes, target Python
version/platform, and venv interpreter from the prepared project.

### Manual project run

For a project outside the curated corpus, provide source roots yourself. Real
checkers require at least one `--src-root`; the stub checker is exempt.

```bash
uv run typebench run \
  --tool mypy \
  --project my-lib \
  --src-root src/my_lib \
  --venv /path/to/project/.venv/bin/python \
  --thread-mode constrained \
  --output results-mylib-mypy.json
```

The `constrained` track defaults to a single core (`--cores 1`); multithreading is
opt-in. Raise it (e.g. `--cores 8`) to pin the checker to N cores and let it use up
to N workers — mypy ≥ 2.0 (`--num-workers`), pyrefly (`--threads`), and ty
(`TY_MAX_PARALLELISM`) all scale; pyright stays effectively single-main-thread.

### Preflight

Preflight checks whether a corpus project is usable by the selected checkers before
it is included in benchmark publication.

```bash
uv run typebench preflight \
  --corpus corpus/suite.toml \
  --project httpx \
  --output preflight-httpx.json
```

By default, preflight probes all four real checkers. Use repeated `--tool` options
to narrow the set.

### Common `run` options

| Option | Default | Meaning |
|--------|---------|---------|
| `--tool` | required | `mypy`, `pyright`, `pyrefly`, `ty`, or `stub`. |
| `--output` | required | JSON output path; parent directory must be writable. |
| `--thread-mode` | `all-cores` | `all-cores` or `constrained`. |
| `--cores` | `1` | Cores for the `constrained` track (default 1 = single-threaded; opt into multithreading with e.g. `--cores 8`). Ignored by `all-cores`. |
| `--runs` | `10` | Hyperfine timed runs. |
| `--warmup` | `3` | Hyperfine warmups. |
| `--mem-runs` | `3` | Resource-pass repeats. |
| `--measure / --no-measure` | `--measure` | Enable cgroup resource measurement when available. |
| `--calibrate / --no-calibrate` | `--calibrate` | Time the calibration workload. |
| `--calib-runs` | `5` | Calibration repeats. |
| `--timeout` | `900.0` | Per-invocation timeout in seconds. |
| `--src-root` | none | Repeatable first-party source root for manual real-checker runs. |
| `--corpus` / `--corpus-project` | none | Use a curated corpus entry. |
| `--cache-root` | `typebench-cache` | Prepared clone/venv cache location. |

### Cache root neutrality

The default cache root is `typebench-cache`, deliberately not a dot-directory.
pyrefly skips dot-directories during file discovery, so a hidden cache could make
the prepared corpus invisible to pyrefly alone. That would produce a recorded
environment failure for one checker while the others ran normally. Keep cache-root
overrides non-hidden.

---

## Architecture

typebench is a small orchestration engine around external measurement tools. The
engine prepares a project, asks one adapter how to invoke a checker, delegates
timing/resource collection, then writes an auditable JSON record.

### System overview

```text
              curated corpus entry or manual CLI args
                              |
                              v
                    normalized project config
                              |
                              v
        +---------------- benchmark engine ----------------+
        |                                                   |
        |  adapter selects checker command and cache policy |
        |  measurement passes collect timing/resources      |
        |  collector writes one JSON record                 |
        |                                                   |
        +------------------------+--------------------------+
                                 |
                                 v
                       results JSON on disk
```

### Checker adapters

Each checker has one adapter. The adapter owns checker-specific details; the
benchmark engine stays neutral.

```text
                         benchmark engine
                               |
         +----------+----------+----------+----------+
         |          |          |          |          |
       mypy      pyright    pyrefly      ty        stub
      adapter    adapter    adapter    adapter    adapter
```

Adapters are responsible for:

- Building the checker command.
- Applying the normalized configuration for that checker.
- Clearing checker caches for cold runs.
- Parsing diagnostic and file counts when available.
- Mapping checker exits into benchmark failure classes.

### Measurement flow

```text
project config
    |
    v
prepare project environment
    |
    v
run checker probe
    |
    +--> resource pass, when available
    |       peak cgroup memory + CPU-time + OOM signal
    |
    +--> timing pass, when hyperfine is available
            wall-time samples
    |
    v
write JSON record
```

The timing and resource passes are separate on purpose. `hyperfine` owns wall
time; cgroup v2 owns peak memory and CPU-time on Linux. typebench coordinates
those tools instead of reimplementing their measurement logic.

### Publishing flow

Single-run JSON is available now. The publishing flow is still in development:

```text
many JSON records
    |
    v
generated result tables
    |
    v
public trend site
```

That separation is important: raw JSON remains the source of truth, while tables
and charts are views over the recorded measurements.

---

## Methodology

### Cold runs

typebench measures cold single-shot runs:

- No checker daemon.
- No checker incremental cache.
- Checker cache clearing happens before each measured run.
- OS page cache may be warm; the benchmark is measuring checker work, not disk
  read latency.

### Failure classes

Every run resolves to one class:

| Class | Meaning | Aggregate treatment |
|-------|---------|---------------------|
| `clean` | Checked successfully with zero diagnostics | Included |
| `diagnostics` | Checked successfully and found diagnostics | Included |
| `failed{env}` | Setup, configuration, import-resolution, or environment failure | Recorded, excluded |
| `failed{crash}` | Panic, internal error, segfault, signal death, or flaky timed-run failure | Recorded, excluded |
| `failed{timeout}` | Exceeded timeout | Recorded, excluded |
| `failed{oom}` | OOM-killed | Recorded, excluded |

Diagnostics are normal checker output, not benchmark failure. Real failures are
recorded explicitly and excluded from aggregate performance tables.

### Timing

Wall time comes from `hyperfine`. typebench uses a lightweight wrapper because type
checkers often exit nonzero when they find diagnostics; that is a successful
measurement, even though `hyperfine` would otherwise treat it as command failure.

### Memory and CPU-time

On supported Linux systems, typebench runs a separate resource pass in a transient
cgroup and reads:

- `memory.peak`
- `memory.stat`
- `cpu.stat`
- `memory.events`

The memory metric is **peak cgroup memory, not RSS**. It includes child processes,
worker threads, page cache, and kernel structures charged to the cgroup. That is
the fair cross-tool measurement because some checkers use subprocesses or worker
threads as part of normal operation.

### Thread modes

typebench supports two tracks:

- `all-cores`: real-world default behavior.
- `constrained`: an N-core CPU affinity floor via `taskset -c 0..N-1` when available,
  where N is `--cores` (default 1). Each adapter's parallelism is also capped to N
  (mypy `--num-workers`, pyrefly `--threads`, ty `TY_MAX_PARALLELISM`; pyright is
  effectively single-main-thread). At the default N=1 this is a single pinned core.

The project does not claim a literal "N threads" mode, because a core-count cap
forces a parallel tool's threads to contend on N cores rather than truly running N
workers — that distinction is documented, not hidden, via the `hard_cap` honesty flag.

### Normalized configuration

Official runs use one normalized input contract:

- Same first-party source roots for each project.
- Same target Python version and platform.
- Vendored, generated, and test files excluded.
- Dependencies installed so imports resolve.
- Diagnostics limited to first-party code where the checker supports that posture.
- All function bodies analyzed; mypy uses `--check-untyped-defs`.
- Stock default rule sets; no plugins in the headline track.

---

## Results

No official results are published yet. Generated tables will appear here once the
publishing workflow is ready.

---

## Contributing

typebench is a measurement tool, so correctness includes methodological honesty.
Avoid changes that make the record claim a methodology the engine did not run.

Run the full gate before considering work complete:

```bash
uv run ruff format && uv run ruff check && uv run pyrefly check && uv run pytest
```

Pre-commit currently enforces `ruff check --fix`, `ruff format`, and
`pyrefly check`. It does not run pytest.

Engineering expectations:

- Use test-driven development for behavior changes.
- Keep every function type-annotated, including tests.
- Keep pyrefly at `preset = "strict"` with zero errors.
- Use `# pyrefly: ignore[<kind>]` only with a specific reason; avoid `# type: ignore`.
- Do not change result fields, failure class names, quality gates, runtime
  dependencies, or measured-path imports without explicit approval.
- Use Conventional Commits with a required scope.

---

## License

Released under the MIT License. See [`LICENSE`](LICENSE).

## Latest results

<!-- TYPEBENCH:BEGIN -->

_Suite `2026-06-10` · generated 2026-06-10T16:13:11.481711+00:00_


#### click — all-cores · all-cores

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| mypy@2.1.0 | clean | 0.948 | 778.6 | 4.791 | 4.91 | 9.9 |


#### click — constrained · cores=1

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| mypy@2.1.0 | clean | 0.998 | 85.0 | 1.007 | 0.98 | 9.4 |


#### httpx — all-cores · all-cores

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| mypy@2.1.0 | diagnostics | 1.156 | 924.8 | 5.525 | 4.67 | 6.3 |


#### httpx — constrained · cores=1

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| mypy@2.1.0 | diagnostics | 1.328 | 105.0 | 1.307 | 0.97 | 5.5 |


> kLOC/s denominator is the canonical analyzed code-LOC (tokei; blanks+comments excluded), identical across tools. `—*` = throughput withheld because the tool over-reports its analyzed set vs the canonical denominator. `!` = swap observed during the memory pass, so peak memory may be understated. Parallel efficiency is cross-pass (cold cgroup CPU-time ÷ warm hyperfine wall). Checker issue counts are intentionally omitted — they are not comparable across tools and are not a ranking.

<!-- TYPEBENCH:END -->
