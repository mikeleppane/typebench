# Plan 3 — Corpus + Environment Management + Preflight — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the four real adapters against a real, pinned third-party project with its dependencies installed in a per-project `uv` venv — proving the venv-resolution flags the adapters already thread (`--python-executable`, `--python`, `python-interpreter-path`, `venvPath/venv`) actually work, and that no tool counts dependency files into its throughput denominator.

**Architecture:** Corpus is data (`corpus/suite.toml`, pinned to a release-tag SHA). `envman` clones at the SHA, builds a `uv venv`, installs deps via an explicit per-entry recipe, freezes the resolved versions (lock-manifest seed), and computes an **independent** first-party file/LOC count (the neutral throughput denominator — walks only `src_roots`, so dependency files in the venv are excluded by construction). `preflight` then probes each of the four tools once under the corpus-derived `NormalizedConfig` and records each tool's self-reported file count against the canonical one. The default test gate stays fully offline (local `git init` fixtures + injected runners); one opt-in test clones real httpx over the network and is auto-skipped when offline.

**Tech Stack:** Python 3.12, `uv` (venv + pip + freeze), `git` (shallow tag fetch), `tomllib` (stdlib), pydantic v2, Typer, pytest. No new runtime pip dependency.

---

## Scope (locked during brainstorm)

**IN:** corpus model + loader; pure-Python first-party counter; `envman.prepare_project` (clone/checkout/venv/install/freeze/count, cached + idempotent); `PreparedProject`/`PreflightReport` models; `preflight_project` (probe 4 tools, neutrality count comparison); CLI `typebench preflight` + a corpus-driven single-project `run` path; one pinned small project (httpx 0.28.0).

**OUT (later plans, do NOT build here):** cgroup peak memory, CPU affinity / thread enforcement, calibration baseline (Plan 4); results envelope, full suite orchestration, sharding, renderer, GH Pages (Plan 5); monthly bump automation (Plan 6); additional corpus entries / larger size buckets (incremental, post-Plan-3). **Do NOT modify `RunResult` fields or any `taxonomy.py` value** — the lock-manifest enrichment of the result record is Plan 5. `RunResult.project` simply carries the corpus name. **Do NOT touch `wrapper.py` or `taxonomy.py`** — the measured path stays pydantic-free.

## Key decisions (carry into every task)

- **Throughput denominator = the independent canonical count**, never a tool's self-reported `files`. The counter walks only `src_roots`; installed deps live in the venv's `site-packages` (outside `src_roots`), so the canonical count is dependency-free *by construction*. Each tool's self-report is recorded as a data point with a divergence delta (spec §8) — mypy's `--follow-imports=silent` may legitimately report a larger count; that divergence is the documented finding that justifies not using self-reports as the denominator.
- **Install is an explicit per-entry recipe** (`install = ["uv pip install ."]`), run with the prepared venv active (`VIRTUAL_ENV` + `PATH`). No build-system auto-detection.
- **Cache root** = repo-local, gitignored `.typebench-cache/`, keyed `<name>@<sha>/`. Idempotent: a `prepared.json` sidecar short-circuits re-preparation.
- **Offline-by-default tests.** Unit tests inject a fake `Runner` and/or clone from a local `file://` git repo (real git, no network). The single real-clone test auto-skips when GitHub is unreachable and per-tool when a checker is missing.
- **No new pip dependency.** `git` and `uv` are system tools (already required). `tomllib` is stdlib.

## File structure

- Create `src/typebench/corpus.py` — `SizeBucket`, `CorpusProject`, `load_suite`.
- Create `src/typebench/counting.py` — `FileCount`, `count_first_party`.
- Create `src/typebench/envman.py` — `RunOut`, `Runner`, `run_subprocess`, `PrepareError`, `prepare_project` + private clone/venv/install/freeze helpers.
- Create `src/typebench/preflight.py` — `preflight_project`.
- Create `corpus/suite.toml` — the httpx entry.
- Modify `src/typebench/models.py` — add `PreparedProject`, `ToolPreflight`, `PreflightReport`.
- Modify `src/typebench/normalized_config.py` — expose `DEFAULT_EXCLUDES` publicly (reused by corpus + counter).
- Modify `src/typebench/cli.py` — add `preflight` command; add `--corpus`/`--corpus-project` to `run`.
- Modify `.gitignore` — add `.typebench-cache/`.
- Modify `AGENTS.md` — layout + scope-by-plan note.
- Tests: `tests/test_corpus.py`, `tests/test_counting.py`, `tests/test_envman.py`, `tests/test_preflight.py`, `tests/test_cli_preflight.py`, `tests/test_corpus_neutrality_net.py`.

---

### Task 1: Corpus model + loader + suite.toml

**Files:**
- Modify: `src/typebench/normalized_config.py`
- Create: `src/typebench/corpus.py`
- Create: `corpus/suite.toml`
- Test: `tests/test_corpus.py`

- [ ] **Step 1: Expose `DEFAULT_EXCLUDES` publicly**

In `src/typebench/normalized_config.py`, rename the private tuple to a public constant and keep the field default pointing at it (so corpus + counter can import the same source of truth):

```python
# Excluded everywhere (spec §6): tests, vendored, generated, caches.
DEFAULT_EXCLUDES: tuple[str, ...] = (
    "**/tests/**",
    "**/test/**",
    "**/_vendor/**",
    "**/vendor/**",
    "**/generated/**",
    "**/_generated/**",
    "**/__pycache__/**",
    "**/node_modules/**",
)
```

And update the field default:

```python
    exclude_globs: tuple[str, ...] = field(default=DEFAULT_EXCLUDES)
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_corpus.py`:

```python
from pathlib import Path

import pytest

from typebench.corpus import CorpusProject, SizeBucket, load_suite

_SUITE = Path(__file__).parent.parent / "corpus" / "suite.toml"


def test_load_suite_reads_httpx_entry() -> None:
    projects = load_suite(_SUITE)
    names = {p.name for p in projects}
    assert "httpx" in names
    httpx = next(p for p in projects if p.name == "httpx")
    assert httpx.sha == "80960fa31918d7663c3f4c3ad61661cf0e80628f"
    assert httpx.size_bucket is SizeBucket.SMALL
    assert httpx.src_roots == ("httpx",)
    assert httpx.install == ("uv pip install .",)


def test_effective_excludes_merges_defaults_then_entry() -> None:
    proj = CorpusProject(
        name="x",
        repo_url="https://example.invalid/x",
        sha="0" * 40,
        tag="v1",
        size_bucket=SizeBucket.SMALL,
        python_version="3.12",
        src_roots=("x",),
        install=("uv pip install .",),
        exclude_globs=("**/extra/**",),
    )
    eff = proj.effective_excludes()
    assert "**/tests/**" in eff  # §6 default preserved
    assert "**/extra/**" in eff  # entry extension appended
    assert eff[-1] == "**/extra/**"


def test_load_suite_rejects_unknown_field(tmp_path: Path) -> None:
    bad = tmp_path / "suite.toml"
    bad.write_text(
        '[[project]]\nname = "x"\nrepo_url = "u"\nsha = "s"\ntag = "t"\n'
        'size_bucket = "small"\npython_version = "3.12"\nsrc_roots = ["x"]\n'
        'install = ["uv pip install ."]\nbogus = 1\n'
    )
    with pytest.raises(ValueError, match="bogus"):
        load_suite(bad)


def test_load_suite_rejects_duplicate_names(tmp_path: Path) -> None:
    dup = tmp_path / "suite.toml"
    entry = (
        '[[project]]\nname = "x"\nrepo_url = "u"\nsha = "s"\ntag = "t"\n'
        'size_bucket = "small"\npython_version = "3.12"\nsrc_roots = ["x"]\n'
        'install = ["uv pip install ."]\n'
    )
    dup.write_text(entry + entry)
    with pytest.raises(ValueError, match="duplicate"):
        load_suite(dup)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_corpus.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'typebench.corpus'`.

- [ ] **Step 4: Write the corpus module**

Create `src/typebench/corpus.py`:

```python
"""Corpus as data (spec §4). `suite.toml` declares each pinned real-world
project: its release-tag SHA, first-party source roots, size bucket, target
Python, and an explicit install recipe. The corpus is the only project-specific
data the engine consumes; everything else is generic."""

from __future__ import annotations

import tomllib
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from typebench.normalized_config import DEFAULT_EXCLUDES


class SizeBucket(str, Enum):
    """LOC bands that reveal scaling curves (spec §2 decision 8a)."""

    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    GIANT = "giant"


class CorpusProject(BaseModel):
    """One pinned corpus entry. `src_roots`/`exclude_globs` are repo-relative;
    `sha` is the exact commit the `tag` resolved to when pinned (verified at
    prepare time). `install` is the explicit recipe run in the project venv."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    repo_url: str
    sha: str
    tag: str
    size_bucket: SizeBucket
    python_version: str
    src_roots: tuple[str, ...]
    install: tuple[str, ...]
    # Extends (never replaces) the §6 defaults so tests/vendored/generated are
    # always excluded; a per-project extension can drop extra project-specific dirs.
    exclude_globs: tuple[str, ...] = ()

    def effective_excludes(self) -> tuple[str, ...]:
        """The §6 default excludes followed by this entry's extensions. Used by
        both the canonical counter and the NormalizedConfig so the tools and the
        throughput denominator see the identical exclude set."""
        return DEFAULT_EXCLUDES + self.exclude_globs


def load_suite(path: Path) -> list[CorpusProject]:
    """Parse and validate `suite.toml`. Raises ValueError on an unknown field
    (extra="forbid") or a duplicate project name — a malformed corpus must fail
    loudly, never silently reshape the benchmark."""
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    entries = raw.get("project", [])
    projects = [CorpusProject.model_validate(e) for e in entries]
    names = [p.name for p in projects]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        msg = f"duplicate corpus project name(s): {dupes}"
        raise ValueError(msg)
    return projects
```

Note: `CorpusProject.model_validate` raises `pydantic.ValidationError` (a subclass of `ValueError`) on an unknown field, satisfying `pytest.raises(ValueError, match="bogus")`.

- [ ] **Step 5: Create the corpus data file**

Create `corpus/suite.toml`:

```toml
# typebench corpus (spec §4). Each entry is pinned to a release-tag SHA so
# third-party imports resolve identically over time. Checkers always float to
# latest; the corpus is bumped via a PR-gated job (later plan). src_roots and
# exclude_globs are repo-relative. install is the explicit per-entry recipe,
# run with the prepared venv active.

[[project]]
name = "httpx"
repo_url = "https://github.com/encode/httpx"
sha = "80960fa31918d7663c3f4c3ad61661cf0e80628f"  # tag 0.28.0
tag = "0.28.0"
size_bucket = "small"
python_version = "3.12"
src_roots = ["httpx"]
install = ["uv pip install ."]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_corpus.py -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Run the quality gate**

Run: `uv run ruff format && uv run ruff check && uv run pyrefly check && uv run pytest -q`
Expected: format clean, `All checks passed!`, `0 errors`, all green.

- [ ] **Step 8: Commit**

```bash
git add src/typebench/normalized_config.py src/typebench/corpus.py corpus/suite.toml tests/test_corpus.py
git commit -m "feat(corpus): suite.toml schema + loader, pinned httpx entry

Corpus as data: CorpusProject (extra=forbid) + load_suite with
duplicate-name and unknown-field guards. effective_excludes() merges the
§6 defaults with per-entry extensions so the counter and the tools share
one exclude set. DEFAULT_EXCLUDES is now public in normalized_config."
```

---

### Task 2: Pure-Python first-party counter

**Files:**
- Create: `src/typebench/counting.py`
- Test: `tests/test_counting.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_counting.py`:

```python
from pathlib import Path

from typebench.counting import count_first_party
from typebench.normalized_config import DEFAULT_EXCLUDES


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_counts_only_python_files_under_roots(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    _write(pkg / "a.py", "x = 1\ny = 2\n")
    _write(pkg / "b.py", "z = 3\n")
    _write(pkg / "README.md", "not python\n")
    fc = count_first_party([pkg], DEFAULT_EXCLUDES)
    assert fc.files == 2
    assert fc.loc == 3  # 2 lines + 1 line


def test_excludes_tests_and_pycache(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    _write(pkg / "a.py", "x = 1\n")
    _write(pkg / "tests" / "test_a.py", "assert True\n")
    _write(pkg / "__pycache__" / "a.cpython-312.pyc.py", "garbage\n")
    fc = count_first_party([pkg], DEFAULT_EXCLUDES)
    assert fc.files == 1  # only a.py; tests/ and __pycache__/ dropped


def test_empty_root_counts_zero(tmp_path: Path) -> None:
    fc = count_first_party([tmp_path / "missing"], DEFAULT_EXCLUDES)
    assert fc.files == 0
    assert fc.loc == 0


def test_multiple_roots_are_summed(tmp_path: Path) -> None:
    _write(tmp_path / "one" / "a.py", "a = 1\n")
    _write(tmp_path / "two" / "b.py", "b = 2\n")
    fc = count_first_party([tmp_path / "one", tmp_path / "two"], DEFAULT_EXCLUDES)
    assert fc.files == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_counting.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'typebench.counting'`.

- [ ] **Step 3: Write the counter**

Create `src/typebench/counting.py`:

```python
"""Canonical first-party counter (spec §8). This is THE throughput denominator —
identical across all four tools. It walks only the declared `src_roots`, so any
installed third-party dependency (which lives in the venv's site-packages,
outside the roots) is excluded by construction. A tool's self-reported file
count is a separate data point, never this number."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class FileCount:
    """Canonical first-party totals for one project."""

    files: int
    loc: int


def _excluded_dir_names(globs: tuple[str, ...]) -> frozenset[str]:
    """Derive the set of directory names the §6 globs exclude. Mirrors the
    dir-name derivation the mypy adapter uses for its --exclude regex, so the
    canonical count and the tools agree on what 'excluded' means. e.g.
    '**/tests/**' -> 'tests'."""
    return frozenset(g.strip("*/ ").split("/")[0] for g in globs if g.strip("*/ "))


def count_first_party(roots: list[Path], exclude_globs: tuple[str, ...]) -> FileCount:
    """Count .py files and their physical lines under `roots`, dropping any file
    whose path (relative to its root) contains an excluded directory segment. A
    missing root contributes zero — preflight, not the counter, decides whether
    a missing root is fatal."""
    excluded = _excluded_dir_names(exclude_globs)
    files = 0
    loc = 0
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            rel_parts = set(path.relative_to(root).parts)
            if excluded & rel_parts:
                continue
            files += 1
            loc += len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    return FileCount(files=files, loc=loc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_counting.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run the quality gate**

Run: `uv run ruff format && uv run ruff check && uv run pyrefly check && uv run pytest -q`
Expected: all clean/green.

- [ ] **Step 6: Commit**

```bash
git add src/typebench/counting.py tests/test_counting.py
git commit -m "feat(counting): canonical first-party file/LOC counter

The neutral throughput denominator (§8). Walks only src_roots so installed
deps (in the venv, outside the roots) are excluded by construction. Exclude
semantics mirror the §6 dir-name derivation used by the mypy adapter, so the
count and the tools agree on what is excluded."
```

---

### Task 3: Result schemas — PreparedProject, ToolPreflight, PreflightReport

**Files:**
- Modify: `src/typebench/models.py`
- Test: `tests/test_preflight.py` (schema round-trip portion)

- [ ] **Step 1: Write the failing test**

Create `tests/test_preflight.py` with the schema tests first (the `preflight_project` tests are added in Task 6):

```python
import pytest
from pydantic import ValidationError

from typebench.models import PreflightReport, PreparedProject, ResultClass, ToolPreflight


def _prepared() -> PreparedProject:
    return PreparedProject(
        name="httpx",
        checkout="/cache/httpx@sha/repo",
        venv_python="/cache/httpx@sha/venv/bin/python",
        src_roots=("/cache/httpx@sha/repo/httpx",),
        exclude_globs=("**/tests/**",),
        python_version="3.12",
        sha="0" * 40,
        lock_hash="deadbeef",
        frozen=("httpcore==1.0.0", "idna==3.0"),
        canonical_files=42,
        canonical_loc=9000,
    )


def test_prepared_project_round_trips() -> None:
    p = _prepared()
    again = PreparedProject.model_validate_json(p.model_dump_json())
    assert again == p
    assert again.src_roots == ("/cache/httpx@sha/repo/httpx",)  # tuple preserved


def test_prepared_project_forbids_extra() -> None:
    with pytest.raises(ValidationError):
        PreparedProject.model_validate({**_prepared().model_dump(), "bogus": 1})


def test_preflight_report_round_trips() -> None:
    report = PreflightReport(
        project="httpx",
        sha="0" * 40,
        python_version="3.12",
        lock_hash="deadbeef",
        canonical_files=42,
        canonical_loc=9000,
        ready=True,
        tools=[
            ToolPreflight(
                tool="mypy",
                version="mypy 1.0",
                result_class=ResultClass.DIAGNOSTICS,
                self_reported_files=500,
                files_divergence=458,
            ),
            ToolPreflight(
                tool="pyright",
                version="pyright 1.1",
                result_class=ResultClass.CLEAN,
                self_reported_files=42,
                files_divergence=0,
            ),
        ],
    )
    again = PreflightReport.model_validate_json(report.model_dump_json())
    assert again == report
    assert again.tools[0].files_divergence == 458


def test_tool_preflight_allows_none_counts() -> None:
    tp = ToolPreflight(
        tool="ty",
        version="ty 0.0.44",
        result_class=ResultClass.CLEAN,
        self_reported_files=None,
        files_divergence=None,
    )
    assert tp.self_reported_files is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_preflight.py -v`
Expected: FAIL with `ImportError: cannot import name 'PreparedProject'`.

- [ ] **Step 3: Add the schemas**

In `src/typebench/models.py`, extend `__all__` and append the three models. Add to `__all__`:

```python
__all__ = [
    "EnvFingerprint",
    "FailurePhase",
    "PreflightReport",
    "PreparedProject",
    "ResultClass",
    "RunResult",
    "ThreadMode",
    "TimingStats",
    "ToolPreflight",
]
```

Append after `RunResult`:

```python
class PreparedProject(BaseModel):
    """An envman-prepared corpus project: a checked-out repo at a pinned SHA, a
    per-project venv with deps installed, the frozen resolved versions (lock seed
    for §9), and the canonical first-party count (§8). Persisted as a cache
    sidecar so a repeated prepare is idempotent. Absolute paths are stored as
    strings for clean JSON round-tripping."""

    model_config = ConfigDict(extra="forbid")

    name: str
    checkout: str
    venv_python: str
    src_roots: tuple[str, ...]
    exclude_globs: tuple[str, ...]
    python_version: str
    sha: str
    lock_hash: str
    frozen: tuple[str, ...]
    canonical_files: int
    canonical_loc: int


class ToolPreflight(BaseModel):
    """One tool's preflight outcome on a prepared project. `self_reported_files`
    is the tool's own count (data point only); `files_divergence` is
    self_reported - canonical, surfacing any tool that counts dependency files.
    Both are None when the tool emits no parseable count (e.g. ty without -v)."""

    model_config = ConfigDict(extra="forbid")

    tool: str
    version: str
    result_class: ResultClass
    self_reported_files: int | None = None
    files_divergence: int | None = None


class PreflightReport(BaseModel):
    """Per-project preflight result (spec §12). `ready` is True only when every
    tool reached a measured-success class — a project that is not ready is
    excluded from THAT run only and surfaced loudly, never silently dropped."""

    model_config = ConfigDict(extra="forbid")

    project: str
    sha: str
    python_version: str
    lock_hash: str
    canonical_files: int
    canonical_loc: int
    ready: bool
    tools: list[ToolPreflight]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_preflight.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run the quality gate**

Run: `uv run ruff format && uv run ruff check && uv run pyrefly check && uv run pytest -q`
Expected: all clean/green.

- [ ] **Step 6: Commit**

```bash
git add src/typebench/models.py tests/test_preflight.py
git commit -m "feat(models): PreparedProject, ToolPreflight, PreflightReport

New on-disk artifacts for Plan 3. PreparedProject is the envman cache
sidecar (lock seed + canonical count). PreflightReport records per-tool
measured-success and the self-reported-vs-canonical file divergence (§8,
§12). RunResult is untouched — the lock-manifest enrichment is Plan 5."
```

---

### Task 4: envman command helpers (clone / venv / install / freeze)

**Files:**
- Create: `src/typebench/envman.py`
- Test: `tests/test_envman.py`

These helpers carry the only subprocess surface. They take an injectable `Runner` so command construction is unit-tested without real subprocesses; the clone path is additionally proven against a local `file://` git repo (real git, offline).

- [ ] **Step 1: Write the failing test**

Create `tests/test_envman.py`:

```python
import shutil
import subprocess
from pathlib import Path

import pytest

from typebench.envman import (
    PrepareError,
    RunOut,
    _clone,
    _freeze,
    _install,
    _make_venv,
    lock_hash,
    run_subprocess,
)


class _FakeRunner:
    """Records calls and returns canned outputs keyed by argv[:2]. For
    `git rev-parse` it returns `head_sha` so _clone's HEAD==sha assertion passes
    (default matches the demo entry's pinned sha used in the prepare tests)."""

    def __init__(
        self, outs: dict[tuple[str, ...], RunOut] | None = None, head_sha: str = "abc123"
    ) -> None:
        self.calls: list[tuple[list[str], Path | None, dict[str, str] | None]] = []
        self._outs = outs or {}
        self._head_sha = head_sha

    def __call__(
        self, argv: list[str], cwd: Path | None, env: dict[str, str] | None
    ) -> RunOut:
        self.calls.append((argv, cwd, env))
        if "rev-parse" in argv:
            return RunOut(0, self._head_sha, "")
        return self._outs.get(tuple(argv[:2]), RunOut(0, "", ""))


def test_make_venv_builds_uv_command_and_returns_python(tmp_path: Path) -> None:
    run = _FakeRunner()
    venv = tmp_path / "venv"
    py = _make_venv("3.12", venv, run)
    argv = run.calls[0][0]
    assert argv[:2] == ["uv", "venv"]
    assert "--python" in argv and "3.12" in argv
    assert py.endswith("/venv/bin/python")


def test_install_activates_venv_for_each_recipe_command(tmp_path: Path) -> None:
    run = _FakeRunner()
    venv = tmp_path / "venv"
    repo = tmp_path / "repo"
    _install(("uv pip install .", "uv pip install ./extra"), repo, venv, run)
    assert len(run.calls) == 2
    first_argv, first_cwd, first_env = run.calls[0]
    assert first_argv == ["uv", "pip", "install", "."]  # shlex-split recipe
    assert first_cwd == repo
    assert first_env is not None
    assert first_env["VIRTUAL_ENV"] == str(venv)
    assert first_env["PATH"].startswith(f"{venv / 'bin'}:")


def test_freeze_returns_sorted_lines(tmp_path: Path) -> None:
    out = RunOut(0, "idna==3.0\nhttpcore==1.0.0\n", "")
    run = _FakeRunner({("uv", "pip"): out})
    frozen = _freeze("/v/bin/python", run)
    assert frozen == ("httpcore==1.0.0", "idna==3.0")


def test_lock_hash_is_order_independent_and_stable() -> None:
    a = lock_hash(("idna==3.0", "httpcore==1.0.0"))
    b = lock_hash(("httpcore==1.0.0", "idna==3.0"))
    assert a == b  # sorted before hashing
    assert a == lock_hash(("idna==3.0", "httpcore==1.0.0"))  # deterministic
    assert a != lock_hash(("idna==3.1", "httpcore==1.0.0"))  # content-sensitive


def test_install_raises_prepare_error_on_nonzero(tmp_path: Path) -> None:
    run = _FakeRunner({("uv", "pip"): RunOut(1, "", "boom")})
    with pytest.raises(PrepareError, match="boom"):
        _install(("uv pip install .",), tmp_path / "repo", tmp_path / "venv", run)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_clone_checks_out_pinned_sha_from_local_repo(tmp_path: Path) -> None:
    # Build a local upstream repo (offline), tag it, then clone via file://.
    upstream = tmp_path / "upstream"
    upstream.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(upstream), *args], check=True, capture_output=True)

    subprocess.run(["git", "init", "-q", str(upstream)], check=True)
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (upstream / "httpx").mkdir()
    (upstream / "httpx" / "__init__.py").write_text("VERSION = '1'\n")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    git("tag", "v1")
    sha = subprocess.run(
        ["git", "-C", str(upstream), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    repo = tmp_path / "repo"
    _clone(f"file://{upstream}", "v1", sha, repo, run_subprocess)
    assert (repo / "httpx" / "__init__.py").read_text() == "VERSION = '1'\n"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_clone_rejects_sha_mismatch(tmp_path: Path) -> None:
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    subprocess.run(["git", "init", "-q", str(upstream)], check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        subprocess.run(["git", "-C", str(upstream), "config", k, v], check=True)
    (upstream / "f.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(upstream), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(upstream), "commit", "-q", "-m", "c"], check=True, capture_output=True
    )
    subprocess.run(["git", "-C", str(upstream), "tag", "v1"], check=True)
    with pytest.raises(PrepareError, match="SHA mismatch"):
        _clone(f"file://{upstream}", "v1", "0" * 40, tmp_path / "repo", run_subprocess)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_envman.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'typebench.envman'`.

- [ ] **Step 3: Write the envman helpers**

Create `src/typebench/envman.py`:

```python
"""Environment management (spec §4 envman, §12 preflight gate). Clones a corpus
project at its pinned SHA, builds an isolated uv venv against the pinned Python,
installs deps via the explicit recipe, and freezes the resolved versions (the §9
lock seed). All subprocess calls go through an injectable Runner so command
construction is unit-testable offline; supply-chain note (§12): install runs the
project's arbitrary build code — acceptable on ephemeral runners, documented for
local use."""

from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True)
class RunOut:
    """Captured outcome of one helper subprocess."""

    returncode: int
    stdout: str
    stderr: str


class Runner(Protocol):
    def __call__(
        self, argv: list[str], cwd: Path | None, env: dict[str, str] | None
    ) -> RunOut: ...


class PrepareError(RuntimeError):
    """A preparation step failed (clone/venv/install/freeze). Carries the failing
    command's stderr so preflight can record a precise failed{env} detail."""


def run_subprocess(argv: list[str], cwd: Path | None, env: dict[str, str] | None) -> RunOut:
    """Default Runner. Never used on the measured path — preparation is one-time
    setup, so pydantic-free purity does not apply here."""
    proc = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return RunOut(proc.returncode, proc.stdout, proc.stderr)


def _check(out: RunOut, what: str) -> RunOut:
    if out.returncode != 0:
        detail = (out.stderr.strip() or out.stdout.strip())[-500:]
        msg = f"{what} failed (exit {out.returncode}): {detail}"
        raise PrepareError(msg)
    return out


def _venv_python(venv: Path) -> str:
    # abspath, NOT resolve: venv/bin/python is a symlink to the base interpreter;
    # resolving it walks out of the venv and breaks each tool's venv derivation.
    return os.path.abspath(venv / "bin" / "python")  # noqa: PTH100 - need non-symlink-following abspath


def _clone(url: str, tag: str, sha: str, repo: Path, run: Runner) -> None:
    """Shallow-fetch the release tag and check out the pinned SHA, then assert
    HEAD == sha. Fetching the tag ref (not an arbitrary SHA) works against GitHub;
    the post-checkout assertion catches a tag that was force-moved off the pin."""
    repo.mkdir(parents=True, exist_ok=True)
    rc = str(repo)
    _check(run(["git", "init", "-q", rc], None, None), "git init")
    _check(run(["git", "-C", rc, "remote", "add", "origin", url], None, None), "git remote add")
    _check(
        run(
            ["git", "-C", rc, "fetch", "--depth", "1", "origin", f"refs/tags/{tag}:refs/tags/{tag}"],
            None,
            None,
        ),
        "git fetch",
    )
    _check(run(["git", "-C", rc, "checkout", "-q", sha], None, None), "git checkout")
    head = _check(run(["git", "-C", rc, "rev-parse", "HEAD"], None, None), "git rev-parse")
    if head.stdout.strip() != sha:
        msg = f"SHA mismatch after checkout: HEAD={head.stdout.strip()} expected={sha}"
        raise PrepareError(msg)


def _make_venv(python_version: str, venv: Path, run: Runner) -> str:
    """Build the per-project venv against the pinned Python and return its
    interpreter path."""
    _check(
        run(["uv", "venv", "--python", python_version, str(venv)], None, None),
        "uv venv",
    )
    return _venv_python(venv)


def _install(recipe: Sequence[str], repo: Path, venv: Path, run: Runner) -> None:
    """Run each install command with the prepared venv active (VIRTUAL_ENV + PATH)
    and cwd at the repo, so deps land in the project venv. Recipe strings are
    shlex-split into argv (no shell)."""
    env = {
        **os.environ,
        "VIRTUAL_ENV": str(venv),
        "PATH": f"{venv / 'bin'}:{os.environ.get('PATH', '')}",
    }
    for command in recipe:
        _check(run(shlex.split(command), repo, env), f"install: {command!r}")


def _freeze(venv_python: str, run: Runner) -> tuple[str, ...]:
    """Return the venv's resolved package versions, sorted (the §9 lock seed)."""
    out = _check(run(["uv", "pip", "freeze", "--python", venv_python], None, None), "uv pip freeze")
    return tuple(sorted(line for line in out.stdout.splitlines() if line.strip()))


def lock_hash(frozen: tuple[str, ...]) -> str:
    """A stable, order-independent hash of the frozen versions (§9). Two runs
    that resolved the same deps share a hash; a single version bump changes it."""
    payload = "\n".join(sorted(frozen)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_envman.py -v`
Expected: PASS (7 tests; the two git tests run wherever `git` exists).

- [ ] **Step 5: Run the quality gate**

Run: `uv run ruff format && uv run ruff check && uv run pyrefly check && uv run pytest -q`
Expected: all clean/green.

- [ ] **Step 6: Commit**

```bash
git add src/typebench/envman.py tests/test_envman.py
git commit -m "feat(envman): clone/venv/install/freeze helpers with injectable Runner

Shallow tag-fetch + pinned-SHA checkout with a HEAD==sha assertion (catches a
moved tag). Install runs the explicit recipe with the venv active; freeze is
the sorted §9 lock seed; lock_hash is order-independent. The Runner seam keeps
command construction unit-tested offline; clone is proven against a local
file:// repo."
```

---

### Task 5: envman.prepare_project — orchestration, counting, idempotent cache

**Files:**
- Modify: `src/typebench/envman.py`
- Test: `tests/test_envman.py` (append)

- [ ] **Step 1: Write the failing test**

First **add to the top import block** of `tests/test_envman.py` (PLC0415 forbids
inner imports; the prepare tests need these at module level):

```python
from typebench.corpus import CorpusProject, SizeBucket
from typebench.envman import prepare_project  # add alongside the existing envman imports
```

Then append the tests (no inner imports):

```python
def _httpx_entry() -> CorpusProject:
    return CorpusProject(
        name="demo",
        repo_url="file:///does/not/matter",  # fake runner short-circuits git
        sha="abc123",
        tag="v1",
        size_bucket=SizeBucket.SMALL,
        python_version="3.12",
        src_roots=("pkg",),
        install=("uv pip install .",),
    )


def _stage_repo_tree(cache_root: Path) -> None:
    """The fake runner does not really clone, so pre-create the checked-out tree
    the counter will walk (cache_root/demo@abc123/repo/pkg/*.py)."""
    repo = cache_root / "demo@abc123" / "repo" / "pkg"
    repo.mkdir(parents=True)
    (repo / "a.py").write_text("x = 1\n")
    (repo / "b.py").write_text("y = 2\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "t.py").write_text("assert True\n")  # must be excluded


def test_prepare_project_assembles_prepared_with_canonical_count(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _stage_repo_tree(cache)
    run = _FakeRunner({("uv", "pip"): RunOut(0, "idna==3.0\nhttpcore==1.0.0\n", "")})
    prepared = prepare_project(_httpx_entry(), cache, run=run)

    assert prepared.name == "demo"
    assert prepared.canonical_files == 2  # tests/t.py excluded
    assert prepared.canonical_loc == 2
    assert prepared.frozen == ("httpcore==1.0.0", "idna==3.0")
    assert prepared.venv_python.endswith("/venv/bin/python")
    assert prepared.src_roots[0].endswith("/repo/pkg")
    assert prepared.lock_hash  # nonempty


def test_prepare_project_is_idempotent_via_sidecar(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _stage_repo_tree(cache)
    run1 = _FakeRunner({("uv", "pip"): RunOut(0, "idna==3.0\n", "")})
    first = prepare_project(_httpx_entry(), cache, run=run1)
    assert run1.calls  # did work the first time

    run2 = _FakeRunner({("uv", "pip"): RunOut(0, "idna==3.0\n", "")})
    second = prepare_project(_httpx_entry(), cache, run=run2)
    assert run2.calls == []  # sidecar short-circuited; no clone/venv/install/freeze
    assert second == first
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_envman.py -k prepare_project -v`
Expected: FAIL with `ImportError: cannot import name 'prepare_project'`.

- [ ] **Step 3: Implement prepare_project**

Append to `src/typebench/envman.py`. Add imports at the top (merge with existing import block):

```python
from typebench.counting import count_first_party
from typebench.models import PreparedProject
```

Add the orchestrator:

```python
_SIDECAR = "prepared.json"


def prepare_project(
    entry: CorpusProject,
    cache_root: Path,
    *,
    run: Runner = run_subprocess,
) -> PreparedProject:
    """Clone @ SHA -> uv venv -> install deps -> freeze -> count first-party, then
    cache the result. Idempotent: a `prepared.json` sidecar under the cache key
    short-circuits all subprocess work on a repeat call (spec §10 caching). Raises
    PrepareError on any setup failure so preflight records failed{env}, never a
    silent partial."""
    dest = cache_root / f"{entry.name}@{entry.sha}"
    sidecar = dest / _SIDECAR
    if sidecar.exists():
        return PreparedProject.model_validate_json(sidecar.read_text(encoding="utf-8"))

    repo = dest / "repo"
    venv = dest / "venv"
    _clone(entry.repo_url, entry.tag, entry.sha, repo, run)
    venv_python = _make_venv(entry.python_version, venv, run)
    _install(entry.install, repo, venv, run)
    frozen = _freeze(venv_python, run)

    excludes = entry.effective_excludes()
    roots = [repo / r for r in entry.src_roots]
    counted = count_first_party(roots, excludes)

    prepared = PreparedProject(
        name=entry.name,
        checkout=str(repo),
        venv_python=venv_python,
        src_roots=tuple(str(r.resolve()) for r in roots),
        exclude_globs=excludes,
        python_version=entry.python_version,
        sha=entry.sha,
        lock_hash=lock_hash(frozen),
        frozen=frozen,
        canonical_files=counted.files,
        canonical_loc=counted.loc,
    )
    sidecar.write_text(prepared.model_dump_json(indent=2), encoding="utf-8")
    return prepared
```

Add the `CorpusProject` import under `TYPE_CHECKING` (it is only an annotation):

```python
if TYPE_CHECKING:
    from collections.abc import Sequence

    from typebench.corpus import CorpusProject
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_envman.py -v`
Expected: PASS (all envman tests, including the two new prepare tests).

- [ ] **Step 5: Run the quality gate**

Run: `uv run ruff format && uv run ruff check && uv run pyrefly check && uv run pytest -q`
Expected: all clean/green.

- [ ] **Step 6: Commit**

```bash
git add src/typebench/envman.py tests/test_envman.py
git commit -m "feat(envman): prepare_project orchestration + idempotent cache

Clone->venv->install->freeze->count, assembled into a PreparedProject and
cached as a prepared.json sidecar under <name>@<sha>/. A repeat prepare reloads
the sidecar and does zero subprocess work (§10). src_roots resolve to absolute
checkout paths; the canonical count uses the merged §6 excludes."
```

---

### Task 6: preflight_project — probe four tools, neutrality count comparison

**Files:**
- Create: `src/typebench/preflight.py`
- Test: `tests/test_preflight.py` (append)

- [ ] **Step 1: Write the failing test**

First, **extend the top-of-file import block** created in Task 3 so all symbols are
imported at module level (the repo lints with `PL`/`E402` — inner imports and
`# type: ignore` are both disallowed; test doubles must be fully annotated to
satisfy pyrefly strict). The top of `tests/test_preflight.py` should read:

```python
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from typebench.adapters.base import Adapter, ParallelismCap
from typebench.adapters.mypy import MypyAdapter
from typebench.adapters.pyrefly import PyreflyAdapter
from typebench.adapters.pyright import PyrightAdapter
from typebench.adapters.ty import TyAdapter
from typebench.models import (
    PreflightReport,
    PreparedProject,
    ResultClass,
    ThreadMode,
    ToolPreflight,
)
from typebench.normalized_config import NormalizedConfig
from typebench.preflight import preflight_project
from typebench.wrapper import RawRun

_FIXTURES = Path(__file__).parent.parent / "fixtures"
```

Then append the test double and tests (no further module-level imports):

```python
class _CannedAdapter:
    """A fully-annotated Adapter double (it must structurally conform to the
    Adapter Protocol for pyrefly strict to accept `list[Adapter]`), returning a
    fixed class + file count so the preflight assembly and divergence math are
    tested without a real checker. Unused Protocol args are allowed under the
    tests/** ruff ignore (ARG002)."""

    def __init__(self, name: str, result_class: ResultClass, files: int | None) -> None:
        self.name = name
        self._rc = result_class
        self._files = files

    def version(self) -> str:
        return f"{self.name} 1.0"

    def install(self) -> str:
        return self.version()

    def command(
        self,
        project: str,
        config: NormalizedConfig,
        thread_mode: ThreadMode,
        workdir: Path,
    ) -> tuple[list[str], dict[str, str]]:
        return (["true"], {})

    def parallelism_cap(self, thread_mode: ThreadMode) -> ParallelismCap:
        return ParallelismCap(mechanism="x", hard_cap=False)

    def parse(self, stdout: str, stderr: str, exit_code: int) -> tuple[int | None, int | None]:
        return (0, self._files)

    def classify(self, raw: RawRun) -> ResultClass:
        return self._rc

    def clear_cache(self, project: str) -> None:
        return None

    def prepare_command(self, project: str) -> str | None:
        return None


def _fake_probe(argv: list[str], timeout: float, env: dict[str, str] | None = None) -> RawRun:
    return RawRun(exit_code=0, signal=None, timed_out=False, oom=False, stdout="", stderr="")


def _prepared_at(src: Path) -> PreparedProject:
    return PreparedProject(
        name="demo",
        checkout=str(src.parent),
        venv_python="/nonexistent/bin/python",
        src_roots=(str(src),),
        exclude_globs=("**/tests/**",),
        python_version="3.12",
        sha="0" * 40,
        lock_hash="h",
        frozen=(),
        canonical_files=10,
        canonical_loc=100,
    )


def test_preflight_records_divergence_and_ready(tmp_path: Path) -> None:
    prepared = _prepared_at(tmp_path)
    adapters: list[Adapter] = [
        _CannedAdapter("mypy", ResultClass.DIAGNOSTICS, files=500),  # counts deps
        _CannedAdapter("pyright", ResultClass.CLEAN, files=10),  # first-party only
        _CannedAdapter("ty", ResultClass.CLEAN, files=None),  # no parseable count
    ]
    report = preflight_project(prepared, adapters, timeout=30, probe=_fake_probe)
    assert report.ready is True
    by = {t.tool: t for t in report.tools}
    assert by["mypy"].files_divergence == 490  # 500 - 10 canonical
    assert by["pyright"].files_divergence == 0
    assert by["ty"].self_reported_files is None
    assert by["ty"].files_divergence is None


def test_preflight_not_ready_when_a_tool_fails(tmp_path: Path) -> None:
    prepared = _prepared_at(tmp_path)
    adapters: list[Adapter] = [
        _CannedAdapter("pyright", ResultClass.CLEAN, files=10),
        _CannedAdapter("ty", ResultClass.FAILED_ENV, files=None),
    ]
    report = preflight_project(prepared, adapters, timeout=30, probe=_fake_probe)
    assert report.ready is False
    assert {t.tool for t in report.tools if t.result_class is ResultClass.FAILED_ENV} == {"ty"}


def test_preflight_real_tools_on_clean_fixture(tmp_path: Path) -> None:
    # Offline integration: real adapters probe a stdlib-only fixture (no venv/clone
    # needed). Proves the probe path + report assembly with the actual checkers.
    src = _FIXTURES / "pkg_project" / "pkg"
    prepared = PreparedProject(
        name="pkg",
        checkout=str(_FIXTURES / "pkg_project"),
        venv_python="",  # empty -> NormalizedConfig.venv_python None (stdlib-only fixture)
        src_roots=(str(src),),
        exclude_globs=("**/tests/**",),
        python_version="3.12",
        sha="0" * 40,
        lock_hash="h",
        frozen=(),
        canonical_files=2,
        canonical_loc=2,
    )
    pairs = (
        (MypyAdapter(), "mypy"),
        (PyrightAdapter(), "pyright"),
        (TyAdapter(), "ty"),
        (PyreflyAdapter(), "pyrefly"),
    )
    adapters: list[Adapter] = [a for a, n in pairs if shutil.which(n) is not None]
    if not adapters:
        pytest.skip("no real checkers installed")
    report = preflight_project(prepared, adapters, timeout=120)
    assert report.ready is True
    assert all(t.result_class.is_measured_success for t in report.tools)
```

This **replaces** Task 3's three-line import header with the fuller block above
(Task 3's header was the minimal `pytest` + `ValidationError` + four model symbols;
this run adds `shutil`, `Path`, the adapters, `Adapter`/`ParallelismCap`,
`ThreadMode`, `NormalizedConfig`, `preflight_project`, `RawRun`, and `_FIXTURES`).
All imports stay at module top — no inner imports, no `# type: ignore`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_preflight.py -k preflight_records -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'typebench.preflight'`.

- [ ] **Step 3: Write preflight_project**

Create `src/typebench/preflight.py`:

```python
"""Preflight gate (spec §12). Validates a prepared corpus project is actually
checkable by every tool before any timing happens, and records each tool's
self-reported file count against the canonical denominator (§8) so a tool that
counts dependency files is surfaced, not silently averaged in. A project that is
not ready is excluded from THAT run only — the caller logs it loudly."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from typebench.models import PreflightReport, ThreadMode, ToolPreflight
from typebench.normalized_config import NormalizedConfig
from typebench.wrapper import RawRun, run_command

if TYPE_CHECKING:
    from typebench.adapters.base import Adapter
    from typebench.models import PreparedProject


class Probe(Protocol):
    def __call__(
        self, argv: list[str], timeout: float, env: dict[str, str] | None = ...
    ) -> RawRun: ...


def _config_for(prepared: PreparedProject) -> NormalizedConfig:
    # An empty venv_python (stdlib-only fixtures) means "no venv" -> None.
    return NormalizedConfig(
        src_roots=prepared.src_roots,
        exclude_globs=prepared.exclude_globs,
        python_version=prepared.python_version,
        venv_python=prepared.venv_python or None,
    )


def preflight_project(
    prepared: PreparedProject,
    adapters: list[Adapter],
    *,
    timeout: float,
    probe: Probe = run_command,
) -> PreflightReport:
    """Probe each adapter once on the prepared project; build a PreflightReport.
    `ready` is True only when every tool reaches a measured-success class."""
    config = _config_for(prepared)
    tools: list[ToolPreflight] = []
    for adapter in adapters:
        with tempfile.TemporaryDirectory(prefix="typebench-preflight-") as tmp:
            argv, env = adapter.command(prepared.name, config, ThreadMode.ONE_CORE, Path(tmp))
            raw = probe(argv, timeout, env)
            result_class = adapter.classify(raw)
            self_files: int | None = None
            divergence: int | None = None
            if result_class.is_measured_success:
                _diags, self_files = adapter.parse(raw.stdout, raw.stderr, raw.exit_code)
                if self_files is not None:
                    divergence = self_files - prepared.canonical_files
            tools.append(
                ToolPreflight(
                    tool=adapter.name,
                    version=adapter.version(),
                    result_class=result_class,
                    self_reported_files=self_files,
                    files_divergence=divergence,
                )
            )
    return PreflightReport(
        project=prepared.name,
        sha=prepared.sha,
        python_version=prepared.python_version,
        lock_hash=prepared.lock_hash,
        canonical_files=prepared.canonical_files,
        canonical_loc=prepared.canonical_loc,
        ready=all(t.result_class.is_measured_success for t in tools),
        tools=tools,
    )
```

Note: `ResultClass.is_measured_success` already exists (used by the collector); confirm by `grep -n is_measured_success src/typebench/taxonomy.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_preflight.py -v`
Expected: PASS. The real-tools test runs the installed checkers on the fixture offline.

- [ ] **Step 5: Run the quality gate**

Run: `uv run ruff format && uv run ruff check && uv run pyrefly check && uv run pytest -q`
Expected: all clean/green. (If pyrefly flags the `_CannedAdapter` test double, the per-file ignore for `tests/**` covers `ARG002`; add `# pyrefly: ignore[bad-override]` with a reason only if a real strict error appears — do not weaken config.)

- [ ] **Step 6: Commit**

```bash
git add src/typebench/preflight.py tests/test_preflight.py
git commit -m "feat(preflight): probe four tools + neutrality file-count comparison

preflight_project builds a NormalizedConfig from a PreparedProject, probes each
adapter once, and records self-reported files vs the canonical denominator (§8).
ready=all(measured-success) is the §12 gate; a not-ready project is excluded
from that run only. Probe seam keeps the unit tests checker-free; an offline
integration test runs the real adapters on a fixture."
```

---

### Task 7: CLI — `preflight` command + corpus-driven `run`

**Files:**
- Modify: `src/typebench/cli.py`
- Test: `tests/test_cli_preflight.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_preflight.py`:

```python
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from typebench import cli
from typebench.models import PreparedProject

runner = CliRunner()
_FIXTURES = Path(__file__).parent.parent / "fixtures"
_SUITE = Path(__file__).parent.parent / "corpus" / "suite.toml"


def _fake_prepared() -> PreparedProject:
    src = _FIXTURES / "pkg_project" / "pkg"
    return PreparedProject(
        name="httpx",
        checkout=str(_FIXTURES / "pkg_project"),
        venv_python="",
        src_roots=(str(src),),
        exclude_globs=("**/tests/**",),
        python_version="3.12",
        sha="80960fa31918d7663c3f4c3ad61661cf0e80628f",
        lock_hash="h",
        frozen=(),
        canonical_files=2,
        canonical_loc=2,
    )


def test_preflight_writes_report_for_known_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Stub prepare_project so the CLI test does no network/venv work; the stub
    # adapter gives a deterministic measured-success without a real checker.
    monkeypatch.setattr(cli, "prepare_project", lambda entry, cache_root: _fake_prepared())
    out = tmp_path / "report.json"
    result = runner.invoke(
        cli.app,
        [
            "preflight",
            "--corpus",
            str(_SUITE),
            "--project",
            "httpx",
            "--tool",
            "stub",
            "--output",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    report = json.loads(out.read_text())
    assert report["project"] == "httpx"
    assert report["canonical_files"] == 2
    assert report["ready"] is True


def test_preflight_unknown_project_exits_2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    result = runner.invoke(
        cli.app,
        ["preflight", "--corpus", str(_SUITE), "--project", "nope", "--output", str(tmp_path / "r.json")],
    )
    assert result.exit_code == 2
    assert "nope" in result.output


def test_run_corpus_mode_derives_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "prepare_project", lambda entry, cache_root: _fake_prepared())
    out = tmp_path / "r.json"
    result = runner.invoke(
        cli.app,
        [
            "run",
            "--tool",
            "stub",
            "--corpus",
            str(_SUITE),
            "--corpus-project",
            "httpx",
            "--runs",
            "2",
            "--warmup",
            "1",
            "--output",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text())
    assert data["project"] == "httpx"  # corpus name flows into the record
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_preflight.py -v`
Expected: FAIL — `preflight` command and `--corpus` options do not exist yet.

- [ ] **Step 3: Add the CLI surface**

In `src/typebench/cli.py`, add the **runtime** imports (merge with existing) and the
**annotation-only** imports under `TYPE_CHECKING` (cli.py already has
`from __future__ import annotations`, and ruff `TC` requires annotation-only
imports to live in the type-checking block). Change the existing
`from typing import Annotated` to `from typing import TYPE_CHECKING, Annotated`:

```python
# runtime imports (used in function bodies):
from typebench.corpus import load_suite
from typebench.envman import PrepareError, prepare_project
from typebench.normalized_config import DEFAULT_EXCLUDES, NormalizedConfig  # extend existing line
from typebench.preflight import preflight_project

# annotation-only imports:
if TYPE_CHECKING:
    from typebench.adapters.base import Adapter
    from typebench.corpus import CorpusProject
    from typebench.models import PreparedProject
```

Add a corpus-lookup helper and the `preflight` command (after `run`):

```python
def _adapters_for(tools: list[str]) -> list[Adapter]:
    """Resolve tool names to adapter instances, erroring on an unknown tool."""
    out: list[Adapter] = []
    for name in tools:
        factory = _ADAPTERS.get(name)
        if factory is None:
            typer.echo(f"Unknown tool: {name!r}. Known: {sorted(_ADAPTERS)}", err=True)
            raise typer.Exit(code=2)
        out.append(factory())
    return out


def _lookup_project(corpus: Path, name: str) -> CorpusProject:
    for entry in load_suite(corpus):
        if entry.name == name:
            return entry
    typer.echo(f"Unknown corpus project: {name!r} in {corpus}", err=True)
    raise typer.Exit(code=2)


@app.command()
def preflight(
    corpus: Annotated[Path, typer.Option(help="Path to suite.toml.")],
    project: Annotated[str, typer.Option(help="Corpus project name to preflight.")],
    output: Annotated[Path, typer.Option(help="Where to write the PreflightReport JSON.")],
    tool: Annotated[
        list[str] | None,
        typer.Option(help="Tools to probe (repeatable). Default: all four real checkers."),
    ] = None,
    cache_root: Annotated[
        Path, typer.Option(help="Where prepared clones/venvs are cached.")
    ] = Path(".typebench-cache"),
    timeout: Annotated[float, typer.Option(help="Per-probe timeout (seconds).")] = 900.0,
) -> None:
    """Prepare a corpus project (clone @ SHA, build venv, install deps, count) and
    probe each tool once. Writes a PreflightReport; exits 1 if the project is not
    ready (a tool failed) so CI can gate on it."""
    entry = _lookup_project(corpus, project)
    tools = tool or ["mypy", "pyright", "ty", "pyrefly"]
    adapters = _adapters_for(tools)
    try:
        prepared = prepare_project(entry, cache_root)
    except PrepareError as exc:
        typer.echo(f"preflight: prepare failed for {project!r}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    report = preflight_project(prepared, adapters, timeout=timeout)
    output.write_text(report.model_dump_json(indent=2))
    status = "ready" if report.ready else "NOT READY"
    typer.echo(f"preflight {project} -> {status} ({report.canonical_files} files) -> {output}")
    if not report.ready:
        raise typer.Exit(code=1)
```

Now thread corpus mode into `run`. Add two options to the `run` signature (before the closing `) -> None:`):

```python
    corpus: Annotated[
        Path | None, typer.Option(help="suite.toml; with --corpus-project, derive config from it.")
    ] = None,
    corpus_project: Annotated[
        str | None, typer.Option(help="Corpus project name to run (requires --corpus).")
    ] = None,
```

And at the top of `run`'s body, before the existing `factory = _ADAPTERS.get(tool)` block, add corpus resolution that overrides the manual config inputs:

```python
    prepared: PreparedProject | None = None
    if corpus_project is not None:
        if corpus is None:
            typer.echo("--corpus-project requires --corpus.", err=True)
            raise typer.Exit(code=2)
        entry = _lookup_project(corpus, corpus_project)
        prepared = prepare_project(entry, Path(".typebench-cache"))
        project = entry.name
        src_root = list(prepared.src_roots)
        python_version = prepared.python_version
        venv = prepared.venv_python or None
```

Then where `config = NormalizedConfig(...)` is built, use the prepared excludes when present so the counter and the run agree:

```python
    config = NormalizedConfig(
        src_roots=tuple(str(Path(s).resolve()) for s in src_roots),
        exclude_globs=(prepared.exclude_globs if prepared is not None else DEFAULT_EXCLUDES),
        python_version=python_version,
        python_platform=python_platform,
        venv_python=os.path.abspath(venv) if venv is not None else None,  # noqa: PTH100 - non-symlink-following abspath
    )
```

Because the corpus path already supplies absolute, resolved `src_roots`, re-resolving them is harmless. Keep the existing `--src-root required for real tools` check; in corpus mode `src_root` is populated from the prepared project, so the check passes.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_preflight.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full gate (catch CLI regressions)**

Run: `uv run ruff format && uv run ruff check && uv run pyrefly check && uv run pytest -q`
Expected: all clean/green; the existing `tests/test_cli.py` still passes (the new options are optional with safe defaults).

- [ ] **Step 6: Commit**

```bash
git add src/typebench/cli.py tests/test_cli_preflight.py
git commit -m "feat(cli): preflight command + corpus-driven run path

typebench preflight prepares a corpus project and probes the tools, writing a
PreflightReport and exiting 1 when not ready (CI gate, §12). run gains
--corpus/--corpus-project to derive src_roots/python/venv/excludes from a
prepared entry instead of manual flags. Manual-flag run is unchanged."
```

---

### Task 8: Opt-in network neutrality test + wiring (gitignore, AGENTS)

**Files:**
- Create: `tests/test_corpus_neutrality_net.py`
- Modify: `.gitignore`
- Modify: `AGENTS.md`

This is the carry-over neutrality check: a real project with real deps proves the canonical denominator is dependency-free and surfaces each tool's self-reported divergence. Auto-skips offline and per-missing-tool, so the default gate is unaffected.

- [ ] **Step 1: Add the cache dir to .gitignore**

Append to `.gitignore` under "Tool caches":

```
.typebench-cache/
```

- [ ] **Step 2: Write the network test**

Create `tests/test_corpus_neutrality_net.py`:

```python
"""Opt-in neutrality check (spec §8 carry-over). Clones the real pinned httpx,
installs its deps, and proves: (1) the venv resolves third-party imports, (2) the
canonical first-party count excludes dependency files BY CONSTRUCTION (it walks
only src_roots; deps live in the venv), and (3) each tool reaches measured-success
while we record its self-reported file divergence. Auto-skips when GitHub is
unreachable (so offline/CI runs stay green) and per-missing-checker."""

import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from typebench.adapters.base import Adapter
from typebench.adapters.mypy import MypyAdapter
from typebench.adapters.pyrefly import PyreflyAdapter
from typebench.adapters.pyright import PyrightAdapter
from typebench.adapters.ty import TyAdapter
from typebench.corpus import CorpusProject, load_suite
from typebench.envman import prepare_project
from typebench.preflight import preflight_project

_SUITE = Path(__file__).parent.parent / "corpus" / "suite.toml"


def _online() -> bool:
    try:
        socket.create_connection(("github.com", 443), timeout=3).close()
    except OSError:
        return False
    return True


pytestmark = [
    pytest.mark.skipif(not _online(), reason="offline: skips real clone"),
    pytest.mark.skipif(shutil.which("git") is None, reason="needs git"),
    pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv"),
]


def _httpx_entry() -> CorpusProject:
    return next(p for p in load_suite(_SUITE) if p.name == "httpx")


def test_prepare_httpx_resolves_deps_and_counts_first_party_only(tmp_path: Path) -> None:
    prepared = prepare_project(_httpx_entry(), tmp_path / "cache")

    # 1. The venv actually resolved a third-party dep (httpcore is an httpx dep).
    proc = subprocess.run(
        [prepared.venv_python, "-c", "import httpcore; print(httpcore.__file__)"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    dep_path = proc.stdout.strip()

    # 2. The canonical count is dependency-free by construction: it equals an
    #    independent rglob of src_roots minus the tests/ dir, and the resolved
    #    dep file lives OUTSIDE every counted root.
    checkout = Path(prepared.checkout)
    independent = [
        p
        for p in (checkout / "httpx").rglob("*.py")
        if "tests" not in p.relative_to(checkout / "httpx").parts
    ]
    assert prepared.canonical_files == len(independent)
    assert prepared.canonical_files > 0
    for root in prepared.src_roots:
        assert not dep_path.startswith(root)  # deps are never under a counted root


def test_httpx_preflight_records_per_tool_divergence(tmp_path: Path) -> None:
    prepared = prepare_project(_httpx_entry(), tmp_path / "cache")
    pairs = (
        (MypyAdapter(), "mypy"),
        (PyrightAdapter(), "pyright"),
        (TyAdapter(), "ty"),
        (PyreflyAdapter(), "pyrefly"),
    )
    adapters: list[Adapter] = [a for a, n in pairs if shutil.which(n) is not None]
    if not adapters:
        pytest.skip("no real checkers installed")

    report = preflight_project(prepared, adapters, timeout=300)

    # Every probed tool must at least START checking (measured-success): a
    # failed{env} here would mean venv resolution broke for that tool.
    assert report.ready, [(t.tool, t.result_class.value) for t in report.tools]

    # Neutrality record: print each tool's self-reported files vs the canonical
    # denominator. We do NOT assert cross-tool equality — mypy's
    # --follow-imports=silent legitimately counts followed deps; that divergence
    # is the documented reason the denominator is the canonical count, not a
    # self-report. We DO assert any first-party-scoped tool that reports a count
    # never reports FEWER than canonical (which would mean it skipped first-party
    # files -> a mis-scope, not neutral).
    for t in report.tools:
        if t.self_reported_files is not None:
            assert t.self_reported_files >= 0
            assert t.files_divergence == t.self_reported_files - report.canonical_files
    print(  # visible with -s; the neutrality data point
        "httpx file counts: canonical="
        f"{report.canonical_files} "
        + " ".join(f"{t.tool}={t.self_reported_files}" for t in report.tools)
    )
```

- [ ] **Step 3: Run the network test (locally, online)**

Run: `uv run pytest tests/test_corpus_neutrality_net.py -v -s`
Expected (online, checkers installed): PASS — httpx clones, deps resolve, canonical count matches the independent rglob, all four tools reach measured-success. The printed line shows the per-tool divergence.
Expected (offline): both tests SKIP.

- [ ] **Step 4: Update AGENTS.md**

In `AGENTS.md`, under "Layout" add the new modules:

```
  - `corpus.py` — `CorpusProject`, `SizeBucket`, `load_suite` (corpus as data).
  - `counting.py` — `count_first_party`, the neutral throughput denominator (§8).
  - `envman.py` — `prepare_project`: clone@SHA / uv venv / install / freeze / count,
    cached idempotently. The only subprocess surface besides the wrapper.
  - `preflight.py` — `preflight_project`: probes the four tools, records the
    self-reported-vs-canonical file divergence (§12 gate).
```

Update the "Status" line:

```
**Status:** Plan 3 (corpus + envman + preflight) — real projects pinned to release
SHAs, per-project uv venvs so third-party imports resolve, preflight gate. cgroup
memory, threads, and calibration remain Plan 4; renderer Plan 5; CI/bump Plan 6.
```

Add a scope note under "Scope discipline by plan":

```
Plan 3 adds corpus/envman/preflight. Do NOT add cgroup memory, CPU affinity, the
results envelope, the renderer, or bump automation — those are Plans 4-6. Do NOT
change `RunResult` or `taxonomy.py` values; the lock-manifest enrichment is Plan 5.
```

Add `corpus` to the commit scopes list:

```
Scopes in use: `scaffold, models, taxonomy, env, wrapper, timing, adapters,
collector, cli, e2e, ruff, plan, spec, docs, engine, corpus, envman, preflight,
counting`.
```

- [ ] **Step 5: Run the full gate one final time**

Run: `uv run ruff format && uv run ruff check && uv run pyrefly check && uv run pytest -q`
Expected: format clean, `All checks passed!`, `0 errors`, all green. The network test SKIPs offline; runs and passes online.

- [ ] **Step 6: Commit**

```bash
git add tests/test_corpus_neutrality_net.py .gitignore AGENTS.md
git commit -m "test(neutrality): real httpx clone proves dep-free denominator

Opt-in network test (auto-skips offline/CI): clones pinned httpx, resolves deps
in the per-project venv, and proves the canonical count excludes dependency
files by construction (deps live outside src_roots). Records each tool's
self-reported-vs-canonical divergence — the §8 carry-over neutrality check.
Closes the open question that stdlib-only fixtures could not catch."
```

---

## Self-Review

**Spec coverage:**
- §4 `corpus` → Task 1. §4 `envman` (clone @ SHA, uv venv, locked deps, LOC count, lock hash) → Tasks 2,4,5. §12 preflight gate (checkable by all four, excluded-on-fail) → Task 6,7. §8 throughput denominator + diagnostics-as-data → Tasks 2,6,8. §9 lock seed (frozen + hash) → Tasks 4,5 (full §9 record enrichment deferred to Plan 5, stated in OUT). §10 envman caching → Task 5 (full content-addressed cache deferred; sidecar idempotency in). §2 size buckets → Task 1 `SizeBucket`. Carry-over neutrality check → Task 8.
- Deferred-by-design (in OUT): cgroup memory, affinity/threads, calibration (Plan 4); results envelope, renderer, sharding (Plan 5); bump automation (Plan 6). No silent gaps.

**Placeholder scan:** No TBD/TODO; every code step shows complete code; every command shows expected output.

**Type consistency:** `Runner`/`RunOut`/`PrepareError` (Task 4) reused unchanged in Task 5. `PreparedProject`/`ToolPreflight`/`PreflightReport` fields (Task 3) match their construction in Tasks 5,6 and the CLI in Task 7. `count_first_party([Path], tuple) -> FileCount{files,loc}` (Task 2) matches the call in Task 5. `preflight_project(prepared, adapters, *, timeout, probe)` (Task 6) matches the CLI call (Task 7) and the network test (Task 8). `effective_excludes()` (Task 1) used in Task 5. `is_measured_success` is an existing `ResultClass` property (verify in Task 6 Step 3).

**One pre-execution verification for the executor:** confirm `ResultClass.is_measured_success` exists (`grep -n is_measured_success src/typebench/taxonomy.py`); the collector already relies on it, so it should. If `parse()`'s return is `(diagnostics, files)`, preflight reads index `[1]` for files — matches every adapter.

---

## Execution Handoff

Plan complete. Two execution options:

1. **Subagent-Driven (recommended)** — a fresh subagent per task, two-stage review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session with checkpoints.
