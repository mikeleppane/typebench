# typebench

[![smoke](https://github.com/mikeleppane/typebench/actions/workflows/smoke.yml/badge.svg)](https://github.com/mikeleppane/typebench/actions/workflows/smoke.yml)
[![release](https://img.shields.io/github/v/tag/mikeleppane/typebench?label=release)](https://github.com/mikeleppane/typebench/releases)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![python](https://img.shields.io/badge/python-3.12%2B-blue)](pyproject.toml)

A neutral, reproducible benchmark engine for Python type-checker performance across
**mypy**, **pyright**, **pyrefly**, and **ty**.

typebench measures cold single-shot checker runs on real Python projects. It is
built around one rule: the numbers must be credible enough for checker maintainers,
Python teams, and independent readers to rerun and audit them.

> **Status:** The engine supports single-project runs, full-suite orchestration
> (`project × checker × thread-mode` matrix), checker comparison, corpus preflight, a
> PR speed-regression GitHub Action, self-contained HTML reports, and a
> render-to-Pages publishing pipeline. The first official benchmark — 14 projects ×
> 4 checkers × 4 thread configs — is published below and on the
> [trend site](https://mikeleppane.github.io/typebench/).

---

## Contents

- [Why typebench exists](#why-typebench-exists)
- [What it measures](#what-it-measures)
- [Quick start](#quick-start)
- [Requirements](#requirements)
- [Usage](#usage)
- [GitHub Action: PR speed regression](#github-action-pr-speed-regression)
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
git clone https://github.com/mikeleppane/typebench
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

### Task shortcuts

Common workflows are wrapped as [mise tasks](https://mise.jdx.dev/tasks/) in
`mise.toml`, so you don't have to retype the `uv run …` invocations (there is no
Makefile — `uv`/`mise` own the toolchain). Trust the config once per clone, then
run any task:

```bash
mise trust          # one-time: approve this repo's mise.toml
mise tasks          # list every task with its description
mise run check      # the full quality gate, in order
```

| Task | Runs |
|------|------|
| `mise run check` | The full gate in order: `ruff format` → `ruff check` → `pyrefly check` → `pytest`. |
| `mise run fmt` | `ruff format`. |
| `mise run lint` | `ruff check` (append `--fix` to autofix). |
| `mise run types` | `pyrefly check` (strict). |
| `mise run test` | The full `pytest` suite. |
| `mise run test-fast` | `pytest` minus `e2e` + `neutrality` — the fast inner loop. |
| `mise run test-e2e` | Only the end-to-end suite. |
| `mise run doctor` | `typebench doctor` — confirm the toolchain resolves. |
| `mise run preflight [project]` | Preflight a corpus project (defaults to `httpx`). |
| `mise run suite` | Run the **full corpus** (every project × all four checkers × both thread tracks) into `results/`. |
| `mise run report` | Build and open a local HTML trend report from `results/`. |
| `mise run render` | Maintainer-only: regenerate the README block + site trends. |
| `mise run clean` | Remove tool caches (`.pytest_cache`, `.ruff_cache`, `__pycache__`). |

Tasks forward extra arguments, so `mise run test tests/engine -k oom` and
`mise run lint --fix` work as expected.

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

| Command | What it does |
|---------|--------------|
| `typebench run` | Measure one checker on one project. |
| `typebench suite` | Run the full `project × checker × thread-mode` matrix and print grouped, fastest-first tables. |
| `typebench compare` | Compare two or more checker specs (`name@version+label`) into one results envelope. |
| `typebench ab` | A/B-measure a candidate checker binary against a baseline (the engine behind the GitHub Action). |
| `typebench preflight` | Prepare a corpus project and probe whether the selected checkers can run it. |
| `typebench doctor` | Verify every external tool resolves at the expected version (`--check` exits nonzero for CI). |
| `typebench report` | Build a self-contained HTML trend report from a local results history. |
| `typebench render` | Maintainer-only: regenerate the README table and `trends.json` from the official store. |
| `typebench config init` / `show` | Scaffold or inspect a `typebench.toml` pinning checker versions. |

Check any command's live options with `--help`:

```bash
uv run typebench run --help
uv run typebench suite --help
uv run typebench doctor --help
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

### Viewing results locally

A `typebench suite` run prints the same grouped, fastest-first tables you see in the
README — one per project / thread-mode / cores — as soon as it finishes, so you get
the numbers on screen without opening the JSON.

For the interactive trend charts without publishing anything, build a self-contained
HTML report from your own results history:

```bash
uv run typebench report --results-dir results --open
```

This folds the site assets and your local trends into a single portable file
(`typebench-report.html` by default) and opens it in your browser. It touches
neither `README.md` nor the published site — those are maintainer-only and handled
by `typebench render` plus CI (see [Publishing flow](#publishing-flow)).

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

## GitHub Action: PR speed regression

`mikeleppane/typebench@v1` A/B-measures your checker's PR build against its latest released
version on projects you choose, and reports a wall-time delta to the PR. It runs on plain
`pull_request` — **never `pull_request_target`** (that would hand a write token to a job that
builds untrusted PR code; see the spec). Fork PRs get a read-only token, so the sticky comment
is skipped and the result stays in the step summary.

Using this action? Add a badge to your README:

[![measured with typebench](https://img.shields.io/badge/measured%20with-typebench-7b3fe4)](https://github.com/mikeleppane/typebench)

```markdown
[![measured with typebench](https://img.shields.io/badge/measured%20with-typebench-7b3fe4)](https://github.com/mikeleppane/typebench)
```

**Rust checker (pyrefly):**

```yaml
on: pull_request
permissions:
  contents: read
  pull-requests: write
jobs:
  bench:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: dtolnay/rust-toolchain@stable
      - run: cargo build --release
      - uses: mikeleppane/typebench@v1
        with:
          checker: pyrefly
          candidate: target/release/pyrefly
          baseline: latest
          targets: |
            ./bench/sample-app
            https://github.com/encode/httpx
          runs: 7
```

**Python checker (mypy):**

```yaml
on: pull_request
permissions:
  contents: read
  pull-requests: write
jobs:
  bench:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - run: uv venv .venv-pr && uv pip install --python .venv-pr .
      - uses: mikeleppane/typebench@v1
        with:
          checker: mypy
          candidate: .venv-pr/bin/mypy
          baseline: latest
          targets: ./src
```

Output: a `Checker | Wall median (s) | Δ wall | runs | spread` table per target, in the run's
step summary, a downloadable `typebench-ab-results` JSON artifact, and (same-repo PRs) a sticky
comment. Memory/throughput are measured locally by the maintainer, not in CI.

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

Raw JSON records feed `typebench render`, which regenerates the README results table
and `trends.json`; CI (`publish.yml`) then deploys the static trend site to GitHub
Pages from the durable store under `data/official/`:

```text
many JSON records
    |
    v
typebench render  ->  README table + trends.json
    |
    v
publish.yml CI  ->  GitHub Pages trend site
```

That separation is important: raw JSON remains the source of truth, while tables
and charts are views over the recorded measurements. The pipeline is wired; what is
still pending is the curated `data/official/` dataset that feeds it.

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

The table below is regenerated by `typebench render` from the latest envelope in
`data/official/`. Numbers are produced on the maintainer's benchmark PC, not CI
runners — see the provenance line under the results table for the exact machine and
run counts.

<!-- TYPEBENCH:BEGIN -->

_Suite `2026-06-10` · generated 2026-06-11T09:10:55.399560+00:00_


#### ansible-core — all-cores · all-cores

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| pyrefly@1.0.0 | diagnostics | 0.644 | 411.9 | 4.454 | 6.60 | 157.1 |
| ty@0.0.48 | diagnostics | 0.671 | 544.3 | 9.266 | 13.20 | 150.7 |
| mypy@2.1.0 | diagnostics | 3.153 | 1506.6 | 15.969 | 5.01 | 32.1 |
| pyright@1.1.410 | diagnostics | 21.844 | 5480.0 | 397.095 | 18.15 | 4.6 |


#### ansible-core — constrained · cores=1

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| pyrefly@1.0.0 | diagnostics | 2.704 | 336.3 | 2.971 | 1.09 | 37.4 |
| ty@0.0.48 | diagnostics | 3.135 | 490.9 | 3.993 | 1.26 | 32.3 |
| mypy@2.1.0 | diagnostics | 4.916 | 254.6 | 5.010 | 1.01 | 20.6 |
| pyright@1.1.410 | diagnostics | 24.059 | 1061.9 | 24.920 | 1.03 | 4.2 |


#### ansible-core — constrained · cores=4

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| pyrefly@1.0.0 | diagnostics | 0.865 | 348.5 | 2.747 | 3.07 | 116.9 |
| ty@0.0.48 | diagnostics | 1.251 | 503.1 | 3.634 | 2.83 | 80.8 |
| mypy@2.1.0 | diagnostics | 2.559 | 534.5 | 6.855 | 2.65 | 39.5 |
| pyright@1.1.410 | diagnostics | 15.149 | 1111.5 | 26.891 | 1.77 | 6.7 |


#### ansible-core — constrained · cores=8

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| pyrefly@1.0.0 | diagnostics | 0.794 | 364.4 | 3.999 | 4.85 | 127.4 |
| ty@0.0.48 | diagnostics | 1.026 | 515.2 | 5.271 | 4.99 | 98.5 |
| mypy@2.1.0 | diagnostics | 2.697 | 795.3 | 8.503 | 3.12 | 37.5 |
| pyright@1.1.410 | diagnostics | 18.341 | 1108.8 | 34.470 | 1.88 | 5.5 |


#### anyio — all-cores · all-cores

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.115 | 120.9 | 0.512 | 3.51 | 99.7 |
| pyrefly@1.0.0 | diagnostics | 0.199 | 137.9 | 0.840 | 3.66 | 57.5 |
| mypy@2.1.0 | diagnostics | 1.541 | 868.0 | 5.726 | 3.64 | 7.4 |
| pyright@1.1.410 | diagnostics | 4.752 | 2112.9 | 43.330 | 9.06 | 2.4 |


#### anyio — constrained · cores=1

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.349 | 75.1 | 0.515 | 1.36 | 32.8 |
| pyrefly@1.0.0 | diagnostics | 0.376 | 95.2 | 0.450 | 1.10 | 30.4 |
| mypy@2.1.0 | diagnostics | 1.317 | 97.4 | 1.333 | 0.99 | 8.7 |
| pyright@1.1.410 | diagnostics | 5.940 | 251.2 | 7.297 | 1.22 | 1.9 |


#### anyio — constrained · cores=4

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.160 | 87.7 | 0.560 | 2.92 | 71.3 |
| pyrefly@1.0.0 | diagnostics | 0.307 | 106.8 | 0.433 | 1.28 | 37.2 |
| mypy@2.1.0 | diagnostics | 1.069 | 274.9 | 1.918 | 1.74 | 10.7 |
| pyright@1.1.410 | diagnostics | 2.916 | 254.2 | 5.974 | 2.03 | 3.9 |


#### anyio — constrained · cores=8

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.142 | 96.8 | 0.670 | 3.88 | 80.7 |
| pyrefly@1.0.0 | diagnostics | 0.299 | 117.9 | 0.465 | 1.41 | 38.2 |
| mypy@2.1.0 | diagnostics | 1.017 | 434.4 | 2.646 | 2.53 | 11.3 |
| pyright@1.1.410 | diagnostics | 2.880 | 257.6 | 5.305 | 1.82 | 4.0 |


#### click — all-cores · all-cores

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.084 | 93.7 | 0.516 | 4.50 | 111.9 |
| pyrefly@1.0.0 | diagnostics | 0.216 | 128.5 | 0.715 | 2.89 | 43.4 |
| mypy@2.1.0 | clean | 1.213 | 765.7 | 6.282 | 5.05 | 7.7 |
| pyright@1.1.410 | diagnostics | 3.156 | 1638.8 | 57.098 | 17.91 | 3.0 |


#### click — constrained · cores=1

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.251 | 59.9 | 0.400 | 1.42 | 37.4 |
| pyrefly@1.0.0 | diagnostics | 0.332 | 86.5 | 0.369 | 1.02 | 28.2 |
| mypy@2.1.0 | clean | 0.961 | 85.0 | 0.997 | 1.00 | 9.7 |
| pyright@1.1.410 | diagnostics | 4.753 | 229.2 | 4.261 | 0.89 | 2.0 |


#### click — constrained · cores=4

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.088 | 69.0 | 0.433 | 3.64 | 106.5 |
| pyrefly@1.0.0 | diagnostics | 0.196 | 100.9 | 0.553 | 2.44 | 47.9 |
| mypy@2.1.0 | clean | 1.119 | 248.4 | 2.111 | 1.83 | 8.4 |
| pyright@1.1.410 | diagnostics | 2.050 | 227.2 | 4.538 | 2.18 | 4.6 |


#### click — constrained · cores=8

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.120 | 78.7 | 0.330 | 2.18 | 77.9 |
| pyrefly@1.0.0 | diagnostics | 0.217 | 110.0 | 0.425 | 1.71 | 43.2 |
| mypy@2.1.0 | clean | 1.084 | 385.9 | 2.738 | 2.46 | 8.6 |
| pyright@1.1.410 | diagnostics | 1.838 | 230.6 | 4.118 | 2.20 | 5.1 |


#### fastapi — all-cores · all-cores

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.109 | 145.9 | 0.770 | 5.52 | 168.5 |
| pyrefly@1.0.0 | diagnostics | 0.303 | 192.6 | 1.031 | 3.08 | 60.3 |
| mypy@2.1.0 | diagnostics | 1.702 | 1072.4 | 6.626 | 3.82 | 10.7 |
| pyright@1.1.410 | diagnostics | 6.700 | 2970.1 | 135.589 | 20.14 | 2.7 |


#### fastapi — constrained · cores=1

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.395 | 101.7 | 0.607 | 1.42 | 46.3 |
| pyrefly@1.0.0 | diagnostics | 0.509 | 145.5 | 0.543 | 1.01 | 35.9 |
| mypy@2.1.0 | diagnostics | 1.973 | 132.9 | 1.927 | 0.96 | 9.3 |
| pyright@1.1.410 | diagnostics | 6.233 | 317.9 | 6.525 | 1.04 | 2.9 |


#### fastapi — constrained · cores=4

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.137 | 112.3 | 0.455 | 2.71 | 133.6 |
| pyrefly@1.0.0 | diagnostics | 0.269 | 160.1 | 0.585 | 1.95 | 68.0 |
| mypy@2.1.0 | diagnostics | 1.227 | 357.5 | 2.589 | 2.06 | 14.9 |
| pyright@1.1.410 | diagnostics | 2.634 | 338.7 | 6.219 | 2.33 | 6.9 |


#### fastapi — constrained · cores=8

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.162 | 123.0 | 0.480 | 2.49 | 113.1 |
| pyrefly@1.0.0 | diagnostics | 0.248 | 169.8 | 0.624 | 2.24 | 73.8 |
| mypy@2.1.0 | diagnostics | 1.296 | 554.1 | 3.860 | 2.91 | 14.1 |
| pyright@1.1.410 | diagnostics | 3.227 | 319.4 | 7.484 | 2.30 | 5.7 |


#### httpx — all-cores · all-cores

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.072 | 103.4 | 0.565 | 5.49 | 101.6 |
| pyrefly@1.0.0 | diagnostics | 0.167 | 124.0 | 0.671 | 3.39 | 43.9 |
| mypy@2.1.0 | diagnostics | 1.261 | 922.1 | 7.105 | 5.50 | 5.8 |
| pyright@1.1.410 | diagnostics | 4.174 | 1967.8 | 35.060 | 8.34 | 1.8 |


#### httpx — constrained · cores=1

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.233 | 59.4 | 0.367 | 1.39 | 31.4 |
| pyrefly@1.0.0 | diagnostics | 0.299 | 86.5 | 0.479 | 1.45 | 24.4 |
| mypy@2.1.0 | diagnostics | 1.290 | 105.1 | 1.342 | 1.02 | 5.7 |
| pyright@1.1.410 | diagnostics | 3.590 | 214.4 | 3.909 | 1.08 | 2.0 |


#### httpx — constrained · cores=4

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.121 | 72.0 | 0.286 | 1.88 | 60.4 |
| pyrefly@1.0.0 | diagnostics | 0.190 | 96.7 | 0.507 | 2.30 | 38.5 |
| mypy@2.1.0 | diagnostics | 0.885 | 289.5 | 1.847 | 2.02 | 8.3 |
| pyright@1.1.410 | diagnostics | 1.710 | 216.7 | 3.905 | 2.24 | 4.3 |


#### httpx — constrained · cores=8

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.112 | 82.1 | 0.317 | 2.22 | 65.1 |
| pyrefly@1.0.0 | diagnostics | 0.249 | 105.5 | 0.400 | 1.43 | 29.3 |
| mypy@2.1.0 | diagnostics | 1.180 | 459.1 | 2.631 | 2.17 | 6.2 |
| pyright@1.1.410 | diagnostics | 2.096 | 220.2 | 3.466 | 1.63 | 3.5 |


#### hypothesis — all-cores · all-cores

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| pyrefly@1.0.0 | diagnostics | 0.341 | 227.0 | 1.561 | 4.20 | 94.1 |
| ty@0.0.48 | diagnostics | 0.429 | 251.4 | 4.082 | 8.87 | 74.7 |
| mypy@2.1.0 | diagnostics | 2.778 | 1046.1 | 9.227 | 3.29 | 11.5 |
| pyright@1.1.410 | diagnostics | 11.820 | 3333.7 | 223.016 | 18.82 | 2.7 |


#### hypothesis — constrained · cores=1

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| pyrefly@1.0.0 | diagnostics | 1.216 | 161.1 | 1.299 | 1.04 | 26.4 |
| ty@0.0.48 | diagnostics | 1.271 | 203.3 | 1.521 | 1.17 | 25.2 |
| mypy@2.1.0 | diagnostics | 2.409 | 142.9 | 2.427 | 0.99 | 13.3 |
| pyright@1.1.410 | diagnostics | 12.736 | 481.7 | 15.484 | 1.21 | 2.5 |


#### hypothesis — constrained · cores=4

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| pyrefly@1.0.0 | diagnostics | 0.379 | 176.3 | 1.534 | 3.74 | 84.6 |
| ty@0.0.48 | diagnostics | 0.529 | 212.2 | 1.842 | 3.29 | 60.6 |
| mypy@2.1.0 | diagnostics | 1.876 | 360.2 | 2.934 | 1.54 | 17.1 |
| pyright@1.1.410 | diagnostics | 7.449 | 497.4 | 15.640 | 2.09 | 4.3 |


#### hypothesis — constrained · cores=8

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| pyrefly@1.0.0 | diagnostics | 0.375 | 193.5 | 1.617 | 3.98 | 85.5 |
| ty@0.0.48 | diagnostics | 0.559 | 222.9 | 2.101 | 3.56 | 57.4 |
| mypy@2.1.0 | diagnostics | 2.570 | 552.9 | 4.336 | 1.67 | 12.5 |
| pyright@1.1.410 | diagnostics | 7.180 | 503.0 | 16.053 | 2.23 | 4.5 |


#### jinja2 — all-cores · all-cores

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.158 | 123.9 | 0.591 | 3.14 | 73.4 |
| pyrefly@1.0.0 | diagnostics | 0.173 | 132.7 | 0.655 | 3.21 | 66.8 |
| mypy@2.1.0 | clean | 1.451 | 820.6 | 5.916 | 3.99 | 8.0 |
| pyright@1.1.410 | diagnostics | 5.418 | 2118.2 | 77.821 | 14.28 | 2.1 |


#### jinja2 — constrained · cores=1

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| pyrefly@1.0.0 | diagnostics | 0.358 | 88.4 | 0.475 | 1.22 | 32.3 |
| ty@0.0.48 | diagnostics | 0.378 | 78.3 | 0.575 | 1.41 | 30.6 |
| mypy@2.1.0 | clean | 1.205 | 101.5 | 1.218 | 0.98 | 9.6 |
| pyright@1.1.410 | diagnostics | 5.117 | 243.6 | 5.231 | 1.02 | 2.3 |


#### jinja2 — constrained · cores=4

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.132 | 89.0 | 0.441 | 2.71 | 87.7 |
| pyrefly@1.0.0 | diagnostics | 0.203 | 102.2 | 0.424 | 1.81 | 57.0 |
| mypy@2.1.0 | clean | 1.037 | 275.8 | 1.752 | 1.64 | 11.1 |
| pyright@1.1.410 | diagnostics | 2.220 | 250.2 | 5.055 | 2.25 | 5.2 |


#### jinja2 — constrained · cores=8

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.158 | 101.2 | 0.479 | 2.53 | 73.1 |
| pyrefly@1.0.0 | diagnostics | 0.178 | 111.6 | 0.677 | 3.23 | 64.8 |
| mypy@2.1.0 | clean | 1.105 | 428.1 | 2.348 | 2.07 | 10.5 |
| pyright@1.1.410 | diagnostics | 2.635 | 249.4 | 6.467 | 2.43 | 4.4 |


#### mypy — all-cores · all-cores

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.970 | 584.7 | 8.117 | 8.11 | —* |
| pyrefly@1.0.0 | diagnostics | 1.120 | 542.1 | 10.240 | 8.90 | —* |
| mypy@2.1.0 | diagnostics | 3.216 | 1374.4 | 20.780 | 6.40 | —* |
| pyright@1.1.410 | diagnostics | 21.268 | 5391.0 | 479.757 | 22.53 | —* |


#### mypy — constrained · cores=1

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 4.077 | 518.0 | 3.239 | 0.79 | —* |
| pyrefly@1.0.0 | diagnostics | 4.111 | 387.6 | 4.596 | 1.11 | —* |
| mypy@2.1.0 | diagnostics | 6.325 | 267.7 | 6.428 | 1.01 | —* |
| pyright@1.1.410 | diagnostics | 30.394 | 1132.2 | 29.981 | 0.99 | —* |


#### mypy — constrained · cores=4

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| pyrefly@1.0.0 | diagnostics | 1.146 | 417.0 | 3.698 | 3.14 | —* |
| ty@0.0.48 | diagnostics | 1.455 | 535.2 | 3.968 | 2.67 | —* |
| mypy@2.1.0 | diagnostics | 2.888 | 480.1 | 8.099 | 2.77 | —* |
| pyright@1.1.410 | diagnostics | 18.282 | 1138.2 | 30.765 | 1.68 | —* |


#### mypy — constrained · cores=8

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| pyrefly@1.0.0 | diagnostics | 0.843 | 457.1 | 4.720 | 5.40 | —* |
| ty@0.0.48 | diagnostics | 1.214 | 546.0 | 7.376 | 5.93 | —* |
| mypy@2.1.0 | diagnostics | 2.868 | 724.2 | 12.363 | 4.27 | —* |
| pyright@1.1.410 | diagnostics | 18.545 | 1129.6 | 30.780 | 1.66 | —* |


#### pydantic — all-cores · all-cores

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| pyrefly@1.0.0 | diagnostics | 0.565 | 195.8 | 3.460 | 5.81 | 47.9 |
| ty@0.0.48 | diagnostics | 0.597 | 243.7 | 2.916 | 4.65 | 45.3 |
| mypy@2.1.0 | diagnostics | 1.997 | 897.1 | 7.760 | 3.83 | 13.5 |
| pyright@1.1.410 | diagnostics | 15.167 | 3497.0 | 226.118 | 14.88 | 1.8 |


#### pydantic — constrained · cores=1

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| pyrefly@1.0.0 | diagnostics | 0.662 | 138.9 | 0.926 | 1.34 | 40.9 |
| ty@0.0.48 | diagnostics | 1.756 | 163.1 | 1.590 | 0.89 | 15.4 |
| mypy@2.1.0 | diagnostics | 1.804 | 115.1 | 2.125 | 1.16 | 15.0 |
| pyright@1.1.410 | diagnostics | 13.566 | 373.8 | 16.615 | 1.22 | 2.0 |


#### pydantic — constrained · cores=4

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| pyrefly@1.0.0 | diagnostics | 0.295 | 154.5 | 1.033 | 3.17 | 91.6 |
| ty@0.0.48 | diagnostics | 0.844 | 193.7 | 2.262 | 2.59 | 32.1 |
| mypy@2.1.0 | diagnostics | 1.401 | 314.5 | 2.317 | 1.62 | 19.3 |
| pyright@1.1.410 | diagnostics | 8.557 | 408.7 | 13.091 | 1.52 | 3.2 |


#### pydantic — constrained · cores=8

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| pyrefly@1.0.0 | diagnostics | 0.291 | 169.3 | 1.128 | 3.50 | 92.9 |
| ty@0.0.48 | diagnostics | 0.609 | 212.0 | 2.428 | 3.79 | 44.4 |
| mypy@2.1.0 | diagnostics | 1.923 | 470.8 | 3.299 | 1.69 | 14.1 |
| pyright@1.1.410 | diagnostics | 9.574 | 429.0 | 15.507 | 1.61 | 2.8 |


#### pylint — all-cores · all-cores

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| pyrefly@1.0.0 | diagnostics | 0.433 | 249.2 | 1.752 | 3.77 | 91.8 |
| ty@0.0.48 | diagnostics | 0.514 | 244.3 | 5.123 | 9.40 | 77.4 |
| mypy@2.1.0 | diagnostics | 2.499 | 1049.7 | 31.470 | 12.44 | 15.9 |
| pyright@1.1.410 | diagnostics | 12.423 | 3577.1 | 213.855 | 17.17 | 3.2 |


#### pylint — constrained · cores=1

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| pyrefly@1.0.0 | diagnostics | 1.311 | 185.1 | 1.500 | 1.12 | 30.4 |
| ty@0.0.48 | diagnostics | 1.498 | 197.0 | 1.568 | 1.03 | 26.6 |
| mypy@2.1.0 | diagnostics | 2.297 | 144.0 | 2.938 | 1.26 | 17.3 |
| pyright@1.1.410 | diagnostics | 14.874 | 502.2 | 14.524 | 0.97 | 2.7 |


#### pylint — constrained · cores=4

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| pyrefly@1.0.0 | diagnostics | 0.575 | 202.6 | 1.520 | 2.51 | 69.2 |
| ty@0.0.48 | diagnostics | 0.640 | 205.5 | 1.873 | 2.79 | 62.2 |
| mypy@2.1.0 | diagnostics | 1.370 | 342.6 | 2.992 | 2.14 | 29.0 |
| pyright@1.1.410 | diagnostics | 7.575 | 511.2 | 15.329 | 2.02 | 5.3 |


#### pylint — constrained · cores=8

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.429 | 217.2 | 2.817 | 6.13 | 92.8 |
| pyrefly@1.0.0 | diagnostics | 0.487 | 218.9 | 1.787 | 3.45 | 81.7 |
| mypy@2.1.0 | diagnostics | 1.455 | 528.5 | 4.604 | 3.10 | 27.4 |
| pyright@1.1.410 | diagnostics | 7.562 | 513.0 | 15.559 | 2.05 | 5.3 |


#### rich — all-cores · all-cores

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.182 | 146.5 | 1.141 | 5.35 | 194.9 |
| pyrefly@1.0.0 | diagnostics | 0.305 | 182.6 | 1.420 | 4.22 | 116.5 |
| mypy@2.1.0 | diagnostics | 1.761 | 883.2 | 8.371 | 4.67 | 20.2 |
| pyright@1.1.410 | diagnostics | 9.011 | 3247.3 | 189.160 | 20.92 | 3.9 |


#### rich — constrained · cores=1

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.613 | 103.1 | 0.698 | 1.08 | 58.0 |
| pyrefly@1.0.0 | diagnostics | 0.690 | 127.1 | 1.005 | 1.39 | 51.5 |
| mypy@2.1.0 | diagnostics | 1.530 | 104.4 | 1.867 | 1.20 | 23.2 |
| pyright@1.1.410 | diagnostics | 10.778 | 395.9 | 10.360 | 0.96 | 3.3 |


#### rich — constrained · cores=4

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.230 | 110.1 | 0.749 | 2.87 | 154.4 |
| pyrefly@1.0.0 | diagnostics | 0.422 | 141.9 | 0.736 | 1.63 | 84.3 |
| mypy@2.1.0 | diagnostics | 1.155 | 289.7 | 2.130 | 1.80 | 30.8 |
| pyright@1.1.410 | diagnostics | 6.272 | 396.7 | 10.311 | 1.64 | 5.7 |


#### rich — constrained · cores=8

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.182 | 119.5 | 0.842 | 3.96 | 195.6 |
| pyrefly@1.0.0 | diagnostics | 0.345 | 154.5 | 0.936 | 2.49 | 103.0 |
| mypy@2.1.0 | diagnostics | 1.613 | 454.2 | 2.824 | 1.72 | 22.0 |
| pyright@1.1.410 | diagnostics | 6.077 | 403.6 | 12.380 | 2.03 | 5.8 |


#### sqlalchemy — all-cores · all-cores

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.636 | 647.9 | 7.537 | 11.30 | 314.8 |
| pyrefly@1.0.0 | diagnostics | 0.682 | 315.7 | 5.681 | 7.97 | 293.5 |
| mypy@2.1.0 | diagnostics | 6.341 | 1609.7 | 17.675 | 2.77 | 31.6 |
| pyright@1.1.410 | diagnostics | 31.434 | 7359.1 | 567.415 | 18.03 | 6.4 |


#### sqlalchemy — constrained · cores=1

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| pyrefly@1.0.0 | diagnostics | 2.507 | 203.0 | 2.556 | 1.01 | 79.9 |
| ty@0.0.48 | diagnostics | 3.878 | 588.7 | 4.680 | 1.20 | 51.6 |
| mypy@2.1.0 | diagnostics | 7.147 | 277.0 | 7.865 | 1.10 | 28.0 |
| pyright@1.1.410 | diagnostics | 30.597 | 1186.8 | 36.502 | 1.19 | 6.5 |


#### sqlalchemy — constrained · cores=4

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| pyrefly@1.0.0 | diagnostics | 0.847 | 208.2 | 2.566 | 2.92 | 236.4 |
| ty@0.0.48 | diagnostics | 1.347 | 597.2 | 4.200 | 3.05 | 148.6 |
| mypy@2.1.0 | diagnostics | 4.946 | 597.0 | 7.290 | 1.46 | 40.5 |
| pyright@1.1.410 | diagnostics | 21.866 | 1221.3 | 33.551 | 1.53 | 9.2 |


#### sqlalchemy — constrained · cores=8

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| pyrefly@1.0.0 | diagnostics | 0.653 | 255.0 | 2.885 | 4.22 | 306.6 |
| ty@0.0.48 | diagnostics | 0.912 | 616.6 | 6.324 | 6.71 | 219.5 |
| mypy@2.1.0 | diagnostics | 6.123 | 872.5 | 8.843 | 1.44 | 32.7 |
| pyright@1.1.410 | diagnostics | 19.341 | 1221.6 | 29.420 | 1.52 | 10.4 |


#### textual — all-cores · all-cores

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| pyrefly@1.0.0 | diagnostics | 0.452 | 319.6 | 3.323 | 6.88 | —* |
| ty@0.0.48 | diagnostics | 0.857 | 352.4 | 6.783 | 7.64 | —* |
| mypy@2.1.0 | diagnostics | 3.066 | 1349.0 | 10.580 | 3.42 | 23.1 |
| pyright@1.1.410 | diagnostics | 13.965 | 4863.6 | 293.590 | 20.98 | —* |


#### textual — constrained · cores=1

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| pyrefly@1.0.0 | diagnostics | 1.469 | 245.8 | 1.506 | 1.00 | —* |
| ty@0.0.48 | diagnostics | 1.781 | 305.0 | 2.149 | 1.19 | —* |
| mypy@2.1.0 | diagnostics | 3.592 | 198.1 | 3.615 | 1.00 | 19.7 |
| pyright@1.1.410 | diagnostics | 14.763 | 726.2 | 14.936 | 1.01 | —* |


#### textual — constrained · cores=4

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| pyrefly@1.0.0 | diagnostics | 0.670 | 267.1 | 1.998 | 2.85 | —* |
| ty@0.0.48 | diagnostics | 0.676 | 313.9 | 2.631 | 3.72 | —* |
| mypy@2.1.0 | diagnostics | 2.726 | 465.2 | 5.031 | 1.82 | 26.0 |
| pyright@1.1.410 | diagnostics | 8.438 | 728.3 | 15.840 | 1.87 | —* |


#### textual — constrained · cores=8

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.530 | 326.0 | 3.277 | 5.84 | —* |
| pyrefly@1.0.0 | diagnostics | 0.636 | 281.7 | 2.559 | 3.84 | —* |
| mypy@2.1.0 | diagnostics | 2.551 | 692.7 | 5.440 | 2.11 | 27.8 |
| pyright@1.1.410 | diagnostics | 8.420 | 738.1 | 16.551 | 1.96 | —* |


#### trio — all-cores · all-cores

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.107 | 138.4 | 1.077 | 7.82 | 175.7 |
| pyrefly@1.0.0 | diagnostics | 0.299 | 172.6 | 1.283 | 3.89 | 62.8 |
| mypy@2.1.0 | diagnostics | 1.768 | 871.1 | 6.921 | 3.85 | 10.6 |
| pyright@1.1.410 | diagnostics | 7.511 | 2892.8 | 152.329 | 20.20 | 2.5 |


#### trio — constrained · cores=1

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.521 | 95.5 | 0.684 | 1.24 | 36.0 |
| pyrefly@1.0.0 | diagnostics | 0.634 | 126.7 | 0.733 | 1.10 | 29.6 |
| mypy@2.1.0 | diagnostics | 1.602 | 105.8 | 1.555 | 0.95 | 11.7 |
| pyright@1.1.410 | diagnostics | 7.155 | 321.1 | 8.007 | 1.11 | 2.6 |


#### trio — constrained · cores=4

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.241 | 103.4 | 0.743 | 2.73 | 77.7 |
| pyrefly@1.0.0 | diagnostics | 0.322 | 135.7 | 0.885 | 2.51 | 58.2 |
| mypy@2.1.0 | diagnostics | 1.397 | 297.3 | 2.229 | 1.56 | 13.4 |
| pyright@1.1.410 | diagnostics | 4.087 | 341.3 | 6.954 | 1.69 | 4.6 |


#### trio — constrained · cores=8

| Checker | Result | Wall median (s) | Peak cgroup mem (MB) | CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |
|------|--------|-----------------|----------------------|--------------|----------------------------|---------------|
| ty@0.0.48 | diagnostics | 0.166 | 112.9 | 0.809 | 4.10 | 112.7 |
| pyrefly@1.0.0 | diagnostics | 0.323 | 147.1 | 0.972 | 2.75 | 58.0 |
| mypy@2.1.0 | diagnostics | 1.346 | 457.6 | 3.449 | 2.51 | 13.9 |
| pyright@1.1.410 | diagnostics | 3.770 | 330.6 | 9.820 | 2.58 | 5.0 |


> kLOC/s denominator is the canonical analyzed code-LOC (tokei; blanks+comments excluded), identical across tools. `—*` = throughput withheld because the tool over-reports its analyzed set vs the canonical denominator. `!` = swap observed during the memory pass, so peak memory may be understated. Parallel efficiency is cross-pass (cold cgroup CPU-time ÷ warm hyperfine wall). Checker issue counts are intentionally omitted — they are not comparable across tools and are not a ranking.


> Measured on 13th Gen Intel(R) Core(TM) i7-13850HX (20 cores), Linux 6.18.33.1-microsoft-standard-WSL2; 10 timed runs, 3 warmup. Absolute times are machine-specific — compare rows within the same suite, or the normalized trend lines on the site; do not compare raw seconds across machines.

<!-- TYPEBENCH:END -->

---

## Contributing

typebench is a measurement tool, so correctness includes methodological honesty.
Avoid changes that make the record claim a methodology the engine did not run.

Run the full gate before considering work complete — `mise run check` is the
shortcut for it (see [Task shortcuts](#task-shortcuts)):

```bash
mise run check
# equivalently:
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
