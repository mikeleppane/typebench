# typebench Engine Spine — Implementation Plan (Plan 1 of 6)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the measurement spine — run any checker invocation cold, classify the outcome against the failure taxonomy, time it with `hyperfine`, and emit one validated `results.json` — proven end-to-end against a stub checker.

**Architecture:** Adapter-driven engine (spec §3). Plan 1 implements the neutral core + a `StubAdapter` (a controllable fake checker) so the whole pipeline — probe → classify → time → collect → serialize — is testable with zero real-checker or cgroup dependencies. Real adapters, corpus, cgroup memory, and rendering arrive in later plans.

**Tech Stack:** Python 3.12+, `uv` (env + packaging), `pydantic` v2 (models + schema + JSON), `typer` (CLI), `pytest` (tests), `hyperfine` (external binary, timing). **Quality gate: `ruff` (lint + format, strict rule set) + `pyrefly` (strict preset) + `pre-commit`** — typebench dogfoods pyrefly on itself. cgroup/`systemd-run`, `scc`, and real checkers are NOT used in Plan 1.

**Spec:** `docs/superpowers/specs/2026-06-07-typebench-design.md`

---

## Quality Gate (MANDATORY — runs before every commit)

Every task's code must pass, and `pre-commit` (installed in Task 1) enforces it automatically on `git commit`:

```bash
uv run ruff format .          # format (must leave no changes)
uv run ruff check .           # lint (strict rule set, zero findings)
uv run pyrefly check          # strict-preset type check, zero errors
uv run pytest                 # tests green
```

**Rules:** no `# noqa` / `# type: ignore` / `# pyrefly: ignore` without an inline reason and reviewer awareness. The gate is non-negotiable — a task is not "done" until all four are clean. If strict pyrefly flags real issues in example code from this plan, fix the code to satisfy strict mode (that is the point of the gate), keeping behavior identical.

**Annotation convention (strict pyrefly):** every function — production code *and* tests — is fully annotated: parameters and return type. The snippets below show this on the smoke test (`def test_...() -> None:`); apply the same to every test in later tasks (`-> None`, and annotate fixtures, e.g. `tmp_path: Path`, `exit_code: int`, `expected: ResultClass`). This is mechanical and keeps the strict gate uniform across `src` and `tests`.

---

## Milestone Roadmap (context — only Plan 1 is detailed here)

1. **Plan 1 — Engine spine (this doc):** scaffold, models+schema, failure taxonomy, exit-code wrapper, adapter protocol + stub adapter, hyperfine timing pass, collector, `typebench run` CLI → `results.json`. Timing-only, stub-tested, cross-platform. **Shippable.**
2. **Plan 2 — Real adapters + normalized config (§6):** mypy/pyright/pyrefly/ty adapters, per-tool flag translation of the locked §6 policies, distribution verification, machine-readable `parse`, per-tool `classify_exit`.
3. **Plan 3 — Corpus + envman + preflight:** `suite.toml`, clone@SHA, `uv` venv, locked deps, `scc` LOC, preflight gate, lock manifest (§9).
4. **Plan 4 — Memory/CPU + thread tracks + calibration:** cgroup v2 `MemoryProbe` (peak + `cpu.stat`), 1-core-constrained vs all-cores (§5.3), parallel efficiency, calibration baseline + normalization (§5.7), statistics (min/median/IQR/MAD + outlier policy, §5.6).
5. **Plan 5 — Renderer + GH Pages:** results→README tables, trend site, calibration-normalized series, bump annotations (§11).
6. **Plan 6 — CI/CD:** weekly sharded run, monthly PR-gated corpus bump, PR smoke gate, cost budget (§10).

---

## File Structure (Plan 1)

| File | Responsibility |
|------|----------------|
| `pyproject.toml` | uv project, deps, pytest + ruff (strict) + pyrefly (strict) config |
| `.pre-commit-config.yaml` | local hooks: ruff check, ruff format, pyrefly strict (versions from uv.lock) |
| `src/typebench/__init__.py` | package marker + version |
| `src/typebench/models.py` | `ResultClass`, `ThreadMode` enums; `TimingStats`, `EnvFingerprint`, `RunResult` pydantic models (results schema — incl. failure metadata + `thread_mode_enforced`) |
| `src/typebench/env.py` | `detect_env() -> EnvFingerprint` |
| `src/typebench/wrapper.py` | `RawRun` (incl. `env_error`), `run_command()` (captures env errors + OOM heuristic), `classify_default()`, and the `python -m typebench.wrapper` CLI used as hyperfine's command (normalizes exit codes) |
| `src/typebench/adapters/base.py` | `Adapter` protocol (final-ish §4 surface) + `ParallelismCap` |
| `src/typebench/adapters/stub.py` | `StubAdapter` driving the in-package fake checker |
| `src/typebench/_fake_checker.py` | controllable fake checker (exit code, sleep, signal, diagnostics, files); ships in the wheel so `typebench run --tool stub` works after install |
| `src/typebench/timing.py` | `parse_hyperfine_json()` (pure) + `run_timing()` (invokes hyperfine) |
| `src/typebench/collector.py` | `run_single()` pipeline → `RunResult` |
| `src/typebench/cli.py` | `typer` app, `typebench run`, adapter registry |
| `tests/test_*.py` | one test module per source module + an end-to-end test covering every taxonomy class |

---

## Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `.pre-commit-config.yaml`
- Create: `src/typebench/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/test_smoke.py`

- [ ] **Step 1: Write the failing test**

`tests/test_smoke.py`:
```python
import typebench


def test_package_has_version() -> None:
    assert isinstance(typebench.__version__, str)
    assert typebench.__version__
```

- [ ] **Step 2: Create `pyproject.toml`** (deps + strict ruff + strict pyrefly + pytest)

```toml
[project]
name = "typebench"
version = "0.1.0"
description = "Neutral, reproducible Python type-checker performance benchmark"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.6",
    "typer>=0.12",
]

[project.scripts]
typebench = "typebench.cli:app"

[dependency-groups]
dev = [
    "pytest>=8.0",
    "ruff>=0.8",
    "pyrefly>=0.16",
    "pre-commit>=3.7",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/typebench"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]

# ---------------------------------------------------------------------------
# Ruff — strict, industry-standard rule set. ruff format owns line wrapping.
# ---------------------------------------------------------------------------
[tool.ruff]
line-length = 100
target-version = "py312"
src = ["src", "tests"]

[tool.ruff.lint]
select = [
    "E", "W",   # pycodestyle
    "F",        # pyflakes
    "I",        # isort
    "N",        # pep8-naming
    "UP",       # pyupgrade
    "B",        # flake8-bugbear
    "C4",       # flake8-comprehensions
    "SIM",      # flake8-simplify
    "PTH",      # flake8-use-pathlib
    "RET",      # flake8-return
    "ARG",      # flake8-unused-arguments
    "TID",      # flake8-tidy-imports
    "TC",       # flake8-type-checking
    "PL",       # pylint
    "RUF",      # ruff-specific
]

[tool.ruff.lint.pylint]
max-args = 8  # run_single / CLI legitimately take several explicit params

[tool.ruff.lint.per-file-ignores]
# Tests may use magic values and unused fixture args.
"tests/**" = ["PLR2004", "ARG001", "ARG002"]
# Adapter implementations conform to the Protocol signature, so they accept
# args the stub does not use yet (project/thread_mode/stderr); real adapters
# (Plan 2) use them. Renaming to _-prefixed would break keyword calls.
"src/typebench/adapters/**" = ["ARG002"]

[tool.ruff.format]
docstring-code-format = true

# ---------------------------------------------------------------------------
# Pyrefly — STRICT preset. typebench dogfoods the checker it benchmarks.
# project-includes at the root prevents pyrefly's "no project root -> 0 errors"
# silent no-op (verified against the live config schema: Preset::Strict).
# ---------------------------------------------------------------------------
[tool.pyrefly]
project-includes = ["src", "tests"]
search-path = ["src"]
python-version = "3.12"
python-platform = "linux"
preset = "strict"
```

- [ ] **Step 3: Create the pre-commit config**

`.pre-commit-config.yaml` — all hooks are `local`/`system` so they run the exact
ruff and pyrefly pinned in `uv.lock` (one source of truth, no network, no rev drift):
```yaml
repos:
  - repo: local
    hooks:
      - id: ruff-check
        name: ruff check
        entry: uv run ruff check --fix
        language: system
        types_or: [python, pyi]
        require_serial: true
      - id: ruff-format
        name: ruff format
        entry: uv run ruff format
        language: system
        types_or: [python, pyi]
        require_serial: true
      - id: pyrefly
        name: pyrefly check (strict)
        entry: uv run pyrefly check
        language: system
        types: [python]
        pass_filenames: false
```

- [ ] **Step 4: Create package + test package markers**

`src/typebench/__init__.py`:
```python
"""typebench — neutral Python type-checker performance benchmark."""

__version__ = "0.1.0"
```

`tests/__init__.py`:
```python
```

- [ ] **Step 5: Install deps and the git hook**

Run:
```bash
cd /home/mikelep/personal/dev/typebench
uv sync
uv run pre-commit install
```
Expected: env synced; `pre-commit installed at .git/hooks/pre-commit`.

- [ ] **Step 6: Run the full quality gate + test**

Run:
```bash
uv run ruff format .
uv run ruff check .
uv run pyrefly check
uv run pytest tests/test_smoke.py -v
```
Expected: ruff format reports "1 file reformatted" or "left unchanged"; ruff check passes (`All checks passed!`); pyrefly reports `0 errors`; pytest 1 passed.

- [ ] **Step 7: Commit** (pre-commit re-runs the gate automatically)

```bash
git add pyproject.toml uv.lock .pre-commit-config.yaml src/ tests/
git commit -m "feat(scaffold): package skeleton with uv, strict ruff + pyrefly, pre-commit"
```

---

## Task 2: Results schema (models)

**Files:**
- Create: `src/typebench/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

`tests/test_models.py`:
```python
import json

import pytest

from typebench.models import (
    EnvFingerprint,
    ResultClass,
    RunResult,
    ThreadMode,
    TimingStats,
)


def test_result_class_measured_success():
    assert ResultClass.CLEAN.is_measured_success
    assert ResultClass.DIAGNOSTICS.is_measured_success
    assert not ResultClass.FAILED_ENV.is_measured_success
    assert not ResultClass.FAILED_CRASH.is_measured_success
    assert not ResultClass.FAILED_TIMEOUT.is_measured_success
    assert not ResultClass.FAILED_OOM.is_measured_success


def test_result_class_values_match_taxonomy():
    # Spec §7 taxonomy strings, stable on disk.
    assert ResultClass.FAILED_ENV.value == "failed{env}"
    assert ResultClass.FAILED_TIMEOUT.value == "failed{timeout}"


def _env() -> EnvFingerprint:
    return EnvFingerprint(
        os="Linux",
        kernel="6.6.0",
        cpu_model="Test CPU",
        core_count=8,
        python_version="3.12.0",
    )


def test_run_result_round_trips_through_json():
    result = RunResult(
        tool="stub",
        tool_version="1.0",
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.DIAGNOSTICS,
        real_exit_code=1,
        diagnostics=3,
        files=10,
        timing=TimingStats(
            runs=3,
            min_s=0.10,
            median_s=0.11,
            mean_s=0.12,
            stddev_s=0.01,
            max_s=0.14,
            times_s=[0.10, 0.11, 0.14],
        ),
        env=_env(),
    )
    blob = result.model_dump_json()
    restored = RunResult.model_validate_json(blob)
    assert restored == result
    assert restored.schema_version == 1
    assert restored.thread_mode_enforced is False  # default; Plan 4 sets it true
    assert json.loads(blob)["result_class"] == "diagnostics"


def test_timing_is_optional_for_failures():
    result = RunResult(
        tool="stub",
        tool_version="1.0",
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.FAILED_CRASH,
        real_exit_code=139,
        env=_env(),
    )
    assert result.timing is None
    assert result.diagnostics is None


def test_run_result_rejects_unknown_fields():
    with pytest.raises(Exception):
        RunResult.model_validate(
            {
                "tool": "stub",
                "tool_version": "1.0",
                "project": "demo",
                "thread_mode": "all-cores",
                "result_class": "clean",
                "real_exit_code": 0,
                "env": _env().model_dump(),
                "bogus": True,
            }
        )


def test_thread_mode_enforced_defaults_false():
    # Plan 1 records the requested thread_mode but applies no CPU affinity, so
    # the JSON must never claim a methodology that was not enforced (spec §5.3).
    result = RunResult(
        tool="stub",
        tool_version="1.0",
        project="demo",
        thread_mode=ThreadMode.ONE_CORE,
        result_class=ResultClass.CLEAN,
        real_exit_code=0,
        env=_env(),
    )
    assert result.thread_mode_enforced is False


def test_failure_metadata_round_trips():
    # Enough detail to audit failed{env} vs failed{crash} after the fact (spec §5.1).
    result = RunResult(
        tool="stub",
        tool_version="1.0",
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.FAILED_ENV,
        real_exit_code=-1,
        signal=None,
        timed_out=False,
        oom=False,
        error_detail="No such file or directory: 'typebench-nonexistent-checker'",
        env=_env(),
    )
    restored = RunResult.model_validate_json(result.model_dump_json())
    assert restored == result
    assert restored.error_detail is not None
    assert restored.timing is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'typebench.models'`.

- [ ] **Step 3: Implement `models.py`**

```python
"""Results schema — the on-disk contract (spec §7, §8, §9)."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class ResultClass(str, Enum):
    """Spec §7 failure taxonomy. String values are stable on disk."""

    CLEAN = "clean"
    DIAGNOSTICS = "diagnostics"
    FAILED_ENV = "failed{env}"
    FAILED_CRASH = "failed{crash}"
    FAILED_TIMEOUT = "failed{timeout}"
    FAILED_OOM = "failed{oom}"

    @property
    def is_measured_success(self) -> bool:
        """clean and diagnostics are successes; only real failures are excluded."""
        return self in (ResultClass.CLEAN, ResultClass.DIAGNOSTICS)


class ThreadMode(str, Enum):
    """Spec §5.3. A literal '1 thread' is not claimed; the floor is 1-core."""

    ONE_CORE = "1-core-constrained"
    ALL_CORES = "all-cores"


class TimingStats(BaseModel):
    """Wall-time statistics from one hyperfine timing pass (spec §5.4)."""

    model_config = ConfigDict(extra="forbid")

    runs: int
    min_s: float
    median_s: float
    mean_s: float
    stddev_s: float
    max_s: float
    times_s: list[float]


class EnvFingerprint(BaseModel):
    """Minimal environment stamp (spec §9 — expanded in later plans)."""

    model_config = ConfigDict(extra="forbid")

    os: str
    kernel: str
    cpu_model: str
    core_count: int
    python_version: str


class RunResult(BaseModel):
    """One (project x tool x thread-mode) measurement record.

    Plan 1 writes ONE record per file. The eventual results file (Plan 5)
    wraps many records in an envelope ({schema_version, runs: [...]}); the
    per-record schema_version below versions the record shape until then."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    tool: str
    tool_version: str
    project: str
    thread_mode: ThreadMode
    # Honesty flag (spec §5.3): True only once CPU affinity / a hard cap is
    # actually applied (Plan 4). Plan 1 never enforces, so it stays False — the
    # record must not imply a methodology the engine did not run.
    thread_mode_enforced: bool = False
    result_class: ResultClass
    real_exit_code: int
    # Failure metadata — enough to audit failed{env} vs failed{crash}/{oom}
    # after the fact (spec §5.1). None/False on success.
    signal: int | None = None
    timed_out: bool = False
    oom: bool = False
    error_detail: str | None = None
    diagnostics: int | None = None
    files: int | None = None
    timing: TimingStats | None = None
    env: EnvFingerprint
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/typebench/models.py tests/test_models.py
git commit -m "feat(models): results schema with failure taxonomy enum"
```

---

## Task 3: Environment fingerprint

**Files:**
- Create: `src/typebench/env.py`
- Test: `tests/test_env.py`

- [ ] **Step 1: Write the failing test**

`tests/test_env.py`:
```python
from typebench.env import detect_env
from typebench.models import EnvFingerprint


def test_detect_env_returns_populated_fingerprint():
    env = detect_env()
    assert isinstance(env, EnvFingerprint)
    assert env.os  # e.g. "Linux"
    assert env.core_count >= 1
    assert env.python_version.count(".") >= 2
    assert env.cpu_model  # never empty; falls back to a placeholder
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_env.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'typebench.env'`.

- [ ] **Step 3: Implement `env.py`**

```python
"""Environment fingerprint (spec §9). Expanded with cgroup/lock data later."""

from __future__ import annotations

import os
import platform
from pathlib import Path

from typebench.models import EnvFingerprint


def _cpu_model() -> str:
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or "unknown"


def detect_env() -> EnvFingerprint:
    return EnvFingerprint(
        os=platform.system(),
        kernel=platform.release(),
        cpu_model=_cpu_model(),
        core_count=os.cpu_count() or 1,
        python_version=platform.python_version(),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_env.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add src/typebench/env.py tests/test_env.py
git commit -m "feat(env): minimal environment fingerprint"
```

---

## Task 4: Command runner + default classifier

**Files:**
- Create: `src/typebench/wrapper.py`
- Test: `tests/test_wrapper.py`

- [ ] **Step 1: Write the failing test**

`tests/test_wrapper.py`:
```python
import os
import sys

import pytest

from typebench.models import ResultClass
from typebench.wrapper import RawRun, classify_default, run_command


def test_run_command_captures_clean_exit() -> None:
    raw = run_command([sys.executable, "-c", "print('hi')"], timeout=10)
    assert raw.exit_code == 0
    assert raw.timed_out is False
    assert raw.env_error is False
    assert "hi" in raw.stdout
    assert raw.signal is None


def test_run_command_captures_nonzero_exit() -> None:
    raw = run_command([sys.executable, "-c", "import sys; sys.exit(1)"], timeout=10)
    assert raw.exit_code == 1


def test_run_command_times_out() -> None:
    raw = run_command([sys.executable, "-c", "import time; time.sleep(5)"], timeout=1)
    assert raw.timed_out is True


def test_run_command_reports_env_error_for_missing_binary() -> None:
    # A missing executable is an environment failure, not a crash: run_command
    # captures it (does NOT propagate) so the collector can record failed{env}.
    raw = run_command(["typebench-nonexistent-checker-xyz"], timeout=10)
    assert raw.env_error is True
    assert raw.timed_out is False
    assert raw.stderr  # carries the OSError text for the audit trail


@pytest.mark.skipif(os.name != "posix", reason="signal semantics are POSIX-specific")
def test_run_command_records_signal() -> None:
    # SIGSEGV (-11) -> Python returncode is negative.
    raw = run_command(
        [sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGSEGV)"],
        timeout=10,
    )
    assert raw.signal == 11


def test_classify_default_maps_classes() -> None:
    assert classify_default(RawRun(0, None, False, False, "", "")) == ResultClass.CLEAN
    assert classify_default(RawRun(1, None, False, False, "", "")) == ResultClass.DIAGNOSTICS
    assert classify_default(RawRun(2, None, False, False, "", "")) == ResultClass.FAILED_CRASH
    assert classify_default(RawRun(0, None, True, False, "", "")) == ResultClass.FAILED_TIMEOUT
    assert classify_default(RawRun(11, 11, False, False, "", "")) == ResultClass.FAILED_CRASH
    # Explicit OOM flag (cgroup-sourced, Plan 4) wins over everything.
    assert classify_default(RawRun(137, None, False, True, "", "")) == ResultClass.FAILED_OOM
    # SIGKILL (9) with no explicit flag -> OOM heuristic until cgroup detection lands.
    assert classify_default(RawRun(-9, 9, False, False, "", "")) == ResultClass.FAILED_OOM
    # Environment error -> failed{env}.
    assert (
        classify_default(RawRun(-1, None, False, False, "", "", env_error=True))
        == ResultClass.FAILED_ENV
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_wrapper.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'typebench.wrapper'`.

- [ ] **Step 3: Implement the runner + classifier in `wrapper.py`**

```python
"""Exit-code wrapper (spec §5.1). Type checkers exit nonzero when they find
diagnostics — that is success, not failure. This module captures the real
outcome and maps it to the §7 taxonomy. It also exposes a CLI (Task 5) used
as hyperfine's command so hyperfine does not abort on diagnostics."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

from typebench.models import ResultClass

# OOM-killer signal. A bare SIGKILL with no cgroup OOM flag is treated as an
# OOM heuristic until cgroup OOM detection lands (Plan 4 sets RawRun.oom).
_SIGKILL = 9


@dataclass(frozen=True)
class RawRun:
    exit_code: int
    signal: int | None
    timed_out: bool
    oom: bool
    stdout: str
    stderr: str
    env_error: bool = False


def run_command(
    argv: list[str], timeout: float, env: dict[str, str] | None = None
) -> RawRun:
    """Run argv to completion, capturing the real outcome. Never raises: a
    nonzero exit, a timeout, a signal death, AND an environment error (missing
    binary / not executable) are all captured as a RawRun so the caller can
    record the right §7 class. `env` is merged over the inherited environment
    (adapters inject e.g. TY_MAX_PARALLELISM)."""
    run_env = {**os.environ, **env} if env else None
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
        )
    except subprocess.TimeoutExpired as exc:
        return RawRun(
            exit_code=-1,
            signal=None,
            timed_out=True,
            oom=False,
            stdout=exc.stdout or "" if isinstance(exc.stdout, str) else "",
            stderr=exc.stderr or "" if isinstance(exc.stderr, str) else "",
        )
    except OSError as exc:
        # Missing binary, not executable, etc. -> environment failure (§7).
        return RawRun(
            exit_code=-1,
            signal=None,
            timed_out=False,
            oom=False,
            stdout="",
            stderr=str(exc),
            env_error=True,
        )
    returncode = proc.returncode
    signal = -returncode if returncode < 0 else None
    return RawRun(
        exit_code=returncode,
        signal=signal,
        timed_out=False,
        oom=False,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def classify_default(raw: RawRun) -> ResultClass:
    """Generic classifier. Real per-tool exit maps arrive in Plan 2 (§7).

    Convention shared by the stub and most checkers: 0 = clean, 1 = diagnostics
    found, anything else / signal / timeout / oom / env-error = failure.
    Order matters: env-error and explicit OOM are checked before the generic
    signal/exit-code fallbacks."""
    if raw.env_error:
        return ResultClass.FAILED_ENV
    if raw.oom:
        return ResultClass.FAILED_OOM
    if raw.timed_out:
        return ResultClass.FAILED_TIMEOUT
    if raw.signal == _SIGKILL:
        return ResultClass.FAILED_OOM
    if raw.signal is not None:
        return ResultClass.FAILED_CRASH
    if raw.exit_code == 0:
        return ResultClass.CLEAN
    if raw.exit_code == 1:
        return ResultClass.DIAGNOSTICS
    return ResultClass.FAILED_CRASH
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_wrapper.py -v`
Expected: PASS (6 passed on Linux/macOS; 5 passed + 1 skipped where the SIGSEGV test is skipped on non-POSIX).

- [ ] **Step 5: Commit**

```bash
git add src/typebench/wrapper.py tests/test_wrapper.py
git commit -m "feat(wrapper): command runner and default result classifier"
```

---

## Task 5: Wrapper CLI (hyperfine's command)

**Files:**
- Modify: `src/typebench/wrapper.py` (append CLI)
- Test: `tests/test_wrapper_cli.py`

The timing pass runs each invocation many times under hyperfine. hyperfine aborts on any nonzero exit, so we hand it `python -m typebench.wrapper -- <argv>`, which runs the real command and **exits 0 for measured-success** (clean or diagnostics) and nonzero only for real failures.

> **Methodology note — wrapper overhead (record in published methodology).** hyperfine times the *wrapped* command, so every measured run includes a Python interpreter start (~30–50 ms) plus one nested `subprocess.run` spawn. The offset is (a) **constant across all four tools**, so it nearly cancels in the inter-checker ratios the trend charts use (spec §5.7/§11), and (b) absorbed by the per-run calibration baseline (§5.7). It is *not* negligible for the smallest-bucket × fastest-tool cells, where it can approach the ~10% noise floor (§5.6) — so it is documented, not hidden. The wrapper is kept (rather than hyperfine's `--ignore-failure` on the bare command) because `--ignore-failure` would silently *time a crash* that occurs mid-timing-loop, whereas the wrapper + `check=True` aborts loudly. Plan 4/6 may subtract a measured wrapper-only baseline if the smallest cells prove sensitive.

- [ ] **Step 1: Write the failing test**

`tests/test_wrapper_cli.py`:
```python
import subprocess
import sys


def _run_wrapper(inner_argv: list[str], timeout: str = "10") -> int:
    return subprocess.run(
        [sys.executable, "-m", "typebench.wrapper", "--timeout", timeout, "--", *inner_argv],
        capture_output=True,
        text=True,
    ).returncode


def test_wrapper_cli_exits_zero_on_clean():
    assert _run_wrapper([sys.executable, "-c", "import sys; sys.exit(0)"]) == 0


def test_wrapper_cli_exits_zero_on_diagnostics():
    # exit 1 == diagnostics == measured success -> wrapper reports 0 to hyperfine.
    assert _run_wrapper([sys.executable, "-c", "import sys; sys.exit(1)"]) == 0


def test_wrapper_cli_exits_nonzero_on_crash():
    assert _run_wrapper([sys.executable, "-c", "import sys; sys.exit(2)"]) != 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_wrapper_cli.py -v`
Expected: FAIL — `python -m typebench.wrapper` has no `__main__` behavior yet (nonzero / error).

- [ ] **Step 3: Append the CLI to `wrapper.py`**

```python
def main(raw_args: list[str] | None = None) -> int:
    """CLI entrypoint used as hyperfine's command. Usage:

        python -m typebench.wrapper --timeout SECONDS -- <argv...>

    Exits 0 for measured-success (clean/diagnostics), 1 for any failure class.
    The real command's stdout/stderr are forwarded so output stays visible."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="typebench.wrapper")
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    ns = parser.parse_args(raw_args)

    argv = ns.argv
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        parser.error("no command given after --")

    raw = run_command(argv, timeout=ns.timeout)
    sys.stdout.write(raw.stdout)
    sys.stderr.write(raw.stderr)
    return 0 if classify_default(raw).is_measured_success else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_wrapper_cli.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/typebench/wrapper.py tests/test_wrapper_cli.py
git commit -m "feat(wrapper): CLI entrypoint that normalizes exit codes for hyperfine"
```

---

## Task 6: Adapter protocol + stub adapter + fake checker

**Files:**
- Create: `src/typebench/adapters/__init__.py`
- Create: `src/typebench/adapters/base.py`
- Create: `src/typebench/adapters/stub.py`
- Create: `src/typebench/_fake_checker.py`
- Test: `tests/test_stub_adapter.py`

> The fake checker lives **in the package** (`src/typebench/_fake_checker.py`), not under `tests/`, so it ships in the wheel — the `stub` adapter is in the CLI registry, so `typebench run --tool stub` must work from an installed package, not only a source checkout. It is invoked as `python -m typebench._fake_checker`, which also removes the brittle `parents[3]` path walk.

- [ ] **Step 1: Write the failing test**

`tests/test_stub_adapter.py`:
```python
from typebench.adapters.base import Adapter
from typebench.adapters.stub import StubAdapter
from typebench.models import ResultClass, ThreadMode
from typebench.wrapper import run_command


def test_stub_command_runs_and_reports_diagnostics() -> None:
    adapter = StubAdapter(exit_code=1, diagnostics=4, files=9)
    argv, env = adapter.command(project="demo", thread_mode=ThreadMode.ALL_CORES)
    raw = run_command(argv, timeout=10, env=env)
    assert raw.exit_code == 1
    diagnostics, files = adapter.parse(raw.stdout, raw.stderr, raw.exit_code)
    assert diagnostics == 4
    assert files == 9
    assert adapter.classify(raw) == ResultClass.DIAGNOSTICS


def test_stub_command_clean() -> None:
    adapter = StubAdapter(exit_code=0, diagnostics=0, files=5)
    argv, env = adapter.command("demo", ThreadMode.ALL_CORES)
    raw = run_command(argv, timeout=10, env=env)
    assert adapter.classify(raw) == ResultClass.CLEAN
    assert adapter.parse(raw.stdout, raw.stderr, raw.exit_code) == (0, 5)


def test_stub_missing_binary_is_env_failure() -> None:
    adapter = StubAdapter(missing_binary=True)
    argv, env = adapter.command("demo", ThreadMode.ALL_CORES)
    raw = run_command(argv, timeout=10, env=env)
    assert raw.env_error is True
    assert adapter.classify(raw) == ResultClass.FAILED_ENV


def test_stub_satisfies_adapter_protocol() -> None:
    # runtime_checkable: the stub is a structural Adapter (catches drift early).
    assert isinstance(StubAdapter(), Adapter)


def test_stub_version_is_stable() -> None:
    assert StubAdapter().version() == "stub-1.0"


def test_stub_clear_cache_and_prepare_are_noops() -> None:
    adapter = StubAdapter()
    adapter.clear_cache("demo")  # must not raise
    assert adapter.prepare_command("demo") is None  # stateless: nothing to clear
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_stub_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'typebench.adapters'`.

- [ ] **Step 3: Implement the protocol, fake checker, and stub adapter**

`src/typebench/adapters/__init__.py`:
```python
```

`src/typebench/adapters/base.py`:
```python
"""Adapter protocol — the only checker-specific surface (spec §4).

The protocol is pinned to its *final-ish* shape now so real adapters (Plan 2)
add behavior, not breaking signatures: `command` already returns (argv, env)
for vars like TY_MAX_PARALLELISM, and `install` / `parallelism_cap` /
`prepare_command` exist as the stable surface. The spine only calls
command/parse/classify/clear_cache/prepare_command/version; install and
parallelism_cap are no-ops on the stub and are wired in Plans 2/4."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from typebench.models import ResultClass, ThreadMode
from typebench.wrapper import RawRun, classify_default


@dataclass(frozen=True)
class ParallelismCap:
    """How a tool is constrained in the 1-core track (spec §5.3). `hard_cap` is
    the honesty flag: True = a real worker cap, False = best-effort only."""

    mechanism: str
    hard_cap: bool


@runtime_checkable
class Adapter(Protocol):
    name: str

    def version(self) -> str:
        """Resolved checker version string."""
        ...

    def install(self) -> str:
        """Resolve + verify the expected distribution; return the resolved
        version (spec §4). Plan 2 implements real verification; stub no-ops."""
        ...

    def command(
        self, project: str, thread_mode: ThreadMode
    ) -> tuple[list[str], dict[str, str]]:
        """(argv, extra_env) that runs the checker on `project` under
        `thread_mode`. `extra_env` carries vars like TY_MAX_PARALLELISM (§5.3),
        empty when none. Plan 2 adds the normalized-config argument (§6)."""
        ...

    def parallelism_cap(self, thread_mode: ThreadMode) -> ParallelismCap:
        """Declare how this tool is constrained in the 1-core track (§5.3)."""
        ...

    def parse(
        self, stdout: str, stderr: str, exit_code: int
    ) -> tuple[int | None, int | None]:
        """Return (diagnostics, files) from the checker's output."""
        ...

    def classify(self, raw: RawRun) -> ResultClass:
        """Map a RawRun to the §7 taxonomy. Override per tool in Plan 2."""
        ...

    def clear_cache(self, project: str) -> None:
        """Remove any checker cache so every run is cold (§5.2)."""
        ...

    def prepare_command(self, project: str) -> str | None:
        """A hyperfine-safe shell command that clears the checker cache before
        EVERY timed run (§5.2, §5.4), or None when the tool is stateless. Wired
        into `hyperfine --prepare` by the collector so warmups/runs stay cold."""
        ...


def default_classify(raw: RawRun) -> ResultClass:
    """Shared fallback so adapters can delegate to the generic map."""
    return classify_default(raw)
```

`src/typebench/_fake_checker.py`:
```python
"""A controllable fake type checker. Prints a JSON summary and exits with a
chosen code — optionally after a sleep, or by killing itself with a signal —
so the engine can be tested without any real checker. Ships in the package so
the stub adapter works from an installed wheel, not only a source checkout."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(prog="typebench._fake_checker")
    parser.add_argument("--exit-code", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--diagnostics", type=int, default=0)
    parser.add_argument("--files", type=int, default=0)
    parser.add_argument(
        "--signal",
        type=int,
        default=None,
        help="If set, kill self with this signal (9 = OOM-like, 11 = crash).",
    )
    ns = parser.parse_args()

    if ns.sleep:
        time.sleep(ns.sleep)
    if ns.signal is not None:
        os.kill(os.getpid(), ns.signal)
    print(json.dumps({"diagnostics": ns.diagnostics, "files": ns.files}))
    return ns.exit_code


if __name__ == "__main__":
    sys.exit(main())
```

`src/typebench/adapters/stub.py`:
```python
"""StubAdapter — drives typebench._fake_checker. Exercises the full pipeline
deterministically: chosen exit code, diagnostics, files, duration, a signal
death, or a missing-binary environment failure."""

from __future__ import annotations

import json
import sys

from typebench.adapters.base import ParallelismCap, default_classify
from typebench.models import ResultClass, ThreadMode
from typebench.wrapper import RawRun


class StubAdapter:
    name = "stub"

    def __init__(
        self,
        exit_code: int = 0,
        diagnostics: int = 0,
        files: int = 0,
        sleep: float = 0.0,
        signal: int | None = None,
        missing_binary: bool = False,
    ) -> None:
        self._exit_code = exit_code
        self._diagnostics = diagnostics
        self._files = files
        self._sleep = sleep
        self._signal = signal
        self._missing_binary = missing_binary

    def version(self) -> str:
        return "stub-1.0"

    def install(self) -> str:
        # No distribution to verify; real checks land in Plan 2.
        return self.version()

    def command(
        self, project: str, thread_mode: ThreadMode
    ) -> tuple[list[str], dict[str, str]]:
        if self._missing_binary:
            # Nonexistent executable -> run_command raises OSError -> failed{env}.
            return (["typebench-nonexistent-checker-xyz"], {})
        argv = [
            sys.executable,
            "-m",
            "typebench._fake_checker",
            "--exit-code",
            str(self._exit_code),
            "--diagnostics",
            str(self._diagnostics),
            "--files",
            str(self._files),
            "--sleep",
            str(self._sleep),
        ]
        if self._signal is not None:
            argv += ["--signal", str(self._signal)]
        return (argv, {})

    def parallelism_cap(self, thread_mode: ThreadMode) -> ParallelismCap:
        # Single process: CPU affinity is the only lever and is a true cap.
        return ParallelismCap(mechanism="cpu-affinity", hard_cap=True)

    def parse(
        self, stdout: str, stderr: str, exit_code: int
    ) -> tuple[int | None, int | None]:
        try:
            payload = json.loads(stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return (None, None)
        return (payload.get("diagnostics"), payload.get("files"))

    def classify(self, raw: RawRun) -> ResultClass:
        return default_classify(raw)

    def clear_cache(self, project: str) -> None:
        return None

    def prepare_command(self, project: str) -> str | None:
        return None  # stateless: no checker cache to clear between runs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_stub_adapter.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/typebench/adapters src/typebench/_fake_checker.py tests/test_stub_adapter.py
git commit -m "feat(adapters): final-ish adapter protocol, stub adapter, in-package fake checker"
```

---

## Task 7: Timing pass (hyperfine)

**Files:**
- Create: `src/typebench/timing.py`
- Test: `tests/test_timing.py`

Split into a **pure parser** (always tested) and a **runner** (integration; skipped when `hyperfine` is absent).

- [ ] **Step 1: Write the failing test**

`tests/test_timing.py`:
```python
import shutil

import pytest

from typebench.adapters.stub import StubAdapter
from typebench.models import ThreadMode, TimingStats
from typebench.timing import parse_hyperfine_json, run_timing


def test_parse_hyperfine_json_builds_timing_stats():
    data = {
        "results": [
            {
                "command": "x",
                "mean": 0.12,
                "stddev": 0.01,
                "median": 0.11,
                "min": 0.10,
                "max": 0.14,
                "times": [0.10, 0.11, 0.14],
            }
        ]
    }
    stats = parse_hyperfine_json(data)
    assert isinstance(stats, TimingStats)
    assert stats.runs == 3
    assert stats.min_s == 0.10
    assert stats.median_s == 0.11
    assert stats.times_s == [0.10, 0.11, 0.14]


def test_parse_hyperfine_json_rejects_empty_results():
    with pytest.raises(ValueError):
        parse_hyperfine_json({"results": []})


@pytest.mark.skipif(shutil.which("hyperfine") is None, reason="hyperfine not installed")
def test_run_timing_against_stub() -> None:
    adapter = StubAdapter(exit_code=0, sleep=0.02)
    argv, env = adapter.command("demo", ThreadMode.ALL_CORES)
    stats = run_timing(
        argv, prepare_cmd=None, extra_env=env, warmup=1, runs=3, timeout=30
    )
    assert stats.runs == 3
    assert stats.min_s > 0
    assert stats.max_s >= stats.min_s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_timing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'typebench.timing'`.

- [ ] **Step 3: Implement `timing.py`**

```python
"""Timing pass via hyperfine (spec §5.4). hyperfine handles warmup, repeated
runs, and statistics; we hand it the wrapper (Task 5) so diagnostics exits do
not abort the run, and `--prepare` clears the checker cache before each run."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from typebench.models import TimingStats


def parse_hyperfine_json(data: dict) -> TimingStats:
    results = data.get("results") or []
    if not results:
        raise ValueError("hyperfine JSON has no results")
    r = results[0]
    times = list(r["times"])
    return TimingStats(
        runs=len(times),
        min_s=float(r["min"]),
        median_s=float(r["median"]),
        mean_s=float(r["mean"]),
        stddev_s=float(r.get("stddev") or 0.0),
        max_s=float(r["max"]),
        times_s=times,
    )


def _wrapped_command_string(argv: list[str], timeout: float) -> str:
    parts = [
        sys.executable,
        "-m",
        "typebench.wrapper",
        "--timeout",
        str(timeout),
        "--",
        *argv,
    ]
    return shlex.join(parts)


def run_timing(
    argv: list[str],
    prepare_cmd: str | None,
    warmup: int,
    runs: int,
    timeout: float,
    extra_env: dict[str, str] | None = None,
) -> TimingStats:
    """Run the timing pass and return wall-time statistics.

    `argv` is the *real* checker invocation; it is wrapped so hyperfine sees a
    success exit for diagnostics. `prepare_cmd` (e.g. cache clear) runs before
    every timed run, keeping each run cold (§5.2); None means nothing to prepare
    (stub has no cache). `extra_env` is set on the hyperfine process and inherited
    by the wrapped command (e.g. TY_MAX_PARALLELISM)."""
    run_env = {**os.environ, **extra_env} if extra_env else None
    with tempfile.TemporaryDirectory() as tmp:
        json_path = Path(tmp) / "hyperfine.json"
        cmd = [
            "hyperfine",
            "--warmup",
            str(warmup),
            "--runs",
            str(runs),
            "--export-json",
            str(json_path),
        ]
        if prepare_cmd:
            cmd += ["--prepare", prepare_cmd]
        cmd.append(_wrapped_command_string(argv, timeout))
        subprocess.run(cmd, check=True, capture_output=True, text=True, env=run_env)
        return parse_hyperfine_json(json.loads(json_path.read_text()))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_timing.py -v`
Expected: PASS (parser tests pass; the hyperfine test passes if hyperfine is installed, else SKIPPED).

> Install hyperfine if missing: `sudo apt-get install -y hyperfine` (or `cargo install hyperfine`). The skip keeps the suite green on machines without it.

- [ ] **Step 5: Commit**

```bash
git add src/typebench/timing.py tests/test_timing.py
git commit -m "feat(timing): hyperfine timing pass with pure JSON parser"
```

---

## Task 8: Collector pipeline (`run_single`)

**Files:**
- Create: `src/typebench/collector.py`
- Test: `tests/test_collector.py`

Two-phase per (project × tool × mode): **probe** once (run, classify, parse), then if measured-success **time** with hyperfine. Failures skip timing and are recorded with the real exit code **plus failure metadata** (signal / timed_out / oom / `error_detail`) so `failed{env}` vs `failed{crash}` is auditable later (§5.1).

> **Notes.** (1) Cache clearing before *every* timed run is wired here via the adapter's `prepare_command(project)` → `hyperfine --prepare` (§5.2); the stub returns None (stateless), real tools return a clear command in Plan 2/3. (2) The probe is an extra cold run on top of hyperfine's warmups+runs — fold it into the §10 cost budget (Plan 6). (3) `thread_mode_enforced` is recorded as **False**: Plan 1 applies no CPU affinity/cap, so the record must not imply the 1-core methodology was actually run (§5.3); Plan 4 sets it True once affinity is applied.

- [ ] **Step 1: Write the failing test**

`tests/test_collector.py`:
```python
import shutil

import pytest

from typebench.adapters.stub import StubAdapter
from typebench.collector import run_single
from typebench.models import ResultClass, ThreadMode


def test_run_single_failure_skips_timing() -> None:
    adapter = StubAdapter(exit_code=2)  # -> FAILED_CRASH
    result = run_single(
        adapter, project="demo", thread_mode=ThreadMode.ALL_CORES,
        warmup=1, runs=2, timeout=10,
    )
    assert result.result_class == ResultClass.FAILED_CRASH
    assert result.real_exit_code == 2
    assert result.timing is None
    assert result.tool == "stub"
    assert result.env.core_count >= 1
    assert result.thread_mode_enforced is False  # no affinity applied (§5.3)


def test_run_single_env_failure_is_recorded() -> None:
    # Missing binary -> failed{env}, captured (not raised), with an audit trail.
    adapter = StubAdapter(missing_binary=True)
    result = run_single(
        adapter, project="demo", thread_mode=ThreadMode.ALL_CORES,
        warmup=1, runs=2, timeout=10,
    )
    assert result.result_class == ResultClass.FAILED_ENV
    assert result.timing is None
    assert result.error_detail  # carries the OSError text


def test_run_single_diagnostics_records_counts():
    adapter = StubAdapter(exit_code=1, diagnostics=3, files=7)
    result = run_single(
        adapter, project="demo", thread_mode=ThreadMode.ALL_CORES,
        warmup=1, runs=2, timeout=10,
    )
    assert result.result_class == ResultClass.DIAGNOSTICS
    assert result.diagnostics == 3
    assert result.files == 7


@pytest.mark.skipif(shutil.which("hyperfine") is None, reason="hyperfine not installed")
def test_run_single_success_includes_timing():
    adapter = StubAdapter(exit_code=0, files=4, sleep=0.02)
    result = run_single(
        adapter, project="demo", thread_mode=ThreadMode.ALL_CORES,
        warmup=1, runs=3, timeout=30,
    )
    assert result.result_class == ResultClass.CLEAN
    assert result.timing is not None
    assert result.timing.runs == 3
    assert result.timing.min_s > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_collector.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'typebench.collector'`.

- [ ] **Step 3: Implement `collector.py`**

```python
"""Collector — assembles one RunResult (spec §4, §8). Probe then time."""

from __future__ import annotations

import shutil

from typebench.adapters.base import Adapter
from typebench.env import detect_env
from typebench.models import RunResult, ThreadMode
from typebench.timing import run_timing
from typebench.wrapper import run_command


def run_single(
    adapter: Adapter,
    project: str,
    thread_mode: ThreadMode,
    warmup: int,
    runs: int,
    timeout: float,
) -> RunResult:
    adapter.clear_cache(project)
    argv, extra_env = adapter.command(project, thread_mode)

    # Phase 1: probe — one real run to classify and parse counts.
    raw = run_command(argv, timeout=timeout, env=extra_env)
    result_class = adapter.classify(raw)
    diagnostics = files = None
    if result_class.is_measured_success:
        diagnostics, files = adapter.parse(raw.stdout, raw.stderr, raw.exit_code)

    # Phase 2: time — only for measured-success, only if hyperfine present.
    # prepare_command clears the checker cache before EVERY timed run (§5.2);
    # None for stateless tools like the stub.
    timing = None
    if result_class.is_measured_success and shutil.which("hyperfine"):
        timing = run_timing(
            argv,
            prepare_cmd=adapter.prepare_command(project),
            warmup=warmup,
            runs=runs,
            timeout=timeout,
            extra_env=extra_env,
        )

    error_detail = None
    if not result_class.is_measured_success:
        error_detail = raw.stderr.strip()[-500:] or None

    return RunResult(
        tool=adapter.name,
        tool_version=adapter.version(),
        project=project,
        thread_mode=thread_mode,
        # Plan 1 applies no CPU affinity/cap -> never claim an unenforced mode (§5.3).
        thread_mode_enforced=False,
        result_class=result_class,
        real_exit_code=raw.exit_code,
        signal=raw.signal,
        timed_out=raw.timed_out,
        oom=raw.oom,
        error_detail=error_detail,
        diagnostics=diagnostics,
        files=files,
        timing=timing,
        env=detect_env(),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_collector.py -v`
Expected: PASS (3 passed, 1 passed-or-skipped depending on hyperfine).

- [ ] **Step 5: Commit**

```bash
git add src/typebench/collector.py tests/test_collector.py
git commit -m "feat(collector): probe-then-time pipeline producing a RunResult"
```

---

## Task 9: CLI (`typebench run`)

**Files:**
- Create: `src/typebench/cli.py`
- Test: `tests/test_cli.py`

For the spine, the registry holds only `stub`. The CLI writes one `RunResult` as JSON.

> **Forward-compat note.** Plan 1 writes a single `RunResult` to `--output`. The eventual results file (Plan 5 renderer / §11) holds *many* records and will wrap them in an envelope — `{schema_version, runs: [RunResult, ...]}`. The per-record `schema_version` versions the record shape until then; consumers should not assume the top-level object is a bare record forever. The CLI always exits 0 once a record is written — a `failed{...}` outcome is data in the record, not a process error.

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:
```python
from typer.testing import CliRunner

from typebench.cli import app
from typebench.models import RunResult

runner = CliRunner()


def test_cli_run_stub_writes_results_json(tmp_path):
    out = tmp_path / "results.json"
    result = runner.invoke(
        app,
        [
            "run",
            "--tool", "stub",
            "--project", "demo",
            "--thread-mode", "all-cores",
            "--runs", "2",
            "--warmup", "1",
            "--output", str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    parsed = RunResult.model_validate_json(out.read_text())
    assert parsed.tool == "stub"
    assert parsed.project == "demo"


def test_cli_run_rejects_unknown_tool(tmp_path):
    result = runner.invoke(
        app,
        ["run", "--tool", "nope", "--project", "demo", "--output", str(tmp_path / "r.json")],
    )
    assert result.exit_code != 0
    assert "Unknown tool" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'typebench.cli'`.

- [ ] **Step 3: Implement `cli.py`**

```python
"""typebench CLI (spec §5). Plan 1 exposes `run` for a single invocation."""

from __future__ import annotations

from pathlib import Path

import typer

from typebench.adapters.stub import StubAdapter
from typebench.collector import run_single
from typebench.models import ThreadMode

app = typer.Typer(help="Neutral Python type-checker performance benchmark.")

# Adapter registry. Real checkers (mypy/pyright/pyrefly/ty) are added in Plan 2.
_ADAPTERS = {
    "stub": StubAdapter,
}


@app.command()
def run(
    tool: str = typer.Option(..., help="Checker to run (e.g. stub)."),
    project: str = typer.Option(..., help="Project name or path."),
    output: Path = typer.Option(..., help="Where to write the results JSON."),
    thread_mode: ThreadMode = typer.Option(ThreadMode.ALL_CORES, help="Thread track."),
    runs: int = typer.Option(10, help="hyperfine timed runs."),
    warmup: int = typer.Option(3, help="hyperfine warmup runs."),
    timeout: float = typer.Option(900.0, help="Per-invocation timeout (seconds)."),
) -> None:
    factory = _ADAPTERS.get(tool)
    if factory is None:
        typer.echo(f"Unknown tool: {tool!r}. Known: {sorted(_ADAPTERS)}", err=True)
        raise typer.Exit(code=2)

    adapter = factory()
    result = run_single(
        adapter,
        project=project,
        thread_mode=thread_mode,
        warmup=warmup,
        runs=runs,
        timeout=timeout,
    )
    output.write_text(result.model_dump_json(indent=2))
    typer.echo(f"{tool} / {project} -> {result.result_class.value} -> {output}")


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/typebench/cli.py tests/test_cli.py
git commit -m "feat(cli): typebench run command writing a results record"
```

---

## Task 10: End-to-end guardrail + full suite green

**Files:**
- Create: `tests/test_e2e.py`
- Create: `README.md`

- [ ] **Step 1: Write the end-to-end test**

`tests/test_e2e.py`:
```python
import os
from pathlib import Path

import pytest

from typebench.adapters.stub import StubAdapter
from typebench.collector import run_single
from typebench.models import ResultClass, RunResult, ThreadMode

requires_posix = pytest.mark.skipif(
    os.name != "posix", reason="signal semantics are POSIX-specific"
)


def _round_trip(result: RunResult, tmp_path: Path) -> RunResult:
    path = tmp_path / "r.json"
    path.write_text(result.model_dump_json())
    restored = RunResult.model_validate_json(path.read_text())
    assert restored == result
    return restored


@pytest.mark.parametrize(
    ("adapter", "expected"),
    [
        (StubAdapter(exit_code=0), ResultClass.CLEAN),
        (StubAdapter(exit_code=1, diagnostics=2, files=5), ResultClass.DIAGNOSTICS),
        (StubAdapter(exit_code=2), ResultClass.FAILED_CRASH),
        (StubAdapter(missing_binary=True), ResultClass.FAILED_ENV),
    ],
)
def test_pipeline_classes_round_trip_to_json(
    adapter: StubAdapter, expected: ResultClass, tmp_path: Path
) -> None:
    result = run_single(
        adapter, project="demo", thread_mode=ThreadMode.ONE_CORE,
        warmup=1, runs=2, timeout=10,
    )
    assert result.result_class == expected
    assert result.thread_mode_enforced is False  # recorded mode was not enforced (§5.3)
    restored = _round_trip(result, tmp_path)
    # Failures must be visible, never silently dropped (spec §12).
    if not expected.is_measured_success:
        assert restored.timing is None
        assert restored.result_class.value.startswith("failed")


def test_pipeline_records_timeout(tmp_path: Path) -> None:
    # Probe sleeps past the timeout -> failed{timeout}, no timing recorded.
    adapter = StubAdapter(exit_code=0, sleep=5.0)
    result = run_single(
        adapter, project="demo", thread_mode=ThreadMode.ALL_CORES,
        warmup=1, runs=2, timeout=1,
    )
    assert result.result_class == ResultClass.FAILED_TIMEOUT
    assert result.timing is None
    _round_trip(result, tmp_path)


@requires_posix
def test_pipeline_records_oom_heuristic(tmp_path: Path) -> None:
    # SIGKILL (9) is the OOM-killer's signal; mapped to failed{oom} until cgroup
    # OOM detection lands in Plan 4.
    adapter = StubAdapter(signal=9)
    result = run_single(
        adapter, project="demo", thread_mode=ThreadMode.ALL_CORES,
        warmup=1, runs=2, timeout=10,
    )
    assert result.result_class == ResultClass.FAILED_OOM
    assert result.timing is None
    _round_trip(result, tmp_path)
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_e2e.py -v`
Expected: PASS (6 passed on Linux/macOS; 5 passed + 1 skipped where the OOM/SIGKILL test is skipped on non-POSIX). Covers clean / diagnostics / failed{crash} / failed{env} / failed{timeout} / failed{oom}.

- [ ] **Step 3: Write a minimal `README.md`**

```markdown
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
```

- [ ] **Step 4: Run the FULL suite**

Run: `uv run pytest -v`
Expected: ALL PASS (hyperfine-dependent tests pass if installed, else skipped). Confirm zero failures.

- [ ] **Step 5: Commit**

```bash
git add tests/test_e2e.py README.md
git commit -m "test(e2e): pipeline class round-trip + project README"
```

---

## Definition of Done (Plan 1)

- **Quality gate green:** `uv run ruff format --check .` (no changes), `uv run ruff check .` (`All checks passed!`), `uv run pyrefly check` (`0 errors`), and `pre-commit` installed + passing on commit.
- `uv run pytest -v` is green (skips allowed only for hyperfine-gated tests when hyperfine is absent).
- `uv run typebench run --tool stub --project demo --output results.json` writes a schema-valid `RunResult`.
- With hyperfine installed, that record contains real wall-time statistics; without it, `timing` is `null` and the result class is still correct.
- **All six taxonomy classes are producible and end-to-end tested** via the fake checker: `clean`, `diagnostics`, `failed{crash}` (exit 2 / signal), `failed{env}` (missing binary), `failed{timeout}` (sleep > timeout), `failed{oom}` (SIGKILL heuristic). Each is recorded with its real exit code + failure metadata (signal/timed_out/oom/`error_detail`) and never silently dropped (spec §7, §12).
- `thread_mode` is recorded alongside `thread_mode_enforced: false` — Plan 1 never claims an unenforced 1-core methodology (spec §5.3).
- No real checker, cgroup, corpus, or renderer code exists yet — those are Plans 2–6.

## Self-Review notes (done during authoring)

- **Spec coverage (Plan 1 scope):** §7 taxonomy → `ResultClass` + `classify_default` (all six classes producible, incl. `failed{env}` via captured `OSError` and `failed{oom}` via SIGKILL heuristic); §5.1 exit-code wrapper → `wrapper.py` + CLI + failure metadata on `RunResult`; §5.4 timing → `timing.py`; §8 metrics (time/diagnostics/files/class/env) → `RunResult`; §5.2 cold → `clear_cache` + `prepare_command` → `hyperfine --prepare` seam wired in the collector (stub stateless → None; real clears in Plan 2/3); §5.3 thread semantics → `ThreadMode` enum + honest `thread_mode_enforced=false` (enforcement deferred to Plan 4); §4 adapter surface → final-ish `Adapter` protocol so Plan 2 adds behavior, not signature breaks. Out-of-scope-by-design here: §5.3 *affinity enforcement*, §5.5 memory, §5.6 stats, §5.7 calibration, §6 config, §9 lock manifest, §10–11 CI/render — assigned to Plans 2–6.
- **Deferred-with-a-seam (called out so they aren't forgotten):** real per-tool `classify_exit` and machine-readable `parse` + **parse-sanity guardrail** (`files > 0` on success, §4/§13) → Plan 2; JSON-Schema artifact export from the pydantic models (§13 schema validation) → Plan 2/5; results **envelope** (`{schema_version, runs: [...]}`) for the multi-record file → Plan 5; the **probe is an extra cold run** to budget in §10 → Plan 6; **wrapper interpreter overhead** documented in Task 5, to be calibration-absorbed or baseline-subtracted (§5.7) → Plan 4/6.
- **Placeholders:** none — every step has full code/commands.
- **Cross-platform:** signal-dependent tests (`SIGSEGV`, `SIGKILL`) are `skipif(os.name != "posix")`; the rest (incl. `detect_env`) run on Linux/macOS/Windows. hyperfine-dependent tests skip when the binary is absent.
- **Type consistency:** `RawRun`, `ResultClass`, `ThreadMode`, `TimingStats`, `EnvFingerprint`, `RunResult`, `ParallelismCap`, `run_command`, `classify_default`, `run_timing`, `parse_hyperfine_json`, `run_single`, `StubAdapter` names are identical across all tasks. `Adapter.command` returns `(argv, env)` and `run_command`/`run_timing` accept `env`/`extra_env` consistently at every call site.
