# typebench Plan 2B — mypy + ty + pyrefly adapters (Plan 2 of 6, part B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Ship the remaining three real adapters — **mypy, ty, pyrefly** — against the Plan 2A framework, so `typebench run --tool {mypy,ty,pyrefly} --src-root <dir>` each produces a schema-valid `RunResult` with real diagnostics + files + (with hyperfine) timing. After 2B, all four checkers are first-class and treated identically.

**Architecture:** Each adapter templates off `src/typebench/adapters/pyright.py` (the 2A reference) and reuses the shared seam — `NormalizedConfig`, the run-scoped `workdir`, `coerce_count`, and the universal §7 failure-prefix. The three new tools differ from pyright in exactly three dimensions, and **nothing else**:

1. **Parsing** — pyright reads JSON stdout. mypy reads a **text summary** (stdout); ty reads `Found N` (stdout) + `Indexed N` (`-v` stderr); pyrefly reads **JSON stdout** (`errors[]`) + a **`--summary=full` stderr** module count.
2. **Failure disambiguation** — pyright's failure codes are unambiguous. mypy **exit 2** is overloaded (usage/config → `failed{env}` vs `INTERNAL ERROR` → `failed{crash}`); pyrefly **exit 1** is overloaded (diagnostics vs fatal-config → `failed{env}`).
3. **Config + thread cap** — mypy is config-suppressed by `--config-file=` (no generated file) and is single-process; ty needs a generated `ty.toml` + `TY_MAX_PARALLELISM=1` (**soft** cap); pyrefly needs a generated `pyrefly.toml` (`preset="default"`) + `--threads 1` (**hard** cap).

To keep the universal failure-prefix a single source of truth even for the overloaded-exit tools, Task 1 extracts `universal_failure_prefix()` in `wrapper.py`; mypy/pyrefly call it, then run their own exit-code logic.

**Key finding (PLAN 2 TRAP — partially defused, one residual blind spot):** all four checkers use **`{0,1}` for measured-success** (0 clean, 1 diagnostics), so on the *success* path the timing wrapper's generic `{0,1}` gate (`wrapper.main`) agrees with every adapter's probe-phase `classify`. **BUT pyrefly's exit 1 is overloaded** (diagnostics OR fatal config/env). Only the adapter's `classify` disambiguates it; `wrapper.main` cannot (the wrapper is pydantic-free by contract — see `test_wrapper_import_does_not_pull_pydantic` — so it cannot import an adapter). The probe phase (which DOES use `Adapter.classify`) gates whether timing runs at all, which covers the common case: a deterministically-broken pyrefly run is caught at probe and never timed. The residual blind spot is a *flaky* fatal pyrefly exit-1 that occurs only **during a timed run** — `classify_default` reads it as `DIAGNOSTICS` → measured-success → hyperfine silently times a broken run. This is a genuine timing-phase blind spot, not "defused." Task 5 documents it honestly in the `PLAN 2 TRAP` comment and the DoD records it as accepted residual risk (the proper fix — adapter-aware timing classification without importing pydantic — is deferred; threading `{0,1}` success codes into the wrapper would NOT fix it, because exit 1 is in the success set yet still ambiguous). mypy's overloaded exit 2 has no such blind spot: it is outside `{0,1}`, so a flaky timed-run exit 2 makes the wrapper return nonzero → hyperfine aborts → collector records a failure (mislabeled crash-vs-env, but never silently timed).

**Tech Stack:** Python 3.12+, pydantic, typer, pytest, hyperfine. **New hard dev deps:** `mypy`, `ty` (PyPI). **`pyrefly` is ALREADY a dev dep** (`pyrefly>=0.16` in `pyproject.toml`, powering the strict gate) — Task 4 confirms it, does not add it. To make "hard dev dep ⇒ tests actually run" enforceable rather than aspirational, Task 6 adds a **non-skipping** `test_hard_deps_present` that FAILS (not skips) if `mypy`/`ty`/`pyrefly`/`pyright` are absent. The per-tool `skipif(which(tool) is None)` live tests stay as defense-in-depth, but the presence test is what guarantees the live path is exercised in the gate. **`hyperfine` stays a `skipif` system binary** — so the cross-tool e2e (which needs the full timed path) runs only where hyperfine is installed; the per-adapter live tests (which call `run_command` directly) run without it. Strict gate unchanged (ruff + pyrefly-strict + pytest).

**Inputs:** spec `docs/superpowers/specs/2026-06-07-typebench-design.md` (§6 LOCKED) · research `docs/superpowers/research/2026-06-08-checker-cli-facts.md` (authoritative per-tool flags/exits/parse) · reference adapter `src/typebench/adapters/pyright.py` (Plan 2A).

> **Version skew warning (from the research doc + local re-verify):** ty is preview (`0.0.x`, churns) and mypy 2.x added flags absent in 1.x. The research doc verified ty 0.0.44 / mypy 2.1.0 / pyright 1.1.410; **pyrefly was verified against `main` but the gate resolves `pyrefly>=0.16` (locally observed: pyrefly 1.0.0)** — so the pyrefly JSON shape (`errors[].severity`), `--summary=full` "N module(s)" stderr line, `--output-format json`, and `preset="default"` MUST be re-confirmed against the *installed* version, not `main` (use `pyrefly dump-config --config <gen>` to validate the config loads). The adapters below use only flags valid across mypy 1.x/2.x and ty 0.0.44. Re-verify on any bump. Pin nothing tighter than the gate needs; the §9 lock manifest (a later plan) owns reproducible pinning.

---

## Quality Gate (MANDATORY — unchanged from Plan 1/2A)
Before every commit (pre-commit enforces): `uv run ruff format .` (clean) · `uv run ruff check .` (All checks passed!) · `uv run pyrefly check` (0 errors) · `uv run pytest` (green). Every function incl. tests fully annotated. No `# noqa`/`# type: ignore` without an inline justification.

---

## File Structure (Plan 2B)

| File | Responsibility |
|------|----------------|
| `src/typebench/wrapper.py` | extract `universal_failure_prefix(raw) -> ResultClass \| None`; `classify_with_map` delegates to it (no behavior change). Update the `PLAN 2 TRAP` comment. |
| `src/typebench/adapters/mypy.py` | `MypyAdapter` — text-summary parse, regex `--exclude`, `--config-file=`, `--cache-dir=/dev/null`, exit-2 disambiguation (`INTERNAL ERROR`→crash), single-process cap. |
| `src/typebench/adapters/ty.py` | `TyAdapter` — `concise` stdout parse + `-v` stderr files, generated `ty.toml` with a `[src]` `exclude` table + **`--force-exclude`** (gitignore-style excludes do NOT apply to command-line paths without it — verified against ty docs/local), `TY_MAX_PARALLELISM` soft cap, exit map (101→crash), files-None tolerated. No `tomllib` import (the test reads the file back, the adapter only writes it). |
| `src/typebench/adapters/pyrefly.py` | `PyreflyAdapter` — generated `pyrefly.toml` (`preset="default"`), JSON `errors[]` + `--summary=full` stderr files (regex tolerates **singular** "1 module"), exit-1 disambiguation, `--threads 1` hard cap. |
| `src/typebench/cli.py` | register `mypy`, `ty`, `pyrefly` in `_ADAPTERS`. |
| `pyproject.toml` | add `mypy`, `ty` to the `dev` group (**`pyrefly` is already there**). |
| `tests/test_mypy_adapter.py`, `tests/test_ty_adapter.py`, `tests/test_pyrefly_adapter.py` | per-tool unit + live tests, including **live exclusion-enforcement** and **live hostile-config-suppression** tests (behavioral, not just structural-argv checks). |
| `tests/test_wrapper.py` | add a `universal_failure_prefix` test; existing classify tests stay green. **Merge** the new imports into the existing header (do not paste a second `from typebench.wrapper import RawRun ...` block — that re-imports `RawRun` and double-imports `ResultClass`, both ruff F811). |
| `tests/test_hard_deps.py` | **non-skipping** presence test — FAILS if `mypy`/`ty`/`pyrefly`/`pyright` are not on PATH, so "hard dev dep ⇒ live path runs" is enforced rather than silently skipped. |
| `tests/test_all_tools_e2e.py` | cross-tool e2e: every registered real tool flags the error fixture (`DIAGNOSTICS`, `diagnostics>0`) and passes the clean fixture (`CLEAN`). Neutrality guardrail — one parametrized test, all tools held to the identical contract. Skips only when `hyperfine` is absent. |
| `fixtures/exclude_project/` | new fixture: `pkg/ok.py` (clean) + `pkg/tests/broken.py` (a blatant type error). The locked excludes must drop `**/tests/**`, so a correct adapter reports `CLEAN`; a broken exclusion reports `DIAGNOSTICS`. Drives the live exclusion test for every tool. Add to `ruff` `extend-exclude` is unnecessary (already `extend-exclude = ["fixtures"]`). |

---

## Task 1: Extract `universal_failure_prefix` (shared §7 prefix for overloaded-exit tools)

**Files:** Modify `src/typebench/wrapper.py`, `tests/test_wrapper.py`.

mypy (exit 2) and pyrefly (exit 1) can't use `classify_with_map` directly — their exit code is overloaded, so they need the universal prefix (env/oom/timeout/signal) and then *custom* exit logic. Extract the prefix so there is still **one** definition.

- [ ] **Step 1: Add the failing test** in `tests/test_wrapper.py`. **MERGE the import below into the file's existing import header** — `test_wrapper.py` already does `from typebench.wrapper import RawRun, classify_default, run_command` and `from typebench.models import ResultClass`. Add only `universal_failure_prefix` to the existing wrapper import line and reuse the existing `ResultClass` import (do NOT add a second `from typebench.taxonomy import ResultClass` — re-binding `ResultClass`/`RawRun` is ruff F811). The two functions below go into the file body:
```python
# (header already imports RawRun, ResultClass; add universal_failure_prefix to the wrapper import)
from typebench.wrapper import universal_failure_prefix


def test_universal_failure_prefix_returns_none_when_no_universal_condition() -> None:
    # exit code alone is NOT a universal condition -> None (caller decides).
    assert universal_failure_prefix(RawRun(0, None, False, False, "", "")) is None
    assert universal_failure_prefix(RawRun(1, None, False, False, "", "")) is None
    assert universal_failure_prefix(RawRun(2, None, False, False, "", "")) is None


def test_universal_failure_prefix_detects_each_condition() -> None:
    assert universal_failure_prefix(RawRun(0, None, False, False, "", "", env_error=True)) == ResultClass.FAILED_ENV
    assert universal_failure_prefix(RawRun(0, None, False, True, "", "")) == ResultClass.FAILED_OOM
    assert universal_failure_prefix(RawRun(0, None, True, False, "", "")) == ResultClass.FAILED_TIMEOUT
    assert universal_failure_prefix(RawRun(0, 9, False, False, "", "")) == ResultClass.FAILED_OOM
    assert universal_failure_prefix(RawRun(0, 11, False, False, "", "")) == ResultClass.FAILED_CRASH
```

- [ ] **Step 2: Run, expect FAIL** (`ImportError`): `uv run pytest tests/test_wrapper.py -v`

- [ ] **Step 3: Edit `src/typebench/wrapper.py`** — add `universal_failure_prefix` and make `classify_with_map` delegate (behavior identical, so all existing `test_wrapper.py` cases stay green):
```python
def universal_failure_prefix(raw: RawRun) -> ResultClass | None:
    """The env/oom/timeout/signal classification shared by EVERY tool, in §7
    precedence order. Returns the failure class when a universal condition
    applies, else None — the caller then applies its own exit-code logic. This
    is the single source of the prefix for both `classify_with_map` (tools with a
    clean exit map) and the overloaded-exit adapters (mypy 2, pyrefly 1) that
    need custom per-code logic afterwards."""
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
    return None


def classify_with_map(raw: RawRun, exit_map: dict[int, ResultClass]) -> ResultClass:
    """Universal §7 prefix then the tool's exit-code map; unknown codes fall to
    FAILED_CRASH. Tools with overloaded codes (mypy 2, pyrefly 1) instead call
    `universal_failure_prefix` directly and run their own exit-code logic."""
    prefix = universal_failure_prefix(raw)
    if prefix is not None:
        return prefix
    return exit_map.get(raw.exit_code, ResultClass.FAILED_CRASH)
```
`classify_default` is unchanged (still delegates to `classify_with_map`). No new imports.

- [ ] **Step 4: Run, expect PASS** (new + all existing wrapper tests): `uv run pytest tests/test_wrapper.py -v`, then the full gate.
- [ ] **Step 5: Commit:** `git add -A && git commit -m "refactor(wrapper): extract universal_failure_prefix for overloaded-exit adapters"`

---

## Task 2: `MypyAdapter`

**Files:** Modify `pyproject.toml`; Create `src/typebench/adapters/mypy.py`; Test `tests/test_mypy_adapter.py`.

Reference facts: research doc → mypy section. **Text** parse (JSON has no files count, empty on clean). `--config-file=` suppresses project config (no generated file). `--cache-dir=/dev/null` + `--no-incremental` = cold/stateless. Exit 2 overloaded: `INTERNAL ERROR` → crash, else env.

- [ ] **Step 0: Add the dev dependency** — in `pyproject.toml` `[dependency-groups] dev`, add `"mypy"`, then `uv sync`. Verify `shutil.which("mypy")` resolves.

- [ ] **Step 1: Write the failing test** — `tests/test_mypy_adapter.py`:
```python
import shutil
from pathlib import Path

import pytest

from typebench.adapters import mypy as mypy_mod
from typebench.adapters.base import Adapter
from typebench.adapters.mypy import MypyAdapter
from typebench.collector import run_single
from typebench.models import ResultClass, RunResult, ThreadMode
from typebench.normalized_config import NormalizedConfig
from typebench.wrapper import RawRun, run_command

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_HAS_MYPY = shutil.which("mypy") is not None


def test_mypy_is_an_adapter() -> None:
    assert isinstance(MypyAdapter(), Adapter)


def test_parse_reads_text_summary_with_errors() -> None:
    out = "sample.py:5: error: ...\nFound 2 errors in 1 file (checked 3 source files)\n"
    assert MypyAdapter().parse(out, "", 1) == (2, 3)


def test_parse_reads_clean_summary() -> None:
    out = "Success: no issues found in 4 source files\n"
    assert MypyAdapter().parse(out, "", 0) == (0, 4)


def test_parse_singular_forms() -> None:
    out = "Found 1 error in 1 file (checked 1 source file)\n"
    assert MypyAdapter().parse(out, "", 1) == (1, 1)


def test_parse_is_graceful_on_garbage() -> None:
    assert MypyAdapter().parse("total nonsense", "", 1) == (None, None)


def test_classify_clean_requires_positive_files() -> None:
    a = MypyAdapter()
    clean = "Success: no issues found in 3 source files\n"
    assert a.classify(RawRun(0, None, False, False, clean, "")) == ResultClass.CLEAN


def test_classify_diagnostics() -> None:
    out = "Found 2 errors in 1 file (checked 3 source files)\n"
    assert MypyAdapter().classify(RawRun(1, None, False, False, out, "")) == ResultClass.DIAGNOSTICS


def test_classify_exit2_usage_is_env_failure() -> None:
    # exit 2 = usage / unreadable target / bad config -> failed{env}.
    raw = RawRun(2, None, False, False, "", "mypy: error: Missing target")
    assert MypyAdapter().classify(raw) == ResultClass.FAILED_ENV


def test_classify_exit2_internal_error_is_crash() -> None:
    # exit 2 WITH "INTERNAL ERROR" is a mypy crash, NOT an env failure.
    raw = RawRun(2, None, False, False, "sample.py: error: INTERNAL ERROR -- ...", "")
    assert MypyAdapter().classify(raw) == ResultClass.FAILED_CRASH


def test_classify_zero_files_on_exit0_is_env_failure() -> None:
    # exit 0 but checked 0 files = mis-scoped target, not a clean project.
    raw = RawRun(0, None, False, False, "Success: no issues found in 0 source files\n", "")
    assert MypyAdapter().classify(raw) == ResultClass.FAILED_ENV


def test_command_builds_first_party_only_argv(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=("/abs/src",), python_version="3.11", venv_python="/v/bin/python")
    argv, env = MypyAdapter().command("demo", cfg, ThreadMode.ONE_CORE, tmp_path)
    assert env == {}
    assert argv[0] == "mypy"
    assert "--config-file=" in argv  # suppress project config
    assert "--follow-imports=silent" in argv  # resolve deps, report first-party only
    assert "--check-untyped-defs" in argv  # analyze all bodies (§6)
    assert "--no-incremental" in argv
    assert "--cache-dir=/dev/null" in argv  # write no cache (cold)
    assert "--python-version" in argv and "3.11" in argv
    assert "--platform" in argv and "linux" in argv
    assert "--python-executable" in argv and "/v/bin/python" in argv
    assert "/abs/src" in argv  # src root analyzed (absolute, mypy accepts it)
    # exclude is a REGEX for mypy, matching the §6 excluded dir names.
    exclude_idx = argv.index("--exclude")
    assert "tests" in argv[exclude_idx + 1]


def test_command_no_venv_omits_python_executable(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=("/abs/src",))
    argv, _env = MypyAdapter().command("demo", cfg, ThreadMode.ONE_CORE, tmp_path)
    assert "--python-executable" not in argv


def test_version_is_no_raise_when_binary_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_k: object) -> object:
        raise FileNotFoundError("mypy")

    monkeypatch.setattr(mypy_mod.subprocess, "run", _boom)
    assert MypyAdapter().version() == "unknown"


def test_missing_mypy_yields_schema_valid_failed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    cfg = NormalizedConfig(src_roots=("/abs/src",))
    result = run_single(
        MypyAdapter(), project="demo", config=cfg,
        thread_mode=ThreadMode.ONE_CORE, warmup=1, runs=2, timeout=10,
    )
    assert isinstance(result, RunResult)
    assert result.result_class == ResultClass.FAILED_ENV
    assert result.tool_version == "unknown"


@pytest.mark.skipif(not _HAS_MYPY, reason="mypy not installed")
def test_live_clean_project(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=(str(_FIXTURES / "clean_project"),))
    argv, env = MypyAdapter().command("clean", cfg, ThreadMode.ONE_CORE, tmp_path)
    raw = run_command(argv, timeout=120, env=env)
    assert MypyAdapter().classify(raw) == ResultClass.CLEAN
    diags, files = MypyAdapter().parse(raw.stdout, raw.stderr, raw.exit_code)
    assert diags == 0
    assert files is not None and files > 0


@pytest.mark.skipif(not _HAS_MYPY, reason="mypy not installed")
def test_live_error_project(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=(str(_FIXTURES / "error_project"),))
    argv, env = MypyAdapter().command("err", cfg, ThreadMode.ONE_CORE, tmp_path)
    raw = run_command(argv, timeout=120, env=env)
    assert MypyAdapter().classify(raw) == ResultClass.DIAGNOSTICS
    diags, _ = MypyAdapter().parse(raw.stdout, raw.stderr, raw.exit_code)
    assert diags is not None and diags > 0


@pytest.mark.skipif(not _HAS_MYPY, reason="mypy not installed")
def test_install_records_compiled_flag() -> None:
    # Reproducibility (§9): the default PyPI mypy wheel is mypyc-compiled and its
    # --version ends with "(compiled: yes)". The lock manifest depends on this, so
    # a NON-compiled mypy in the gate is a real reproducibility regression the test
    # must catch — assert the marker, not merely "not unknown". (If a future build
    # ships non-compiled by design, downgrade this to a warning then, deliberately.)
    info = MypyAdapter().install()
    assert info and info != "unknown"
    assert "(compiled: yes)" in info, f"expected mypyc-compiled mypy for §9 reproducibility, got: {info!r}"
```

- [ ] **Step 2: Run, expect FAIL** (`ModuleNotFoundError`): `uv run pytest tests/test_mypy_adapter.py -v`

- [ ] **Step 3: Implement `src/typebench/adapters/mypy.py`:**
```python
"""mypy adapter (spec §4, §6). Text-summary parse (mypy JSON has no files count
and is empty on clean). See docs/superpowers/research/2026-06-08-checker-cli-facts.md."""

from __future__ import annotations

import re
import subprocess
from typing import TYPE_CHECKING

from typebench.adapters.base import ParallelismCap
from typebench.models import ResultClass, ThreadMode
from typebench.wrapper import universal_failure_prefix

if TYPE_CHECKING:
    from pathlib import Path

    from typebench.normalized_config import NormalizedConfig
    from typebench.wrapper import RawRun

# Text summaries (research doc). errors+files, and the clean form.
_FOUND_RE = re.compile(r"Found (\d+) errors? in \d+ files? \(checked (\d+) source files?\)")
_CLEAN_RE = re.compile(r"Success: no issues found in (\d+) source files?")


def _globs_to_exclude_regex(globs: tuple[str, ...]) -> str:
    """mypy --exclude takes a REGEX (matched against discovered paths), not globs.
    Render the §6 exclude globs as an alternation of their dir-name segments, e.g.
    '**/tests/**' -> 'tests'. Anchored on path separators so it matches a segment,
    not a substring."""
    names = sorted({g.strip("*/ ").split("/")[0] for g in globs if g.strip("*/ ")})
    if not names:
        return r"(?!)"  # match nothing
    return r"(^|/)(" + "|".join(re.escape(n) for n in names) + r")(/|$)"


class MypyAdapter:
    name = "mypy"

    def version(self) -> str:
        # No-raise (runs during RunResult assembly even on the env-failure path).
        try:
            out = subprocess.run(["mypy", "--version"], capture_output=True, text=True, check=False)
        except OSError:
            return "unknown"
        return out.stdout.strip() or out.stderr.strip() or "unknown"

    def install(self) -> str:
        # Records the version string, which carries "(compiled: yes)" for the §9
        # lock manifest (mypyc-compiled wheels are the default distribution).
        return self.version()

    def command(
        self,
        project: str,
        config: NormalizedConfig,
        thread_mode: ThreadMode,
        workdir: Path,
    ) -> tuple[list[str], dict[str, str]]:
        argv = [
            "mypy",
            "--python-version", config.python_version,
            "--platform", config.python_platform,
            "--check-untyped-defs",  # analyze all bodies (§6)
            "--follow-imports=silent",  # resolve dep types, report first-party only
            "--config-file=",  # empty value suppresses the project's own config (§6)
            "--no-incremental",
            "--cache-dir=/dev/null",  # write no cache (cold single-shot)
            "--exclude", _globs_to_exclude_regex(config.exclude_globs),
        ]
        if config.venv_python is not None:
            # Resolve installed third-party from the project venv (else mypy uses
            # its own interpreter env). First-party-only stays via follow-imports.
            argv += ["--python-executable", config.venv_python]
        argv += list(config.src_roots)
        return (argv, {})

    def parallelism_cap(self, thread_mode: ThreadMode) -> ParallelismCap:
        # mypy is single-process by default (--num-workers is experimental and can
        # change diagnostics). We never enable workers, so the cap is effectively
        # hard via single-process; affinity (Plan 4) pins the core.
        return ParallelismCap(mechanism="single-process + cpu-affinity", hard_cap=True)

    def parse(self, stdout: str, stderr: str, exit_code: int) -> tuple[int | None, int | None]:
        m = _FOUND_RE.search(stdout)
        if m is not None:
            return (int(m.group(1)), int(m.group(2)))
        c = _CLEAN_RE.search(stdout)
        if c is not None:
            return (0, int(c.group(1)))
        return (None, None)

    def classify(self, raw: RawRun) -> ResultClass:
        prefix = universal_failure_prefix(raw)
        if prefix is not None:
            return prefix
        code = raw.exit_code
        if code == 0:
            _diags, files = self.parse(raw.stdout, raw.stderr, raw.exit_code)
            # exit 0 must come with a positive checked-files count, else the target
            # was mis-scoped (false clean). mypy text always carries the count on
            # success, so None here also means broken output -> failed{env}.
            return ResultClass.CLEAN if files else ResultClass.FAILED_ENV
        if code == 1:
            return ResultClass.DIAGNOSTICS
        if code == 2:
            # Overloaded: an mypy crash also exits 2 but prints INTERNAL ERROR;
            # everything else at exit 2 is a usage/config/unreadable-target env error.
            blob = raw.stdout + raw.stderr
            return ResultClass.FAILED_CRASH if "INTERNAL ERROR" in blob else ResultClass.FAILED_ENV
        return ResultClass.FAILED_CRASH

    def clear_cache(self, project: str) -> None:
        return None  # --cache-dir=/dev/null + --no-incremental -> stateless

    def prepare_command(self, project: str) -> str | None:
        return None
```
> ruff: `re` and `subprocess` are stdlib; `S`/bandit not selected so fixed-argv `subprocess.run` needs no noqa. Unused `project`/`thread_mode`/`stderr`/`exit_code` params are covered by the `adapters/**` `ARG002` ignore. Verify with `uv run ruff check`.

- [ ] **Step 4: Run, expect PASS** (live tests RUN since mypy is a dev dep): `uv run pytest tests/test_mypy_adapter.py -v`. Confirm live tests are not skipped.
- [ ] **Step 5: Commit:** `git add -A && git commit -m "feat(adapters): MypyAdapter (text-summary parse, regex exclude, exit-2 disambiguation)"`

---

## Task 3: `TyAdapter`

**Files:** Modify `pyproject.toml`; Create `src/typebench/adapters/ty.py`; Test `tests/test_ty_adapter.py`.

Reference facts: research doc → ty section. **No JSON.** `concise` stdout `Found N diagnostic(s)` / `All checks passed!`; files only via `-v` stderr `INFO Indexed N file(s)` (fragile → `files` may be `None`, which is tolerated, NOT promoted to env). Generated `ty.toml` + explicit CLI flags suppress project config. `TY_MAX_PARALLELISM=1` = **soft** cap. Exit: 0/1 success, 2→env, 101→crash.

- [ ] **Step 0: Add the dev dependency** — `"ty"` to `[dependency-groups] dev`; `uv sync`; confirm `shutil.which("ty")`.

- [ ] **Step 1: Write the failing test** — `tests/test_ty_adapter.py`:
```python
import shutil
import tomllib
from pathlib import Path

import pytest

from typebench.adapters import ty as ty_mod
from typebench.adapters.base import Adapter
from typebench.adapters.ty import TyAdapter
from typebench.collector import run_single
from typebench.models import ResultClass, RunResult, ThreadMode
from typebench.normalized_config import NormalizedConfig
from typebench.wrapper import RawRun, run_command

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_HAS_TY = shutil.which("ty") is not None


def test_ty_is_an_adapter() -> None:
    assert isinstance(TyAdapter(), Adapter)


def test_parse_diagnostics_from_stdout_and_files_from_stderr() -> None:
    stdout = "error[...]: ...\nFound 3 diagnostics\n"
    stderr = "INFO Indexed 7 file(s)\n"
    assert TyAdapter().parse(stdout, stderr, 1) == (3, 7)


def test_parse_clean_with_indexed_files() -> None:
    assert TyAdapter().parse("All checks passed!\n", "INFO Indexed 5 file(s)\n", 0) == (0, 5)


def test_parse_files_none_when_no_verbose_line() -> None:
    # Without the -v "Indexed N" line the files count is unknowable -> None (tolerated).
    assert TyAdapter().parse("All checks passed!\n", "", 0) == (0, None)


def test_parse_singular_diagnostic_and_file() -> None:
    assert TyAdapter().parse("Found 1 diagnostic\n", "INFO Indexed 1 file(s)\n", 1) == (1, 1)


def test_parse_is_graceful_on_garbage() -> None:
    assert TyAdapter().parse("???", "???", 1) == (None, None)


def test_classify_exit_map() -> None:
    a = TyAdapter()
    clean = "All checks passed!\n"
    assert a.classify(RawRun(0, None, False, False, clean, "INFO Indexed 4 file(s)\n")) == ResultClass.CLEAN
    assert a.classify(RawRun(1, None, False, False, "Found 2 diagnostics\n", "")) == ResultClass.DIAGNOSTICS
    assert a.classify(RawRun(2, None, False, False, "", "")) == ResultClass.FAILED_ENV
    assert a.classify(RawRun(101, None, False, False, "", "")) == ResultClass.FAILED_CRASH
    assert a.classify(RawRun(0, None, True, False, clean, "")) == ResultClass.FAILED_TIMEOUT


def test_classify_clean_without_files_count_stays_clean() -> None:
    # ty's files count is best-effort (stderr -v). exit 0 + files None is NOT
    # promoted to env (unlike pyright/mypy, whose counts are reliable). Only a
    # CONFIRMED 0 is a false-clean.
    assert TyAdapter().classify(RawRun(0, None, False, False, "All checks passed!\n", "")) == ResultClass.CLEAN


def test_classify_zero_indexed_files_on_exit0_is_env_failure() -> None:
    raw = RawRun(0, None, False, False, "All checks passed!\n", "INFO Indexed 0 file(s)\n")
    assert TyAdapter().classify(raw) == ResultClass.FAILED_ENV


def test_command_writes_ty_toml_and_builds_argv(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=("/abs/src",), python_version="3.11", venv_python="/v/bin/python")
    argv, env = TyAdapter().command("demo", cfg, ThreadMode.ONE_CORE, tmp_path)
    assert env == {"TY_MAX_PARALLELISM": "1"}  # 1-core soft cap
    cfg_path = tmp_path / "ty.toml"
    assert "--config-file" in argv and str(cfg_path) in argv
    written = tomllib.loads(cfg_path.read_text())
    assert written["environment"]["python-version"] == "3.11"
    assert written["environment"]["python-platform"] == "linux"
    # §6 excludes rendered into [src].exclude (gitignore-style, project-anchored).
    assert written["src"]["exclude"]
    assert any("tests" in e for e in written["src"]["exclude"])
    assert argv[0] == "ty" and argv[1] == "check"
    assert "/abs/src" in argv
    assert "--force-exclude" in argv  # excludes MUST apply to command-line paths
    assert "--python" in argv and "/v/bin/python" in argv
    assert "--output-format" in argv and "concise" in argv
    assert "-v" in argv  # needed for the Indexed-files count
    assert "--color" in argv and "never" in argv


def test_command_all_cores_omits_parallelism_cap(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=("/abs/src",))
    _argv, env = TyAdapter().command("demo", cfg, ThreadMode.ALL_CORES, tmp_path)
    assert "TY_MAX_PARALLELISM" not in env  # all cores -> no cap


def test_parallelism_cap_is_soft() -> None:
    cap = TyAdapter().parallelism_cap(ThreadMode.ONE_CORE)
    assert cap.hard_cap is False


def test_version_is_no_raise_when_binary_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_k: object) -> object:
        raise FileNotFoundError("ty")

    monkeypatch.setattr(ty_mod.subprocess, "run", _boom)
    assert TyAdapter().version() == "unknown"


def test_missing_ty_yields_schema_valid_failed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    cfg = NormalizedConfig(src_roots=("/abs/src",))
    result = run_single(
        TyAdapter(), project="demo", config=cfg,
        thread_mode=ThreadMode.ONE_CORE, warmup=1, runs=2, timeout=10,
    )
    assert isinstance(result, RunResult)
    assert result.result_class == ResultClass.FAILED_ENV


@pytest.mark.skipif(not _HAS_TY, reason="ty not installed")
def test_live_clean_project(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=(str(_FIXTURES / "clean_project"),))
    argv, env = TyAdapter().command("clean", cfg, ThreadMode.ONE_CORE, tmp_path)
    raw = run_command(argv, timeout=120, env=env)
    assert TyAdapter().classify(raw) == ResultClass.CLEAN
    diags, _files = TyAdapter().parse(raw.stdout, raw.stderr, raw.exit_code)
    assert diags == 0


@pytest.mark.skipif(not _HAS_TY, reason="ty not installed")
def test_live_error_project(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=(str(_FIXTURES / "error_project"),))
    argv, env = TyAdapter().command("err", cfg, ThreadMode.ONE_CORE, tmp_path)
    raw = run_command(argv, timeout=120, env=env)
    assert TyAdapter().classify(raw) == ResultClass.DIAGNOSTICS
    diags, _ = TyAdapter().parse(raw.stdout, raw.stderr, raw.exit_code)
    assert diags is not None and diags > 0
```

- [ ] **Step 2: Run, expect FAIL:** `uv run pytest tests/test_ty_adapter.py -v`

- [ ] **Step 3: Implement `src/typebench/adapters/ty.py`:**
```python
"""ty adapter (spec §4, §6). ty has NO JSON output: diagnostics come from the
`concise` stdout summary, file count only from `-v` stderr (fragile -> may be
None). See docs/superpowers/research/2026-06-08-checker-cli-facts.md (ty)."""

from __future__ import annotations

import json
import re
import subprocess
from typing import TYPE_CHECKING

from typebench.adapters.base import ParallelismCap, coerce_count
from typebench.models import ResultClass, ThreadMode
from typebench.wrapper import classify_with_map

if TYPE_CHECKING:
    from pathlib import Path

    from typebench.normalized_config import NormalizedConfig
    from typebench.wrapper import RawRun

_FOUND_RE = re.compile(r"Found (\d+) diagnostics?")
_INDEXED_RE = re.compile(r"Indexed (\d+) file\(s\)")

# Exit codes (research doc): 0 clean, 1 diagnostics, 2 config/IO/CLI, 101 panic.
_EXIT_MAP: dict[int, ResultClass] = {
    0: ResultClass.CLEAN,
    1: ResultClass.DIAGNOSTICS,
    2: ResultClass.FAILED_ENV,
    101: ResultClass.FAILED_CRASH,
}


class TyAdapter:
    name = "ty"

    def version(self) -> str:
        try:
            out = subprocess.run(["ty", "--version"], capture_output=True, text=True, check=False)
        except OSError:
            return "unknown"
        return out.stdout.strip() or out.stderr.strip() or "unknown"

    def install(self) -> str:
        return self.version()

    def command(
        self,
        project: str,
        config: NormalizedConfig,
        thread_mode: ThreadMode,
        workdir: Path,
    ) -> tuple[list[str], dict[str, str]]:
        # Hand-render the tiny ty.toml (tomllib is read-only — no writer; the test
        # reads it back, the adapter only writes it). [environment] carries
        # version/platform; [src].exclude carries the §6 excludes in ty's
        # gitignore-style syntax (anchored to the project root). json.dumps quotes
        # the string values safely; keys are fixed literals (no user strings).
        exclude_items = ", ".join(json.dumps(g) for g in config.exclude_globs)
        lines = [
            "[environment]",
            f'python-version = "{config.python_version}"',
            f'python-platform = "{config.python_platform}"',
            "",
            "[src]",
            f"exclude = [{exclude_items}]",
        ]
        config_path = workdir / "ty.toml"
        config_path.write_text("\n".join(lines) + "\n")

        argv = [
            "ty", "check",
            *config.src_roots,
            "--config-file", str(config_path),  # suppress project [tool.ty] discovery
            # CRITICAL (ty docs, locally verified): gitignore-style excludes do NOT
            # apply to paths passed on the command line unless --force-exclude is set.
            # Without it a nested tests/ dir under an absolute src_root is still
            # checked -> breaks the §6 excludes contract and inflates ty's
            # diagnostics/file-count vs the others (neutrality leak).
            "--force-exclude",
            "--python-version", config.python_version,
            "--python-platform", config.python_platform,
            "--output-format", "concise",
            "-v",  # emits "Indexed N file(s)" on stderr (the only files source)
            "--no-progress",
            "--color", "never",
        ]
        for glob in config.exclude_globs:
            argv += ["--exclude", glob]  # belt-and-suspenders alongside [src].exclude
        if config.venv_python is not None:
            argv += ["--python", config.venv_python]  # resolve third-party from venv

        env: dict[str, str] = {}
        if thread_mode is ThreadMode.ONE_CORE:
            env["TY_MAX_PARALLELISM"] = "1"  # SOFT cap (ty may still spawn threads)
        return (argv, env)

    def parallelism_cap(self, thread_mode: ThreadMode) -> ParallelismCap:
        # TY_MAX_PARALLELISM is a soft task cap, not a hard thread cap.
        return ParallelismCap(mechanism="TY_MAX_PARALLELISM + cpu-affinity", hard_cap=False)

    def parse(self, stdout: str, stderr: str, exit_code: int) -> tuple[int | None, int | None]:
        files = None
        idx = _INDEXED_RE.search(stderr)
        if idx is not None:
            files = coerce_count(int(idx.group(1)))
        if "All checks passed!" in stdout:
            return (0, files)
        found = _FOUND_RE.search(stdout)
        if found is not None:
            return (coerce_count(int(found.group(1))), files)
        return (None, files)

    def classify(self, raw: RawRun) -> ResultClass:
        result = classify_with_map(raw, _EXIT_MAP)
        if result is ResultClass.CLEAN:
            _diags, files = self.parse(raw.stdout, raw.stderr, raw.exit_code)
            # ty's files count is best-effort (stderr -v). Only a CONFIRMED 0 is a
            # mis-scoped false-clean; files None is tolerated (unknowable, not broken)
            # -> stays CLEAN. (Contrast pyright/mypy, whose counts are reliable.)
            if files == 0:
                return ResultClass.FAILED_ENV
        return result

    def clear_cache(self, project: str) -> None:
        return None  # stateless `check`

    def prepare_command(self, project: str) -> str | None:
        return None
```
> ruff: the adapter imports `json`/`re`/`subprocess` (all stdlib, all used) and does **not** import `tomllib` (only the *test* reads the file back). `ParallelismCap`/`coerce_count`/`classify_with_map` are all used. Verify clean with `uv run ruff check` — the canonical block above is gate-clean as written (no F401, no `_ =` discard).

- [ ] **Step 4: Run, expect PASS** (live tests run): `uv run pytest tests/test_ty_adapter.py -v`.
- [ ] **Step 5: Commit:** `git add -A && git commit -m "feat(adapters): TyAdapter (concise+stderr parse, ty.toml, soft TY_MAX_PARALLELISM cap)"`

---

## Task 4: `PyreflyAdapter`

**Files:** Modify `pyproject.toml`; Create `src/typebench/adapters/pyrefly.py`; Test `tests/test_pyrefly_adapter.py`.

Reference facts: research doc → pyrefly section. Generated `pyrefly.toml` with **`preset="default"`** (the loose-file fallback to `basic` silences errors → false-clean; ALWAYS supply `--config` with `project-includes` + `preset="default"`). JSON stdout `errors[]` (count `severity=="error"`); files from `--summary=full` stderr `N modules`. **Exit 1 overloaded** (diagnostics vs fatal-config). `--threads 1` = **HARD** cap.

> **NEUTRALITY NOTE:** pyrefly is the user's own project. It gets the *exact same* treatment as the others — generated neutral config, stock `preset="default"` (not the favorable `basic`, not the harsh `strict`), identical parse-sanity and failure taxonomy. No special-casing in either direction. This adapter is reviewed to the same bar; the cross-tool e2e (Task 5) holds it to the identical contract.

- [ ] **Step 0: Confirm the dev dependency** — `pyrefly` is **already** in `[dependency-groups] dev` (`pyrefly>=0.16`, it powers the strict gate). Do NOT re-add it. Just `uv sync` and confirm `shutil.which("pyrefly")` resolves and `pyrefly --version` (record it — locally 1.0.0; the JSON/summary facts must match this version, not `main`).

- [ ] **Step 1: Write the failing test** — `tests/test_pyrefly_adapter.py`:
```python
import json
import shutil
import tomllib
from pathlib import Path

import pytest

from typebench.adapters import pyrefly as pyrefly_mod
from typebench.adapters.base import Adapter
from typebench.adapters.pyrefly import PyreflyAdapter
from typebench.collector import run_single
from typebench.models import ResultClass, RunResult, ThreadMode
from typebench.normalized_config import NormalizedConfig
from typebench.wrapper import RawRun, run_command

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_HAS_PYREFLY = shutil.which("pyrefly") is not None


def test_pyrefly_is_an_adapter() -> None:
    assert isinstance(PyreflyAdapter(), Adapter)


def test_parse_counts_only_error_severity() -> None:
    # The errors[] array includes non-error directives (e.g. reveal_type info);
    # diagnostics = count of severity == "error" only.
    blob = json.dumps({"errors": [
        {"severity": "error", "name": "bad-assignment"},
        {"severity": "error", "name": "bad-argument"},
        {"severity": "info", "name": "reveal-type"},
    ]})
    stderr = "INFO 3 modules\n"
    assert PyreflyAdapter().parse(blob, stderr, 1) == (2, 3)


def test_parse_clean_zero_errors() -> None:
    assert PyreflyAdapter().parse(json.dumps({"errors": []}), "INFO 5 modules\n", 0) == (0, 5)


def test_parse_counts_singular_module() -> None:
    # pyrefly prints "1 module" (singular) for a one-module project; the regex
    # must tolerate it or single-module fixtures parse files=None (then false-clean
    # detection silently weakens). Locally observed on pyrefly 1.0.0.
    assert PyreflyAdapter().parse(json.dumps({"errors": []}), "INFO 1 module\n", 0) == (0, 1)


def test_parse_files_none_without_summary() -> None:
    assert PyreflyAdapter().parse(json.dumps({"errors": []}), "", 0) == (0, None)


def test_parse_is_graceful_on_garbage() -> None:
    assert PyreflyAdapter().parse("not json", "", 1) == (None, None)


def test_classify_exit1_with_parseable_errors_is_diagnostics() -> None:
    blob = json.dumps({"errors": [{"severity": "error", "name": "x"}]})
    raw = RawRun(1, None, False, False, blob, "INFO 2 modules\n")
    assert PyreflyAdapter().classify(raw) == ResultClass.DIAGNOSTICS


def test_classify_exit1_without_parseable_json_is_env_failure() -> None:
    # exit 1 is overloaded: diagnostics OR fatal config/IO. No parseable JSON ->
    # it was a fatal config error, NOT diagnostics -> failed{env}.
    raw = RawRun(1, None, False, False, "Fatal configuration error", "error finding Python interpreter")
    assert PyreflyAdapter().classify(raw) == ResultClass.FAILED_ENV


def test_classify_clean_zero_modules_is_env_failure() -> None:
    raw = RawRun(0, None, False, False, json.dumps({"errors": []}), "INFO 0 modules\n")
    assert PyreflyAdapter().classify(raw) == ResultClass.FAILED_ENV


def test_classify_exit3_is_env_and_101_is_crash() -> None:
    a = PyreflyAdapter()
    assert a.classify(RawRun(3, None, False, False, "", "")) == ResultClass.FAILED_ENV
    assert a.classify(RawRun(101, None, False, False, "", "")) == ResultClass.FAILED_CRASH


def test_command_writes_pyrefly_toml_with_default_preset(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=("/abs/src",), python_version="3.11", venv_python="/v/bin/python")
    argv, env = PyreflyAdapter().command("demo", cfg, ThreadMode.ONE_CORE, tmp_path)
    cfg_path = tmp_path / "pyrefly.toml"
    assert "--config" in argv and str(cfg_path) in argv
    written = tomllib.loads(cfg_path.read_text())
    assert written["preset"] == "default"  # stock-neutral, NOT basic/strict
    assert written["project-includes"] == ["/abs/src"]
    assert written["python-version"] == "3.11"
    assert written["python-platform"] == "linux"
    assert written["check-unannotated-defs"] is True
    assert written["python-interpreter-path"] == "/v/bin/python"
    assert "--output-format" in argv and "json" in argv
    assert "--summary=full" in argv
    assert "--threads" in argv and "1" in argv  # 1-core HARD cap
    assert "--check-all" not in argv and "-a" not in argv  # would report deps too


def test_command_all_cores_omits_threads(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=("/abs/src",))
    argv, _env = PyreflyAdapter().command("demo", cfg, ThreadMode.ALL_CORES, tmp_path)
    assert "--threads" not in argv


def test_parallelism_cap_is_hard() -> None:
    assert PyreflyAdapter().parallelism_cap(ThreadMode.ONE_CORE).hard_cap is True


def test_version_is_no_raise_when_binary_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_k: object) -> object:
        raise FileNotFoundError("pyrefly")

    monkeypatch.setattr(pyrefly_mod.subprocess, "run", _boom)
    assert PyreflyAdapter().version() == "unknown"


def test_missing_pyrefly_yields_schema_valid_failed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    cfg = NormalizedConfig(src_roots=("/abs/src",))
    result = run_single(
        PyreflyAdapter(), project="demo", config=cfg,
        thread_mode=ThreadMode.ONE_CORE, warmup=1, runs=2, timeout=10,
    )
    assert isinstance(result, RunResult)
    assert result.result_class == ResultClass.FAILED_ENV


@pytest.mark.skipif(not _HAS_PYREFLY, reason="pyrefly not installed")
def test_live_clean_project(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=(str(_FIXTURES / "clean_project"),))
    argv, env = PyreflyAdapter().command("clean", cfg, ThreadMode.ONE_CORE, tmp_path)
    raw = run_command(argv, timeout=120, env=env)
    assert PyreflyAdapter().classify(raw) == ResultClass.CLEAN
    diags, files = PyreflyAdapter().parse(raw.stdout, raw.stderr, raw.exit_code)
    assert diags == 0
    # The --summary=full "N module(s)" line must parse on the real tool, else the
    # false-clean guard silently degrades to "tolerate None". Assert it works live.
    assert files is not None and files > 0


@pytest.mark.skipif(not _HAS_PYREFLY, reason="pyrefly not installed")
def test_live_error_project(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=(str(_FIXTURES / "error_project"),))
    argv, env = PyreflyAdapter().command("err", cfg, ThreadMode.ONE_CORE, tmp_path)
    raw = run_command(argv, timeout=120, env=env)
    assert PyreflyAdapter().classify(raw) == ResultClass.DIAGNOSTICS
    diags, files = PyreflyAdapter().parse(raw.stdout, raw.stderr, raw.exit_code)
    assert diags is not None and diags > 0
    assert files is not None and files > 0
```

- [ ] **Step 2: Run, expect FAIL:** `uv run pytest tests/test_pyrefly_adapter.py -v`

- [ ] **Step 3: Implement `src/typebench/adapters/pyrefly.py`:**
```python
"""pyrefly adapter (spec §4, §6). Project-mode check driven by a generated
pyrefly.toml (preset="default" — the loose-file fallback to basic silences
errors). JSON stdout errors[] + --summary=full stderr module count. Exit 1 is
overloaded (diagnostics vs fatal config). pyrefly is treated identically to every
other entrant — no favorable defaults. See the research doc (pyrefly)."""

from __future__ import annotations

import json
import re
import subprocess
from typing import TYPE_CHECKING

from typebench.adapters.base import ParallelismCap, coerce_count
from typebench.models import ResultClass, ThreadMode
from typebench.wrapper import universal_failure_prefix

if TYPE_CHECKING:
    from pathlib import Path

    from typebench.normalized_config import NormalizedConfig
    from typebench.wrapper import RawRun

_MODULES_RE = re.compile(r"(\d+) modules?")  # singular "1 module" is real output


def _toml_str_list(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(json.dumps(v) for v in values) + "]"


class PyreflyAdapter:
    name = "pyrefly"

    def version(self) -> str:
        try:
            out = subprocess.run(["pyrefly", "--version"], capture_output=True, text=True, check=False)
        except OSError:
            return "unknown"
        return out.stdout.strip() or out.stderr.strip() or "unknown"

    def install(self) -> str:
        return self.version()

    def command(
        self,
        project: str,
        config: NormalizedConfig,
        thread_mode: ThreadMode,
        workdir: Path,
    ) -> tuple[list[str], dict[str, str]]:
        # kebab-case keys (research doc). preset="default" is the stock-neutral
        # policy — NOT basic (under-reports -> false-clean) and NOT strict
        # (over-reports). Explicit project-includes prevents the loose-file
        # fallback to basic. json.dumps quotes string values safely.
        lines = [
            'preset = "default"',
            f"project-includes = {_toml_str_list(config.src_roots)}",
            f"project-excludes = {_toml_str_list(config.exclude_globs)}",
            f'python-version = "{config.python_version}"',
            f'python-platform = "{config.python_platform}"',
            "check-unannotated-defs = true",
            'infer-return-types = "checked"',
        ]
        if config.venv_python is not None:
            lines.append(f"python-interpreter-path = {json.dumps(config.venv_python)}")
        config_path = workdir / "pyrefly.toml"
        config_path.write_text("\n".join(lines) + "\n")

        argv = [
            "pyrefly", "check",
            "--config", str(config_path),  # short-circuits discovery (suppress project cfg)
            "--output-format", "json",
            "--summary=full",  # emits "N modules" on stderr (the files source)
        ]
        if thread_mode is ThreadMode.ONE_CORE:
            argv += ["--threads", "1"]  # HARD cap (rayon pool = 1)
        return (argv, {})

    def parallelism_cap(self, thread_mode: ThreadMode) -> ParallelismCap:
        # --threads 1 is a HARD cap (rayon num_threads(1)); RAYON_NUM_THREADS is
        # NOT honored. Affinity (Plan 4) pins the core on top.
        return ParallelismCap(mechanism="--threads (rayon) + cpu-affinity", hard_cap=True)

    def _files(self, stderr: str) -> int | None:
        m = _MODULES_RE.search(stderr)
        return coerce_count(int(m.group(1))) if m is not None else None

    def parse(self, stdout: str, stderr: str, exit_code: int) -> tuple[int | None, int | None]:
        files = self._files(stderr)
        try:
            payload = json.loads(stdout)
        except ValueError:
            return (None, files)
        if not isinstance(payload, dict):
            return (None, files)
        errors = payload.get("errors")
        if not isinstance(errors, list):
            return (None, files)
        # The array includes non-error directives; count severity == "error" only.
        count = sum(1 for e in errors if isinstance(e, dict) and e.get("severity") == "error")
        return (count, files)

    def classify(self, raw: RawRun) -> ResultClass:
        prefix = universal_failure_prefix(raw)
        if prefix is not None:
            return prefix
        code = raw.exit_code
        diags, files = self.parse(raw.stdout, raw.stderr, raw.exit_code)
        if code == 0:
            # Clean only if confirmed by a positive module count; 0 = mis-scoped
            # includes (false-clean). files None tolerated (stderr-scraped).
            if files == 0:
                return ResultClass.FAILED_ENV
            return ResultClass.CLEAN
        if code == 1:
            # Overloaded: parseable JSON with >=1 error -> diagnostics; otherwise a
            # fatal config/IO error reported via anyhow -> failed{env}.
            if diags is not None and diags > 0:
                return ResultClass.DIAGNOSTICS
            return ResultClass.FAILED_ENV
        if code == 3:
            return ResultClass.FAILED_ENV
        return ResultClass.FAILED_CRASH

    def clear_cache(self, project: str) -> None:
        return None  # stateless `check`

    def prepare_command(self, project: str) -> str | None:
        return None
```
> ruff: `json`/`re`/`subprocess` stdlib. `_toml_str_list` keeps key/value quoting in one place. Verify no unused imports. `uv run ruff check`.

- [ ] **Step 4: Run, expect PASS** (live tests run): `uv run pytest tests/test_pyrefly_adapter.py -v`.
- [ ] **Step 5: Commit:** `git add -A && git commit -m "feat(adapters): PyreflyAdapter (preset=default config, JSON+summary parse, exit-1 disambiguation, hard threads)"`

---

## Task 5: Register all three + cross-tool e2e + update the PLAN 2 TRAP note

**Files:** Modify `src/typebench/cli.py`, `src/typebench/wrapper.py` (comment only); Create `tests/test_all_tools_e2e.py`.

- [ ] **Step 1: Write the failing test** — `tests/test_all_tools_e2e.py` (the neutrality guardrail — every real tool, identical contract):
```python
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from typebench.cli import app
from typebench.models import ResultClass, RunResult

runner = CliRunner()
_FIXTURES = Path(__file__).parent.parent / "fixtures"
_REAL_TOOLS = ["mypy", "pyright", "ty", "pyrefly"]


def _run(tool: str, fixture: str, out: Path) -> RunResult:
    res = runner.invoke(
        app,
        [
            "run", "--tool", tool, "--project", fixture,
            "--src-root", str(_FIXTURES / fixture),
            "--runs", "2", "--warmup", "1", "--output", str(out),
        ],
    )
    assert res.exit_code == 0, res.output
    return RunResult.model_validate_json(out.read_text())


@pytest.mark.skipif(shutil.which("hyperfine") is None, reason="needs hyperfine")
@pytest.mark.parametrize("tool", _REAL_TOOLS)
def test_tool_flags_error_fixture(tool: str, tmp_path: Path) -> None:
    if shutil.which(tool) is None:
        pytest.skip(f"{tool} not installed")
    rr = _run(tool, "error_project", tmp_path / "r.json")
    assert rr.tool == tool
    assert rr.result_class == ResultClass.DIAGNOSTICS
    assert rr.diagnostics is not None and rr.diagnostics > 0
    assert rr.timing is not None and rr.timing.runs == 2


@pytest.mark.skipif(shutil.which("hyperfine") is None, reason="needs hyperfine")
@pytest.mark.parametrize("tool", _REAL_TOOLS)
def test_tool_passes_clean_fixture(tool: str, tmp_path: Path) -> None:
    if shutil.which(tool) is None:
        pytest.skip(f"{tool} not installed")
    rr = _run(tool, "clean_project", tmp_path / "r.json")
    assert rr.tool == tool
    assert rr.result_class == ResultClass.CLEAN
    assert rr.diagnostics == 0
```

- [ ] **Step 2: Run, expect FAIL** (tools not registered → "Unknown tool"): `uv run pytest tests/test_all_tools_e2e.py -v`

- [ ] **Step 3: Register the three adapters** in `src/typebench/cli.py` (match the existing import + registry style):
```python
from typebench.adapters.mypy import MypyAdapter
from typebench.adapters.pyrefly import PyreflyAdapter
from typebench.adapters.ty import TyAdapter
...
_ADAPTERS = {
    "mypy": MypyAdapter,
    "pyright": PyrightAdapter,
    "pyrefly": PyreflyAdapter,
    "stub": StubAdapter,
    "ty": TyAdapter,
}
```

- [ ] **Step 4: Update the `PLAN 2 TRAP` comment** in `src/typebench/wrapper.py` `main()` to record the HONEST finding — do not claim the trap is fully "defused":
  - **Success path:** all four tools' measured-success codes ⊆ `{0,1}`, so the generic gate agrees with each adapter's probe `classify`. No success-code threading is needed for the clean/diagnostics case.
  - **Residual blind spot (pyrefly):** exit 1 is overloaded (diagnostics OR fatal env). The probe uses the real `Adapter.classify`, disambiguates it, and gates whether timing runs — so a *deterministically*-broken run is caught at probe and never timed. But a **flaky fatal pyrefly exit-1 that occurs only during a timed run** is read by `classify_default` as `DIAGNOSTICS` → measured-success → hyperfine silently times a broken run. Threading `{0,1}` success codes would NOT fix this (exit 1 is in the success set yet still ambiguous). The only real fix is adapter-aware timing classification, which the wrapper **cannot** do without importing pydantic — it must stay pydantic-free (`test_wrapper_import_does_not_pull_pydantic`). Record this as **accepted residual risk for 2B**; revisit when the timing path is reworked.
  - **mypy (no blind spot):** overloaded exit 2 is OUTSIDE `{0,1}`, so a flaky timed-run exit 2 → wrapper returns nonzero → hyperfine aborts → collector records a failure (mislabeled crash-vs-env, but never silently timed).
  Keep the gate code as-is (do not thread tool-specific codes — they wouldn't close the pyrefly gap anyway).

- [ ] **Step 5: Run, expect PASS** (all four tools registered; e2e runs with hyperfine present): `uv run pytest tests/test_all_tools_e2e.py -v`, then the FULL suite `uv run pytest -q`.
- [ ] **Step 6: Commit:** `git add -A && git commit -m "feat(cli): register mypy/ty/pyrefly; cross-tool e2e neutrality guardrail"`

---

## Task 6: Neutrality enforcement — hard-dep presence, live exclusions, hostile-config suppression

**Files:** Create `fixtures/exclude_project/...`, `tests/test_hard_deps.py`, `tests/test_neutrality_live.py`.

The argv/structural tests prove the adapters *build* the right flags; this task proves the flags *behave* — that excludes actually drop files and that the project's own config is actually ignored. Without these, two neutrality-critical contracts (locked §6 excludes, config suppression) are asserted only structurally. These run without hyperfine (they call `run_command` directly, like the per-adapter live tests).

- [ ] **Step 1: Create the exclusion fixture** — a clean top-level module plus a type error buried under `tests/` that the §6 excludes MUST drop:
  - `fixtures/exclude_project/__init__.py` — empty.
  - `fixtures/exclude_project/ok.py` — `x: int = 1`
  - `fixtures/exclude_project/tests/__init__.py` — empty.
  - `fixtures/exclude_project/tests/broken.py` — `y: str = 123  # excluded -> must NOT be reported`
  (`fixtures/` is already in ruff `extend-exclude`, so the deliberate error is not linted.)

- [ ] **Step 2: Write `tests/test_hard_deps.py`** — the non-skipping presence guard so "hard dev dep ⇒ live path runs" is enforced, not silently skipped:
```python
import shutil

import pytest

# pyrefly is already a dev dep; mypy + ty are added in 2B; pyright came in 2A.
_HARD_DEPS = ["mypy", "ty", "pyrefly", "pyright"]


@pytest.mark.parametrize("binary", _HARD_DEPS)
def test_hard_dependency_is_installed(binary: str) -> None:
    # These are declared dev deps, so the gate MUST have them on PATH. This test
    # FAILS (does not skip) when one is missing — turning the per-tool live tests'
    # silent `skipif` skips into one loud, intentional failure. hyperfine is the
    # ONLY tool allowed to be absent (it stays a skipif system binary).
    assert shutil.which(binary) is not None, (
        f"{binary} is a hard dev dep but is not on PATH; run `uv sync` and retry"
    )
```

- [ ] **Step 3: Write `tests/test_neutrality_live.py`** — behavioral exclusion + config-suppression, parametrized over all four adapters:
```python
import shutil
from pathlib import Path

import pytest

from typebench.adapters.base import Adapter
from typebench.adapters.mypy import MypyAdapter
from typebench.adapters.pyrefly import PyreflyAdapter
from typebench.adapters.pyright import PyrightAdapter
from typebench.adapters.ty import TyAdapter
from typebench.models import ResultClass, ThreadMode
from typebench.normalized_config import NormalizedConfig
from typebench.wrapper import run_command

_FIXTURES = Path(__file__).parent.parent / "fixtures"

# (name, adapter, discovered-config filename, body that WOULD hide all diagnostics
# if the tool honored the project's own config instead of our generated one).
_CASES: list[tuple[str, Adapter, str, str]] = [
    ("mypy", MypyAdapter(), "mypy.ini", "[mypy]\nignore_errors = True\n"),
    ("ty", TyAdapter(), "ty.toml", '[src]\nexclude = ["**/*.py"]\n'),
    ("pyrefly", PyreflyAdapter(), "pyrefly.toml", 'project-excludes = ["**/*.py"]\n'),
    ("pyright", PyrightAdapter(), "pyrightconfig.json", '{"typeCheckingMode": "off"}\n'),
]
_IDS = [c[0] for c in _CASES]


def _classify(adapter: Adapter, src_root: Path, workdir: Path) -> ResultClass:
    cfg = NormalizedConfig(src_roots=(str(src_root),))
    argv, env = adapter.command("neutral", cfg, ThreadMode.ONE_CORE, workdir)
    raw = run_command(argv, timeout=120, env=env)
    return adapter.classify(raw)


@pytest.mark.parametrize(("name", "adapter", "_fn", "_body"), _CASES, ids=_IDS)
def test_excludes_drop_nested_tests_dir(
    name: str, adapter: Adapter, _fn: str, _body: str, tmp_path: Path
) -> None:
    if shutil.which(name) is None:
        pytest.skip(f"{name} not installed")
    # exclude_project = clean ok.py + a type error under tests/. The §6 excludes
    # MUST drop **/tests/** so the error never surfaces -> CLEAN. A broken exclusion
    # (e.g. ty without --force-exclude) checks tests/broken.py -> DIAGNOSTICS, which
    # fails here. This is the live regression guard for the exclude contract.
    result = _classify(adapter, _FIXTURES / "exclude_project", tmp_path)
    assert result == ResultClass.CLEAN, f"{name} did not exclude tests/ (got {result})"


@pytest.mark.parametrize(("name", "adapter", "filename", "body"), _CASES, ids=_IDS)
def test_hostile_project_config_is_suppressed(
    name: str,
    adapter: Adapter,
    filename: str,
    body: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if shutil.which(name) is None:
        pytest.skip(f"{name} not installed")
    # A project with a REAL type error plus a hostile config that would silence it
    # if discovered. cwd is set to the project so each tool's normal config
    # discovery (cwd / walk-up) WOULD find the hostile file — making the test
    # non-trivial. Our adapter suppresses the project config (mypy --config-file=,
    # ty/pyrefly/pyright explicit generated config), so the error MUST still be
    # reported -> DIAGNOSTICS. If the tool honored the hostile config we'd get
    # CLEAN -> neutrality breach -> failure here.
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "__init__.py").write_text("")
    (proj / "broken.py").write_text("bad: str = 123\n")
    (proj / filename).write_text(body)
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(proj)  # so discovery WOULD find the hostile config absent suppression
    result = _classify(adapter, proj, workdir)
    assert result == ResultClass.DIAGNOSTICS, (
        f"{name} appears to have honored {filename} (got {result}); project config not suppressed"
    )
```
> The hostile-config test deliberately `chdir`s into the project so each tool's discovery would otherwise pick up the file (mypy reads config from cwd, not the target dir, so without the chdir its case would pass trivially). If a future tool discovers config by a mechanism cwd doesn't cover, strengthen its case rather than trusting a green pass. Adapters use absolute `src_roots`, so the chdir does not perturb path math.

- [ ] **Step 4: Run, expect PASS** (all four installed via hard deps): `uv run pytest tests/test_hard_deps.py tests/test_neutrality_live.py -v`, then the FULL suite. If `test_excludes_drop_nested_tests_dir[ty]` fails, `--force-exclude` / `[src].exclude` is the culprit. If a `test_hostile_project_config_is_suppressed[*]` fails, that tool's config suppression is leaking.
- [ ] **Step 5: Commit:** `git add -A && git commit -m "test(neutrality): live exclusion + hostile-config suppression + hard-dep presence guards"`

---

## Definition of Done (Plan 2B)
- Quality gate green (ruff + pyrefly-strict + pytest). The new code blocks are gate-clean as written (no F401/F811/`_ =` discards). `mypy`+`ty` are added as dev deps; `pyrefly` already is one. `test_hard_deps.py` **fails** (not skips) if any of mypy/ty/pyrefly/pyright is missing, so the per-tool live `skipif` tests genuinely run in the gate. `hyperfine` is the only allowed-absent binary: the per-adapter live tests run without it, but the **cross-tool e2e requires it** and skips where it is absent (CI that wants the neutrality guardrail must install hyperfine).
- `typebench run --tool {mypy,ty,pyrefly} --src-root <dir> --output r.json` each produce a schema-valid `RunResult` with real `diagnostics`, `files` (where the tool exposes it), and (with hyperfine) `timing`.
- Each adapter **provably** suppresses the project's own config — asserted behaviorally by `test_hostile_project_config_is_suppressed` (a hostile config that would silence real errors is ignored → DIAGNOSTICS still reported), not just structurally. Each uses stock-neutral severity (`mypy` default, `ty` default, `pyrefly preset="default"`) and analyzes all bodies.
- The §6 excludes are **provably enforced** — `test_excludes_drop_nested_tests_dir` checks that a type error buried under `tests/` is dropped by every tool (CLEAN). ty specifically requires `--force-exclude` + a `[src].exclude` table, because gitignore excludes do not apply to command-line paths otherwise.
- Third-party resolution is wired (`mypy --python-executable`/`--follow-imports=silent`, `ty --python`, `pyrefly python-interpreter-path`; all report first-party only) and **structurally** unit-tested, but on stdlib-only fixtures it is only stdlib-exercised here — full first-party-only-with-real-deps verification is **deferred to Plan 3**.
- Overloaded exits are disambiguated: mypy 2 (`INTERNAL ERROR`→crash else env), pyrefly 1 (parseable errors→diagnostics else env). ty 101→crash, 2→env.
- Thread caps honest: pyrefly `--threads 1` hard, ty `TY_MAX_PARALLELISM=1` soft, mypy single-process. `thread_mode_enforced` stays `False` for all (full 1-core honesty needs affinity, Plan 4) even though pyrefly's cap is already hard.
- Parse-sanity per tool: exit-0 with a CONFIRMED 0 file/module count → `failed{env}` for all; `files is None` is promoted to env only for the reliable-count tools (mypy, pyright), tolerated for the stderr-scraped tools (ty, pyrefly). The pyrefly module regex tolerates singular "1 module"; live pyrefly tests assert `files > 0`.
- `universal_failure_prefix` is the single source of the §7 prefix; `classify_with_map` delegates to it; all Plan 1/2A tests stay green.
- The cross-tool e2e holds every real tool to the identical contract (error fixture → DIAGNOSTICS+counts+timing; clean fixture → CLEAN) — the neutrality guardrail (hyperfine-gated).
- **PLAN 2 TRAP residual (documented, accepted for 2B):** the timing wrapper's generic `{0,1}` gate cannot disambiguate pyrefly's overloaded exit 1, so a flaky fatal pyrefly exit-1 that occurs only during a timed run would be silently timed. The probe gates the deterministic case; the flaky-during-timing case is an accepted blind spot (the wrapper must stay pydantic-free, so adapter-aware timing classification is deferred).
- No corpus/envman/cgroup/renderer (Plans 3–6). Multi-core track wiring exists (omit cap) but affinity/calibration is Plan 4.

## Self-Review notes
- **Neutrality:** all four adapters use the same NormalizedConfig, the same failure taxonomy, the same parse-sanity discipline, and stock-neutral severity. pyrefly gets `preset="default"` (not the favorable `basic`), reviewed to the same bar, held to the identical cross-tool e2e contract. Neutrality is now enforced *behaviorally*, not just structurally: Task 6 proves excludes drop files and project config is suppressed for every tool (live). No winner framing anywhere.
- **Open question (verify before exec):** is `infer-return-types = "checked"` (pyrefly config) the *stock default*? If it is a non-default choice it is a pyrefly-only knob with no cross-tool equivalent — a small neutrality asymmetry. Confirm via `pyrefly dump-config`; drop the line if it diverges from the default.
- **§6 coverage (per tool):** target file set (src_roots) · excludes · python version+platform · resolve-deps-report-first-party (mypy `--follow-imports=silent`+`--python-executable`; ty `--python`; pyrefly `python-interpreter-path`; all report first-party only) · analyze all bodies (mypy `--check-untyped-defs`, ty/pyrefly default-on) · stock severities · suppress project config · no plugins. Third-party *resolution* is wired + unit-tested but only stdlib-exercised here (fully exercised in Plan 3).
- **Wrapper/timing interaction (honest):** on the success path all four measured-success code sets ⊆ {0,1}, so the generic wrapper gate agrees with each probe `classify`. The PLAN 2 TRAP is **partially** defused — pyrefly's overloaded exit 1 leaves a residual blind spot: a flaky fatal exit-1 *during a timed run* is read as DIAGNOSTICS and silently timed. The probe gates the deterministic case; the flaky-during-timing case is accepted residual risk for 2B (the wrapper must stay pydantic-free, so adapter-aware timing classification is deferred). Threading {0,1} success codes would not close it. Task 5's comment + the DoD record this honestly.
- **Known fragilities (flagged, not hidden):** ty has no JSON → `concise` text + `-v` stderr files (may be None, tolerated); pyrefly files come from a `--summary=full` stderr line (singular "1 module" handled). Both are version-fragile (ty 0.0.x churns; pyrefly resolved at `>=0.16`, locally 1.0.0 — re-verify JSON/summary against the *installed* version, not `main`). mypy `--num-workers` deliberately unused (experimental, perturbs diagnostics).
- **Carry-over to Plan 3:** (1) verify each tool's file/module count excludes dependency files (neutral throughput denominator) once the corpus has real third-party deps — today's fixtures are stdlib-only. (2) verify **exclude-glob dialect parity**: mypy gets a regex (handled), but ty/pyright/pyrefly all receive the same glob strings despite potentially different dialects (ty is gitignore-style); confirm the excluded set is identical across tools on a corpus that actually contains excluded dirs, else the throughput denominator diverges.
- **Placeholders:** none — every step has full, gate-clean code/commands. The earlier ty `tomllib`/`ty_config` smell is fixed in-place (the adapter no longer imports `tomllib` or carries a discarded dict); the Task 1 test note flags the import-merge so the block does not double-import.
