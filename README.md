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

The source-of-truth output is a structured JSON record for one `(project x checker
x thread-mode)` run. The curated official store in `data/official/` is rendered
into the README results table below and the GitHub Pages trend site.

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
and charts are views over the recorded measurements. The pipeline is live: the
curated `data/official/` dataset feeds it, and the first official snapshot is
published.

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

Benchmark results across the full corpus, grouped by project size and ordered
smallest to largest. Each table ranks the four checkers fastest-first and shows
how each scales from one pinned core up to the whole machine. All numbers are
measured on a dedicated benchmark machine, never on CI runners; the provenance
line beneath the tables records the exact CPU and run counts.

<!-- TYPEBENCH:BEGIN -->

_Corpus snapshot 2026-06-13 · measured 2026-06-13 12:11 UTC_


### Small projects


#### httpx

7,312 code LOC analyzed across 23 files

| Checker | All-cores | 1c | Peak mem (MB) | kLOC/s |
|------|--:|--:|--:|--:|
| ty@0.0.49 | **0.074** | **0.252** | **103.2** | **99.3** |
| pyrefly@1.0.0 | 0.165 | 0.300 | 123.3 | 44.2 |
| mypy@2.1.0 | 1.057 | 1.301 | 924.5 | 6.9 |
| pyright@1.1.410 | 2.267 | 3.527 | 1987.7 | 3.2 |


#### click

9,373 code LOC analyzed across 17 files

| Checker | All-cores | 1c | Peak mem (MB) | kLOC/s |
|------|--:|--:|--:|--:|
| ty@0.0.49 | **0.083** | **0.242** | **92.4** | **113.6** |
| pyrefly@1.0.0 | 0.170 | 0.325 | 128.9 | 55.2 |
| mypy@2.1.0 | 0.947 | 0.942 | 775.6 | 9.9 |
| pyright@1.1.410 | 2.382 | 4.134 | 1666.0 | 3.9 |


#### anyio

11,445 code LOC analyzed across 42 files

| Checker | All-cores | 1c | Peak mem (MB) | kLOC/s |
|------|--:|--:|--:|--:|
| ty@0.0.49 | **0.111** | **0.324** | **115.2** | **102.7** |
| pyrefly@1.0.0 | 0.188 | 0.364 | 137.5 | 60.8 |
| mypy@2.1.0 | 1.132 | 1.293 | 862.2 | 10.1 |
| pyright@1.1.410 | 2.904 | 4.853 | 2169.7 | 3.9 |


#### jinja2

11,564 code LOC analyzed across 25 files

| Checker | All-cores | 1c | Peak mem (MB) | kLOC/s |
|------|--:|--:|--:|--:|
| ty@0.0.49 | **0.091** | 0.308 | **122.1** | **127.8** |
| pyrefly@1.0.0 | 0.150 | **0.304** | 131.8 | 77.2 |
| mypy@2.1.0 | 1.151 | 1.187 | 822.5 | 10.0 |
| pyright@1.1.410 | 3.184 | 4.340 | 2091.5 | 3.6 |


#### fastapi

18,287 code LOC analyzed across 48 files

| Checker | All-cores | 1c | Peak mem (MB) | kLOC/s |
|------|--:|--:|--:|--:|
| ty@0.0.49 | **0.103** | **0.359** | **135.9** | **178.3** |
| pyrefly@1.0.0 | 0.204 | 0.510 | 192.5 | 89.6 |
| mypy@2.1.0 | 1.263 | 1.853 | 1068.1 | 14.5 |
| pyright@1.1.410 | 4.339 | 6.237 | 2968.9 | 4.2 |


#### trio

18,745 code LOC analyzed across 78 files

| Checker | All-cores | 1c | Peak mem (MB) | kLOC/s |
|------|--:|--:|--:|--:|
| ty@0.0.49 | **0.089** | **0.401** | **125.8** | **209.6** |
| pyrefly@1.0.0 | 0.200 | 0.544 | 170.2 | 93.7 |
| mypy@2.1.0 | 1.235 | 1.425 | 876.1 | 15.2 |
| pyright@1.1.410 | 4.042 | 6.876 | 2915.7 | 4.6 |


### Medium projects


#### pydantic

27,049 code LOC analyzed across 79 files

| Checker | All-cores | 1c | Peak mem (MB) | kLOC/s |
|------|--:|--:|--:|--:|
| pyrefly@1.0.0 | **0.230** | **0.640** | **198.0** | **117.8** |
| ty@0.0.49 | 0.460 | 1.506 | 238.4 | 58.8 |
| mypy@2.1.0 | 1.453 | 1.750 | 896.0 | 18.6 |
| pyright@1.1.410 | 7.700 | 12.627 | 3517.0 | 3.5 |


#### hypothesis

32,072 code LOC analyzed across 102 files

| Checker | All-cores | 1c | Peak mem (MB) | kLOC/s |
|------|--:|--:|--:|--:|
| ty@0.0.49 | **0.162** | 1.024 | **220.8** | **198.3** |
| pyrefly@1.0.0 | 0.256 | **0.990** | 227.6 | 125.3 |
| mypy@2.1.0 | 1.929 | 2.343 | 1042.4 | 16.6 |
| pyright@1.1.410 | 5.776 | 12.232 | 3366.3 | 5.6 |


#### rich

35,544 code LOC analyzed across 100 files

| Checker | All-cores | 1c | Peak mem (MB) | kLOC/s |
|------|--:|--:|--:|--:|
| ty@0.0.49 | **0.088** | **0.445** | **132.9** | **404.8** |
| pyrefly@1.0.0 | 0.206 | 0.642 | 181.2 | 172.7 |
| mypy@2.1.0 | 1.213 | 1.451 | 880.6 | 29.3 |
| pyright@1.1.410 | 4.629 | 9.155 | 3237.8 | 7.7 |


#### pylint

39,792 code LOC analyzed across 178 files

| Checker | All-cores | 1c | Peak mem (MB) | kLOC/s |
|------|--:|--:|--:|--:|
| ty@0.0.49 | **0.173** | **1.040** | **216.7** | **230.2** |
| pyrefly@1.0.0 | 0.263 | 1.089 | 250.1 | 151.5 |
| mypy@2.1.0 | 1.317 | 2.167 | 1046.5 | 30.2 |
| pyright@1.1.410 | 5.693 | 11.439 | 3528.5 | 7.0 |


### Large projects


#### textual

70,948 code LOC analyzed across 247 files

| Checker | All-cores | 1c | Peak mem (MB) | kLOC/s |
|------|--:|--:|--:|--:|
| ty@0.0.49 | **0.246** | **1.487** | **284.8** | —* |
| pyrefly@1.0.0 | 0.302 | 1.584 | 320.2 | —* |
| mypy@2.1.0 | 2.668 | 3.464 | 1344.4 | 26.6 |
| pyright@1.1.410 | 8.092 | 14.793 | 4878.0 | —* |


#### ansible-core

101,119 code LOC analyzed across 575 files

| Checker | All-cores | 1c | Peak mem (MB) | kLOC/s |
|------|--:|--:|--:|--:|
| pyrefly@1.0.0 | **0.427** | **2.645** | **410.1** | **236.9** |
| ty@0.0.49 | 0.489 | 2.716 | 477.7 | 206.8 |
| mypy@2.1.0 | 2.239 | 5.042 | 1514.5 | 45.2 |
| pyright@1.1.410 | 12.472 | 24.715 | 5477.3 | 8.1 |


#### mypy

123,278 code LOC analyzed across 247 files

| Checker | All-cores | 1c | Peak mem (MB) | kLOC/s |
|------|--:|--:|--:|--:|
| ty@0.0.49 | **0.395** | **2.673** | **461.8** | —* |
| pyrefly@1.0.0 | 0.593 | 2.981 | 541.0 | —* |
| mypy@2.1.0 | 1.917 | 4.873 | 1370.2 | —* |
| pyright@1.1.410 | 10.906 | 23.515 | 5383.7 | —* |


#### sqlalchemy

200,180 code LOC analyzed across 256 files

| Checker | All-cores | 1c | Peak mem (MB) | kLOC/s |
|------|--:|--:|--:|--:|
| pyrefly@1.0.0 | **0.405** | **2.422** | **310.2** | **494.1** |
| ty@0.0.49 | 0.556 | 3.578 | 646.4 | 360.3 |
| mypy@2.1.0 | 4.557 | 6.026 | 1603.7 | 43.9 |
| pyright@1.1.410 | 14.301 | 27.839 | 7305.2 | 14.0 |


#### home-assistant

1,058,062 code LOC analyzed across 8,789 files

| Checker | All-cores | 1c | Peak mem (MB) | kLOC/s |
|------|--:|--:|--:|--:|
| ty@0.0.49 | **3.570** | **26.429** | 4200.6 | —* |
| pyrefly@1.0.0 | 4.221 | 27.302 | **4025.7** | —* |
| mypy@2.1.0 | 18.641 | 73.395 | 13583.6 | 56.8 |
| pyright@1.1.410 | 42.341 | 241.502 | 18656.6! | —* |


> Wall is the hyperfine median in seconds, fastest first; the best cell in each metric column is in **bold**. **All-cores** uses the whole machine; the per-core columns are the constrained track, each pinned to the core count in its header. Peak mem and kLOC/s are from the all-cores pass. kLOC/s denominator is the canonical analyzed code-LOC, identical across tools. `—*` = throughput withheld because the tool over-reports its analyzed set vs the canonical denominator. `!` = swap observed during the memory pass, so peak memory may be understated. Checker issue counts are intentionally omitted — they are not comparable across tools and are not a ranking.


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
