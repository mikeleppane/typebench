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
| `src/typebench/models.py` | `ResultClass`, `ThreadMode` enums; `TimingStats`, `EnvFingerprint`, `RunResult` pydantic models (the results schema) |
| `src/typebench/env.py` | `detect_env() -> EnvFingerprint` |
| `src/typebench/wrapper.py` | `RawRun`, `run_command()`, `classify_default()`, and the `python -m typebench.wrapper` CLI used as hyperfine's command (normalizes exit codes) |
| `src/typebench/adapters/base.py` | `Adapter` protocol |
| `src/typebench/adapters/stub.py` | `StubAdapter` wrapping the fake checker |
| `src/typebench/timing.py` | `parse_hyperfine_json()` (pure) + `run_timing()` (invokes hyperfine) |
| `src/typebench/collector.py` | `run_single()` pipeline → `RunResult` |
| `src/typebench/cli.py` | `typer` app, `typebench run`, adapter registry |
| `tests/fixtures/fake_checker.py` | controllable fake checker (exit code, sleep, diagnostics, files) |
| `tests/test_*.py` | one test module per source module + an end-to-end test |

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
    """One (project x tool x thread-mode) measurement record."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    tool: str
    tool_version: str
    project: str
    thread_mode: ThreadMode
    result_class: ResultClass
    real_exit_code: int
    diagnostics: int | None = None
    files: int | None = None
    timing: TimingStats | None = None
    env: EnvFingerprint
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (5 passed).

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
import sys

from typebench.models import ResultClass
from typebench.wrapper import RawRun, classify_default, run_command


def test_run_command_captures_clean_exit():
    raw = run_command([sys.executable, "-c", "print('hi')"], timeout=10)
    assert raw.exit_code == 0
    assert raw.timed_out is False
    assert "hi" in raw.stdout
    assert raw.signal is None


def test_run_command_captures_nonzero_exit():
    raw = run_command([sys.executable, "-c", "import sys; sys.exit(1)"], timeout=10)
    assert raw.exit_code == 1


def test_run_command_times_out():
    raw = run_command([sys.executable, "-c", "import time; time.sleep(5)"], timeout=1)
    assert raw.timed_out is True


def test_run_command_records_signal():
    # SIGSEGV (-11) -> Python returncode is negative.
    raw = run_command(
        [sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGSEGV)"],
        timeout=10,
    )
    assert raw.signal == 11


def test_classify_default_maps_classes():
    assert classify_default(RawRun(0, None, False, False, "", "")) == ResultClass.CLEAN
    assert classify_default(RawRun(1, None, False, False, "", "")) == ResultClass.DIAGNOSTICS
    assert classify_default(RawRun(2, None, False, False, "", "")) == ResultClass.FAILED_CRASH
    assert classify_default(RawRun(0, None, True, False, "", "")) == ResultClass.FAILED_TIMEOUT
    assert classify_default(RawRun(11, 11, False, False, "", "")) == ResultClass.FAILED_CRASH
    assert classify_default(RawRun(137, None, False, True, "", "")) == ResultClass.FAILED_OOM
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

import subprocess
from dataclasses import dataclass

from typebench.models import ResultClass


@dataclass(frozen=True)
class RawRun:
    exit_code: int
    signal: int | None
    timed_out: bool
    oom: bool
    stdout: str
    stderr: str


def run_command(argv: list[str], timeout: float) -> RawRun:
    """Run argv to completion, capturing the real outcome. Never raises on a
    nonzero exit; only environment errors (e.g. binary missing) propagate."""
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
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
    found, anything else / signal / timeout / oom = failure."""
    if raw.oom:
        return ResultClass.FAILED_OOM
    if raw.timed_out:
        return ResultClass.FAILED_TIMEOUT
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
Expected: PASS (6 passed).

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
- Create: `tests/fixtures/__init__.py`
- Create: `tests/fixtures/fake_checker.py`
- Test: `tests/test_stub_adapter.py`

- [ ] **Step 1: Write the failing test**

`tests/test_stub_adapter.py`:
```python
from typebench.adapters.stub import StubAdapter
from typebench.models import ResultClass, ThreadMode
from typebench.wrapper import run_command


def test_stub_command_runs_and_reports_diagnostics():
    adapter = StubAdapter(exit_code=1, diagnostics=4, files=9)
    argv = adapter.command(project="demo", thread_mode=ThreadMode.ALL_CORES)
    raw = run_command(argv, timeout=10)
    assert raw.exit_code == 1
    diagnostics, files = adapter.parse(raw.stdout, raw.stderr, raw.exit_code)
    assert diagnostics == 4
    assert files == 9
    assert adapter.classify(raw) == ResultClass.DIAGNOSTICS


def test_stub_command_clean():
    adapter = StubAdapter(exit_code=0, diagnostics=0, files=5)
    raw = run_command(adapter.command("demo", ThreadMode.ALL_CORES), timeout=10)
    assert adapter.classify(raw) == ResultClass.CLEAN
    assert adapter.parse(raw.stdout, raw.stderr, raw.exit_code) == (0, 5)


def test_stub_version_is_stable():
    assert StubAdapter().version() == "stub-1.0"


def test_stub_clear_cache_is_noop():
    StubAdapter().clear_cache("demo")  # must not raise
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
"""Adapter protocol — the only checker-specific surface (spec §4)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from typebench.models import ResultClass, ThreadMode
from typebench.wrapper import RawRun, classify_default


@runtime_checkable
class Adapter(Protocol):
    name: str

    def version(self) -> str:
        """Resolved checker version string."""
        ...

    def command(self, project: str, thread_mode: ThreadMode) -> list[str]:
        """argv that runs the checker on `project` under `thread_mode`.

        Plan 2 adds the normalized-config argument (§6)."""
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


def default_classify(raw: RawRun) -> ResultClass:
    """Shared fallback so adapters can delegate to the generic map."""
    return classify_default(raw)
```

`tests/fixtures/__init__.py`:
```python
```

`tests/fixtures/fake_checker.py`:
```python
"""A controllable fake type checker. Prints a JSON summary and exits with a
chosen code, so the engine can be tested without any real checker."""

from __future__ import annotations

import argparse
import json
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(prog="fake_checker")
    parser.add_argument("--exit-code", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--diagnostics", type=int, default=0)
    parser.add_argument("--files", type=int, default=0)
    ns = parser.parse_args()

    if ns.sleep:
        time.sleep(ns.sleep)
    print(json.dumps({"diagnostics": ns.diagnostics, "files": ns.files}))
    return ns.exit_code


if __name__ == "__main__":
    sys.exit(main())
```

`src/typebench/adapters/stub.py`:
```python
"""StubAdapter — drives tests/fixtures/fake_checker.py. Lets the full pipeline
be exercised deterministically (chosen exit code, diagnostics, files, duration)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from typebench.adapters.base import default_classify
from typebench.models import ResultClass, ThreadMode
from typebench.wrapper import RawRun

_FAKE_CHECKER = (
    Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "fake_checker.py"
)


class StubAdapter:
    name = "stub"

    def __init__(
        self,
        exit_code: int = 0,
        diagnostics: int = 0,
        files: int = 0,
        sleep: float = 0.0,
    ) -> None:
        self._exit_code = exit_code
        self._diagnostics = diagnostics
        self._files = files
        self._sleep = sleep

    def version(self) -> str:
        return "stub-1.0"

    def command(self, project: str, thread_mode: ThreadMode) -> list[str]:
        return [
            sys.executable,
            str(_FAKE_CHECKER),
            "--exit-code",
            str(self._exit_code),
            "--diagnostics",
            str(self._diagnostics),
            "--files",
            str(self._files),
            "--sleep",
            str(self._sleep),
        ]

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
```

> Note: `parents[3]` resolves `src/typebench/adapters/stub.py` → repo root. If the executing engineer moves the file, recompute this. The fake checker path is only used by the stub.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_stub_adapter.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/typebench/adapters tests/fixtures tests/test_stub_adapter.py
git commit -m "feat(adapters): adapter protocol, stub adapter, fake checker fixture"
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
import sys

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
def test_run_timing_against_stub():
    adapter = StubAdapter(exit_code=0, sleep=0.02)
    argv = adapter.command("demo", ThreadMode.ALL_CORES)
    stats = run_timing(argv, prepare_cmd=None, warmup=1, runs=3, timeout=30)
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
) -> TimingStats:
    """Run the timing pass and return wall-time statistics.

    `argv` is the *real* checker invocation; it is wrapped so hyperfine sees a
    success exit for diagnostics. `prepare_cmd` (e.g. cache clear) runs before
    every timed run; None means nothing to prepare (stub has no cache)."""
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
        subprocess.run(cmd, check=True, capture_output=True, text=True)
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

Two-phase per (project × tool × mode): **probe** once (run, classify, parse), then if measured-success **time** with hyperfine. Failures skip timing and are recorded with the real exit code.

- [ ] **Step 1: Write the failing test**

`tests/test_collector.py`:
```python
import shutil

import pytest

from typebench.adapters.stub import StubAdapter
from typebench.collector import run_single
from typebench.models import ResultClass, ThreadMode


def test_run_single_failure_skips_timing():
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
    argv = adapter.command(project, thread_mode)

    # Phase 1: probe — one real run to classify and parse counts.
    raw = run_command(argv, timeout=timeout)
    result_class = adapter.classify(raw)
    diagnostics = files = None
    if result_class.is_measured_success:
        diagnostics, files = adapter.parse(raw.stdout, raw.stderr, raw.exit_code)

    # Phase 2: time — only for measured-success, only if hyperfine present.
    timing = None
    if result_class.is_measured_success and shutil.which("hyperfine"):
        timing = run_timing(
            argv,
            prepare_cmd=None,  # real cache-clear command injected in Plan 2/3
            warmup=warmup,
            runs=runs,
            timeout=timeout,
        )

    return RunResult(
        tool=adapter.name,
        tool_version=adapter.version(),
        project=project,
        thread_mode=thread_mode,
        result_class=result_class,
        real_exit_code=raw.exit_code,
        diagnostics=diagnostics,
        files=files,
        timing=timing,
        env=detect_env(),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_collector.py -v`
Expected: PASS (2 passed, 1 passed-or-skipped depending on hyperfine).

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

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:
```python
import json

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
import json
import shutil

import pytest

from typebench.adapters.stub import StubAdapter
from typebench.collector import run_single
from typebench.models import ResultClass, RunResult, ThreadMode


@pytest.mark.parametrize(
    ("exit_code", "expected"),
    [
        (0, ResultClass.CLEAN),
        (1, ResultClass.DIAGNOSTICS),
        (2, ResultClass.FAILED_CRASH),
    ],
)
def test_pipeline_classes_round_trip_to_json(exit_code, expected, tmp_path):
    adapter = StubAdapter(exit_code=exit_code, diagnostics=2, files=5)
    result = run_single(
        adapter, project="demo", thread_mode=ThreadMode.ONE_CORE,
        warmup=1, runs=2, timeout=10,
    )
    assert result.result_class == expected

    path = tmp_path / "r.json"
    path.write_text(result.model_dump_json())
    restored = RunResult.model_validate_json(path.read_text())
    assert restored == result
    # Failures must be visible, never silently dropped (spec §12).
    if not expected.is_measured_success:
        assert restored.timing is None
        assert json.loads(path.read_text())["result_class"].startswith("failed")
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_e2e.py -v`
Expected: PASS (3 passed).

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
- Every failure class is recorded with its real exit code and never silently dropped (spec §7, §12).
- No real checker, cgroup, corpus, or renderer code exists yet — those are Plans 2–6.

## Self-Review notes (done during authoring)

- **Spec coverage (Plan 1 scope):** §7 taxonomy → `ResultClass` + `classify_default`; §5.1 exit-code wrapper → `wrapper.py` + CLI; §5.4 timing → `timing.py`; §8 metrics (time/diagnostics/files/class/env) → `RunResult`; §5.2 cold (cache-clear hook) → `clear_cache` + `--prepare` seam (real clears in Plan 2/3). Out-of-scope-by-design here: §5.3 thread tracks, §5.5 memory, §5.6 stats, §5.7 calibration, §6 config, §9 lock manifest, §10–11 CI/render — assigned to Plans 2–6.
- **Placeholders:** none — every step has full code/commands.
- **Type consistency:** `RawRun`, `ResultClass`, `ThreadMode`, `TimingStats`, `EnvFingerprint`, `RunResult`, `run_command`, `classify_default`, `run_timing`, `parse_hyperfine_json`, `run_single`, `StubAdapter` names are identical across all tasks.
