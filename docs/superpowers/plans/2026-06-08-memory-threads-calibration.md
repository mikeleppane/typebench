# Plan 4 — Memory · Threads · Calibration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the three measurement dimensions the timing-only engine is missing — uniform CPU-affinity thread enforcement (Track A), peak cgroup memory + CPU-time under a transient cgroup v2 scope, and a per-run calibration baseline — while keeping every honesty flag true by construction and never biasing the timed path.

**Architecture:** CPU affinity is a *uniform* `taskset -c 0` prefix prepended to `argv` once in the collector, so the same pinned command flows through the probe, the hyperfine timing pass, and the resource pass — apples-to-apples across all four tools, layered over each adapter's already-declared per-tool `parallelism_cap()`. A new pydantic-free `measure.py` runs the checker under `systemd-run --user --scope` and reads `memory.peak`/`memory.stat`/`cpu.stat`/`memory.events` **before the transient cgroup is torn down**, repeated M≥3 times for variance; this same scoped run also yields real cgroup OOM detection (paying down the `wrapper.py` `RawRun.oom` debt). A new `calibration.py` times a fixed, dep-free Python CPU-bound workload so weekly trends can be normalized against VM-to-VM hardware lottery. All three feed a v2 `RunResult` via nested sub-models.

**Tech Stack:** Python 3.12, `systemd-run` + cgroup v2 (`/sys/fs/cgroup`), `taskset` (util-linux), `hyperfine` (existing timing), pydantic v2, Typer, pytest. No new pip dependency — `systemd-run`/`taskset` are system tools, gated by a runtime capability probe with graceful fallback.

---

## Review fixes incorporated (2026-06-08, two senior reviews)

This revision folds in every issue raised by both reviews. Each is wired into the task it belongs to; this list is the index.

1. **Repeat-failure honesty** — `scoped_probe` classifies *every* resource repeat (generic env/oom/timeout/crash-signal via the pydantic-free `universal_failure_prefix`) and surfaces the first failure as the authoritative outcome, so a 1-in-M crash can no longer hide behind a "clean" run #1. (Tasks 4, 6)
2. **Cgroup OOM actually reaches `RawRun.oom`** — `_raw_from_payload` folds `memory.events.oom_kill > 0` into `oom`, so the record reclassifies `FAILED_OOM`. The earlier separate flag did not flow into classification. (Task 4)
3. **The pass never escapes / never drops a record** — per-repeat harness exceptions (systemd-run nonzero, missing/corrupt payload, timeout, scope race) are caught and skipped; a *total* failure raises `MeasureError`, which the collector catches and falls back to a plain `run_command` probe (`memory=None`). (Tasks 4, 6)
4. **Cold repeats** — the resource loop clears the checker cache before EVERY repeat via a `prepare` callback (§5.2 parity with the timing pass). (Tasks 4, 6)
5. **No production `assert`; repeat counts validated** — `--mem-runs`/`--calib-runs` floored at ≥1 at the CLI + collector boundaries (≥3 is the §5.5 methodology floor, noted in help); the `assert raw0 is not None` is gone. (Tasks 4, 7, 8)
6. **Capability probe matches the real path** — probes the exact `-p MemoryAccounting=yes -p CPUAccounting=yes` the resource pass uses, no longer conflated with `taskset` (taskset gates only the ONE_CORE floor); the real-scope test asserts `cpu_time_s > 0` to catch non-delegated cpu controllers. (Tasks 2, 4)
7. **`hard_cap`/`cap_mechanism` never claim affinity that didn't run** — recorded only when `taskset` actually pinned (the adapter mechanism strings bake in "cpu-affinity"). (Task 5)
8. **Gate-clean** — the existing `schema_version == 1` assertion is updated to 2; the unused `# noqa: F821` is replaced with a `TYPE_CHECKING` import; all new test helpers/fixtures are annotated (pyrefly `strict` covers `tests/`); seam monkeypatches use `raising=True` where the seam exists. (Tasks 1, 5, 6, 7, all)
9. **Verification uses the corpus-driven path + non-hidden cache** (`--corpus-project httpx --cache-root typebench-cache`); the `AGENTS.md` "Plan 4 DONE" stamp moves to AFTER the real bench passes. (Tasks 9, 10)

---

## Scope (locked during brainstorm + 4 confirmed decisions)

**Confirmed by user (AskUserQuestion, 2026-06-08):**
1. **Single Plan 4** — one plan/branch (not split 4A/4B/4C).
2. **Affinity mechanism = `taskset -c 0` argv prefix** — zero import cost on the measured path, uniform across tools, visible/auditable in the *command argv that runs* (logs + the wrapped hyperfine string). The JSON `RunResult` has no raw-command field, so on-disk auditability of "was affinity applied?" is carried by `thread_mode_enforced` + `cap_mechanism` (persisting the full argv is a possible Plan 5 §9 enrichment, out of scope here).
3. **Schema = nested sub-models** (`MemoryStats`, `CalibrationStats`) mirroring the existing `TimingStats` precedent.
4. **Calibration = dep-free embedded Python CPU loop** (`calib-pyloop-v1`), content-locked by `workload_id` + fixed `iterations`.

**IN:** `taskset` affinity prefix (collector, ONE_CORE only) + `thread_mode_enforced` flip + per-tool `hard_cap`/`cap_mechanism` recording; pydantic-free `measure.py` (cgroup-stats reader, in-scope wrapper CLI, scoped-probe orchestrator with injectable runner, capability probe + fallback); real cgroup OOM → `RawRun.oom`; `calibration.py` (fixed Python CPU workload + `calibrate()`); v2 `RunResult` with `MemoryStats`/`CalibrationStats` sub-models + `cpu_time_s`/`parallel_efficiency`/`hard_cap`/`cap_mechanism`; CLI `run` wiring (`--mem-runs`, `--calib-runs`, `--no-measure`, `--no-calibrate`) + calibration computed once per invocation; a real httpx ×4 corpus bench before declaring done.

**OUT (later plans, do NOT build here):**
- **Normalized/factor computation + trend charts** — the renderer divides raw metrics by the stored calibration baseline (Plan 5). Plan 4 stores raw calibration only (see Decision C).
- **Results envelope, suite orchestration, sharding, GH Pages, scc code-LOC** (Plan 5).
- **`EnvFingerprint` expansion** (cgroup-capable flag, dedicated-runner stamp) — spec §9 enrichment is Plan 5. `cpu_model` already present for CPU-model segmentation (§5.7).
- **`MemoryMax`/enforced memory limits** — the scope sets accounting only (`MemoryAccounting=yes`), never a cap; we measure, we do not constrain memory.
- **Per-core selection / NUMA pinning** — affinity is hardcoded to core 0 uniformly (Decision B).
- **New `taxonomy.py` enum values** — ASK-FIRST; a memory-pass OOM reuses the existing `FAILED_OOM` class (see Decision E).

## Key decisions (carry into every task)

- **Decision A — Affinity is a uniform collector-level prefix, never per-adapter.** Each adapter's `parallelism_cap()` already declares `"... + cpu-affinity"` (verified: stub `cpu-affinity`, mypy `single-process + cpu-affinity`, ty `TY_MAX_PARALLELISM + cpu-affinity`, pyright `cpu-affinity + single-thread`, pyrefly `--threads (rayon) + cpu-affinity`) and the adapters already comment "affinity in Plan 4." The collector prepends `["taskset", "-c", "0"]` to `argv` **once**, for `ThreadMode.ONE_CORE` only, and that pinned `argv` is reused by the probe, the resource pass, and the wrapped hyperfine timing command. This keeps the measured path (`wrapper.py`, `taxonomy.py`) untouched and pydantic-free. **Because the adapter mechanism strings bake in "cpu-affinity", the collector records `hard_cap`/`cap_mechanism` ONLY when `taskset` actually pinned** (`thread_mode is ONE_CORE` *and* `thread_enforced`); on the ONE_CORE track without `taskset` (mac/dev) it records neither, exactly like ALL_CORES, so the record never claims a pin we did not apply.
- **Decision B — Core 0, hardcoded, documented.** Spec §5.3 says "a single core." We pin to core 0 uniformly; the suite runs one (project×tool) at a time so there is no cross-process contention on core 0. Configurable core selection is YAGNI.
- **Decision C — `CalibrationStats` stores RAW min/median/max only, no baked `factor`.** ⚠️ *Sub-decision made during planning — flagged for review.* Storing the raw per-run calibration time is the durable, honest contract: the Plan 5 renderer picks any reference (first run / fixed anchor / per-CPU-model median) and divides. Baking a `factor` now would require an arbitrary magic reference constant that we cannot honestly establish from a single machine. "Report both raw and normalized" (§5.7) is satisfied: raw is stored, normalized is a render-time transform over the stored raw.
- **Decision D — `thread_mode_enforced` is ONE_CORE-only.** ⚠️ *Sub-decision made during planning — flagged for review.* The flag tracks specifically whether the 1-core affinity floor was applied: `True` only when `thread_mode is ONE_CORE` **and** `taskset` actually ran. `ALL_CORES` is unconstrained by design and leaves the flag `False` (it claims no pinning, so `False` is not dishonest). This keeps the flag literal and requires zero churn to existing `test_collector` assertions. The `models.py` docstring is updated to say exactly this.
- **Decision E — A memory-pass OOM reuses `FAILED_OOM`, no new enum value.** Adding a `taxonomy.py` value or a `FailurePhase` member is ASK-FIRST. The scope sets accounting only (no `MemoryMax`), so a synthetic OOM cannot occur — `memory.events.oom_kill > 0` means a *real* system-pressure OOM happened during the run. The fold is explicit and the source of truth for reclassification: **`_raw_from_payload` sets `RawRun.oom = payload["oom"] or (cgroup.oom_kill > 0)`** for each repeat, so the cgroup signal — not just the SIGKILL heuristic — drives `adapter.classify` → `universal_failure_prefix` → `FAILED_OOM`. Because every repeat is classified (Decision I) and a generic failure on ANY repeat wins, an OOM on repeat 2 or 3 reclassifies the whole record, not only an OOM on run #1. The handover's "wrapper.py:16 → set RawRun.oom" is paid down: `measure.py` populates `RawRun.oom` from `memory.events`. The SIGKILL heuristic stays as the non-cgroup fallback path.
- **Decision F — Peak includes a ~constant Python-harness baseline; this is honest and cancels.** The in-scope wrapper is a small Python process (`run_command` for correct kill-tree + signal/timeout capture, which we need for `classify`). Its ~10–15 MB interpreter baseline is charged to the cgroup alongside the checker. It is the **same offset for every tool**, so cross-tool comparison stays fair; checker stdout is bounded (diagnostics text, KBs), not GBs, so capturing it does not meaningfully inflate peak. Labelled **"peak cgroup memory," not RSS** (§5.5), with the baseline documented in code.
- **Decision G — Capability-gated with graceful fallback.** `measure.capable()` probes `systemd-run --user --scope --quiet true` + cgroup v2 presence at runtime. When false (mac dev, CI without a systemd user session), the resource pass no-ops: `memory=None`, `cpu_time_s=None`, `parallel_efficiency=None`, and the probe falls back to plain `run_command` (current behavior, SIGKILL OOM heuristic). Spec §15 already documents mac = timing-only.
- **Decision H — `schema_version` bumps 1 → 2.** The record shape changes (new sub-models + fields). The bump is the on-disk contract signal. The existing round-trip test (`tests/test_models.py`) asserts `schema_version == 1` and MUST be updated to `2` in the same task, or the gate fails.
- **Decision I — Every resource repeat is classified; the first failure wins.** `scoped_probe` does not blindly trust run #1. It builds a `RawRun` for each repeat and, using the pydantic-free `universal_failure_prefix`, surfaces the first repeat with a *generic* failure (env / oom / timeout / crash-signal) as the authoritative outcome that drives classify+parse. A 1-in-M crash/OOM therefore records a failure, never a "clean" hiding behind a lucky run #1 (§5.1, §5.5). The pyrefly-flaky-exit-1-on-a-repeat edge stays an accepted residual, identical to the timing pass's documented blind spot (`wrapper.py`), because disambiguating it needs the adapter and would couple `measure.py` to pydantic.
- **Decision J — Resource repeats are COLD, and the pass is best-effort.** Spec §5.2 requires the checker cache cleared before *every* measured run; the resource loop calls a `prepare` callback (`adapter.clear_cache(project)`) before each repeat, matching the timing pass's `--prepare`. Per-repeat harness exceptions (systemd-run nonzero, missing/corrupt payload, timeout, scope race) are caught and the repeat is skipped; if *no* repeat yields a payload, `scoped_probe` raises `MeasureError`, which the collector catches and falls back to a plain `run_command` probe (`memory=None`). This is what makes the "never raises into the collector / never drop a record" invariant true *by construction*, not just by intent.
- **Decision K — Repeat counts are validated, never asserted.** No production `assert`. `--mem-runs`/`--calib-runs` are floored at **≥1** at the CLI boundary (controlled `typer.Exit(2)`) and at the `run_single`/`scoped_probe`/`calibrate` boundaries (controlled `ValueError`). ≥3 is the §5.5 methodology floor for official numbers (stated in `--mem-runs` help), but ≥1 is the hard guard that prevents an empty-sample `statistics.median([])` / `min([])` crash. The `assert raw0 is not None` is removed — the empty case is handled by the `MeasureError` path (Decision J).
- **Measured path stays pydantic-free.** `measure.py` imports only stdlib + `typebench.wrapper` (itself pydantic-free). A new import-guard test asserts `import typebench.measure` pulls no `pydantic`. `wrapper.py` and `taxonomy.py` are not touched except the one comment update at `wrapper.py:16` noting OOM is now wired.
- **Never drop a record; the resource/calibration passes never raise into the collector.** Any `measure`/`calibrate` failure is swallowed to `None` (best-effort instrumentation), exactly like the existing `timing` pass swallows hyperfine errors.

## File structure

- Create `src/typebench/measure.py` — `CgroupSample`, `read_cgroup_stats`, `_self_cgroup_dir`, `capable` (probes the real `MemoryAccounting`/`CPUAccounting` props, NOT `taskset`), in-scope wrapper `main()` CLI, `MemorySummary`/`ResourceResult` dataclasses, `MeasureError`, `_raw_from_payload` (folds `oom_kill` → `RawRun.oom`), `scoped_probe` orchestrator (injectable `runner`, `prepare` callback for cold repeats, per-repeat exception guard, every-repeat classification). Pydantic-free (stdlib + `typebench.wrapper`'s `RawRun`/`run_command`/`universal_failure_prefix`).
- Create `src/typebench/calibration.py` — `WORKLOAD_ID`, `ITERATIONS`, `_run_workload`, `calibrate` (validates `runs >= 1`), `main()` CLI. Pydantic-free import (lazy + `TYPE_CHECKING` model import; no `# noqa`).
- Modify `src/typebench/models.py` — add `MemoryStats`, `CalibrationStats`; add fields to `RunResult`; bump `schema_version` default to 2; update `thread_mode_enforced` docstring; **update the existing `schema_version == 1` round-trip assertion to `2`**.
- Modify `src/typebench/collector.py` — add `_resource_capable`/`_scoped_probe` seams + `_taskset_available`; apply affinity prefix; choose scoped-probe vs plain probe by capability, **wrapped in try/except → plain-probe fallback** (`MeasureError`/harness errors never drop the record); pass the `prepare` cache-clear callback for cold repeats; record `hard_cap`/`cap_mechanism` **only when affinity actually ran**; build `MemoryStats`/`cpu_time_s`/`parallel_efficiency`; thread `calibration`/`mem_runs`/`measure_enabled` (validate `mem_runs >= 1`); flip `thread_mode_enforced`.
- Modify `src/typebench/cli.py` — compute calibration once; add `--mem-runs`, `--calib-runs`, `--no-measure`, `--no-calibrate` (validate `>= 1`); pass through to `run_single`.
- Modify `src/typebench/wrapper.py` — update the `_SIGKILL` comment at line ~16 (OOM now wired via cgroup in `measure.py`); **no signature change**.
- Modify `AGENTS.md` — layout note (new modules) + scope-by-plan note (Plan 4 done).
- Tests: `tests/test_measure.py` (new), `tests/test_calibration.py` (new), `tests/test_models.py` (extend), `tests/test_collector.py` (extend + autouse capability gate), `tests/test_cli.py` (extend).

---

### Conventions for EVERY test edit below (the gate enforces these)

The snippets use an "append to the test file" style for readability. When applying them, obey these or `ruff`/`pyrefly` fail:

1. **Imports go at the TOP of the file, merged + sorted** — never append an `import` after a function (ruff `E402`/`I001`). Every `import pytest` / `import subprocess` / `from typebench... import ...` shown inside a snippet must be hoisted into the file's existing top import block. (Imports *inside a function body* are fine and intentional where shown — they stay local.)
2. **Annotate every new helper, fixture, and inner function** — pyrefly runs `preset = "strict"` over `project-includes = ["src", "tests"]`, so untyped params/returns error. Use `monkeypatch: pytest.MonkeyPatch`, `tmp_path: Path`, and concrete types for runner/payload helpers (`payloads: list[dict[str, object]]`, `-> subprocess.CompletedProcess[str]`, etc.). The snippets below are already annotated; keep it that way for any you adapt.
3. **`monkeypatch.setattr(..., raising=True)` (the default) for seams that already exist** — only the Task 5 autouse fixture uses `raising=False`, because in strict TDD order it is written before the seam it patches. Everywhere else, let a typo'd seam name fail loudly.

---

### Task 1: Schema — `MemoryStats`, `CalibrationStats`, `RunResult` v2

**Files:**
- Modify: `src/typebench/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_models.py`:

```python
from typebench.models import CalibrationStats, MemoryStats, RunResult
from typebench.models import EnvFingerprint, ResultClass, ThreadMode


def _env() -> EnvFingerprint:
    return EnvFingerprint(
        os="Linux", kernel="6.6", cpu_model="x", core_count=8, python_version="3.12.0"
    )


def test_run_result_v2_carries_memory_cpu_calibration() -> None:
    mem = MemoryStats(
        runs=3,
        peak_bytes_min=100,
        peak_bytes_median=110,
        peak_bytes_max=120,
        memory_stat={"anon": 90, "file": 10},
    )
    calib = CalibrationStats(
        workload_id="calib-pyloop-v1",
        iterations=5_000_000,
        runs=5,
        raw_min_s=0.30,
        raw_median_s=0.31,
        raw_max_s=0.33,
    )
    r = RunResult(
        tool="mypy",
        tool_version="1.0",
        project="demo",
        thread_mode=ThreadMode.ONE_CORE,
        thread_mode_enforced=True,
        hard_cap=True,
        cap_mechanism="single-process + cpu-affinity",
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        memory=mem,
        cpu_time_s=0.42,
        parallel_efficiency=0.95,
        calibration=calib,
        env=_env(),
    )
    assert r.schema_version == 2
    assert r.memory is not None and r.memory.peak_bytes_median == 110
    assert r.cpu_time_s == 0.42
    assert r.parallel_efficiency == 0.95
    assert r.calibration is not None and r.calibration.workload_id == "calib-pyloop-v1"
    assert r.hard_cap is True


def test_run_result_v2_defaults_are_none() -> None:
    # A capability-gated engine on a non-cgroup host produces a record with the new
    # fields absent — they must default to None, not break the schema.
    r = RunResult(
        tool="stub",
        tool_version="0",
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        env=_env(),
    )
    assert r.schema_version == 2
    assert r.memory is None
    assert r.cpu_time_s is None
    assert r.parallel_efficiency is None
    assert r.calibration is None
    assert r.hard_cap is None
    assert r.cap_mechanism is None
    assert r.thread_mode_enforced is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py::test_run_result_v2_carries_memory_cpu_calibration tests/test_models.py::test_run_result_v2_defaults_are_none -v`
Expected: FAIL — `ImportError: cannot import name 'MemoryStats'` (and `schema_version == 1`).

- [ ] **Step 3: Add the sub-models**

In `src/typebench/models.py`, after the `TimingStats` class (before `EnvFingerprint`), add:

```python
class MemoryStats(BaseModel):
    """Peak cgroup-memory statistics from the resource pass (spec §5.5).

    "Peak cgroup memory," NOT RSS: cgroup v2 `memory.peak` is the max usage charged
    to the scope and ALL descendants (page cache, kernel structs, every child) — the
    right cross-tool number (it catches pyright's Node process and worker threads
    `/usr/bin/time -v` would miss). It includes a ~constant ~10-15 MB Python-harness
    baseline that is identical for every tool, so cross-tool comparison stays fair.
    `peak_bytes_*` are min/median/max over `runs` repeats. `memory_stat` is the
    `memory.stat` snapshot of the median-peak run (a data point, never a ranking)."""

    model_config = ConfigDict(extra="forbid")

    runs: int
    peak_bytes_min: int
    peak_bytes_median: int
    peak_bytes_max: int
    memory_stat: dict[str, int] | None = None


class CalibrationStats(BaseModel):
    """Per-run calibration baseline (spec §5.7). A fixed CPU-bound Python workload
    timed alongside the real run so weekly trends can be normalized against the
    VM-to-VM hardware lottery. RAW seconds only (min/median/max over `runs`):
    normalization to a reference is a render-time transform (Plan 5), so no factor
    is baked here — storing raw keeps the choice of reference open and honest.
    `workload_id` + `iterations` lock the workload identity for the manifest."""

    model_config = ConfigDict(extra="forbid")

    workload_id: str
    iterations: int
    runs: int
    raw_min_s: float
    raw_median_s: float
    raw_max_s: float
```

- [ ] **Step 4: Add `MemoryStats`/`CalibrationStats` to `__all__`**

In `src/typebench/models.py`, insert into the `__all__` list (keep it sorted):

```python
    "CalibrationStats",
    "EnvFingerprint",
    "FailurePhase",
    "MemoryStats",
    "PreflightReport",
```

(Add `"CalibrationStats"` and `"MemoryStats"` to the existing list — the snippet shows their neighbours for placement.)

- [ ] **Step 5: Extend `RunResult` + bump `schema_version`**

In `src/typebench/models.py`, change the `RunResult` `schema_version` default and the `thread_mode_enforced` docstring, then add the new fields. Replace:

```python
    schema_version: int = 1
```

with:

```python
    schema_version: int = 2
```

Replace the `thread_mode_enforced` field + comment:

```python
    # Honesty flag (spec §5.3): True only once CPU affinity / a hard cap is
    # actually applied (Plan 4). Plan 1 never enforces, so it stays False — the
    # record must not imply a methodology the engine did not run.
    thread_mode_enforced: bool = False
```

with:

```python
    # Honesty flag (spec §5.3), ONE_CORE-specific: True only when the 1-core
    # taskset affinity floor was actually applied. ALL_CORES is unconstrained by
    # design and leaves this False (it claims no pinning, so False is honest).
    # The record must never imply a methodology the engine did not run.
    thread_mode_enforced: bool = False
    # Per-tool worker-cap honesty (spec §5.3), from Adapter.parallelism_cap():
    # hard_cap True = a real worker cap (pyrefly --threads 1, single-process mypy),
    # False = best-effort (ty's soft TY_MAX_PARALLELISM, pyright --threads hint).
    # Recorded only for the constrained ONE_CORE track; None for ALL_CORES.
    hard_cap: bool | None = None
    cap_mechanism: str | None = None
```

Then, after the `timing: TimingStats | None = None` line (before `env: EnvFingerprint`), add:

```python
    # Resource pass (spec §5.5), None when measurement is unavailable (mac/CI):
    memory: MemoryStats | None = None
    cpu_time_s: float | None = None  # user+sys from cgroup cpu.stat
    parallel_efficiency: float | None = None  # cpu_time_s / wall median (~1 single-thread)
    calibration: CalibrationStats | None = None
```

- [ ] **Step 5b: Update the existing round-trip assertion (the v1→v2 bump breaks it)**

`tests/test_models.py::test_run_result_round_trips_through_json` already asserts the old default. Replace:

```python
    assert restored.schema_version == 1
```

with:

```python
    assert restored.schema_version == 2
```

(The neighbouring `assert restored.thread_mode_enforced is False` stays correct — `False` is still the default.) Without this change `uv run pytest tests/test_models.py` fails on a pre-existing test, not just the new ones.

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (all model tests — the two new ones AND the updated round-trip test).

- [ ] **Step 7: Commit**

```bash
git add src/typebench/models.py tests/test_models.py
git commit -m "feat(models): RunResult v2 — MemoryStats/CalibrationStats + cpu/efficiency/hard_cap fields"
```

---

### Task 2: cgroup-stats reader (pure function) + capability probe

**Files:**
- Create: `src/typebench/measure.py`
- Test: `tests/test_measure.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_measure.py`:

```python
from pathlib import Path

from typebench.measure import CgroupSample, read_cgroup_stats


def _write_cgroup(tmp: Path, *, peak: int, oom_kill: int = 0) -> Path:
    (tmp / "memory.peak").write_text(f"{peak}\n")
    (tmp / "cpu.stat").write_text(
        "usage_usec 44116\nuser_usec 25734\nsystem_usec 18381\nnr_periods 0\n"
    )
    (tmp / "memory.stat").write_text("anon 1000\nfile 2000\nslab 30\n")
    (tmp / "memory.events").write_text(f"low 0\nhigh 0\nmax 0\noom 0\noom_kill {oom_kill}\n")
    return tmp


def test_read_cgroup_stats_parses_all_files(tmp_path: Path) -> None:
    _write_cgroup(tmp_path, peak=44998656)
    s = read_cgroup_stats(tmp_path)
    assert isinstance(s, CgroupSample)
    assert s.peak_bytes == 44998656
    assert s.cpu_usage_usec == 44116
    assert s.cpu_user_usec == 25734
    assert s.cpu_system_usec == 18381
    assert s.oom_kill == 0
    assert s.mem_stat["anon"] == 1000
    assert s.mem_stat["file"] == 2000


def test_read_cgroup_stats_flags_oom(tmp_path: Path) -> None:
    _write_cgroup(tmp_path, peak=1, oom_kill=2)
    assert read_cgroup_stats(tmp_path).oom_kill == 2


def test_read_cgroup_stats_tolerates_missing_optional_keys(tmp_path: Path) -> None:
    # A kernel that omits user_usec/system_usec must not crash the reader.
    (tmp_path / "memory.peak").write_text("500\n")
    (tmp_path / "cpu.stat").write_text("usage_usec 10\n")
    (tmp_path / "memory.stat").write_text("anon 5\n")
    (tmp_path / "memory.events").write_text("oom_kill 0\n")
    s = read_cgroup_stats(tmp_path)
    assert s.peak_bytes == 500
    assert s.cpu_usage_usec == 10
    assert s.cpu_user_usec == 0
    assert s.cpu_system_usec == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_measure.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'typebench.measure'`.

- [ ] **Step 3: Create `measure.py` reader + capability probe**

Create `src/typebench/measure.py`:

```python
"""Resource pass (spec §5.5) — peak cgroup memory + CPU-time under a transient
cgroup v2 scope, plus real cgroup OOM detection. Deliberately pydantic-free: the
in-scope wrapper runs as a child process and reuses the (also pydantic-free)
exit-code wrapper; importing pydantic here would add startup cost to every scoped
run. Stays stdlib-only + `typebench.wrapper`."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from typebench.wrapper import RawRun, run_command

_CGROUP_ROOT = Path("/sys/fs/cgroup")
# core 0, uniform single-core floor (spec §5.3, Decision B). Applied by the
# collector as an argv prefix; referenced here only for documentation parity.
_AFFINITY_CORE = 0


@dataclass(frozen=True)
class CgroupSample:
    """One read of a scope's cgroup v2 accounting files (read before teardown)."""

    peak_bytes: int
    cpu_usage_usec: int
    cpu_user_usec: int
    cpu_system_usec: int
    oom_kill: int
    mem_stat: dict[str, int]


def _read_kv(path: Path) -> dict[str, int]:
    """Parse a `key value` cgroup file into a dict; missing file -> empty."""
    out: dict[str, int] = {}
    try:
        text = path.read_text()
    except OSError:
        return out
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("-").isdigit():
            out[parts[0]] = int(parts[1])
    return out


def _read_int(path: Path) -> int:
    return int(path.read_text().strip())


def read_cgroup_stats(cgroup_dir: Path) -> CgroupSample:
    """Read memory.peak / cpu.stat / memory.stat / memory.events from a cgroup v2
    directory. Pure (no process spawned) so it is unit-testable against fixture
    files. Callers MUST invoke this while the scope still exists (§5.5)."""
    cpu = _read_kv(cgroup_dir / "cpu.stat")
    events = _read_kv(cgroup_dir / "memory.events")
    return CgroupSample(
        peak_bytes=_read_int(cgroup_dir / "memory.peak"),
        cpu_usage_usec=cpu.get("usage_usec", 0),
        cpu_user_usec=cpu.get("user_usec", 0),
        cpu_system_usec=cpu.get("system_usec", 0),
        oom_kill=events.get("oom_kill", 0),
        mem_stat=_read_kv(cgroup_dir / "memory.stat"),
    )


def _self_cgroup_dir() -> Path:
    """The cgroup v2 directory of the current process (the `0::/path` line of
    /proc/self/cgroup, mounted under /sys/fs/cgroup)."""
    for line in Path("/proc/self/cgroup").read_text().splitlines():
        if line.startswith("0::"):
            rel = line.split("::", 1)[1].strip().lstrip("/")
            return _CGROUP_ROOT / rel
    raise OSError("no cgroup v2 (0::) entry in /proc/self/cgroup")


def capable(runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> bool:
    """True iff a transient user scope with cgroup v2 memory+cpu ACCOUNTING is
    usable here. Probes the EXACT properties the real resource pass sets
    (`MemoryAccounting`/`CPUAccounting`), not a bare `true` scope — on a host where
    the cpu controller is not delegated to user scopes, `-p CPUAccounting=yes`
    fails, so we fall back to timing-only (§15) instead of silently recording
    `cpu_time_s=0`. `taskset` is deliberately NOT checked here: it gates only the
    ONE_CORE affinity floor (`collector._taskset_available`), never all-cores
    memory measurement, so a box without `taskset` still measures memory."""
    if shutil.which("systemd-run") is None:
        return False
    if not (_CGROUP_ROOT / "cgroup.controllers").exists():
        return False
    try:
        proc = runner(
            [
                "systemd-run", "--user", "--scope", "--quiet",
                "-p", "MemoryAccounting=yes", "-p", "CPUAccounting=yes", "true",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0
```

> Note (kernel/delegation floor): `memory.peak` requires kernel ≥ 5.19 and cpu/memory accounting requires the controllers to be delegated to the `--user` scope. The probe above catches the common non-delegated case; the residual ("scope starts but `cpu.stat` reports nothing") is caught by the real-scope integration test (Task 4 Step 5, asserts `cpu_time_s > 0`) and the mandatory httpx bench (Task 10). A kernel without `memory.peak` degrades gracefully — `read_cgroup_stats` raises `OSError`, the in-scope wrapper records `cgroup: None`, and `memory`/`cpu_time_s` come back `None`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_measure.py -v`
Expected: PASS (the three reader tests).

- [ ] **Step 5: Commit**

```bash
git add src/typebench/measure.py tests/test_measure.py
git commit -m "feat(measure): cgroup v2 stats reader + capability probe (pydantic-free)"
```

---

### Task 3: in-scope wrapper CLI (`measure.main`) — runs the checker, reads its own cgroup before exit

**Files:**
- Modify: `src/typebench/measure.py`
- Test: `tests/test_measure.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_measure.py`:

```python
# NOTE: hoist these imports into the top import block of tests/test_measure.py
# (ruff E402/I001) — shown here for locality only.
import json
import sys

import pytest

from typebench import measure


def test_measure_main_runs_command_and_writes_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Point _self_cgroup_dir at a fixture dir so the test needs no real cgroup.
    cg = tmp_path / "cg"
    cg.mkdir()
    _write_cgroup(cg, peak=12345)
    monkeypatch.setattr(measure, "_self_cgroup_dir", lambda: cg)
    out = tmp_path / "payload.json"
    rc = measure.main(
        ["--out", str(out), "--timeout", "30", "--",
         sys.executable, "-c", "import sys; sys.stdout.write('hi'); sys.exit(1)"]
    )
    assert rc == 0  # wrapper always exits 0; outcome is in the payload
    payload = json.loads(out.read_text())
    assert payload["exit_code"] == 1
    assert payload["stdout"] == "hi"
    assert payload["cgroup"]["peak_bytes"] == 12345
    assert payload["cgroup"]["oom_kill"] == 0


def test_measure_main_payload_cgroup_none_when_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom() -> Path:
        raise OSError("no cgroup")
    monkeypatch.setattr(measure, "_self_cgroup_dir", _boom)
    out = tmp_path / "p.json"
    measure.main(["--out", str(out), "--timeout", "30", "--",
                  sys.executable, "-c", "pass"])
    payload = json.loads(out.read_text())
    assert payload["exit_code"] == 0
    assert payload["cgroup"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_measure.py::test_measure_main_runs_command_and_writes_payload -v`
Expected: FAIL — `AttributeError: module 'typebench.measure' has no attribute 'main'`.

- [ ] **Step 3: Add `main()` to `measure.py`**

Append to `src/typebench/measure.py`:

```python
def _sample_to_dict(sample: CgroupSample) -> dict[str, object]:
    return {
        "peak_bytes": sample.peak_bytes,
        "cpu_usage_usec": sample.cpu_usage_usec,
        "cpu_user_usec": sample.cpu_user_usec,
        "cpu_system_usec": sample.cpu_system_usec,
        "oom_kill": sample.oom_kill,
        "mem_stat": sample.mem_stat,
    }


def main(raw_args: list[str] | None = None) -> int:
    """In-scope wrapper. Run as `systemd-run --user --scope -- python -m
    typebench.measure --out FILE --timeout S -- <argv>`. Runs the checker to
    completion, then — WHILE STILL INSIDE THE SCOPE (§5.5 read-before-teardown) —
    reads its own cgroup and writes a JSON payload (outcome + cgroup sample) to
    --out. Always exits 0 so systemd-run sees success; the real outcome is in the
    payload. The checker output is captured by run_command (bounded diagnostics
    text), and this Python process's small footprint is a ~constant per-tool
    baseline charged to the scope (Decision F)."""
    parser = argparse.ArgumentParser(prog="typebench.measure")
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    ns = parser.parse_args(raw_args)
    argv = ns.argv[1:] if ns.argv and ns.argv[0] == "--" else ns.argv

    raw = run_command(argv, timeout=ns.timeout)
    try:
        sample = read_cgroup_stats(_self_cgroup_dir())
        cgroup: dict[str, object] | None = _sample_to_dict(sample)
    except OSError:
        cgroup = None

    payload = {
        "exit_code": raw.exit_code,
        "signal": raw.signal,
        "timed_out": raw.timed_out,
        "oom": raw.oom,
        "env_error": raw.env_error,
        "stdout": raw.stdout,
        "stderr": raw.stderr,
        "cgroup": cgroup,
    }
    Path(ns.out).write_text(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_measure.py -v`
Expected: PASS (all measure tests).

- [ ] **Step 5: Add the pydantic-free import-guard test**

Append to `tests/test_measure.py`:

```python
# Hoist into the top import block (ruff E402/I001):
import subprocess as _sp


def test_measure_import_does_not_pull_pydantic() -> None:
    # measure runs as a child process under systemd-run; pydantic startup cost
    # would tax every scoped run. Keep it stdlib + typebench.wrapper only.
    code = (
        "import sys, typebench.measure\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] == 'pydantic')\n"
        "print(','.join(bad))\n"
    )
    out = _sp.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "", f"pydantic leaked into measure import: {out.stdout!r}"
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_measure.py::test_measure_import_does_not_pull_pydantic -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/typebench/measure.py tests/test_measure.py
git commit -m "feat(measure): in-scope wrapper CLI reads cgroup before teardown + import guard"
```

---

### Task 4: `scoped_probe` orchestrator — M repeats, injectable runner, aggregation

**Files:**
- Modify: `src/typebench/measure.py`
- Test: `tests/test_measure.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_measure.py`:

```python
# Hoist these into the top import block of tests/test_measure.py (ruff E402/I001):
#   import json, subprocess as _sp
#   from collections.abc import Callable
#   from pathlib import Path
#   import pytest
#   from typebench import measure
#   from typebench.measure import ResourceResult, scoped_probe
from collections.abc import Callable

import pytest

from typebench import measure
from typebench.measure import ResourceResult, scoped_probe

Runner = Callable[..., "_sp.CompletedProcess[str]"]


def _fake_runner_factory(payloads: list[dict[str, object]]) -> Runner:
    """Return a runner that, on each call, writes the next payload to the --out
    path embedded in the systemd-run command and returns success (returncode 0)."""
    calls = {"i": 0}

    def runner(cmd: list[str], **kwargs: object) -> "_sp.CompletedProcess[str]":
        out_path = cmd[cmd.index("--out") + 1]
        Path(out_path).write_text(json.dumps(payloads[calls["i"]]))
        calls["i"] += 1
        return _sp.CompletedProcess(cmd, 0, "", "")

    return runner


def _payload(
    peak: int, usage: int = 1000, user: int = 600, system: int = 400, oom: int = 0
) -> dict[str, object]:
    return {
        "exit_code": 1, "signal": None, "timed_out": False, "oom": False,
        "env_error": False, "stdout": "found 3 errors", "stderr": "",
        "cgroup": {
            "peak_bytes": peak, "cpu_usage_usec": usage, "cpu_user_usec": user,
            "cpu_system_usec": system, "oom_kill": oom, "mem_stat": {"anon": peak},
        },
    }


def test_scoped_probe_aggregates_min_median_max() -> None:
    runner = _fake_runner_factory([_payload(100), _payload(140), _payload(120)])
    res = scoped_probe(["mypy", "."], extra_env={}, timeout=60, repeats=3, runner=runner)
    assert isinstance(res, ResourceResult)
    assert res.raw.exit_code == 1
    assert res.raw.stdout == "found 3 errors"
    assert res.memory is not None
    assert res.memory.peak_bytes_min == 100
    assert res.memory.peak_bytes_median == 120
    assert res.memory.peak_bytes_max == 140
    assert res.cpu_time_s == 0.001  # median usage_usec 1000 -> 0.001 s
    assert res.oom is False


def test_scoped_probe_first_run_is_the_probe() -> None:
    # raw (exit/stdout for classify+parse) comes from run #1 when ALL repeats are
    # measured-success, not a later repeat.
    runner = _fake_runner_factory(
        [{**_payload(100), "stdout": "RUN1"}, {**_payload(100), "stdout": "RUN2"}]
    )
    res = scoped_probe(["t"], extra_env={}, timeout=60, repeats=2, runner=runner)
    assert res.raw.stdout == "RUN1"


def test_scoped_probe_oom_killed_repeat_folds_into_raw_oom() -> None:
    # The CGROUP oom signal (memory.events.oom_kill) on ANY repeat must reach
    # res.raw.oom so adapter.classify -> FAILED_OOM. Run #1 succeeds; repeat 2 is
    # oom-killed -> the failure wins and raw.oom is True (Decision E + I).
    runner = _fake_runner_factory([_payload(100), _payload(100, oom=1)])
    res = scoped_probe(["t"], extra_env={}, timeout=60, repeats=2, runner=runner)
    assert res.oom is True
    assert res.raw.oom is True  # the fold actually happened (regression guard)


def test_scoped_probe_first_generic_failure_becomes_authoritative() -> None:
    # Run #1 is clean-ish (exit 1 = diagnostics) but repeat 2 timed out -> the
    # record must reflect the failure, never the lucky run #1.
    timed_out = {**_payload(100), "timed_out": True, "exit_code": -1}
    runner = _fake_runner_factory([{**_payload(100), "stdout": "OK1"}, timed_out])
    res = scoped_probe(["t"], extra_env={}, timeout=60, repeats=2, runner=runner)
    assert res.raw.timed_out is True


def test_scoped_probe_memory_none_when_cgroup_missing() -> None:
    runner = _fake_runner_factory([{**_payload(100), "cgroup": None}])
    res = scoped_probe(["t"], extra_env={}, timeout=60, repeats=1, runner=runner)
    assert res.memory is None
    assert res.cpu_time_s is None
    assert res.raw.exit_code == 1  # outcome still recovered


def test_scoped_probe_prepare_runs_before_every_repeat() -> None:
    # Cold runs: the cache-clear callback must fire before EACH repeat (§5.2).
    runner = _fake_runner_factory([_payload(100), _payload(100), _payload(100)])
    calls = {"n": 0}

    def prepare() -> None:
        calls["n"] += 1

    scoped_probe(["t"], extra_env={}, timeout=60, repeats=3, runner=runner, prepare=prepare)
    assert calls["n"] == 3


def test_scoped_probe_skips_repeat_that_raises_but_uses_survivors() -> None:
    # A transient harness exception on one repeat is skipped; survivors still
    # produce memory. The pass never lets the harness error escape.
    good = [_payload(100), _payload(140)]
    state = {"i": 0}

    def runner(cmd: list[str], **kwargs: object) -> "_sp.CompletedProcess[str]":
        i = state["i"]
        state["i"] += 1
        if i == 1:
            raise OSError("transient scope race")
        out_path = cmd[cmd.index("--out") + 1]
        Path(out_path).write_text(json.dumps(good.pop(0)))
        return _sp.CompletedProcess(cmd, 0, "", "")

    res = scoped_probe(["t"], extra_env={}, timeout=60, repeats=3, runner=runner)
    assert res.memory is not None
    assert res.memory.runs == 2  # 3 repeats, middle one skipped


def test_scoped_probe_raises_measure_error_when_no_payload() -> None:
    # Every scope fails to launch (returncode != 0, no payload) -> MeasureError so
    # the collector can fall back to a plain probe (Decision J).
    def runner(cmd: list[str], **kwargs: object) -> "_sp.CompletedProcess[str]":
        return _sp.CompletedProcess(cmd, 1, "", "scope failed")

    with pytest.raises(measure.MeasureError):
        scoped_probe(["t"], extra_env={}, timeout=60, repeats=3, runner=runner)


def test_scoped_probe_rejects_zero_repeats() -> None:
    with pytest.raises(ValueError, match="repeats must be >= 1"):
        scoped_probe(["t"], extra_env={}, timeout=60, repeats=0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_measure.py::test_scoped_probe_aggregates_min_median_max -v`
Expected: FAIL — `ImportError: cannot import name 'ResourceResult'`.

- [ ] **Step 3: Add the orchestrator**

Add to the imports at the top of `src/typebench/measure.py`:

```python
import os
import statistics
```

And extend the existing `typebench.wrapper` import (added in Task 2) to bring in the generic classifier:

```python
from typebench.wrapper import RawRun, run_command, universal_failure_prefix
```

Append to `src/typebench/measure.py`:

```python
class MeasureError(RuntimeError):
    """Raised when the scoped resource pass produced NO usable payload across all
    repeats (every transient scope failed to launch / write). The collector
    catches this and falls back to a plain probe so a record is still produced
    (Decision J — never drop a record)."""


@dataclass(frozen=True)
class MemorySummary:
    """Aggregated peak memory + the median-peak run's memory.stat snapshot."""

    runs: int
    peak_bytes_min: int
    peak_bytes_median: int
    peak_bytes_max: int
    memory_stat: dict[str, int]


@dataclass(frozen=True)
class ResourceResult:
    """Outcome of the resource pass. `raw` is the AUTHORITATIVE outcome across
    repeats (run #1 if all succeeded, else the first generically-failing repeat;
    drives classify + parse). `memory`/`cpu_time_s` are None when no run yielded a
    cgroup sample (§5.5). `oom` is True if any repeat was cgroup-OOM-killed."""

    raw: RawRun
    memory: MemorySummary | None
    cpu_time_s: float | None
    oom: bool


def _raw_from_payload(p: dict[str, object], *, oom_killed: bool) -> RawRun:
    return RawRun(
        exit_code=int(p["exit_code"]),
        signal=p["signal"] if isinstance(p["signal"], int) else None,
        timed_out=bool(p["timed_out"]),
        # Real cgroup OOM (memory.events.oom_kill, read in-scope) is folded in here
        # so adapter.classify -> universal_failure_prefix -> FAILED_OOM. Without this
        # OR-in, the in-scope run_command's oom (always False) would mask a real OOM.
        oom=bool(p["oom"]) or oom_killed,
        stdout=str(p["stdout"]),
        stderr=str(p["stderr"]),
        env_error=bool(p["env_error"]),
    )


def _median_int(values: list[int]) -> int:
    return int(statistics.median(values))


def scoped_probe(
    argv: list[str],
    extra_env: dict[str, str],
    timeout: float,
    repeats: int,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    prepare: Callable[[], None] | None = None,
) -> ResourceResult:
    """Run `argv` under a transient cgroup v2 scope `repeats` times, reading peak
    memory + cpu.stat + oom before each scope is torn down (via the in-scope
    `main` wrapper). Every repeat is classified; the authoritative RawRun is run #1
    unless a later repeat hit a GENERIC failure (env/oom/timeout/crash-signal), in
    which case the first such failure wins — a flaky 1-in-M crash/OOM must record a
    failure, not a "clean" (Decision I). `prepare` (the checker cache-clear) runs
    before EVERY repeat to keep each cold (§5.2, Decision J). `extra_env` (e.g.
    TY_MAX_PARALLELISM) is forwarded via the child env and explicit --setenv.
    `runner` is injectable for tests.

    Best-effort: a per-repeat harness failure (systemd-run nonzero, missing/corrupt
    payload, timeout, scope race) skips that repeat. If NO repeat yields a payload,
    raises MeasureError so the collector can fall back to a plain probe — it never
    drops the record and never lets a harness error escape as the checker's result."""
    if repeats < 1:
        raise ValueError(f"repeats must be >= 1, got {repeats}")
    run_env = {**os.environ, **extra_env}
    setenv_args = [arg for k, v in extra_env.items() for arg in ("--setenv", f"{k}={v}")]

    raws: list[RawRun] = []
    peaks: list[int] = []
    cpu_usages: list[int] = []
    mem_stats: list[dict[str, int]] = []
    oom = False

    for _ in range(repeats):
        try:
            # Cold run: clear the checker cache before EVERY repeat (§5.2), exactly
            # as the timing pass does via --prepare. Without this, repeats 2..M run
            # with a warm checker cache and under-report peak memory + cpu-time.
            # Inside the try so a flaky clear skips this repeat, not the whole pass.
            if prepare is not None:
                prepare()
            with tempfile.TemporaryDirectory() as tmp:
                out_path = Path(tmp) / "payload.json"
                cmd = [
                    "systemd-run", "--user", "--scope", "--quiet",
                    "-p", "MemoryAccounting=yes", "-p", "CPUAccounting=yes",
                    *setenv_args, "--",
                    sys.executable, "-m", "typebench.measure",
                    "--out", str(out_path), "--timeout", str(timeout), "--", *argv,
                ]
                # Give systemd-run headroom over the inner timeout so the wrapper,
                # not the runner, owns timeout handling.
                proc = runner(
                    cmd, capture_output=True, text=True, env=run_env, timeout=timeout + 120
                )
                if proc.returncode != 0:
                    # Scope setup failed (no user bus, controller not delegated,
                    # --setenv rejected). The wrapper never ran -> no payload here.
                    continue
                payload: dict[str, object] = json.loads(out_path.read_text())
        except (OSError, ValueError, subprocess.SubprocessError):
            # Transient harness failure for THIS repeat (timeout, missing/corrupt
            # payload, scope race). Skip it; other repeats may still succeed.
            continue

        cg = payload.get("cgroup")
        oom_killed = isinstance(cg, dict) and int(cg["oom_kill"]) > 0
        raws.append(_raw_from_payload(payload, oom_killed=oom_killed))
        if isinstance(cg, dict):
            peaks.append(int(cg["peak_bytes"]))
            cpu_usages.append(int(cg["cpu_usage_usec"]))
            mem_stats.append({str(k): int(v) for k, v in dict(cg["mem_stat"]).items()})
            if oom_killed:
                oom = True

    if not raws:
        raise MeasureError("scoped resource pass produced no usable payload")

    # Authoritative outcome: run #1 is the probe, but a GENERIC failure on ANY
    # repeat (env/oom/timeout/crash-signal) means the tool did not reliably
    # complete -> surface the first such failure (Decision I).
    authoritative = raws[0]
    for r in raws:
        if universal_failure_prefix(r) is not None:
            authoritative = r
            break

    if not peaks:
        return ResourceResult(raw=authoritative, memory=None, cpu_time_s=None, oom=oom)

    median_peak = _median_int(peaks)
    # memory.stat snapshot of the run whose peak is the median (representative).
    rep_idx = min(range(len(peaks)), key=lambda j: abs(peaks[j] - median_peak))
    memory = MemorySummary(
        runs=len(peaks),
        peak_bytes_min=min(peaks),
        peak_bytes_median=median_peak,
        peak_bytes_max=max(peaks),
        memory_stat=mem_stats[rep_idx],
    )
    cpu_time_s = _median_int(cpu_usages) / 1_000_000
    return ResourceResult(raw=authoritative, memory=memory, cpu_time_s=cpu_time_s, oom=oom)
```

> `universal_failure_prefix` lives in the pydantic-free `typebench.wrapper`, so importing it keeps the `measure` import guard green. The pyrefly-flaky-exit-1-on-a-repeat case is NOT caught here (it needs the adapter); that is the same accepted residual the timing pass documents in `wrapper.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_measure.py -v`
Expected: PASS (all orchestrator + reader + main tests).

- [ ] **Step 5: Add a capability-gated REAL integration test**

Append to `tests/test_measure.py`:

```python
import pytest

from typebench import measure as _measure


@pytest.mark.skipif(not _measure.capable(), reason="no cgroup v2 / systemd-run user scope")
def test_scoped_probe_real_scope_measures_nonzero_peak() -> None:
    # Real systemd-run scope on a capable host (WSL2/Linux CI). Allocates ~40 MB
    # in a child and asserts the scope's peak captured it.
    argv = [sys.executable, "-c", "x = bytearray(40_000_000); print(len(x))"]
    res = _measure.scoped_probe(argv, extra_env={}, timeout=60, repeats=3)
    assert res.raw.exit_code == 0
    assert res.memory is not None
    assert res.memory.peak_bytes_max >= 30_000_000
    # > 0, NOT >= 0: a host where the cpu controller is not delegated to user
    # scopes reads an empty cpu.stat -> cpu_time_s 0. Asserting > 0 turns that
    # silent-zero into a real failure on a box that claims to be capable.
    assert res.cpu_time_s is not None and res.cpu_time_s > 0
    assert res.oom is False
```

- [ ] **Step 6: Run test to verify it passes (on this capable box)**

Run: `uv run pytest tests/test_measure.py::test_scoped_probe_real_scope_measures_nonzero_peak -v`
Expected: PASS on WSL2 (capable); SKIPPED on a non-capable host.

- [ ] **Step 7: Commit**

```bash
git add src/typebench/measure.py tests/test_measure.py
git commit -m "feat(measure): scoped_probe orchestrator — M-repeat peak/cpu/oom aggregation"
```

---

### Task 5: Affinity prefix + `hard_cap` recording in the collector (no resource pass yet)

**Files:**
- Modify: `src/typebench/collector.py`
- Test: `tests/test_collector.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_collector.py`:

```python
# Hoist into the top import block of tests/test_collector.py (ruff E402/I001):
#   from typebench.wrapper import RawRun
from typebench.wrapper import RawRun


def _stub_raw() -> RawRun:
    return RawRun(exit_code=0, signal=None, timed_out=False, oom=False,
                  stdout='{"diagnostics": 0, "files": 1}', stderr="")


def test_one_core_prepends_taskset_and_enforces(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run_command(
        argv: list[str], timeout: float, env: dict[str, str] | None = None
    ) -> RawRun:
        captured["argv"] = argv
        return _stub_raw()

    monkeypatch.setattr(collector, "run_command", fake_run_command)
    monkeypatch.setattr(collector, "_taskset_available", lambda: True)

    adapter = StubAdapter(exit_code=0, diagnostics=0, files=1)
    result = run_single(
        adapter, project="demo", config=NormalizedConfig(),
        thread_mode=ThreadMode.ONE_CORE, warmup=1, runs=2, timeout=10,
    )
    assert captured["argv"][:3] == ["taskset", "-c", "0"]
    assert result.thread_mode_enforced is True
    assert result.hard_cap is True  # stub cap is hard
    assert result.cap_mechanism == "cpu-affinity"


def test_one_core_without_taskset_is_not_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collector, "_taskset_available", lambda: False)
    adapter = StubAdapter(exit_code=0, diagnostics=0, files=1)
    result = run_single(
        adapter, project="demo", config=NormalizedConfig(),
        thread_mode=ThreadMode.ONE_CORE, warmup=1, runs=2, timeout=10,
    )
    # taskset missing -> we did NOT pin -> must not claim enforcement OR the cap
    # (the adapter mechanism string bakes in "cpu-affinity"), §5.3 honesty.
    assert result.thread_mode_enforced is False
    assert result.hard_cap is None
    assert result.cap_mechanism is None


def test_all_cores_no_taskset_no_enforcement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collector, "_taskset_available", lambda: True)
    captured: dict[str, list[str]] = {}

    def fake_run_command(
        argv: list[str], timeout: float, env: dict[str, str] | None = None
    ) -> RawRun:
        captured["argv"] = argv
        return _stub_raw()

    monkeypatch.setattr(collector, "run_command", fake_run_command)
    adapter = StubAdapter(exit_code=0, diagnostics=0, files=1)
    result = run_single(
        adapter, project="demo", config=NormalizedConfig(),
        thread_mode=ThreadMode.ALL_CORES, warmup=1, runs=2, timeout=10,
    )
    assert captured["argv"][0] != "taskset"  # ALL_CORES is never pinned
    assert result.thread_mode_enforced is False
    assert result.hard_cap is None  # cap recorded only for the constrained track
    assert result.cap_mechanism is None
```

Also add an autouse fixture at the TOP of `tests/test_collector.py` (after imports) so the existing tests never trigger a real scoped resource pass on a capable host:

```python
@pytest.fixture(autouse=True)
def _disable_resource_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep collector unit tests hermetic + fast: force the plain (non-scoped)
    # probe path. Dedicated resource-pass behaviour is covered in Task 6's tests.
    # raising=False is the ONE justified use: in TDD order this fixture is written
    # before the `_resource_capable` seam (added in Task 6 Step 3) exists.
    monkeypatch.setattr(collector, "_resource_capable", lambda: False, raising=False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_collector.py::test_one_core_prepends_taskset_and_enforces -v`
Expected: FAIL — `AttributeError: <module 'collector'> has no attribute '_taskset_available'`.

- [ ] **Step 3: Add affinity + cap recording to the collector**

In `src/typebench/collector.py`, add imports + helpers. After the existing imports add:

```python
import shutil
```

(if not already imported — it is; keep one import.) Add near the top, after the `if TYPE_CHECKING` block:

```python
_AFFINITY_PREFIX = ["taskset", "-c", "0"]  # uniform single-core floor (spec §5.3)


def _taskset_available() -> bool:
    return shutil.which("taskset") is not None


def _apply_affinity(
    argv: list[str], thread_mode: ThreadMode
) -> tuple[list[str], bool]:
    """Prepend the uniform single-core affinity prefix for the ONE_CORE track.
    Returns (argv, enforced). ALL_CORES is unconstrained by design (not pinned).
    enforced is True ONLY when ONE_CORE AND taskset is actually available — the
    honesty flag must never claim a pin we could not apply (§5.3, Decision D)."""
    if thread_mode is ThreadMode.ONE_CORE and _taskset_available():
        return ([*_AFFINITY_PREFIX, *argv], True)
    return (argv, False)
```

Now thread the affinity + cap through `run_single`. Replace the block that builds `argv` and runs the probe:

```python
        try:
            argv, extra_env = adapter.command(project, config, thread_mode, workdir)
        except (OSError, ValueError) as exc:
```

stays as-is up to the end of that `except` (the construction-failure RunResult). After the `except` block closes, the current code is:

```python
        # Phase 1: probe — one real run to classify and parse counts.
        raw = run_command(argv, timeout=timeout, env=extra_env)
```

Replace those two lines with:

```python
        # Apply the uniform 1-core affinity prefix (ONE_CORE only) BEFORE any run,
        # so probe + resource + timing all share the same pinned command (§5.3).
        argv, thread_enforced = _apply_affinity(argv, thread_mode)
        cap = adapter.parallelism_cap(thread_mode)
        # The adapter mechanism strings bake in "cpu-affinity" (Plan 4's floor), so
        # record the cap ONLY when affinity actually ran. On ONE_CORE without
        # taskset (mac/dev), or on ALL_CORES, record neither — never claim a pin we
        # did not apply (§5.3 honesty, Decision A).
        record_cap = thread_mode is ThreadMode.ONE_CORE and thread_enforced
        hard_cap = cap.hard_cap if record_cap else None
        cap_mechanism = cap.mechanism if record_cap else None

        # Phase 1: probe — one real run to classify and parse counts.
        raw = run_command(argv, timeout=timeout, env=extra_env)
```

In the final `return RunResult(...)`, replace:

```python
            # Plan 1 applies no CPU affinity/cap -> never claim an unenforced mode (§5.3).
            thread_mode_enforced=False,
```

with:

```python
            thread_mode_enforced=thread_enforced,
            hard_cap=hard_cap,
            cap_mechanism=cap_mechanism,
```

(The construction-failure early-return keeps `thread_mode_enforced=False` and omits `hard_cap`/`cap_mechanism` — they default to None, correct for a run that never started.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_collector.py -v`
Expected: PASS (new affinity tests + all existing collector tests via the autouse fixture).

- [ ] **Step 5: Commit**

```bash
git add src/typebench/collector.py tests/test_collector.py
git commit -m "feat(collector): uniform taskset affinity + hard_cap recording (ONE_CORE)"
```

---

### Task 6: Wire the resource pass into the collector (scoped probe, cpu_time, parallel_efficiency, oom)

**Files:**
- Modify: `src/typebench/collector.py`
- Test: `tests/test_collector.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_collector.py`:

```python
# Hoist into the top import block of tests/test_collector.py (ruff E402/I001):
#   from typebench.measure import MemorySummary, ResourceResult
#   from typebench.models import TimingStats
from typebench.measure import MemorySummary, ResourceResult
from typebench.models import TimingStats


def test_resource_pass_populates_memory_cpu_efficiency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Force capability ON but inject a fake scoped_probe (no real systemd needed).
    # raising=True: the _resource_capable/_scoped_probe seams exist by now (Step 3),
    # so a typo'd seam name fails loudly instead of silently creating a no-op.
    monkeypatch.setattr(collector, "_resource_capable", lambda: True)

    def fake_scoped_probe(
        argv: list[str],
        extra_env: dict[str, str],
        timeout: float,
        repeats: int,
        runner: object = None,
        prepare: object = None,
    ) -> ResourceResult:
        return ResourceResult(
            raw=RawRun(exit_code=0, signal=None, timed_out=False, oom=False,
                       stdout='{"diagnostics": 0, "files": 5}', stderr=""),
            memory=MemorySummary(runs=3, peak_bytes_min=10, peak_bytes_median=12,
                                 peak_bytes_max=15, memory_stat={"anon": 12}),
            cpu_time_s=2.0,
            oom=False,
        )

    monkeypatch.setattr(collector, "_scoped_probe", fake_scoped_probe)

    # parallel_efficiency = cpu_time / wall is computed ONLY when timing ran, and
    # the collector gates timing on `shutil.which("hyperfine")`. Patch which so the
    # stubbed run_timing is actually invoked on hosts WITHOUT hyperfine (else this
    # test silently passes only where hyperfine happens to be installed).
    monkeypatch.setattr(collector.shutil, "which", lambda _name: "/usr/bin/hyperfine")
    monkeypatch.setattr(
        collector, "run_timing",
        lambda *a, **k: TimingStats(runs=2, min_s=1.0, median_s=4.0, mean_s=4.0,
                                    stddev_s=0.0, max_s=5.0, times_s=[4.0, 4.0]),
    )

    adapter = StubAdapter(exit_code=0, diagnostics=0, files=5)
    result = run_single(
        adapter, project="demo", config=NormalizedConfig(),
        thread_mode=ThreadMode.ALL_CORES, warmup=1, runs=2, timeout=10,
        mem_runs=3,
    )
    assert result.result_class == ResultClass.CLEAN
    assert result.files == 5
    assert result.memory is not None and result.memory.peak_bytes_median == 12
    assert result.cpu_time_s == 2.0
    assert result.parallel_efficiency == 0.5  # cpu 2.0 / wall median 4.0


def test_resource_pass_oom_reclassifies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collector, "_resource_capable", lambda: True)

    def fake_scoped_probe(
        argv: list[str],
        extra_env: dict[str, str],
        timeout: float,
        repeats: int,
        runner: object = None,
        prepare: object = None,
    ) -> ResourceResult:
        return ResourceResult(
            raw=RawRun(exit_code=-1, signal=9, timed_out=False, oom=True,
                       stdout="", stderr="killed"),
            memory=None, cpu_time_s=None, oom=True,
        )

    monkeypatch.setattr(collector, "_scoped_probe", fake_scoped_probe)
    adapter = StubAdapter(exit_code=0)
    result = run_single(
        adapter, project="demo", config=NormalizedConfig(),
        thread_mode=ThreadMode.ALL_CORES, warmup=1, runs=2, timeout=10,
    )
    assert result.result_class == ResultClass.FAILED_OOM
    assert result.oom is True
    assert result.timing is None  # OOM -> not a measured success -> no timing


def test_resource_pass_falls_back_to_plain_probe_on_measure_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A TOTAL resource-pass failure (MeasureError) must NOT drop the record: the
    # collector falls back to a plain run_command probe (Decision J).
    monkeypatch.setattr(collector, "_resource_capable", lambda: True)

    def boom_scoped_probe(
        argv: list[str],
        extra_env: dict[str, str],
        timeout: float,
        repeats: int,
        runner: object = None,
        prepare: object = None,
    ) -> ResourceResult:
        raise measure.MeasureError("no usable payload")

    monkeypatch.setattr(collector, "_scoped_probe", boom_scoped_probe)
    monkeypatch.setattr(collector.shutil, "which", lambda _name: None)  # skip timing

    adapter = StubAdapter(exit_code=0, diagnostics=0, files=5)
    result = run_single(
        adapter, project="demo", config=NormalizedConfig(),
        thread_mode=ThreadMode.ALL_CORES, warmup=1, runs=2, timeout=10,
    )
    # Record still produced via the plain probe; just no memory.
    assert result.result_class == ResultClass.CLEAN
    assert result.files == 5
    assert result.memory is None
    assert result.cpu_time_s is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_collector.py::test_resource_pass_populates_memory_cpu_efficiency -v`
Expected: FAIL — `TypeError: run_single() got an unexpected keyword argument 'mem_runs'`.

- [ ] **Step 3: Wire the resource pass into `run_single`**

In `src/typebench/collector.py`, add the seams near the affinity helpers:

```python
from typebench import measure
from typebench.models import CalibrationStats, MemoryStats

# Module seams (overridable in tests + by capability):
_resource_capable = measure.capable
_scoped_probe = measure.scoped_probe
```

Extend the `run_single` signature. Replace:

```python
def run_single(
    adapter: Adapter,
    project: str,
    config: NormalizedConfig,
    thread_mode: ThreadMode,
    warmup: int,
    runs: int,
    timeout: float,
) -> RunResult:
```

with:

```python
def run_single(
    adapter: Adapter,
    project: str,
    config: NormalizedConfig,
    thread_mode: ThreadMode,
    warmup: int,
    runs: int,
    timeout: float,
    mem_runs: int = 3,
    measure_enabled: bool = True,
    calibration: CalibrationStats | None = None,
) -> RunResult:
```

Add a boundary guard as the FIRST statement of `run_single` (Decision K — no production `assert`, defend direct callers):

```python
    if mem_runs < 1:
        raise ValueError(f"mem_runs must be >= 1, got {mem_runs}")
    adapter.clear_cache(project)
```

(the `adapter.clear_cache(project)` line already exists — the guard goes immediately above it.)

Replace the probe block (added in Task 5):

```python
        # Phase 1: probe — one real run to classify and parse counts.
        raw = run_command(argv, timeout=timeout, env=extra_env)
```

with the capability-branched probe + resource pass:

```python
        # Phase 1: probe. When measurement is available, the probe runs UNDER a
        # transient cgroup scope (M COLD repeats) so it yields peak memory +
        # cpu-time + real OOM in one pass (§5.5); the authoritative repeat drives
        # classify+parse and a generic failure on any repeat is surfaced
        # (Decision I). A TOTAL harness failure (MeasureError / OSError / timeout /
        # bad JSON) falls back to a plain run_command so the record is NEVER dropped
        # (Decision J). The prepare callback clears the checker cache before each
        # cold repeat (§5.2).
        resource: measure.ResourceResult | None = None
        if measure_enabled and _resource_capable():
            try:
                resource = _scoped_probe(
                    argv,
                    extra_env=extra_env,
                    timeout=timeout,
                    repeats=mem_runs,
                    prepare=lambda: adapter.clear_cache(project),
                )
            except (measure.MeasureError, OSError, ValueError, subprocess.SubprocessError):
                resource = None
        if resource is not None:
            raw = resource.raw
            memory_summary = resource.memory
            cpu_time_s = resource.cpu_time_s
        else:
            raw = run_command(argv, timeout=timeout, env=extra_env)
            memory_summary = None
            cpu_time_s = None
```

The existing classify/parse/timing block stays. After the timing block, before building the final `RunResult`, compute parallel efficiency and the pydantic MemoryStats. Insert right before `error_detail = None`:

```python
        memory = (
            MemoryStats(
                runs=memory_summary.runs,
                peak_bytes_min=memory_summary.peak_bytes_min,
                peak_bytes_median=memory_summary.peak_bytes_median,
                peak_bytes_max=memory_summary.peak_bytes_max,
                memory_stat=memory_summary.memory_stat,
            )
            if memory_summary is not None
            else None
        )
        parallel_efficiency = (
            cpu_time_s / timing.median_s
            if cpu_time_s is not None and timing is not None and timing.median_s > 0
            else None
        )
```

In the final `return RunResult(...)`, add these to the keyword args (alongside `timing=timing`):

```python
            memory=memory,
            cpu_time_s=cpu_time_s,
            parallel_efficiency=parallel_efficiency,
            calibration=calibration,
```

Note: OOM reclassification works because `scoped_probe`'s `_raw_from_payload` folds `memory.events.oom_kill > 0` into `raw.oom` (Decision E), and the existing `result_class` derivation runs `adapter.classify(raw)` → `universal_failure_prefix` → `FAILED_OOM`. The `oom=raw.oom` field is already in the final RunResult (from Plan 1), so `result.oom` matches the `FAILED_OOM` class. The regression guard for the fold is `test_scoped_probe_oom_killed_repeat_folds_into_raw_oom` (Task 4); the collector-level reclassification guard is `test_resource_pass_oom_reclassifies` here. No extra wiring in the final `RunResult` beyond the existing `oom=raw.oom`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_collector.py -v`
Expected: PASS (resource-pass tests + all existing).

- [ ] **Step 5: Commit**

```bash
git add src/typebench/collector.py tests/test_collector.py
git commit -m "feat(collector): scoped resource pass — peak memory, cpu-time, parallel efficiency, cgroup OOM"
```

---

### Task 7: Calibration workload + `calibrate()`

**Files:**
- Create: `src/typebench/calibration.py`
- Test: `tests/test_calibration.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_calibration.py`:

```python
import sys

import pytest

from typebench.calibration import ITERATIONS, WORKLOAD_ID, calibrate
from typebench.models import CalibrationStats


def test_calibrate_returns_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    # Inject a deterministic timer so the test is fast + stable.
    times = iter([0.0, 0.20, 0.0, 0.21, 0.0, 0.19])  # start/stop pairs

    import typebench.calibration as cal
    monkeypatch.setattr(cal.time, "perf_counter", lambda: next(times))
    monkeypatch.setattr(cal, "_run_workload", lambda: None)  # skip real CPU work

    stats = calibrate(runs=3)
    assert isinstance(stats, CalibrationStats)
    assert stats.workload_id == WORKLOAD_ID
    assert stats.iterations == ITERATIONS
    assert stats.runs == 3
    assert stats.raw_min_s == 0.19
    assert round(stats.raw_median_s, 2) == 0.20
    assert stats.raw_max_s == 0.21


def test_calibration_workload_is_deterministic_and_cpu_bound() -> None:
    # The real workload runs without error and consumes measurable time.
    import typebench.calibration as cal
    import time
    t0 = time.perf_counter()
    cal._run_workload()
    assert time.perf_counter() - t0 > 0.0


def test_calibration_import_does_not_pull_pydantic() -> None:
    import subprocess
    code = (
        "import sys, typebench.calibration\n"
        "print(','.join(sorted(m for m in sys.modules if m.split('.')[0]=='pydantic')))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_calibration.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'typebench.calibration'`.

- [ ] **Step 3: Create `calibration.py`**

Create `src/typebench/calibration.py`:

```python
"""Calibration baseline (spec §5.7). A fixed, dep-free CPU-bound Python workload
timed alongside each run so weekly trends can be normalized against the VM-to-VM
hardware lottery. Pydantic-free except the final stats construction is done by the
caller (collector); this module returns plain floats via `calibrate`.

The workload identity is LOCKED by WORKLOAD_ID + ITERATIONS — changing either is a
new workload id (the manifest records it). Normalization (raw / reference) is a
render-time transform (Plan 5); we store RAW seconds only (Decision C)."""

from __future__ import annotations

import statistics
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Annotation-only import: keeps `import typebench.calibration` pydantic-free at
    # runtime (the import-guard test asserts this) while letting pyrefly resolve the
    # `calibrate` return type — no `# noqa` needed. The real construction import is
    # lazy, inside calibrate().
    from typebench.models import CalibrationStats

# Locked workload identity. Bump the version suffix if the loop body or ITERATIONS
# changes — trend continuity depends on a stable workload.
WORKLOAD_ID = "calib-pyloop-v1"
# Tuned for ~0.2-0.4 s on a modern single core. The ABSOLUTE time is irrelevant
# (it is a relative hardware scalar); only stability + determinism matter.
ITERATIONS = 5_000_000


def _run_workload() -> None:
    """Deterministic integer/float CPU-bound loop. No allocation growth, no I/O,
    no randomness — pure ALU work whose wall-time scales inversely with core speed.
    A documented limitation (§5.7): this calibrates Python/CPU speed, not the Rust
    (pyrefly/ty) or Node (pyright) runtimes; inter-checker ratios + CPU-model
    segmentation cover the residual. It is a coarse hardware scalar by design."""
    acc = 0
    x = 1.0
    for i in range(ITERATIONS):
        acc = (acc + i * 2654435761) & 0xFFFFFFFF
        x = x * 1.0000001 + 1.0
    # Consume results so the loop cannot be optimized away (CPython does not, but
    # be explicit for clarity / future runtimes).
    if acc == -1 and x == 0.0:  # pragma: no cover - never true
        raise RuntimeError("unreachable")


def calibrate(runs: int = 5) -> CalibrationStats:
    """Time the workload `runs` times and return raw min/median/max seconds.
    `CalibrationStats` is imported lazily so this module stays pydantic-free on
    import; the return annotation resolves via the TYPE_CHECKING import above."""
    if runs < 1:
        raise ValueError(f"calibration runs must be >= 1, got {runs}")
    from typebench.models import CalibrationStats

    samples: list[float] = []
    for _ in range(runs):
        start = time.perf_counter()
        _run_workload()
        samples.append(time.perf_counter() - start)
    return CalibrationStats(
        workload_id=WORKLOAD_ID,
        iterations=ITERATIONS,
        runs=runs,
        raw_min_s=min(samples),
        raw_median_s=statistics.median(samples),
        raw_max_s=max(samples),
    )


def main(raw_args: list[str] | None = None) -> int:
    """CLI: `python -m typebench.calibration` prints the calibration JSON. Useful
    for a standalone calibration probe / debugging."""
    import argparse

    parser = argparse.ArgumentParser(prog="typebench.calibration")
    parser.add_argument("--runs", type=int, default=5)
    ns = parser.parse_args(raw_args)
    stats = calibrate(runs=ns.runs)
    print(stats.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
```

Note: the `calibrate` return annotation resolves via the `TYPE_CHECKING` import (no `# noqa`, no RUF100), and the real construction import is lazy inside the function, so `import typebench.calibration` does not pull pydantic (the import-guard test asserts this). `from __future__ import annotations` means the annotation is never evaluated at runtime. `main()` imports pydantic transitively via `calibrate`, which is fine — `main` is not the hot import path.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_calibration.py -v`
Expected: PASS (all three).

- [ ] **Step 5: Verify the import guard explicitly**

Run: `uv run python -c "import sys, typebench.calibration; print([m for m in sys.modules if m.startswith('pydantic')])"`
Expected: `[]`

- [ ] **Step 6: Commit**

```bash
git add src/typebench/calibration.py tests/test_calibration.py
git commit -m "feat(calibration): fixed dep-free CPU workload + calibrate() baseline (calib-pyloop-v1)"
```

---

### Task 8: CLI wiring — calibrate once, thread resource/calibration options into `run`

**Files:**
- Modify: `src/typebench/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py` (mirror the existing `run` CLI test style — inspect the file for the runner/import pattern already used and reuse it):

```python
# Hoist into the top import block of tests/test_cli.py (ruff E402/I001):
#   import pytest
#   from typebench import cli
#   from typebench.models import (CalibrationStats, EnvFingerprint, ResultClass,
#                                 RunResult, ThreadMode)
import pytest

from typebench import cli
from typebench.models import (
    CalibrationStats, EnvFingerprint, ResultClass, RunResult, ThreadMode,
)


def _fake_result() -> RunResult:
    # Fixed values keep the fake type-safe under pyrefly strict (kwargs are typed
    # `object`); the test asserts against `captured`, not this record's fields.
    return RunResult(
        tool="stub", tool_version="0", project="demo",
        thread_mode=ThreadMode.ALL_CORES, result_class=ResultClass.CLEAN,
        real_exit_code=0,
        env=EnvFingerprint(os="Linux", kernel="x", cpu_model="x",
                           core_count=1, python_version="3.12.0"),
    )


def test_run_passes_measure_and_calibration_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Capture what run_single receives so we assert the flags + calibration wire
    # through, without invoking real systemd / hyperfine.
    captured: dict[str, object] = {}

    def fake_run_single(adapter: object, **kwargs: object) -> RunResult:
        captured.update(kwargs)
        return _fake_result()

    monkeypatch.setattr(cli, "run_single", fake_run_single)

    sentinel = CalibrationStats(workload_id="calib-pyloop-v1", iterations=1, runs=1,
                                raw_min_s=0.1, raw_median_s=0.1, raw_max_s=0.1)
    monkeypatch.setattr(cli, "calibrate", lambda runs: sentinel)

    out = tmp_path / "r.json"
    # Use the existing CliRunner pattern from this test module:
    result = _invoke_run(  # helper already present in tests/test_cli.py
        ["--tool", "stub", "--project", "demo", "--output", str(out),
         "--mem-runs", "4", "--no-calibrate"]
    )
    assert result.exit_code == 0
    assert captured["mem_runs"] == 4
    assert captured["calibration"] is None  # --no-calibrate -> not computed


def test_run_calibrates_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def fake_run_single(adapter: object, **kwargs: object) -> RunResult:
        captured.update(kwargs)
        return _fake_result()

    monkeypatch.setattr(cli, "run_single", fake_run_single)
    sentinel = CalibrationStats(workload_id="calib-pyloop-v1", iterations=1, runs=1,
                                raw_min_s=0.1, raw_median_s=0.1, raw_max_s=0.1)
    monkeypatch.setattr(cli, "calibrate", lambda runs: sentinel)

    out = tmp_path / "r.json"
    result = _invoke_run(["--tool", "stub", "--project", "demo", "--output", str(out)])
    assert result.exit_code == 0
    assert captured["calibration"] is sentinel


def test_run_rejects_zero_mem_runs(tmp_path: Path) -> None:
    out = tmp_path / "r.json"
    result = _invoke_run(
        ["--tool", "stub", "--project", "demo", "--output", str(out), "--mem-runs", "0"]
    )
    assert result.exit_code == 2  # controlled validation error, not a crash
```

If `tests/test_cli.py` has no `_invoke_run` helper, add one near the top that wraps the existing `CliRunner().invoke(app, ["run", *args])` pattern the file already uses (match the import of `app`/`CliRunner` already there) and annotate it, e.g. `def _invoke_run(args: list[str]) -> Result:` (the `click.testing.Result` that `CliRunner.invoke` returns).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_run_passes_measure_and_calibration_flags -v`
Expected: FAIL — unknown option `--mem-runs` (exit code 2) or `run_single` missing kwargs.

- [ ] **Step 3: Add the imports + options + wiring to `cli.py`**

In `src/typebench/cli.py`, add to imports:

```python
from typebench.calibration import calibrate
```

In the `run` command signature, after the existing `warmup` option, add:

```python
    mem_runs: Annotated[int, typer.Option(help="Resource-pass repeats (peak memory variance, spec §5.5). >=1; >=3 for official numbers.")] = 3,
    measure: Annotated[bool, typer.Option(help="Run the cgroup memory/CPU pass (auto-skips if unavailable).")] = True,
    calibrate_baseline: Annotated[bool, typer.Option("--calibrate/--no-calibrate", help="Time the calibration workload (spec §5.7).")] = True,
    calib_runs: Annotated[int, typer.Option(help="Calibration workload repeats (>=1).")] = 5,
```

Replace the `run_single(...)` call:

```python
    result = run_single(
        adapter,
        project=project,
        config=config,
        thread_mode=thread_mode,
        warmup=warmup,
        runs=runs,
        timeout=timeout,
    )
```

with:

```python
    if mem_runs < 1:
        typer.echo("--mem-runs must be >= 1 (>= 3 recommended, spec §5.5).", err=True)
        raise typer.Exit(code=2)
    if calib_runs < 1:
        typer.echo("--calib-runs must be >= 1.", err=True)
        raise typer.Exit(code=2)
    calibration = calibrate(runs=calib_runs) if calibrate_baseline else None
    result = run_single(
        adapter,
        project=project,
        config=config,
        thread_mode=thread_mode,
        warmup=warmup,
        runs=runs,
        timeout=timeout,
        mem_runs=mem_runs,
        measure_enabled=measure,
        calibration=calibration,
    )
```

> Place the two guards alongside the existing fail-fast checks (e.g. right after the `--output` writable check), so a bad count errors before any measurement work. These mirror the §5.5 floor (Decision K); the `run_single`/`scoped_probe`/`calibrate` `ValueError` guards are the defense-in-depth layer for non-CLI callers.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS (new CLI tests + existing).

- [ ] **Step 5: Commit**

```bash
git add src/typebench/cli.py tests/test_cli.py
git commit -m "feat(cli): wire resource pass + calibration into run (--mem-runs/--measure/--calibrate)"
```

---

### Task 9: Pay down the `wrapper.py` OOM comment + AGENTS.md scope/layout

**Files:**
- Modify: `src/typebench/wrapper.py`
- Modify: `AGENTS.md`

- [ ] **Step 1: Update the `wrapper.py` OOM comment (no behavior change)**

In `src/typebench/wrapper.py`, replace the comment at lines ~16-18:

```python
# OOM-killer signal. A bare SIGKILL with no cgroup OOM flag is treated as an
# OOM heuristic until cgroup OOM detection lands (Plan 4 sets RawRun.oom).
_SIGKILL = 9
```

with:

```python
# OOM-killer signal. A bare SIGKILL is the OOM heuristic on the FALLBACK (non-
# cgroup) probe path. The cgroup-scoped resource pass (typebench.measure, Plan 4)
# sets RawRun.oom authoritatively from memory.events.oom_kill when available.
_SIGKILL = 9
```

- [ ] **Step 2: Verify wrapper tests still pass (incl. the pydantic guard)**

Run: `uv run pytest tests/test_wrapper.py -v`
Expected: PASS (comment-only change).

- [ ] **Step 3: Update `AGENTS.md`**

In `AGENTS.md`, find the module-layout section and add the two new modules:

```markdown
- `measure.py` — resource pass (spec §5.5): cgroup v2 peak memory + CPU-time +
  OOM under a transient `systemd-run --scope`. Pydantic-free (runs as a scoped
  child); capability-gated with a timing-only fallback on mac/CI.
- `calibration.py` — fixed dep-free CPU workload (`calib-pyloop-v1`) timed per run
  for VM-to-VM trend normalization (spec §5.7). Pydantic-free import.
```

**Do NOT add the "Plan 4 DONE" scope-by-plan note here.** The modules above are factual (they now exist), but marking the plan *done* before the mandatory real httpx bench (Task 10) would be a premature claim. The scope-by-plan stamp is added as the final step of Task 10, only after the bench is green.

- [ ] **Step 4: Commit**

```bash
git add src/typebench/wrapper.py AGENTS.md
git commit -m "docs(wrapper): OOM now wired via cgroup; AGENTS module-layout note"
```

---

### Task 10: Full gate + REAL corpus bench (mandatory before declaring done)

**Files:** none (verification only)

> Hard rule from the handover: run a real corpus bench, not just fixtures/tests, before declaring an env phase done — the ad-hoc httpx bench has caught bugs every reviewer + every test missed.

- [ ] **Step 1: Run the full quality gate**

Run: `uv run ruff format && uv run ruff check && uv run pyrefly check && uv run pytest`
Expected: ruff clean; pyrefly strict 0 errors; all tests pass (new + existing), the real-scope measure test PASSES on this WSL2 box (not skipped).

- [ ] **Step 2: Real httpx ×4 bench — ALL_CORES, with resource + calibration**

Run each real tool once through the full Plan 4 path via the **corpus-driven** `run` path (it prepares httpx and resolves `--src-root`/`--venv`/`--python-version`/`--python-platform` from `suite.toml`, so they are NOT passed manually). Run for each `TOOL` in `mypy pyright ty pyrefly`:

```bash
uv run typebench run \
  --tool "$TOOL" --corpus corpus/suite.toml --corpus-project httpx \
  --cache-root typebench-cache \
  --thread-mode all-cores --runs 3 --warmup 1 --mem-runs 3 \
  --output "/tmp/tb-$TOOL-all.json"
```

Note: `--cache-root typebench-cache` is **non-hidden on purpose** — the CLI default (`DEFAULT_CACHE_ROOT = Path("typebench-cache")`) avoids a dot-directory because pyrefly skips dot-dirs during discovery (a hidden cache makes the corpus invisible to pyrefly ALONE → a neutrality defect). Do NOT use `.typebench-cache`.

Expected per record: `result_class` in {clean, diagnostics}; `memory.peak_bytes_median > 0`; `cpu_time_s > 0`; `parallel_efficiency` present (≈1 for mypy/pyright, possibly >1 for pyrefly/ty under all-cores); `calibration.raw_median_s > 0`; `thread_mode_enforced: false` (ALL_CORES); `hard_cap: null` (ALL_CORES).

- [ ] **Step 3: Real httpx ×4 bench — ONE_CORE (the affinity floor)**

Same loop with `--thread-mode 1-core-constrained`:

```bash
uv run typebench run \
  --tool "$TOOL" --corpus corpus/suite.toml --corpus-project httpx \
  --cache-root typebench-cache \
  --thread-mode 1-core-constrained --runs 3 --warmup 1 --mem-runs 3 \
  --output "/tmp/tb-$TOOL-1core.json"
```

Expected per record: `thread_mode_enforced: true` (taskset present on this box); `hard_cap` matches each adapter's declaration (mypy/pyrefly/stub `true`, ty/pyright `false`); `cap_mechanism` populated; `parallel_efficiency ≈ 1.0` for the parallel tools too (pinned to one core, so CPU-time ≈ wall-time).

- [ ] **Step 4: Sanity-check the records by eye**

Run: `for f in /tmp/tb-*-1core.json; do echo "== $f =="; uv run python -c "import json,sys; d=json.load(open(sys.argv[1])); print({k:d[k] for k in ('tool','result_class','thread_mode_enforced','hard_cap','cap_mechanism','cpu_time_s','parallel_efficiency')}); print('peak_med', d['memory'] and d['memory']['peak_bytes_median'])" "$f"; done`
Expected: every tool measured-success, sensible peak (mypy/pyright tens-hundreds MB, ty/pyrefly lower), `parallel_efficiency` near 1.0 under 1-core, OOM false everywhere.

- [ ] **Step 5: Investigate ANY anomaly before declaring done**

If `parallel_efficiency` for a parallel tool under 1-core is far from ~1, or peak memory is implausibly low (harness-only baseline → checker didn't actually run under the scope), or `thread_mode_enforced` is false on this capable box → STOP and debug (likely: env vars not reaching the scope, taskset prefix lost through the scope, or the scope tearing down before the read). These are exactly the drift bugs the real bench exists to catch.

- [ ] **Step 6: Re-run the full gate after any fix**

Run: `uv run ruff format && uv run ruff check && uv run pyrefly check && uv run pytest`
Expected: fully green.

- [ ] **Step 7: Final commit (only if Step 5 required a fix)**

```bash
git add -A
git commit -m "fix(measure): <specific drift the real httpx bench surfaced>"
```

- [ ] **Step 8: Stamp `AGENTS.md` "Plan 4 DONE" — ONLY now that the bench is green**

The full gate AND the real httpx ×4 bench (both thread modes) now pass, so the "done" claim is earned. Add the scope-by-plan note deferred from Task 9. In the scope-by-plan / "Ask first" section of `AGENTS.md`, add:

```markdown
- **Plan 4 (DONE):** `taskset -c 0` affinity floor (ONE_CORE), cgroup resource
  pass, calibration baseline. `RunResult` is now **v2** (MemoryStats/CalibrationStats
  + cpu_time_s/parallel_efficiency/hard_cap/cap_mechanism). The measured path
  (`wrapper.py`, `taxonomy.py`, `measure.py`, `calibration.py`) stays pydantic-free.
```

```bash
git add AGENTS.md
git commit -m "docs(agents): mark Plan 4 done (verified by real httpx bench)"
```

---

## Self-Review (run before handing off)

**1. Spec coverage:**
- §5.1 "memory/CPU pass judged by the same classification, not raw exit code" → every resource repeat is classified; first generic failure wins (Decision I, Task 4). ✓
- §5.2 "cache cleared before EVERY measured run" → resource loop calls a `prepare` cache-clear before each repeat, like the timing pass's `--prepare` (Decision J, Tasks 4, 6). ✓
- §5.3 threads/affinity → Task 5 (affinity prefix, enforced flag); `hard_cap`/`cap_mechanism` recorded ONLY when affinity actually ran (Decision A). ✓
- §5.5 memory + CPU-time (memory.peak/memory.stat/cpu.stat, read-before-teardown, M≥3, min/median/max) → Tasks 2-4, 6. ✓
- §5.5 "peak cgroup memory not RSS" labelling → `MemoryStats` docstring (Task 1), Decision F. ✓
- §5.7 calibration (fixed CPU-bound reference, raw + normalized, workload id in manifest) → Task 7; normalized = render-time (Decision C, flagged). ✓
- §8 metrics (peak min/median/max, cpu-time, parallel efficiency, calibration variants) → Tasks 1, 6, 7. ✓
- §12 "never drop a record" → `MeasureError` + collector try/except → plain-probe fallback (Decision J, Tasks 4, 6). ✓
- §15 mac/CI = timing-only → capability gate (probes the real accounting props) + fallback (Decision G, Task 2). ✓
- Handover "wrapper.py:16 → RawRun.oom" → cgroup `oom_kill` folded into `RawRun.oom` in `_raw_from_payload` (Decision E, Tasks 4, 6, 9). ✓
- Handover "flip thread_mode_enforced only when affinity applied" → Decision D + Task 5 tests. ✓

**2. Placeholder scan:** No TBD/TODO/"add error handling"/"similar to Task N" — every code step shows complete code. ✓ (The one `<specific drift...>` in Task 10 Step 7 is a commit-message slot for a fix that may not be needed, not a code placeholder.)

**3. Type consistency:** `MemoryStats`/`CalibrationStats`/`MemorySummary`/`ResourceResult`/`CgroupSample`/`MeasureError` names are consistent across measure.py ↔ models.py ↔ collector.py. `scoped_probe(argv, extra_env, timeout, repeats, runner, prepare)` signature matches its callers in collector (`prepare=lambda: adapter.clear_cache(project)`) + every fake in tests (which all accept `runner=None, prepare=None`). `calibrate(runs)` matches cli + tests. `_resource_capable`/`_scoped_probe`/`_taskset_available` seams match their monkeypatch sites (`raising=True` post-Task-6; `raising=False` only on the Task 5 autouse fixture). ✓

**4. Gate-cleanliness (added this revision):** existing `schema_version == 1` assertion bumped to 2 (Task 1 Step 5b); no `# noqa` in `calibration.py` (TYPE_CHECKING import); all new test helpers/fixtures annotated and all snippet imports hoisted to the top block (Conventions callout); `--mem-runs`/`--calib-runs` and `run_single`/`scoped_probe`/`calibrate` validate `>= 1` (no production `assert`). ✓

## ⚠️ Two sub-decisions flagged for your review before execution

1. **Decision C — calibration stores raw seconds, NO `factor`.** The AskUserQuestion option text mentioned a "normalized factor"; I store raw min/median/max instead and defer the normalize-divide to the Plan 5 renderer, because baking a `factor` now needs an arbitrary magic reference constant we can't honestly establish from one machine. Veto if you want a `factor` field now (I'd need you to specify the reference anchor).
2. **Decision D — `thread_mode_enforced` is ONE_CORE-only** (ALL_CORES stays False). Keeps the flag literal + zero churn to existing tests. Veto if you'd rather it read True for ALL_CORES ("unconstrained mode faithfully realized").
