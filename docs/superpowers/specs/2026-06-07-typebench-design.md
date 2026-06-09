# typebench — Design Spec

**Date:** 2026-06-07
**Last revised:** 2026-06-07 (methodology hardening after two-reviewer pass)
**Status:** Approved (brainstorm) + **all four gating contracts LOCKED** — (1) Normalized Config Contract §6, (2) Failure Taxonomy §7, (3) Thread/core semantics §5.3, (4) Memory measurement protocol §5.5. **Ready for implementation planning.**
**One-liner:** A neutral, reproducible benchmark suite measuring Python type-checker performance (execution time, memory, throughput) across a curated corpus of real-world projects.

---

## 1. Purpose & Goals

Measure and compare the performance of Python type checkers — **mypy, pyright, pyrefly, ty** — on real, large Python codebases, and publish defensible, reproducible results over time.

### Primary goal: neutral, rigorous benchmark
Credibility-first. Methodology that the mypy, Microsoft (pyright), and Astral (ty) teams — and anyone else — cannot dismiss. Designed to be **cited and re-run by anyone**.

### Hard principle: NOT pyrefly-vs-the-world
pyrefly is **one entrant treated identically** to the others. This is non-negotiable and shapes the whole design:
- No "winner" framing, no editorializing, no pyrefly-favorable defaults.
- The normalized config must be equally fair/unfair to all entrants.
- Neutral presentation — results sorted by the measured metric or alphabetically; sorting by speed is *ranking by the measured quantity*, not editorializing (see §11 on this distinction).
- Project lives as its own neutral project, **not** under pyrefly branding.
- Adding a 5th checker must be trivial; no entrant is privileged in the architecture.

### Non-goals (v1)
- Not a correctness/soundness comparison. Diagnostic *counts* are reported as a data point, never as a ranking.
- Not warm/incremental/daemon benchmarking (deferred to a later milestone).
- Not a hosted service; results are static artifacts in a git repo + GitHub Pages.

---

## 2. Key Decisions (locked during brainstorm + revised post-review)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Primary goal | Neutral rigorous benchmark, credibility-first |
| 2 | Config fairness | **Normalized config** is the official/headline default; per-tool & per-project **overrides supported** for exploration. Official numbers always use the documented normalized config. The config is a **locked contract** (§6), not an implementation detail. |
| 3 | Execution environment | **GitHub-hosted runners + statistical defense** (many iterations, median/stddev, variance bars, "indicative ±X%" caveat) **+ per-run calibration baseline** (§5.7) so cross-time trends survive VM-to-VM hardware variance. **Local runs are a first-class requirement** — same harness/commands. |
| 4 | Harness language | **Python orchestrator** + `uv` for env management + `hyperfine` (timing) + cgroup v2 (memory & CPU-time). Rust adds friction, zero accuracy benefit — measurement is delegated. |
| 5 | Project shape | **Generic engine + curated suite as data.** `benchmark run <repo>` works on any repo; the curated suite is just a config file the engine consumes. |
| 6 | Threading | **Two tracks:** (a) **1-core constrained** (CPU affinity to one core + best-effort per-tool parallelism cap) for an algorithmic floor, and (b) **all-cores** (real-world UX). See §5.3 — a literal uniform "1 thread" is *not* achievable across all four tools and is not claimed. Plus CPU-time vs wall-time for parallel efficiency. |
| 7 | Execution mode | **Cold single-shot only (v1).** Cold = no checker cache/daemon; OS page cache is deliberately warm (§5.2). Warm/incremental deferred. |
| 8a | Corpus size spread | **Size buckets** — small (~10k), medium (~50k), large (>100k), giant (>500k LOC). Reveals scaling curves. |
| 8b | Corpus pinning | **Pin corpus to commit SHAs at the latest release tag** (HEAD only as a fallback for tagless projects), bumped monthly **via a PR-gated job** (preflight + LOC/dep diff + rendered annotation, never a blind commit — §4 bump, §11). **Checkers always float to latest**, but every run writes a full lock manifest (§9). Weekly checker runs = checker deltas; monthly corpus bumps = annotated chart markers. |
| 9 | Results storage | **JSON-in-git** (source of truth) → **auto-generated README** + **GitHub Pages** trend charts. README/Pages are *rendered views*, never hand-edited. |
| — | Cadence | **Weekly** checker runs + **monthly** corpus PR-gated bump. |
| — | Engine architecture | **#1 Adapter-driven engine** — neutral core + one declarative adapter per checker. |

---

## 3. Architecture

Neutral core engine + pluggable checker adapters + declarative corpus. Python, `uv`-managed. Measurement delegated to `hyperfine` (timing) and cgroup v2 (peak memory + CPU-time). Results are versioned JSON; README + GH Pages are rendered views.

```
suite.toml (corpus: pinned SHAs, buckets, deps, python version)
        │
        ▼
  ┌──────────────────── engine ────────────────────┐
  │ preflight → prepare → measure → collect → render │
  └─────────────────────────────────────────────────┘
     │           │          │            │        │
  adapters   hyperfine   cgroup v2    calibration results.json
 (mypy/pyright/ (timing)  (peak mem +  (normalize    + lock-manifest
  pyrefly/ty)            cpu-time)    trends)   → README + Pages
```

### Why adapter-driven
Each checker's specifics live in exactly one file. Adding a 5th checker = drop in one adapter, touch nothing else. Neutral by construction — the core engine knows nothing tool-specific.

---

## 4. Components

- **`adapters/`** — one module per checker. This is the **only** checker-specific code. Interface:
  - `install(version) -> resolved_version` — and **verify the expected distribution** (e.g. assert mypy is the mypyc-compiled wheel, not pure-Python; assert pyright's pinned Node; record install source). A wrong build silently distorts results.
  - `version() -> str`
  - `command(project, config, threads_mode) -> argv | (argv, env)` — invocation under the normalized config (§6) and a thread mode (§5.3). May return environment variables (e.g. `TY_MAX_PARALLELISM`) as well as argv.
  - `parallelism_cap(threads_mode) -> {mechanism, hard_cap: bool}` — declares *how* this tool is constrained in the 1-core track and whether the cap is a true worker cap or best-effort. Drives the honesty labelling in §5.3.
  - `clear_cache()` — per-tool cache kill, verified per adapter: mypy `--no-incremental` / wipe `.mypy_cache` / `--cache-dir`; pyright stateless; pyrefly stateless (cache only in `--watch`); ty stateless (incrementality only in `--watch`).
  - `parse(stdout, stderr, exit_code) -> {diagnostics: int, files: int}` — **prefer machine-readable output** to survive floating-checker format drift: pyright `--outputjson`, pyrefly `--output-format=json`, ty JSON output; mypy has no clean JSON summary → regex on stderr **guarded by version**. Must assert sanity (e.g. files > 0 on a successful check) and fail loudly rather than record garbage.
  - `classify_exit(exit_code, signals, timed_out, oom) -> ResultClass` — maps the real exit code to the failure taxonomy (§7). Each adapter owns its exit-code map (pyrefly: `0` clean / `1` diagnostics / `3` env / `101` panic; mypy: `0` / `1` / `2`; pyright/ty per their docs).
- **`corpus`** — `suite.toml`: each project = `name`, repo URL, **pinned SHA**, `size_bucket`, **pinned Python version**, **dependency lock** (uv lockfile or hashed resolution — §9), extras/optional groups, native/system-package prerequisites, and optional per-project config overrides. Corpus health is a **preflight gate** (§12), not best-effort.
- **`bump` job** — separate scheduled job that proposes new SHAs **as a pull request**, not a blind commit. **Pin to the latest release tag** (more stable and more meaningful as a "real-world version" than bleeding-edge HEAD). Fallback: a project with no tags pins to default-branch HEAD, recorded explicitly per project so the choice is visible. The PR must carry: preflight pass for every bumped project, LOC delta, dependency diff, and the rendered chart annotation. A bump that breaks env setup or distorts scale is caught in review before it pollutes official numbers.
- **`envman`** — clones project @ SHA, builds an isolated `uv` venv against the **pinned Python version**, installs **locked** project deps (so third-party imports resolve identically across time), counts Python LOC with `scc`. Records the resolved lock hash.
- **`runner`** — orchestrates the measurement passes per (project × tool × thread-mode), including the wrapper that normalizes exit codes for hyperfine (§5.1).
- **`collector`** — normalizes raw measurements → one results record + environment fingerprint + lock manifest (§9).
- **`renderer`** — results JSON → README tables + GH Pages data files. Trends are rendered from **calibration-normalized** numbers (§5.7) in addition to raw.

---

## 5. Measurement Methodology (the credibility core)

### 5.1 Failure handling & the exit-code wrapper
Type checkers **exit nonzero when they find diagnostics** — that is normal success, not a measurement failure. hyperfine, by default, aborts on any nonzero exit. So the runner wraps every invocation:
- The wrapper runs the real command, captures the **real exit code**, stdout, stderr, signal, timeout flag, and OOM flag, then **exits 0 to hyperfine** for any *measured-success* class (clean **or** diagnostics-found).
- For *failure* classes (crash/panic, env error, timeout, OOM) the wrapper propagates a nonzero code so the run is recorded as `failed{reason}`, never silently averaged in.
- Classification is owned per-adapter via `classify_exit` and resolved against the taxonomy in §7.
- Equivalent for the memory/CPU pass — the scoped run is judged by the same classification, not by raw exit code.

### 5.2 What "cold" means (and what it does not)
- **Cold = no checker cache and no daemon.** Tool cache cleared/disabled before **every** measured run (§4 `clear_cache`).
- **OS page cache is deliberately warm.** We are benchmarking the *checker*, not disk reads. Warmup runs (`hyperfine --warmup W`) and repeated runs leave project source in the page cache; this is intentional and stabilizes variance.
- **`--warmup W` does not contradict "cold."** Because `--prepare` clears the *checker* cache before each run, warmups only stabilize page cache and CPU frequency/turbo — they never warm a checker cache. This is stated so the two settings are not read as contradictory.

### 5.3 Thread / core semantics (honesty about asymmetry)
The four tools do not parallelize equally, and a literal uniform "1 thread" is **not achievable**:
- **mypy** — single-process historically, but **mypy ≥ 2.0 adds experimental parallel type-checking** (`--num-workers N` / `-nN`; `0` disables, the default). The adapter version-guards: ≥ 2.0 sets `--num-workers` to the configured core count, < 2.0 stays single-process. `--num-workers` is a **hard** process cap. Two honesty notes, because mypy's parallel mode is the one entrant whose cold path changes: (a) mypy's docs warn parallel mode *can* change diagnostics, so the headline **constrained track defaults to N=1 = single-process** (no parallel) — parallel mypy appears only in the all-cores track or an explicit `--cores N>1` opt-in, never in the apples-to-apples N=1 floor; the real `--cores 8` validation showed identical diagnostics/files vs single-process, but the default containment is the guarantee, not that observation. (b) Parallel mode *requires* the cache (`Cache must be enabled in parallel mode`), incompatible with cold `--no-incremental --cache-dir=/dev/null`; the adapter instead uses a **fresh per-run cache dir** wiped before every run (cold = empty cache → full recompute). The cache-serialization write is a fixed tax the single-process baseline never pays — it is correctly attributed to mypy's parallel mode (intrinsic, not pure compute), and surfaces in wall-time/parallel-efficiency, not hidden.
- **pyright** — single main analysis thread (`--threads` is a hint, not an OS cap).
- **pyrefly** — Rust, parallel by design; `--threads N` is a hard rayon-pool cap.
- **ty** — Rust, parallel by design; `TY_MAX_PARALLELISM` caps *concurrent tasks* but is **explicitly not a thread limit** (ty may still spawn background threads).

The constrained track is parameterized by a configurable **core count N** (`--cores N`, default **1** = single-threaded; multithreading is opt-in). Therefore:

- **Track A — "constrained":** uniform mechanism is **CPU affinity to the first N cores** (`taskset -c 0..N-1`) for all tools, *plus* each adapter's parallelism cap scaled to N (mypy `--num-workers N` when ≥ 2.0, pyright single main thread, ty `TY_MAX_PARALLELISM=N`, pyrefly `--threads N`). Affinity-to-N-cores is the apples-to-apples floor; the per-tool cap is reported with a `hard_cap: true|false` honesty flag. **This is named "constrained," not "N threads,"** precisely because a core-count cap forces a parallel tool's threads to contend on N cores rather than truly running N workers — that distinction is documented, not hidden. At the default N=1 this reduces to a single pinned core.
- **Track B — "all-cores" (default):** real-world UX. mypy (≥ 2.0) and pyright/pyrefly/ty all use the available cores here; pyright remains effectively single-main-thread, so it benefits least. Reported in the entrants' favor — not a strawman against the less-parallel tools.
- **Parallel efficiency** = CPU-time (user+sys, from the cgroup, §5.5) ÷ wall-time (from hyperfine). ~1 for single-threaded runs (pyright, and mypy at N=1 / pre-2.0); > 1 when a tool actually parallelizes.

### 5.4 Timing pass
`hyperfine --warmup W --runs N --prepare "<clear-cache>"` (wrapped per §5.1) → min/median/mean/stddev wall time. Cache cleared before **every** run. `min` is retained as the noise-robust comparator (§5.6).

### 5.5 Memory & CPU-time pass (separate from timing)
hyperfine measures time only, so a second pass runs each command under a transient cgroup v2 scope (`systemd-run --scope`):
- **Peak memory** = read `memory.peak` (**and snapshot `memory.stat`**) of the scope. Label this **"peak cgroup memory," not "peak RSS"** — cgroup v2 reports max memory usage charged to the cgroup and all descendants (page cache, kernel structures, every child process), which is the right number for a fair cross-tool comparison (it catches pyright's Node process and mypy/pyrefly/ty workers that `/usr/bin/time -v` would miss) but is not pure RSS.
- **Read-before-teardown:** `systemd-run --scope` cgroups are transient and vanish when the process exits. The wrapper must read `memory.peak`/`memory.stat`/`cpu.stat` **while the scope still exists** (or use a lingering scope) — otherwise the number is lost.
- **CPU-time** (user+sys) comes from this scope's `cpu.stat`, not from hyperfine.
- **Repeat the memory pass** (≥ the same N as timing, or a documented smaller M ≥ 3): peak memory varies run-to-run. Store **min/median/max** (at minimum median + max).

### 5.6 Statistical reporting
- Timing on shared VMs is right-skewed; do not lean on stddev alone (it assumes near-normal). Report **min (noise-robust comparator) + median + a dispersion measure (IQR or MAD)** alongside mean/stddev.
- **Define "±X%" precisely** in the published methodology (e.g. relative stddev or a bootstrap CI) — not a hand-wave.
- **Noise floor:** timing deltas under ~10% are flagged unreliable. State a documented **outlier policy** (e.g. discard runs beyond k·MAD, log how many were discarded).

### 5.7 Calibration / trend normalization
The headline feature is trend charts over weeks, but each weekly run lands on a **different physical VM, possibly a different CPU model**. Absolute wall-time week-over-week is otherwise a hardware lottery. Mitigations (at least the first is required):
- **Per-run calibration baseline** — every run also executes a fixed CPU-bound reference workload; all numbers are reported both raw and **normalized to the calibration baseline**, cancelling VM-to-VM speed differences.
- **Relative metrics for trends** — also plot inter-checker ratios (checker ÷ slowest, or ÷ calibration), which cancel hardware almost entirely.
- **Segment trends by CPU model** (already in the fingerprint, §9).
- Consider **larger GH runners** (dedicated vCPUs) for headline numbers — cheap insurance for a credibility-first project.

### 5.8 Fairness controls
- Identical normalized config per tool (§6; overridable only for exploration, never for official numbers).
- Same venv + same **locked** deps + same Python target + same project SHA for all tools in a run.
- Checkers always latest; **all versions + full lock manifest recorded** in every result (§9).
- Interleave tools across rounds to spread runner drift (within-run hardware noise cancels).
- `timeout` cap per run (→ `failed{timeout}`, §7).
- Every result stamped with the fingerprint in §9.

---

## 6. Normalized Config Contract (LOCKED)

The normalized config **is** the credibility core; it cannot be a downstream implementation detail. Guiding principle: **normalize observable inputs (same files analyzed, same target, equal analyzed work, stock default rule sets), not rule-for-rule equivalence.** Document every per-tool deviation rather than pretending the tools are identical. Locked decisions for all four tools:

- **Target file set** — the first-party source root(s) declared per project in `suite.toml`; the *exact same* set per tool. This is also the throughput denominator (§8).
- **Excludes — LOCKED:** vendored code OUT, generated code OUT, **test files OUT** (analyze shipped/first-party source only; per-project exclude globs documented). Rationale: tests are often laxly typed/dynamic (noise) and most real type-check setups target shipped code.
- **Python version & platform** — `--python-version`/`--target-version` and `--python-platform` (or equivalents) identical per project, pinned in `suite.toml`.
- **Import-following / third-party policy — LOCKED:** deps are always installed so imports resolve; checkers **read third-party public types/stubs to resolve imports but emit diagnostics on first-party code only** (no deep-analysis of dependency internals). Each tool configured to match this posture (e.g. mypy `--follow-imports=normal` with third-party diagnostics suppressed; pyright workspace-only reporting; pyrefly/ty equivalents). Keeps the analyzed-work denominator comparable and matches how the tools are actually run.
- **Unannotated-function behavior — LOCKED:** **all function bodies are analyzed.** mypy runs with `--check-untyped-defs`; pyright/pyrefly/ty analyze all bodies by default. Equalizes the actual work measured so mypy gets no free win by skipping untyped bodies.
- **Severity / diagnostic threshold — LOCKED:** each tool's **stock default rule set / default severities** (low stakes — affects the secondary diagnostics data point, never ranking). No custom strictness dialing per tool.
- **Config discovery rules — LOCKED:** per-project `mypy.ini`/`pyrightconfig.json`/`pyrefly.toml`/`[tool.ty]` are **suppressed for official runs** in favor of the normalized config; honored **only** in explicit override/exploration mode.
- **Plugins — LOCKED:** official/headline track is **stock, zero plugins** (equal across all tools). Projects that genuinely require a mypy plugin to run are **labelled/bucketed separately with an asterisk**, never in the apples-to-apples headline. Documented as a known asymmetry (plugins have no cross-tool equivalent and materially change mypy's runtime cost).
- **Config hash** — a hash of the resolved normalized config is stamped on every result.

**Remaining implementation task (not a gate):** translate these locked policies into the exact per-tool flags/config files and verify each tool actually honors them (e.g. confirm third-party diagnostic suppression behaves identically). This translation carries a focused review during implementation, but the *policy* is locked here.

---

## 7. Failure Taxonomy (LOCKED)

Every run resolves to exactly one class. Diagnostics are success; only real failures are excluded from headline aggregates (but always recorded — a missing bar must read as "didn't compete," never as "fast").

| Class | Meaning | hyperfine sees | Recorded as |
|-------|---------|----------------|-------------|
| `clean` | Checked, zero diagnostics (exit 0) | success | measured |
| `diagnostics` | Checked, diagnostics found (mypy 1 / pyright 1 / pyrefly 1 / ty nonzero-for-errors) | success (wrapper exits 0) | measured |
| `failed{env}` | Environment/setup error (pyrefly 3, dep/import resolution failure) | failure | failed, excluded from aggregate |
| `failed{crash}` | Panic / segfault / internal error (pyrefly 101, mypy 2, signals) | failure | failed, excluded |
| `failed{timeout}` | Exceeded per-run `timeout` cap | failure | failed, excluded |
| `failed{oom}` | OOM-killed (esp. giant bucket) | failure | failed, excluded |

Each adapter's `classify_exit` maps its real exit codes/signals to these classes. The wrapper (§5.1) preserves the real exit code in the result record regardless of what it reports to hyperfine.

---

## 8. Metrics (per project × tool × thread-mode)

- Wall time: min / median / mean / stddev + IQR/MAD (§5.6)
- **Peak cgroup memory** (cgroup v2 `memory.peak`, full process tree) — reported as **min/median/max** over repeated runs (§5.5); labelled "peak cgroup memory," not RSS
- CPU time (user + sys, from cgroup `cpu.stat`)
- Parallel efficiency (CPU-time ÷ wall-time)
- **Throughput (kLOC/s)** — denominator **locked**: report against the **analyzed file set defined by the normalized config (§6)**, which is identical across tools, not raw total LOC. If a tool's actual analyzed set diverges, report per-tool analyzed LOC and caveat; never publish a headline throughput whose denominator differs from the measured work.
- Files checked (per tool, from machine-readable output)
- Diagnostics count — **data point only, never a ranking.** Counts are incomparable across tools (different rules/strictness) and will be misread; keep it out of headline tables (show as a secondary/expandable figure or as exit-class only) to avoid handing critics a false soundness ranking.
- Result class (§7)
- Full version + environment fingerprint + lock manifest (§9)
- Calibration-normalized variants of time/throughput (§5.7)

---

## 9. Reproducibility & Lock Manifest

Floating checkers buy "always current" but cost historical reproducibility unless every run is fully pinned in the record. Each result writes a **lock manifest**:
- Tool versions (all four), and their install source (PyPI wheel / npm+Node / etc.)
- **Exact installed package versions** for the project's locked deps (uv lockfile hash + resolved versions)
- Node/npm version (pyright), uv version, Python version
- Platform: OS, kernel, CPU model, core count, available memory, cgroup v2 availability
- Project SHA, normalized-config hash, calibration baseline id
- Corpus suite version

This makes any historical run re-creatable and lets trends be segmented (e.g. by CPU model) and attributed (checker bump vs corpus bump vs dep bump).

---

## 10. Cost & Scheduling Budget

The combinatorial load must fit GitHub's job limits, or the weekly cadence is fiction. Load = projects × 4 tools × 2 thread-modes × N runs × 2 passes (+ warmups + calibration). A giant >500k-LOC project under single-process mypy (N=1 / pre-2.0) can take minutes per invocation; the budget section must specify:
- Target **N** (and memory-pass M), with the statistics/cost tradeoff stated.
- Per-bucket wall-time estimates and the total CI minutes/cost per weekly run.
- **Sharding** strategy across jobs (e.g. one job per size bucket or per tool) to stay under the 6-hour job limit.
- **envman caching** — cache venvs keyed by (project SHA + lock hash) so clone+install isn't paid every run.

(Numbers to be filled during planning; the section is required, not optional.)

---

## 11. Data Flow & Outputs

1. Run → `results/<date>.json` committed to git (git history = time-series), including lock manifest (§9).
2. README table **auto-generated** from latest results. Default ordering is the measured metric (fastest-first) or alphabetical; both are legitimate — ranking by the *measured quantity* is the point of a performance benchmark and is distinct from the prohibited editorializing/pyrefly-favorable framing. Alphabetical is offered for the strictly-neutral view.
3. GitHub Pages static site renders trend charts from the committed JSON history, using **calibration-normalized** series (and optional raw + inter-checker-ratio series) so VM variance doesn't masquerade as checker change (§5.7).
4. Corpus-bump events (PR-gated, §4) appear as annotated markers on the trend charts, so a step-change in the ruler is visible and attributable.

Raw JSON is the open, neutral source of truth; README and Pages are rendered views. Methodology prose lives in hand-written docs, separate from the generated tables, so regeneration never clobbers it.

---

## 12. Error Handling

- Run outcome is always one taxonomy class (§7); failures are recorded as explicit `failed{reason}`, **never silently dropped**. A missing bar reads as "didn't compete" and is misleading.
- **Corpus health is a preflight gate, not best-effort.** Before a run, `preflight` verifies each project: pinned Python available, deps install from lock, extras/native/system prerequisites present, project actually checkable by all four tools. A project that fails preflight is excluded from **that run only**, logged loudly, and surfaced — env-setup failure must never quietly reshape the official benchmark.
- Partial suite still publishes (with the exclusions visible).
- **Supply chain:** cloning corpus repos and installing their deps runs **arbitrary code** (build hooks, `setup.py`, native builds). Acceptable on ephemeral GH runners; for first-class **local** runs this is documented as a warning, deps are pinned/locked, and isolation is recommended.

---

## 13. Testing Strategy

- **Unit:** adapter parsers against fixture outputs (incl. JSON outputs), `classify_exit` against real exit codes, normalized-config generation, results + lock-manifest schema validation, renderer (golden README), calibration normalization math.
- **Integration:** a tiny fake project + a stub checker → exercise the full pipeline (preflight → prepare → measure → collect → render), including a deliberately-failing stub to prove the failure taxonomy and exit-code wrapper.
- **Smoke (CI PR gate):** one real *small* project × all 4 checkers — not the full suite (keeps PR CI fast).
- **Weekly-run guardrail:** a parse-sanity assertion (files > 0 on success, version-matched output format) that fails the scheduled run loudly when a floated checker changes its output format, rather than silently recording garbage.
- **Code quality gate (LOCKED, all plans + CI):** `ruff` (strict rule set: E/W/F/I/N/UP/B/C4/SIM/PTH/RET/ARG/TID/TC/PL/RUF) for lint, `ruff format` for formatting, and **pyrefly `preset = "strict"`** for types — typebench **dogfoods pyrefly** on its own source. Enforced locally via `pre-commit` (local hooks → versions pinned by `uv.lock`) and in CI. `[tool.pyrefly]` lives at the repo root with `project-includes` so pyrefly does not silently no-op (it reports `0 errors` on files outside a project root). All functions, tests included, are fully type-annotated.

---

## 14. Scope

### v1
Cold single-shot · 4 checkers (mypy, pyright, pyrefly, ty) · size buckets · **locked** normalized config (overridable for exploration only) · both thread tracks (1-core constrained + all-cores) · failure taxonomy · peak-cgroup-memory + CPU-time + calibration · JSON + lock manifest + auto-README + GH Pages · weekly run + monthly **PR-gated** corpus bump · first-class local runs.

### Later milestones
- Warm/incremental/daemon track (`dmypy`, watch modes, cached re-check) as a separate category.
- More checkers.
- Per-PR regression alerts against the time-series.

---

## 15. Known Limitations / Open Items

- **Memory metric is "peak cgroup memory," not RSS** (§5.5) — it includes page cache and descendant processes by design (the fair cross-tool number), and **requires cgroup v2 + `systemd-run`** — available on GitHub Ubuntu runners and most Linux. **macOS local runs cannot do cgroups → local mac = timing-only** (documented; CI is the source of memory truth).
- **Trend stability on shared runners** depends on the calibration baseline (§5.7); absolute cross-week numbers without normalization are explicitly not claimed as reliable.
- **A literal "1 thread" track is not offered** (§5.3); the floor is "1-core constrained," and per-tool caps are labelled `hard_cap: true|false`. ty's `TY_MAX_PARALLELISM` is a task cap, not a thread cap; pyrefly thread limiting is adapter-verified.
- **Normalized config (§6)** is the central credibility risk and carries its own locked review before harness work; the neutrality claim is only as strong as that contract.
- **Diagnostics counts** are reported but are not comparable across tools; kept out of headline tables to avoid misuse as a soundness ranking.
- Standard tool distributions assumed and verified per adapter: mypy = mypyc-compiled PyPI wheel; pyright = pinned npm + pinned Node; pyrefly/ty = pinned PyPI. Recorded per run (§9).
