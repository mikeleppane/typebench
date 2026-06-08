# typebench Plan 2A — Adapter Framework + Normalized Config + pyright (Plan 2 of 6, part A)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Evolve the adapter seam to carry a generated **normalized config (§6)**, and ship the **first real checker adapter (pyright)** end-to-end — `typebench run --tool pyright --src-root <dir>` produces a schema-valid `RunResult` with real diagnostics + timing against a real (in-repo fixture) project.

**Architecture:** Add a `NormalizedConfig` value object and a run-scoped `workdir` so adapters can emit a tool-specific config file that suppresses the project's own config (spec §6). pyright is the reference adapter (cleanest: `--outputjson` summary gives both `errorCount` and `filesAnalyzed`; unambiguous exit codes). mypy/ty/pyrefly follow in Plan 2B against this template.

**Tech Stack:** Python 3.12+, pydantic, typer, pytest, hyperfine. New external dep for tests: **pyright** (pinned npm or PyPI wrapper; tests `skipif` absent). Strict gate unchanged (ruff + pyrefly-strict + pytest).

**Inputs:** spec `docs/superpowers/specs/2026-06-07-typebench-design.md` (§6 LOCKED) · research `docs/superpowers/research/2026-06-08-checker-cli-facts.md` (authoritative flag facts).

---

## Quality Gate (MANDATORY — unchanged from Plan 1)
Before every commit (pre-commit enforces): `uv run ruff format .` (clean) · `uv run ruff check .` (All checks passed!) · `uv run pyrefly check` (0 errors) · `uv run pytest` (green). Every function incl. tests fully annotated. No `# noqa`/`# type: ignore` without an inline justification.

---

## File Structure (Plan 2A)

| File | Responsibility |
|------|----------------|
| `src/typebench/normalized_config.py` | `NormalizedConfig` frozen value object (§6 inputs: src roots, excludes, python version/platform, venv python) |
| `src/typebench/adapters/base.py` | extend `Adapter.command(...)` to take `config` + `workdir`; add `classify_with_map()` shared helper |
| `src/typebench/adapters/stub.py` | update `command(...)` signature (ignores config/workdir) |
| `src/typebench/collector.py` | `run_single(...)` takes `config`, creates a run-scoped `workdir` tempdir, threads both into `command(...)` |
| `src/typebench/cli.py` | `run` gains `--src-root/--python-version/--python-platform/--venv`; builds a `NormalizedConfig`; register `pyright` |
| `src/typebench/adapters/pyright.py` | `PyrightAdapter` (install/version, config-gen + argv, JSON parse, exit map, cap) |
| `tests/fixtures/clean_project/` | tiny stdlib-only package that type-checks clean |
| `tests/fixtures/error_project/` | tiny stdlib-only package with deliberate type errors |
| `tests/test_normalized_config.py`, `tests/test_pyright_adapter.py` | new tests |
| (modified) `tests/test_stub_adapter.py`, `tests/test_collector.py`, `tests/test_e2e.py`, `tests/test_cli.py` | updated for the new signatures |

---

## Task 1: `NormalizedConfig` value object

**Files:** Create `src/typebench/normalized_config.py`; Test `tests/test_normalized_config.py`.

- [ ] **Step 1: Write the failing test** — `tests/test_normalized_config.py`:
```python
from typebench.normalized_config import NormalizedConfig


def test_defaults_are_neutral() -> None:
    cfg = NormalizedConfig()
    assert cfg.src_roots == ()
    assert cfg.python_version == "3.12"
    assert cfg.python_platform == "linux"
    assert cfg.venv_python is None
    # tests / vendored / generated are excluded by default (spec §6).
    assert any("tests" in g for g in cfg.exclude_globs)


def test_is_frozen_and_carries_src_roots() -> None:
    cfg = NormalizedConfig(src_roots=("/abs/src",), python_version="3.11", venv_python="/v/bin/python")
    assert cfg.src_roots == ("/abs/src",)
    assert cfg.python_version == "3.11"
    assert cfg.venv_python == "/v/bin/python"
    import dataclasses

    assert dataclasses.is_dataclass(cfg)
    try:
        cfg.python_version = "x"  # type: ignore[misc]  # frozen: must raise
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("NormalizedConfig must be frozen")
```

- [ ] **Step 2: Run, expect FAIL** (`ModuleNotFoundError`): `uv run pytest tests/test_normalized_config.py -v`

- [ ] **Step 3: Implement `src/typebench/normalized_config.py`:**
```python
"""The normalized benchmark config (spec §6) — the equal observable inputs fed
to every checker. A pure value object; each adapter renders it into its own
config file / flags. Defaults are the neutral, stock-but-equal policy."""

from __future__ import annotations

from dataclasses import dataclass, field

# Excluded everywhere (spec §6): tests, vendored, generated, caches.
_DEFAULT_EXCLUDES: tuple[str, ...] = (
    "**/tests/**",
    "**/test/**",
    "**/_vendor/**",
    "**/vendor/**",
    "**/generated/**",
    "**/_generated/**",
    "**/__pycache__/**",
    "**/node_modules/**",
)


@dataclass(frozen=True)
class NormalizedConfig:
    """§6 inputs. `src_roots` are absolute first-party dirs to analyze (the
    throughput denominator); `venv_python` is the project venv interpreter used
    to resolve installed third-party imports (deps resolved, first-party
    diagnostics only)."""

    src_roots: tuple[str, ...] = ()
    exclude_globs: tuple[str, ...] = field(default=_DEFAULT_EXCLUDES)
    python_version: str = "3.12"
    python_platform: str = "linux"
    venv_python: str | None = None
```

- [ ] **Step 4: Run, expect PASS:** `uv run pytest tests/test_normalized_config.py -v`
- [ ] **Step 5: Commit:** `git add -A && git commit -m "feat(config): NormalizedConfig value object (§6 inputs)"`

---

## Task 2: Evolve the adapter seam (command takes config + workdir) + shared classifier

**Files:** Modify `src/typebench/adapters/base.py`, `src/typebench/adapters/stub.py`, `tests/test_stub_adapter.py`.

The §4 protocol said "Plan 2 adds the normalized-config argument." Do it now, plus a `workdir` (a run-scoped dir an adapter writes its generated config into; persists across probe + timing).

- [ ] **Step 1: Update the failing test first** — in `tests/test_stub_adapter.py`, the stub `command(...)` calls gain `config` + `workdir`. Replace the command-call sites and add a protocol assertion. New relevant tests:
```python
from pathlib import Path

from typebench.adapters.base import Adapter, classify_with_map
from typebench.adapters.stub import StubAdapter
from typebench.models import ResultClass, ThreadMode
from typebench.normalized_config import NormalizedConfig
from typebench.wrapper import RawRun, run_command


def _cfg() -> NormalizedConfig:
    return NormalizedConfig()


def test_stub_command_runs_and_reports_diagnostics(tmp_path: Path) -> None:
    adapter = StubAdapter(exit_code=1, diagnostics=4, files=9)
    argv, env = adapter.command("demo", _cfg(), ThreadMode.ALL_CORES, tmp_path)
    raw = run_command(argv, timeout=10, env=env)
    assert raw.exit_code == 1
    assert adapter.parse(raw.stdout, raw.stderr, raw.exit_code) == (4, 9)
    assert adapter.classify(raw) == ResultClass.DIAGNOSTICS


def test_stub_command_clean(tmp_path: Path) -> None:
    adapter = StubAdapter(exit_code=0, diagnostics=0, files=5)
    argv, env = adapter.command("demo", _cfg(), ThreadMode.ALL_CORES, tmp_path)
    raw = run_command(argv, timeout=10, env=env)
    assert adapter.classify(raw) == ResultClass.CLEAN
    assert adapter.parse(raw.stdout, raw.stderr, raw.exit_code) == (0, 5)


def test_stub_missing_binary_is_env_failure(tmp_path: Path) -> None:
    adapter = StubAdapter(missing_binary=True)
    argv, env = adapter.command("demo", _cfg(), ThreadMode.ALL_CORES, tmp_path)
    raw = run_command(argv, timeout=10, env=env)
    assert raw.env_error is True
    assert adapter.classify(raw) == ResultClass.FAILED_ENV


def test_stub_satisfies_adapter_protocol() -> None:
    assert isinstance(StubAdapter(), Adapter)


def test_classify_with_map_honors_universal_prefix_then_exit_map() -> None:
    m = {0: ResultClass.CLEAN, 1: ResultClass.DIAGNOSTICS, 2: ResultClass.FAILED_ENV}
    assert classify_with_map(RawRun(0, None, False, False, "", ""), m) == ResultClass.CLEAN
    assert classify_with_map(RawRun(2, None, False, False, "", ""), m) == ResultClass.FAILED_ENV
    # unknown code -> crash floor
    assert classify_with_map(RawRun(9, None, False, False, "", ""), m) == ResultClass.FAILED_CRASH
    # universal prefix wins over the map
    assert classify_with_map(RawRun(0, None, True, False, "", ""), m) == ResultClass.FAILED_TIMEOUT
    assert classify_with_map(RawRun(0, None, False, False, "", "", env_error=True), m) == ResultClass.FAILED_ENV
    assert classify_with_map(RawRun(0, 9, False, False, "", ""), m) == ResultClass.FAILED_OOM
```
(Keep the existing stateless/parse-garbage tests in the file; just update any `command(...)` calls to the new 4-arg form.)

- [ ] **Step 2: Run, expect FAIL** (signature/argument + missing `classify_with_map`/`NormalizedConfig`): `uv run pytest tests/test_stub_adapter.py -v`

- [ ] **Step 3: Edit `src/typebench/adapters/base.py`** — extend the protocol `command` signature and add the shared classifier. Replace the `command` method signature and add `classify_with_map` + the needed imports:
```python
# add to the TYPE_CHECKING block:
#   from pathlib import Path
#   from typebench.normalized_config import NormalizedConfig
# and at runtime import the SIGKILL constant + ResultClass for classify_with_map:
from typebench.wrapper import RawRun, _SIGKILL, classify_default
from typebench.models import ResultClass  # promote to runtime import (used by classify_with_map)
```
Change the protocol method to:
```python
    def command(
        self,
        project: str,
        config: NormalizedConfig,
        thread_mode: ThreadMode,
        workdir: Path,
    ) -> tuple[list[str], dict[str, str]]:
        """(argv, extra_env) running the checker on `project` under the
        normalized `config` (§6) and `thread_mode`. `workdir` is a run-scoped
        dir the adapter may write a generated tool config into (it persists
        across the probe + all timed runs). `extra_env` carries vars like
        TY_MAX_PARALLELISM (§5.3)."""
        ...
```
Add the shared classifier (DRYs the universal §7 prefix across adapters):
```python
def classify_with_map(raw: RawRun, exit_map: dict[int, ResultClass]) -> ResultClass:
    """Universal §7 prefix (env/oom/timeout/signal) then the tool's exit-code
    map; unknown codes fall to FAILED_CRASH. Tools with overloaded codes
    (mypy 2, pyrefly 1) override classify() with extra stdout/stderr logic."""
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
    return exit_map.get(raw.exit_code, ResultClass.FAILED_CRASH)
```
> Note: `_SIGKILL` is module-private in `wrapper.py` but reused here intentionally to keep one source of truth for the heuristic. If ruff/pyrefly object to importing a private name across modules, promote `_SIGKILL` to a public `SIGKILL` constant in `wrapper.py` and update `classify_default` + this import. Keep behavior identical.

- [ ] **Step 4: Edit `src/typebench/adapters/stub.py`** — update `command` to the new signature (ignores `config`/`workdir`; ARG002 already ignored for `adapters/**`):
```python
    def command(
        self,
        project: str,
        config: NormalizedConfig,
        thread_mode: ThreadMode,
        workdir: Path,
    ) -> tuple[list[str], dict[str, str]]:
        if self._missing_binary:
            return (["typebench-nonexistent-checker-xyz"], {})
        argv = [
            sys.executable, "-m", "typebench._fake_checker",
            "--exit-code", str(self._exit_code),
            "--diagnostics", str(self._diagnostics),
            "--files", str(self._files),
            "--sleep", str(self._sleep),
        ]
        if self._fail_after_runs is not None and self._state_file is not None:
            argv += ["--fail-after-runs", str(self._fail_after_runs), "--state-file", self._state_file]
        if self._signal is not None:
            argv += ["--signal", str(self._signal)]
        return (argv, {})
```
Add the TYPE_CHECKING imports it now needs: `from pathlib import Path` and `from typebench.normalized_config import NormalizedConfig`.

- [ ] **Step 5: Run, expect PASS:** `uv run pytest tests/test_stub_adapter.py -v` (then full gate).
- [ ] **Step 6: Commit:** `git add -A && git commit -m "feat(adapters): command() takes NormalizedConfig + workdir; add classify_with_map"`

---

## Task 3: Thread config through `collector.run_single` + the CLI

**Files:** Modify `src/typebench/collector.py`, `src/typebench/cli.py`, and tests `tests/test_collector.py`, `tests/test_e2e.py`, `tests/test_cli.py`.

- [ ] **Step 1: Update tests first.** In `tests/test_collector.py` and `tests/test_e2e.py`, every `run_single(adapter, project=..., thread_mode=..., ...)` call gains `config=NormalizedConfig()` (import it). Example (collector):
```python
from typebench.normalized_config import NormalizedConfig
...
    result = run_single(
        adapter, project="demo", config=NormalizedConfig(),
        thread_mode=ThreadMode.ALL_CORES, warmup=1, runs=2, timeout=10,
    )
```
Apply the same `config=NormalizedConfig()` addition to the parametrized + timeout + oom + timing-phase-failure tests in `test_e2e.py` and `test_collector.py`. In `tests/test_cli.py`, the stub invocations already go through the CLI which will default the config — no change needed beyond confirming `--tool stub` still works without `--src-root` (stub ignores config).

- [ ] **Step 2: Run, expect FAIL** (`run_single` missing `config`): `uv run pytest tests/test_collector.py tests/test_e2e.py -v`

- [ ] **Step 3: Edit `src/typebench/collector.py`** — add `config` param + run-scoped workdir. New signature + body head:
```python
import tempfile
from pathlib import Path
# add to TYPE_CHECKING: from typebench.normalized_config import NormalizedConfig


def run_single(
    adapter: Adapter,
    project: str,
    config: NormalizedConfig,
    thread_mode: ThreadMode,
    warmup: int,
    runs: int,
    timeout: float,
) -> RunResult:
    adapter.clear_cache(project)
    with tempfile.TemporaryDirectory(prefix="typebench-") as wd:
        workdir = Path(wd)
        argv, extra_env = adapter.command(project, config, thread_mode, workdir)
        # ... the existing probe + timing + RunResult assembly stays IDENTICAL,
        # just indented under this `with` (so the generated config file in
        # `workdir` persists across the probe and all timed runs).
        ...
```
Move the entire existing probe/time/assembly block inside the `with` (the `return RunResult(...)` happens inside, before the tempdir is cleaned up). Everything else (classification, timing, error_detail, CalledProcessError handling) is unchanged.

- [ ] **Step 4: Edit `src/typebench/cli.py`** — add normalized-config flags + build a `NormalizedConfig`, pass to `run_single`. Add options (Annotated form) and resolve src roots to absolute:
```python
from typebench.normalized_config import NormalizedConfig
...
    src_root: Annotated[
        list[str], typer.Option(help="First-party source dir to analyze (repeatable). Required for real tools.")
    ] = [],
    python_version: Annotated[str, typer.Option(help="Target Python version.")] = "3.12",
    python_platform: Annotated[str, typer.Option(help="Target platform.")] = "linux",
    venv: Annotated[
        str | None, typer.Option(help="Project venv interpreter for dep resolution.")
    ] = None,
```
Then before calling `run_single`:
```python
    config = NormalizedConfig(
        src_roots=tuple(str(Path(s).resolve()) for s in src_root),
        python_version=python_version,
        python_platform=python_platform,
        venv_python=venv,
    )
    result = run_single(
        adapter, project=project, config=config,
        thread_mode=thread_mode, warmup=warmup, runs=runs, timeout=timeout,
    )
```
> typer mutable default `[]` for `src_root`: ruff B006 may flag a mutable default. typer requires it for repeatable options; if ruff flags it, the established fix is `Annotated[list[str], typer.Option(...)] = []` is actually typer-idiomatic — but if B006 fires, change default to `None` typed `list[str] | None` and coerce `src_root = src_root or []` inside. Keep behavior identical; prefer whichever passes the strict gate cleanly.

- [ ] **Step 5: Run, expect PASS:** full gate `uv run pytest -q` (stub path still green; CLI builds config).
- [ ] **Step 6: Commit:** `git add -A && git commit -m "feat(collector,cli): thread NormalizedConfig + run-scoped workdir through the pipeline"`

---

## Task 4: Fixture projects (clean + error)

**Files:** Create `tests/fixtures/clean_project/__init__.py`, `tests/fixtures/clean_project/sample.py`, `tests/fixtures/error_project/__init__.py`, `tests/fixtures/error_project/sample.py`. Stdlib-only (no third-party deps → no envman needed; deps resolution is fully exercised in Plan 3).

> **Strict-gate scope:** these fixtures must NOT be type-checked by typebench's own pyrefly gate (the error fixture is type-wrong on purpose). Add `tests/fixtures/**` to `[tool.pyrefly] project-excludes` AND to ruff `per-file-ignores` (or `extend-exclude`) so the deliberately-broken fixture doesn't fail the repo gate.

- [ ] **Step 1: Add gate exclusions** in `pyproject.toml`:
  - `[tool.pyrefly]`: add `project-excludes = ["**/tests/fixtures/**"]` (pyrefly excludes the fixtures from the dogfood check).
  - `[tool.ruff]`: add `extend-exclude = ["tests/fixtures"]` (ruff skips them — they are sample inputs, not project code).
  Run `uv run pyrefly check` and `uv run ruff check .` to confirm still clean (fixtures ignored).

- [ ] **Step 2: Create the clean fixture** — `tests/fixtures/clean_project/sample.py`:
```python
def add(a: int, b: int) -> int:
    return a + b


result: int = add(2, 3)
```
and an empty `tests/fixtures/clean_project/__init__.py`.

- [ ] **Step 3: Create the error fixture** — `tests/fixtures/error_project/sample.py` (at least one unambiguous type error every checker flags):
```python
def add(a: int, b: int) -> int:
    return a + b


wrong: int = add("not", "ints")  # two type errors: str args to int params
bad: str = 123  # assignment type error
```
and an empty `tests/fixtures/error_project/__init__.py`.

- [ ] **Step 4: Verify gate still green** (fixtures excluded): `uv run ruff check . && uv run pyrefly check && uv run pytest -q`.
- [ ] **Step 5: Commit:** `git add -A && git commit -m "test(fixtures): clean + error sample projects for adapter tests"`

---

## Task 5: `PyrightAdapter`

**Files:** Create `src/typebench/adapters/pyright.py`; Test `tests/test_pyright_adapter.py`.

Reference facts: research doc → pyright section. JSON output gives both counts; exit map 0/1/2/3/4; `--project <workdir>` suppresses the project's own config; stateless.

- [ ] **Step 1: Write the failing test** — `tests/test_pyright_adapter.py` (pure parse/classify tests always run; live tests `skipif` pyright absent):
```python
import json
import shutil
from pathlib import Path

import pytest

from typebench.adapters.base import Adapter
from typebench.adapters.pyright import PyrightAdapter
from typebench.models import ResultClass, ThreadMode
from typebench.normalized_config import NormalizedConfig
from typebench.wrapper import RawRun, run_command

_FIXTURES = Path(__file__).parent / "fixtures"
_HAS_PYRIGHT = shutil.which("pyright") is not None


def test_pyright_is_an_adapter() -> None:
    assert isinstance(PyrightAdapter(), Adapter)


def test_parse_reads_summary_counts() -> None:
    blob = json.dumps(
        {"summary": {"errorCount": 3, "warningCount": 1, "filesAnalyzed": 7}, "generalDiagnostics": []}
    )
    assert PyrightAdapter().parse(blob, "", 1) == (3, 7)


def test_parse_is_graceful_on_garbage() -> None:
    assert PyrightAdapter().parse("not json", "", 2) == (None, None)


def test_classify_exit_map() -> None:
    a = PyrightAdapter()
    assert a.classify(RawRun(0, None, False, False, "", "")) == ResultClass.CLEAN
    assert a.classify(RawRun(1, None, False, False, "", "")) == ResultClass.DIAGNOSTICS
    assert a.classify(RawRun(2, None, False, False, "", "")) == ResultClass.FAILED_CRASH
    assert a.classify(RawRun(3, None, False, False, "", "")) == ResultClass.FAILED_ENV
    assert a.classify(RawRun(4, None, False, False, "", "")) == ResultClass.FAILED_ENV
    assert a.classify(RawRun(0, None, True, False, "", "")) == ResultClass.FAILED_TIMEOUT


def test_command_writes_pyrightconfig_and_targets_project(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=("/abs/src",), python_version="3.11")
    argv, env = PyrightAdapter().command("demo", cfg, ThreadMode.ONE_CORE, tmp_path)
    written = json.loads((tmp_path / "pyrightconfig.json").read_text())
    assert written["include"] == ["/abs/src"]
    assert written["typeCheckingMode"] == "standard"
    assert written["pythonVersion"] == "3.11"
    assert "--project" in argv and str(tmp_path) in argv
    assert "--outputjson" in argv
    assert "--skipunannotated" not in argv  # analyze ALL bodies (§6)


@pytest.mark.skipif(not _HAS_PYRIGHT, reason="pyright not installed")
def test_live_clean_project(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=(str(_FIXTURES / "clean_project"),))
    argv, env = PyrightAdapter().command("clean", cfg, ThreadMode.ONE_CORE, tmp_path)
    raw = run_command(argv, timeout=120, env=env)
    assert PyrightAdapter().classify(raw) == ResultClass.CLEAN
    diags, files = PyrightAdapter().parse(raw.stdout, raw.stderr, raw.exit_code)
    assert diags == 0
    assert files is not None and files > 0


@pytest.mark.skipif(not _HAS_PYRIGHT, reason="pyright not installed")
def test_live_error_project(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=(str(_FIXTURES / "error_project"),))
    argv, env = PyrightAdapter().command("err", cfg, ThreadMode.ONE_CORE, tmp_path)
    raw = run_command(argv, timeout=120, env=env)
    assert PyrightAdapter().classify(raw) == ResultClass.DIAGNOSTICS
    diags, _ = PyrightAdapter().parse(raw.stdout, raw.stderr, raw.exit_code)
    assert diags is not None and diags > 0


@pytest.mark.skipif(not _HAS_PYRIGHT, reason="pyright not installed")
def test_version_probe() -> None:
    v = PyrightAdapter().version()
    assert v.startswith("pyright") or v[0].isdigit()
```
> Note: the test references `Path(__file__).parent / "fixtures"` — but the fixtures live under `tests/fixtures`, and this test file is `tests/test_pyright_adapter.py`, so `Path(__file__).parent / "fixtures"` resolves to `tests/fixtures`. Correct.

- [ ] **Step 2: Run, expect FAIL** (`ModuleNotFoundError`): `uv run pytest tests/test_pyright_adapter.py -v`

- [ ] **Step 3: Implement `src/typebench/adapters/pyright.py`:**
```python
"""pyright adapter (spec §4, §6). Node-based; reference adapter for Plan 2.
See docs/superpowers/research/2026-06-08-checker-cli-facts.md (pyright)."""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import TYPE_CHECKING

from typebench.adapters.base import ParallelismCap, classify_with_map
from typebench.models import ResultClass, ThreadMode

if TYPE_CHECKING:
    from pathlib import Path

    from typebench.normalized_config import NormalizedConfig
    from typebench.wrapper import RawRun

# Exit codes (research doc): 0 clean, 1 errors, 2 fatal, 3 config, 4 bad-CLI/missing-path.
_EXIT_MAP: dict[int, ResultClass] = {
    0: ResultClass.CLEAN,
    1: ResultClass.DIAGNOSTICS,
    2: ResultClass.FAILED_CRASH,
    3: ResultClass.FAILED_ENV,
    4: ResultClass.FAILED_ENV,
}


class PyrightAdapter:
    name = "pyright"

    def version(self) -> str:
        out = subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["pyright", "--version"], capture_output=True, text=True, check=False
        )
        return out.stdout.strip() or out.stderr.strip()

    def install(self) -> str:
        # Distribution/version verification (Node pinning is an env concern,
        # documented in the research doc). Records the version for the manifest.
        return self.version()

    def command(
        self,
        project: str,
        config: NormalizedConfig,
        thread_mode: ThreadMode,
        workdir: Path,
    ) -> tuple[list[str], dict[str, str]]:
        pyright_config: dict[str, object] = {
            "include": list(config.src_roots),
            "exclude": list(config.exclude_globs),
            "typeCheckingMode": "standard",  # stock default (§6)
            "useLibraryCodeForTypes": True,  # resolve deps, report first-party only
            "pythonVersion": config.python_version,
            "pythonPlatform": "Linux",
        }
        if config.venv_python is not None:
            from pathlib import Path as _P  # local: only needed when a venv is set

            venv_dir = _P(config.venv_python).resolve().parent.parent
            pyright_config["venvPath"] = str(venv_dir.parent)
            pyright_config["venv"] = venv_dir.name
        (workdir / "pyrightconfig.json").write_text(json.dumps(pyright_config, indent=2))

        argv = [
            "pyright",
            "--project", str(workdir),
            "--outputjson",
            "--pythonversion", config.python_version,
            "--pythonplatform", "Linux",
        ]
        if thread_mode is ThreadMode.ALL_CORES:
            argv.append("--threads")  # bare = auto-parallelism (research doc)
        # ONE_CORE: omit --threads (default single main thread); affinity in Plan 4.
        return (argv, {})

    def parallelism_cap(self, thread_mode: ThreadMode) -> ParallelismCap:
        # pyright --threads is a hint, not an OS cap; affinity (Plan 4) makes it hard.
        return ParallelismCap(mechanism="cpu-affinity + single-thread", hard_cap=False)

    def parse(
        self, stdout: str, stderr: str, exit_code: int
    ) -> tuple[int | None, int | None]:
        try:
            payload = json.loads(stdout)
        except ValueError:
            return (None, None)
        if not isinstance(payload, dict):
            return (None, None)
        summary = payload.get("summary")
        if not isinstance(summary, dict):
            return (None, None)
        errors = summary.get("errorCount")
        files = summary.get("filesAnalyzed")
        return (
            errors if isinstance(errors, int) else None,
            files if isinstance(files, int) else None,
        )

    def classify(self, raw: RawRun) -> ResultClass:
        return classify_with_map(raw, _EXIT_MAP)

    def clear_cache(self, project: str) -> None:
        return None  # stateless single-shot (research doc)

    def prepare_command(self, project: str) -> str | None:
        return None
```
> ruff `S603` (subprocess without shell-injection check) may fire on `version()`. The argv is a fixed literal, no shell. `S` (flake8-bandit) is NOT in the selected rule set per Plan 1's config (`E,W,F,I,N,UP,B,C4,SIM,PTH,RET,ARG,TID,TC,PL,RUF`), so S603 will NOT fire — **remove the `# noqa: S603`** when implementing (an unused noqa trips ruff RUF100). It's shown here only to flag the consideration. Verify with `uv run ruff check`.

- [ ] **Step 4: Run, expect PASS** (pure tests pass; live tests pass if pyright installed, else skip): `uv run pytest tests/test_pyright_adapter.py -v`
- [ ] **Step 5: Commit:** `git add -A && git commit -m "feat(adapters): PyrightAdapter (config-gen, JSON parse, exit map)"`

---

## Task 6: Register pyright + end-to-end CLI run + parse-sanity guardrail

**Files:** Modify `src/typebench/cli.py` (registry); Create `tests/test_pyright_e2e.py`.

- [ ] **Step 1: Write the failing test** — `tests/test_pyright_e2e.py`:
```python
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from typebench.cli import app
from typebench.models import ResultClass, RunResult

runner = CliRunner()
_HAS = shutil.which("pyright") is not None and shutil.which("hyperfine") is not None
_FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.skipif(not _HAS, reason="needs pyright + hyperfine")
def test_cli_pyright_on_error_fixture(tmp_path: Path) -> None:
    out = tmp_path / "r.json"
    res = runner.invoke(
        app,
        [
            "run", "--tool", "pyright", "--project", "error_project",
            "--src-root", str(_FIXTURES / "error_project"),
            "--runs", "2", "--warmup", "1", "--output", str(out),
        ],
    )
    assert res.exit_code == 0, res.output
    rr = RunResult.model_validate_json(out.read_text())
    assert rr.tool == "pyright"
    assert rr.result_class == ResultClass.DIAGNOSTICS
    assert rr.diagnostics is not None and rr.diagnostics > 0
    assert rr.timing is not None and rr.timing.runs == 2
    assert rr.files is not None and rr.files > 0  # parse-sanity: files checked
```

- [ ] **Step 2: Run, expect FAIL** (pyright not registered → "Unknown tool"): `uv run pytest tests/test_pyright_e2e.py -v`

- [ ] **Step 3: Register pyright** in `src/typebench/cli.py`:
```python
from typebench.adapters.pyright import PyrightAdapter
...
_ADAPTERS = {
    "stub": StubAdapter,
    "pyright": PyrightAdapter,
}
```

- [ ] **Step 4: Run, expect PASS** (with pyright + hyperfine installed): `uv run pytest tests/test_pyright_e2e.py -v` then the FULL suite `uv run pytest -q`.
- [ ] **Step 5: Commit:** `git add -A && git commit -m "feat(cli): register pyright; e2e run on fixture project"`

---

## Definition of Done (Plan 2A)
- Quality gate green (ruff + pyrefly-strict + pytest); fixtures excluded from the dogfood gate.
- `typebench run --tool pyright --src-root <dir> --output r.json` produces a schema-valid `RunResult` with real `diagnostics`, `files`, and (with hyperfine) `timing`.
- `NormalizedConfig` drives a generated `pyrightconfig.json` that suppresses the project's own config; `--skipunannotated` is never passed (analyze all bodies, §6).
- Stub path + all Plan 1 tests still green under the new `command(config, workdir)` / `run_single(config)` signatures.
- pyright exit codes mapped (0/1/2/3/4 + universal env/oom/timeout/signal prefix); parse is garbage-safe; parse-sanity asserts `filesAnalyzed > 0`.
- No real mypy/ty/pyrefly adapters yet (Plan 2B); no corpus/envman/cgroup/renderer (Plans 3–6).

## Self-Review notes
- **§6 coverage (pyright):** target file set (`include`=src_roots) · excludes (tests/vendored/generated) · python version+platform · resolve-deps-report-first-party (`useLibraryCodeForTypes`+`include`) · analyze all bodies (no `--skipunannotated`, `standard` mode) · stock severities (`standard`) · suppress project config (`--project <workdir>`) · no plugins (n/a). Third-party *resolution* via venv is wired (`venvPath`/`venv`) but only lightly tested here (stdlib fixtures) — fully exercised in Plan 3.
- **Deferred to 2B:** mypy (text-summary parse, `--config-file=`, `--cache-dir=/dev/null`, overloaded exit 2), ty (no-JSON `concise` parse, `-v` files count, soft `TY_MAX_PARALLELISM`, exit 101), pyrefly (`--config` + `preset=default`, JSON parse, overloaded exit 1 disambiguation, hard `--threads 1`). All follow this template.
- **Cross-tool seam:** `classify_with_map` + `NormalizedConfig` + `workdir` are the shared surface 2B reuses; only parse/classify/config-gen differ per tool.
- **Placeholders:** none — every step has full code/commands.
