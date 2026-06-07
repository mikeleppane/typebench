# typebench — Design Spec

**Date:** 2026-06-07
**Status:** Approved (brainstorm), pending implementation plan
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
- Neutral presentation — results sorted by the metric or alphabetically, never "pyrefly first".
- Project lives as its own neutral project, **not** under pyrefly branding.
- Adding a 5th checker must be trivial; no entrant is privileged in the architecture.

### Non-goals (v1)
- Not a correctness/soundness comparison. Diagnostic *counts* are reported as a data point, never as a ranking.
- Not warm/incremental/daemon benchmarking (deferred to a later milestone).
- Not a hosted service; results are static artifacts in a git repo + GitHub Pages.

---

## 2. Key Decisions (locked during brainstorm)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Primary goal | Neutral rigorous benchmark, credibility-first |
| 2 | Config fairness | **Normalized config** is the official/headline default; per-tool & per-project **overrides supported** for exploration. Official numbers always use the documented normalized config. |
| 3 | Execution environment | **GitHub-hosted runners + statistical defense** (many iterations, median/stddev, variance bars, "indicative ±X%" caveat). **Local runs are a first-class requirement** — same harness/commands. |
| 4 | Harness language | **Python orchestrator** + `uv` for env management + `hyperfine`/cgroups for measurement. (Rust adds friction, zero accuracy benefit — measurement is delegated.) |
| 5 | Project shape | **Generic engine + curated suite as data.** `benchmark run <repo>` works on any repo; the curated suite is just a config file the engine consumes. |
| 6 | Threading | **Both tracks** — pinned 1-thread (algorithmic apples-to-apples) **and** all-cores (real-world UX). Plus CPU-time vs wall-time for parallel efficiency. |
| 7 | Execution mode | **Cold single-shot only (v1).** Fresh process, caches disabled/cleared, no daemon. Warm/incremental deferred. |
| 8a | Corpus size spread | **Size buckets** — small (~10k), medium (~50k), large (>100k), giant (>500k LOC). Reveals scaling curves. |
| 8b | Corpus pinning | **Pin corpus to commit SHAs, auto-bumped monthly** by a separate job (discrete, logged event). **Checkers always float to latest.** Weekly checker runs = pure checker deltas; monthly corpus bumps = annotated chart markers. |
| 9 | Results storage | **JSON-in-git** (source of truth) → **auto-generated README** + **GitHub Pages** trend charts. README/Pages are *rendered views*, never hand-edited. |
| — | Cadence | **Weekly** checker runs + **monthly** corpus auto-bump. |
| — | Engine architecture | **#1 Adapter-driven engine** — neutral core + one declarative adapter per checker. |

---

## 3. Architecture

Neutral core engine + pluggable checker adapters + declarative corpus. Python, `uv`-managed. Measurement delegated to `hyperfine` (timing) and cgroup v2 (memory). Results are versioned JSON; README + GH Pages are rendered views.

```
suite.toml (corpus: pinned SHAs, buckets, deps)
        │
        ▼
  ┌───────────── engine ─────────────┐
  │ prepare → measure → collect → render │
  └──────────────────────────────────┘
     │          │           │        │
  adapters   hyperfine   cgroup   results.json → README + Pages
 (mypy/pyright/  (timing)  (mem)
  pyrefly/ty)
```

### Why adapter-driven
Each checker's specifics live in exactly one file. Adding a 5th checker = drop in one adapter, touch nothing else. Neutral by construction — the core engine knows nothing tool-specific.

---

## 4. Components

- **`adapters/`** — one module per checker. Interface:
  - `install(version) -> resolved_version`
  - `version() -> str`
  - `command(project, config, threads) -> argv` (invocation under normalized config + thread mode)
  - `clear_cache()` (per-tool cache kill: `.mypy_cache`/`--no-incremental`, pyright stateless, pyrefly/ty stateless — verified per adapter)
  - `parse(stdout, stderr, exit_code) -> {diagnostics: int, files: int}`
  This is the **only** checker-specific code.
- **`corpus`** — `suite.toml`: each project = `name`, repo URL, **pinned SHA**, `size_bucket`, dependency install strategy, optional per-project config overrides.
- **`bump` job** — separate scheduled job that rewrites SHAs to latest default-branch HEAD monthly and commits as one logged event.
- **`envman`** — clones project @ SHA, builds an isolated `uv` venv, installs project deps (so third-party imports resolve), counts Python LOC (`scc` or `tokei`).
- **`runner`** — orchestrates the two measurement passes per (project × tool × thread-mode).
- **`collector`** — normalizes raw measurements → one results record + environment fingerprint.
- **`renderer`** — results JSON → README tables + GH Pages data files.

---

## 5. Measurement Methodology (the credibility core)

### Two separate passes (they perturb each other)
- **Timing pass** — `hyperfine --warmup W --runs N --prepare "<clear-cache>"` → min/median/mean/stddev wall time. Cache cleared before **every** run.
- **Memory pass** — run under a cgroup v2 scope (`systemd-run --scope`), read `memory.peak` → true peak RSS of the **whole process tree**. This catches pyright's Node process and mypy/pyrefly/ty workers; `/usr/bin/time -v` would miss descendants.

### Two thread modes
- **Pinned 1 core / 1 thread** — algorithmic apples-to-apples.
- **All cores (default)** — real-world UX.
- Derive **parallel efficiency** = CPU-time (user+sys) ÷ wall-time.

### Cold always
Fresh process, caches disabled/cleared, no daemon (v1).

### Fairness controls
- Identical normalized config per tool (overridable for exploration).
- Same venv + same installed deps + same Python target + same project SHA for all tools.
- Checkers always latest; **all versions recorded** in every result.
- Interleave tools across rounds to spread runner drift.
- `timeout` cap per run.
- Every result stamped with: tool versions, CPU model, core count, OS, Python version, project SHA, normalized-config hash.

### Environment honesty
GH-hosted runners are shared VMs. Defend statistically (iterations + median/stddev + variance bars) and label results "indicative, ±X%". Timing deltas under ~10% are explicitly flagged as unreliable.

---

## 6. Metrics (per project × tool × thread-mode)

- Wall time: min / median / mean / stddev
- Peak RSS (cgroup `memory.peak`, full process tree)
- CPU time (user + sys)
- Throughput (kLOC/s)
- Files checked
- Diagnostics count — **data point only, never a ranking**
- Exit status
- Full version + environment fingerprint

---

## 7. Data Flow & Outputs

1. Run → `results/<date>.json` committed to git (git history = time-series).
2. README table **auto-generated** from latest results (sorted by metric or alphabetical — never "pyrefly first").
3. GitHub Pages static site renders trend charts from the committed JSON history.
4. Corpus-bump events appear as annotated markers on the trend charts (so a step-change in the ruler is visible and attributable).

Raw JSON is the open, neutral source of truth; README and Pages are rendered views.

---

## 8. Error Handling

- Checker crash / OOM / timeout on a giant = recorded as explicit `failed{reason}` in results, **never silently dropped**. A missing bar reads as "didn't compete" and is misleading.
- Partial suite still publishes.
- Env-setup failure (deps won't install) excludes that project from **that run only**, logged loudly.

---

## 9. Testing Strategy

- **Unit:** adapter parsers (fixture outputs), normalized-config generation, results schema validation, renderer (golden README).
- **Integration:** a tiny fake project + a stub checker → exercise the full pipeline (prepare → measure → collect → render).
- **Smoke (CI PR gate):** one real *small* project × all 4 checkers — not the full suite (keeps PR CI fast).

---

## 10. Scope

### v1
Cold single-shot · 4 checkers (mypy, pyright, pyrefly, ty) · size buckets · normalized config (overridable) · both thread modes · JSON + auto-README + GH Pages · weekly run + monthly corpus bump · first-class local runs.

### Later milestones
- Warm/incremental/daemon track (`dmypy`, watch modes, cached re-check) as a separate category.
- More checkers.
- Per-PR regression alerts against the time-series.

---

## 11. Known Limitations / Open Items

- **cgroup memory** requires cgroup v2 + `systemd-run` — available on GitHub Ubuntu runners and most Linux. **macOS local runs cannot do cgroups → local mac = timing-only** (documented limitation; CI is the source of memory truth).
- Standard tool distributions assumed: mypy = mypyc-compiled PyPI wheel; pyright = pinned npm + pinned Node; pyrefly/ty = pinned PyPI. Recorded per run.
- Normalized config must be designed carefully so it is genuinely neutral across all four tools — a v1 implementation task with its own review.
