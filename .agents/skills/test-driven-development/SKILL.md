---
name: test-driven-development
description: Test-driven development skill for the typebench repo. Use whenever you are about to write or change product behavior — a new pipeline stage, a bug fix, a refactor of anything observable. Write the failing test first; use the Prove-It pattern for bugs. Apply on every behavioral change, not only when the user asks for tests. Covers typebench's conventions — `@pytest.mark.skipif` gating, the Typer `CliRunner`, `monkeypatch.setattr` for boundaries, the `_fake_checker`/`StubAdapter` taxonomy harness, JSON round-trips with `extra="forbid"` — and the `ruff` + `pyrefly` + `pytest` floor.
---

# Test-Driven Development (typebench)

Write the failing test before the code. For bug fixes, reproduce the bug with a test *before* attempting to fix it. Tests are proof — "seems right" is not done. A repo with good tests is an agent's superpower; a repo without tests is a liability, because an agent that cannot check itself has nothing to fall back on.

typebench's only product is **trust in the numbers**. The tests are where that trust is earned: a measurement tool that quietly mis-classifies a crash, drops a failure, or biases a ratio is worse than no tool. Every test here exists to lock an honesty or measurement invariant, not to chase a coverage number.

This skill covers the *process* of driving development through tests and the *shape* of a good test in this repo. It does **not** restate the repo's production-code rules — when a rule is about how the code under test must be written (typing, `subprocess` list-form, `ConfigDict(extra="forbid")`, `pathlib.Path`, keeping the wrapper free of heavy imports, etc.), cite [coding-guidance-python](../coding-guidance-python/SKILL.md) rather than duplicate. When a rule is about reviewing a test change, cite [code-review-and-quality](../code-review-and-quality/SKILL.md).

Project-specific rules live in [AGENTS.md](../../../AGENTS.md) and override anything here.

---

## Scope note — what this skill covers

This skill governs the pytest suite under `tests/` that exercises the `src/typebench/` package and the Typer CLI. The engine spine is **fully synchronous** — there is no asyncio anywhere in typebench, so there is no async-mode, no `@pytest.mark.asyncio`, and none of that machinery appears below.

The single hardest thing to test here is the measurement pipeline without a real type checker attached. typebench solves that with an in-package controllable fake (`_fake_checker.py`) driven by `StubAdapter` — that pattern is the spine of this skill (see *The taxonomy harness* below), not an afterthought.

---

## Testing conventions — source of truth

Re-read AGENTS.md "Testing" before every test commit. The highlights:

- **No pytest markers are registered.** typebench does **not** use `unit`/`component`/`integration` markers, and there is no `pytestmark`. Do not invent a marker scheme. Tier separation is done by **skip guards**, not markers:
  - Tests that shell out to the real `hyperfine` binary: `@pytest.mark.skipif(shutil.which("hyperfine") is None, reason="hyperfine not installed")`.
  - Tests that rely on POSIX signal / process-group semantics: `@pytest.mark.skipif(os.name != "posix", reason="signal semantics are POSIX-specific")`.

  A guarded test runs everywhere; it just skips cleanly where the dependency is absent, instead of failing or hanging. A skip guard is a real test-design decision — pick it deliberately for any test that touches `hyperfine` or signals.
- **Tests get annotations too.** Pyrefly `preset = "strict"` applies to `tests/` (it's in `project-includes`). `def test_…() -> None:` is the minimum; fixtures and helpers are annotated too. typebench dogfoods a checker it benchmarks, so the test suite is held to the same strict bar as the package.
- **Tests are exempt from `PLR2004`, `ARG001`, `ARG002`** (see `pyproject.toml [tool.ruff.lint.per-file-ignores]` `"tests/**"`). Magic numbers in assertions and unused fixture args are fine. `assert` is expected. Keep test code terse.
- **Tests live flat under `tests/`**, one file per package module: `test_collector.py`, `test_wrapper.py`, `test_stub_adapter.py`, `test_cli.py`, `test_models.py`, `test_timing.py`, `test_env.py`, `test_e2e.py`, `test_smoke.py`. `pyproject.toml` sets `pythonpath = ["src"]` and `testpaths = ["tests"]`, so import the package directly (`from typebench.collector import run_single`).

These are hard constraints — the rest of this skill works within them.

---

## When to use TDD

- Implementing any new logic, helper, or pipeline stage (a new adapter, a classify rule, a parse path).
- Fixing any bug — the **Prove-It pattern** below is the default. No fix without a failing test first.
- Modifying existing behavior — change the test first so the regression is caught if the change is later reverted.
- Tightening a type or contract (e.g. a new `extra="forbid"` model field, a coercion rule) — the test is how you prove the old bad input now rejects.
- Locking a measurement invariant — a regression test that asserts the wrapper does not import pydantic, or that a timeout kills the whole process group, is legitimate TDD even though no new feature lands.

## When *not* to use TDD

- Pure config edits that do not change behavior (a ruff rule note, a pyproject comment).
- Formatter-only edits, rename-only refactors where a `pyrefly check` is sufficient proof of equivalence.
- Docs, comments, `.agents/skills/**`, spec/plan edits — no runtime behavior.
- Scaffolding a deliberately-deferred Protocol method (`install`, `parallelism_cap`) that nothing calls yet — add the test when the first real call site lands (AGENTS.md "Scope discipline by plan").

If unsure, lean toward writing the test. Writing a test you later delete is cheap; shipping a behavior change to a measurement tool with no test is expensive.

---

## The TDD cycle

```
    RED                 GREEN               REFACTOR
 Write a test     Write minimal code     Clean up the
 that fails  ───→  to make it pass  ───→  implementation  ───→  (repeat)
      │                   │                      │
      ▼                   ▼                      ▼
   Test FAILS         Test PASSES          Tests still PASS
```

### Step 1 — RED: write the failing test

A test that passes on the first run proves nothing. It usually means the test asserts on something already true (an enum default, an import side effect) or the behavior you intended to add already existed under another name. **If RED doesn't go red, stop and figure out why.**

```python
# tests/test_wrapper.py
from typebench.models import ResultClass
from typebench.wrapper import RawRun, classify_default


def test_classify_default_maps_explicit_oom_flag_to_failed_oom() -> None:
    # A cgroup-sourced OOM flag (Plan 4) must win over the raw exit code.
    raw = RawRun(137, None, False, oom=True, stdout="", stderr="")
    assert classify_default(raw) == ResultClass.FAILED_OOM
```

At this point `classify_default` either doesn't handle the `oom` flag or maps it wrong. `uv run pytest tests/test_wrapper.py` fails with an `AssertionError` (or `TypeError` if the field doesn't exist yet). Good — that's RED confirmed.

### Step 2 — GREEN: minimum code to pass

Resist the urge to design the whole module. Pass *this* test. Do not add future options, generalized extension points, compatibility shims, unused branches, or defensive handling for impossible inputs. The next failing test earns the next behavior. This is also AGENTS.md "Scope discipline by plan" — Plan 1 is the engine spine; don't build cgroup or real-checker behavior early just because a branch "might want it".

That's it. **The next test drives the next piece of the design** — not speculation.

### Step 3 — REFACTOR: clean up under green

With the test passing, improve without changing observable behavior. Run the test after *each* refactor step, not at the end.

- Extract helpers when duplication appears (third use, not first).
- Tighten types — return a concrete enum or `RunResult`, not a bare `str`/`int`.
- Replace a magic exit code with a named constant when it's read from more than one place.
- Push a coercion into `coerce_count` / `default_classify` when more than one adapter needs it.

If the refactor reveals that the test was too implementation-coupled (e.g. "this refactor should be invisible but it broke three tests"), the tests are the problem — fix them to assert on behavior, not plumbing. See *Test state, not interactions* below.

---

## The Prove-It pattern (bug fixes)

When a bug is reported, the first thing you write is **not the fix**. It is a test that reproduces the bug against the current code and fails.

```
Bug report
     │
     ▼
Write a test that reproduces the observed failure
     │
     ▼
Test FAILS → bug confirmed (and you now understand it)
     │
     ▼
Implement the fix
     │
     ▼
Test PASSES → fix works
     │
     ▼
ruff + pyrefly + pytest → no regressions
```

Skipping the reproduction test is how "fixes" that don't fix anything get merged. If the test was already there, it would have caught the bug. The Prove-It test is the guard that prevents the same bug from coming back silently in six months — and in a measurement tool, a silently-returning bug means silently-wrong numbers.

**Rule:** every bug-fix commit includes a test that would fail against `HEAD~1`. No test, no merge. In the commit message body (`git-conventions`), name the symptom and how the test proves it.

**Subagent note:** for non-trivial bugs, dispatch a subagent to write the reproduction test *without showing it the fix*. A test written after the fix tends to assert exactly what the fix does, not the underlying contract. Separating reproducer-author from fixer-author hardens the test.

### Example

Bug report: "When the probe succeeds (exit 0, clean) but a *timed* run crashes under hyperfine, the collector mis-records it as a clean result — `real_exit_code` is the probe's 0, so the crash is invisible." This is a measurement-honesty bug: a dropped failure biases the benchmark (AGENTS.md "Record every failure, never drop one").

Reproduce it deterministically by mocking the boundary, *not* by needing a real hyperfine:

```python
# tests/test_collector.py
import subprocess

import pytest

from typebench import collector
from typebench.adapters.stub import StubAdapter
from typebench.collector import run_single
from typebench.models import FailurePhase, ResultClass, ThreadMode


def test_run_single_timing_crash_marks_timing_phase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collector.shutil, "which", lambda _name: "/usr/bin/hyperfine")

    def _boom(*_a: object, **_k: object) -> object:
        raise subprocess.CalledProcessError(1, "hyperfine", stderr="timed run died")

    monkeypatch.setattr(collector, "run_timing", _boom)

    result = run_single(
        StubAdapter(exit_code=0),  # probe is clean
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        warmup=1,
        runs=2,
        timeout=10,
    )

    assert result.result_class == ResultClass.FAILED_CRASH
    assert result.failure_phase == FailurePhase.TIMING  # disambiguates real_exit_code
    assert result.real_exit_code == 0  # the clean probe's exit, no longer misread as "clean"
    assert result.error_detail == "timed run died"  # audit trail preserved
    assert result.timing is None
```

Run it: `FAILED` against the buggy collector (it returns `CLEAN`). Bug confirmed. Now fix `run_single` to set `failure_phase=TIMING` and `result_class=FAILED_CRASH` when the timed run dies after a clean probe. Re-run: passes. Now the incident is a test-guarded failure forever.

---

## The test pyramid — how typebench layers coverage

There is no marker scheme, so the pyramid maps to *what each test touches*, gated by skip guards rather than tiers:

```
          ╱╲
         ╱  ╲       real hyperfine        @pytest.mark.skipif(shutil.which("hyperfine") is None, …)
        ╱    ╲                            Shells out to the real binary; a deliberate few.
       ╱──────╲
      ╱        ╲    fake-driven pipeline  run_single + StubAdapter + _fake_checker
     ╱          ╲                         Full probe→classify path, deterministic, no real checker.
    ╱────────────╲
   ╱              ╲  pure unit            classify_default, parse, coerce_count, model round-trips
  ╱                ╲                      One function, in-memory inputs, sub-millisecond.
```

**Decision guide:**

```
Can the behavior be proven from pure inputs / outputs (a classify rule, a parse coercion,
a model round-trip)?
  → pure unit. No subprocess, no skip guard.

Does it need the whole probe→classify pipeline but NOT real wall-time numbers?
  → drive it through StubAdapter + _fake_checker. Mock run_timing / shutil.which with
    monkeypatch where a clean probe must be followed by a failing timed run.

Does it genuinely need the real hyperfine binary or real signal delivery?
  → add the matching @pytest.mark.skipif guard.
```

**The "own your behavior" rule.** *If you liked it, you should've put a test on it* — the Beyonce rule from Google's testing lore. A later plan's refactor (real adapters, cgroup memory) is not responsible for catching the engine spine's bugs — the spine's tests are. If a change broke something and there was no test, that is on the author of the original code, not on whoever refactored it.

---

## The taxonomy harness — typebench's signature pattern

The whole failure taxonomy (`clean`, `diagnostics`, `failed{env}`, `failed{crash}`, `failed{timeout}`, `failed{oom}`) must be exercisable **without a real type checker**, deterministically, in CI that may not even have hyperfine. typebench does this with an in-package controllable fake:

- `src/typebench/_fake_checker.py` — a tiny program that prints a JSON `{"diagnostics": …, "files": …}` summary and exits with a chosen code, optionally after a `--sleep`, optionally killing itself with `--signal`, optionally failing only on the Nth invocation via a `--state-file` counter. It ships in the wheel so the stub works from an installed package, not just a source checkout.
- `src/typebench/adapters/stub.py` — `StubAdapter`, which builds the argv for `_fake_checker` from constructor knobs (`exit_code`, `diagnostics`, `files`, `sleep`, `signal`, `missing_binary`, `fail_after_runs` + `state_file`).

This pair *is* the project's pipeline-testing pattern. Reach for it whenever a test needs a controlled checker outcome:

```python
# tests/test_collector.py — exercise a whole taxonomy class through the fake
def test_run_single_diagnostics_records_counts() -> None:
    adapter = StubAdapter(exit_code=1, diagnostics=3, files=7)  # -> DIAGNOSTICS
    result = run_single(
        adapter,
        project="demo",
        thread_mode=ThreadMode.ALL_CORES,
        warmup=1,
        runs=2,
        timeout=10,
    )
    assert result.result_class == ResultClass.DIAGNOSTICS
    assert result.diagnostics == 3
    assert result.files == 7
```

The trickiest taxonomy case — **"probe passes, timed run fails"** (flaky checker) — is reproduced deterministically with the state-file counter, so the first invocation (the probe) succeeds and later ones fail:

```python
@pytest.mark.skipif(shutil.which("hyperfine") is None, reason="hyperfine not installed")
def test_run_single_records_timing_phase_failure(tmp_path: Path) -> None:
    state = tmp_path / "count"
    adapter = StubAdapter(state_file=str(state), fail_after_runs=1)
    result = run_single(adapter, project="demo", thread_mode=ThreadMode.ALL_CORES,
                        warmup=1, runs=2, timeout=10)
    assert result.result_class == ResultClass.FAILED_CRASH
    assert result.failure_phase == FailurePhase.TIMING
    assert result.error_detail  # hyperfine's stderr, the audit trail
```

When you do **not** want to depend on hyperfine being installed, drive the same flaky path through the boundary mock instead (`monkeypatch.setattr(collector, "run_timing", _boom)`), as in the Prove-It example above. Two ways to reach the same taxonomy class — pick the mock when you need it to run everywhere, the real binary when the assertion is about real timing output.

A `missing_binary=True` stub gives you `failed{env}` for free — `run_command` captures the `OSError` (never raises) so the collector records it:

```python
def test_run_single_env_failure_is_recorded() -> None:
    adapter = StubAdapter(missing_binary=True)
    result = run_single(adapter, project="demo", thread_mode=ThreadMode.ALL_CORES,
                        warmup=1, runs=2, timeout=10)
    assert result.result_class == ResultClass.FAILED_ENV
    assert result.error_detail  # carries the OSError text
```

The e2e test (`tests/test_e2e.py`) parametrizes every taxonomy class through this harness and round-trips each `RunResult` to JSON — the single best example of the pattern at full scale.

---

## Writing good tests

### Test state, not interactions

Assert on the *outcome* (the returned `RunResult`, the written JSON, the parsed counts), not on which internal methods got called. Interaction tests calcify the implementation — any refactor that preserves behavior breaks the test, which trains authors to update tests blindly until they stop catching anything.

```python
# Good — asserts on observable state
def test_run_single_failure_skips_timing() -> None:
    result = run_single(StubAdapter(exit_code=2), project="demo",
                        thread_mode=ThreadMode.ALL_CORES, warmup=1, runs=2, timeout=10)
    assert result.result_class == ResultClass.FAILED_CRASH
    assert result.timing is None  # a failed run must not carry timing


# Bad — asserts on how it's done, not what it does
def test_run_single_calls_run_timing_once() -> None:
    with patch("typebench.collector.run_timing") as mock_time:
        run_single(StubAdapter(exit_code=2), ...)
        assert mock_time.call_count == 0  # brittle: breaks on any internal restructure
```

The legitimate exception is when the behavior under test *is* the interaction — e.g. "a timed-out parent kills the whole process group". There the only observable proof is a side effect (an orphaned grandchild's marker file *not* appearing). See *Boundary effects under test* below.

### DAMP over DRY in tests

In production code, DRY usually wins. In tests, **DAMP — Descriptive And Meaningful Phrases — wins.** A test should read like a spec on its own, without the reader tracing through shared helpers. Construct the `StubAdapter` and call `run_single` inline; don't hide the inputs behind a fixture.

```python
# DAMP — each test is self-contained and reads as a specification
def test_stub_parse_coerces_string_diagnostics_to_none() -> None:
    assert StubAdapter().parse('{"diagnostics": "3", "files": 7}', "", 0) == (None, 7)


def test_stub_parse_coerces_float_diagnostics_to_none() -> None:
    assert StubAdapter().parse('{"diagnostics": 3.5, "files": 7}', "", 0) == (None, 7)
```

When one contract holds across many inputs, the codebase parametrizes (next section) rather than copying the body. Duplication is otherwise fine — each failure tells you exactly what broke without a hunt through fixtures.

### Parametrize when the contract is identical, split when the reasoning differs

`@pytest.mark.parametrize` is the right tool when the *shape* of the assertion is identical across rows — the stub-parse coercion table and the e2e taxonomy sweep both use it:

```python
@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ('{"diagnostics": 3, "files": 7}', (3, 7)),
        ('{"diagnostics": true, "files": 7}', (None, 7)),   # JSON bool is not a count
        ('{"diagnostics": "3", "files": 7}', (None, 7)),    # string is not a count
        ('{"diagnostics": 3.5, "files": 7}', (None, 7)),    # float is not a count
        ('{"files": 7}', (None, 7)),                         # missing key -> None
    ],
)
def test_stub_parse_coerces_counts_to_int_or_none(
    line: str, expected: tuple[int | None, int | None]
) -> None:
    assert StubAdapter().parse(line, "", 0) == expected
```

Don't parametrize when the *reasoning* differs per case — a too-large table where each row needs its own comment is several distinct tests wearing a trench coat, and one row failing hides the rest. Separate tests read better there.

### Round-trip every model through JSON, and assert `extra="forbid"`

`RunResult` and friends are an on-disk stability contract (AGENTS.md "Ask first" — schema changes need clearance). Two assertions guard that contract:

```python
# 1. A model survives a full dump → validate round trip unchanged.
def test_run_result_round_trips_through_json() -> None:
    result = RunResult(tool="stub", ..., result_class=ResultClass.DIAGNOSTICS, ...)
    restored = RunResult.model_validate_json(result.model_dump_json())
    assert restored == result


# 2. An unknown on-disk field is rejected loudly (ConfigDict(extra="forbid")).
def test_run_result_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        RunResult.model_validate({..., "bogus": True})
```

The taxonomy strings are part of the contract too — assert the literal on-disk value (`ResultClass.FAILED_ENV.value == "failed{env}"`), not just the enum identity, so a rename can't slip through.

### Arrange-Act-Assert

```python
def test_classify_default_timeout_precedes_sigkill_oom() -> None:
    # Arrange: a run that is BOTH timed out and killed by signal 9.
    raw = RawRun(0, 9, True, False, "", "")

    # Act
    result = classify_default(raw)

    # Assert: documented precedence — timeout wins over the SIGKILL→OOM heuristic.
    assert result == ResultClass.FAILED_TIMEOUT
```

One block per step. If "Act" has more than one meaningful call, the test is probably testing two things — split it.

### One assertion per concept

```python
# Good — one concept per test; failure tells you exactly what regressed
def test_cli_run_rejects_unknown_tool(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", "--tool", "nope", "--project", "demo",
                                 "--output", str(tmp_path / "r.json")])
    assert result.exit_code == 2
    assert "Unknown tool" in result.output


def test_cli_run_rejects_unwritable_output_dir(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist" / "results.json"
    result = runner.invoke(app, ["run", "--tool", "stub", "--project", "demo",
                                 "--output", str(missing)])
    assert result.exit_code == 2
    assert "writable" in result.output.lower()
```

Related assertions that describe *one* outcome (exit code + the message explaining it) belong together; unrelated concepts get their own test.

### Name tests as sentences

`test_<unit>_<scenario>_<expected_behavior>` — the name reads as a sentence in failure output. This is the AGENTS.md "Testing" rule and [coding-guidance-python](../coding-guidance-python/SKILL.md) "Tests and verification":

```python
# Good — failure output reads as a spec
def test_run_command_reports_env_error_for_missing_binary() -> None: ...
def test_run_single_diagnostics_records_counts() -> None: ...
def test_classify_default_timeout_precedes_sigkill_oom() -> None: ...

# Bad — tells you nothing when it fails
def test_classify() -> None: ...
def test_run_works() -> None: ...
def test_error_handling() -> None: ...
```

If you can't name the test as a sentence, you probably don't know yet what behavior you're asserting.

---

## Framework-specific patterns

### The Typer CLI under test — `CliRunner`, assert exit code + output

Test `typebench run` through Typer's test harness, never by calling internal functions and re-implementing argument parsing. Assert on the **exit code** and the **output**, and validate any written JSON through the real model.

```python
# tests/test_cli.py
from typer.testing import CliRunner

from typebench.cli import app
from typebench.models import RunResult

runner = CliRunner()


def test_cli_run_stub_writes_results_json(tmp_path: Path) -> None:
    out = tmp_path / "results.json"
    result = runner.invoke(app, ["run", "--tool", "stub", "--project", "demo",
                                 "--thread-mode", "all-cores", "--runs", "2",
                                 "--warmup", "1", "--output", str(out)])

    assert result.exit_code == 0, result.output  # include output in the message on failure
    parsed = RunResult.model_validate_json(out.read_text())
    assert parsed.tool == "stub"
    assert parsed.project == "demo"
```

Note `assert result.exit_code == 0, result.output` — when the CLI fails, the captured output is the only diagnostic you get, so attach it to the assertion.

### Boundary seams under test — `monkeypatch.setattr`, mock the boundary not the behavior

The seams worth mocking are the **external boundaries**: the `hyperfine` subprocess, the `shutil.which` probe for it, the filesystem. Patch them on the *module that uses them* (`collector.run_timing`, `collector.shutil`) so you can drive failure paths deterministically and without the real binary. Never mock the behavior you are actually trying to prove.

```python
def test_run_single_timing_harness_error_is_failed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # A garbled/empty hyperfine JSON (ValueError) is a HARNESS failure, not a
    # checker crash. It must record failed{env}, never drop the run.
    monkeypatch.setattr(collector.shutil, "which", lambda _name: "/usr/bin/hyperfine")

    def _boom(*_a: object, **_k: object) -> object:
        raise ValueError("hyperfine JSON has no results")

    monkeypatch.setattr(collector, "run_timing", _boom)

    result = run_single(StubAdapter(exit_code=0), project="demo",
                        thread_mode=ThreadMode.ALL_CORES, warmup=1, runs=2, timeout=10)

    assert result.result_class == ResultClass.FAILED_ENV
    assert result.failure_phase == FailurePhase.TIMING
    assert result.error_detail
```

`monkeypatch.setattr(target, name, value)` auto-reverts after the test — prefer it over `unittest.mock.patch`. The thing under test (the collector's *classification* of a harness error vs. a checker crash) runs for real; only the boundary that would otherwise need a real subprocess is faked.

### Filesystem under test — `tmp_path`, not hardcoded paths

```python
def test_cli_run_stub_writes_results_json(tmp_path: Path) -> None:
    out = tmp_path / "results.json"
    # ... invoke, then read back from `out`
```

Never write to `/tmp/…`, `~/.cache/…`, or a relative path — tests share worker state and trash each other. `tmp_path` is per-test, cleaned automatically. The `_fake_checker` state-file counter (`tmp_path / "count"`) and the process-tree marker test both rely on this.

### Boundary effects under test — when an interaction *is* the behavior

Some measurement invariants are only observable as a side effect, and there the side effect is the assertion. Two real examples:

- **Process-group kill on timeout** (`tests/test_wrapper.py`). The invariant: a timed-out parent must not leave a grandchild stealing CPU from later runs. The test spawns a parent→grandchild, times out the parent, and asserts the grandchild's marker file *never appears*. Guarded with `@pytest.mark.skipif(os.name != "posix", …)` because process-group kill is POSIX-specific.
- **Signal recording** (`tests/test_wrapper.py`). A child that `os.kill`s itself with SIGSEGV must surface `raw.signal == 11`. Also POSIX-guarded.

These are not "interaction mocks" — they assert on a real, observable consequence of the code, not on whether some method was called.

### Measurement-invariant regression tests are first-class

A test that locks a *performance* invariant counts as real TDD. The canonical one (`tests/test_wrapper.py`): the wrapper is hyperfine's per-run command, so any heavy import it pulls is paid on *every* timed measurement and biases comparative ratios (AGENTS.md "Measurement fidelity"). The guard runs a **fresh interpreter** via `subprocess` — because pytest itself has already imported pydantic, you can't check `sys.modules` in-process:

```python
def test_wrapper_import_does_not_pull_pydantic() -> None:
    code = (
        "import sys, typebench.wrapper\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] == 'pydantic')\n"
        "assert not bad, bad\n"
    )
    proc = subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
```

If you add an import to `wrapper.py` or `taxonomy.py`, this test is your early warning that you put pydantic (or anything heavy) back on the measured path.

---

## Test anti-patterns

| Anti-pattern | Why it hurts | Do this instead |
|---|---|---|
| Inventing a `unit`/`component`/`integration` marker or `pytestmark` | typebench registers no markers; nothing selects on them and it misleads readers. | Gate with `@pytest.mark.skipif(shutil.which("hyperfine") is None, …)` or the POSIX guard. |
| A hyperfine- or signal-dependent test with no skip guard | Fails or hangs in environments without the binary / on non-POSIX. | Add the matching `skipif`. |
| `@pytest.mark.asyncio` / any async test machinery | typebench is fully synchronous; there is no asyncio in the engine spine. | Write a plain `def test_…() -> None:`. |
| Mocking the *checker outcome* with a bare `Mock` | Parallel infrastructure when `StubAdapter` + `_fake_checker` produce a real, deterministic outcome. | Drive the outcome through `StubAdapter(exit_code=…, signal=…, …)`. |
| `unittest.mock.patch("typebench.x.…")` reaching through module paths | Couples the test to import structure; breaks silently on rename. | `monkeypatch.setattr(collector, "run_timing", …)` on the module that uses the boundary. |
| Calling the CLI's internal functions instead of `runner.invoke(app, …)` | Re-implements Typer's arg parsing; misses exit-code / output contract. | Use `CliRunner`; assert `result.exit_code` and `result.output`. |
| Test writes to `/tmp/…` or a relative path | Tests share worker state and trash each other. | Use `tmp_path`. |
| Asserting on `mock.call_count == N` | Implementation-coupled; refactors break green tests. | Assert on the returned `RunResult` / written JSON. |
| Snapshot-comparing a whole `model_dump()` blob | Nobody reviews the snapshot; breaks on harmless schema churn. | Round-trip and assert the specific fields the behavior cares about. |
| Asserting on a Pydantic *message string* | Messages get reworded between releases. | Assert on `ValidationError` being raised (and the field name via `match=` if needed). |
| Test passes on first run without you seeing it fail | May be asserting on an enum default or import side effect. | Always confirm RED before GREEN. |
| `test_it_works` / `test_run` / `test_error` | Zero signal in failure output. | `test_<unit>_<scenario>_<expected_behavior>`. |
| Adding a test-only field/flag to a `models.py` schema so a test can peek | The on-disk schema is a stability contract; test pressure must not reshape it. | Assert through the existing public surface; the schema is the same in test and runtime. |

---

## Interaction with other skills

- **[coding-guidance-python](../coding-guidance-python/SKILL.md)** owns the rules for *production* code under test — typing, `subprocess` list-form, `ConfigDict(extra="forbid")`, `pathlib.Path`, keeping the wrapper free of heavy imports, illegal-states-unrepresentable, error handling. This skill does not restate those.
- **[code-review-and-quality](../code-review-and-quality/SKILL.md)** owns the review output format. When reviewing a test change, that skill's test-review step is where these findings get filed.
- **[AGENTS.md](../../../AGENTS.md)** — the authoritative project rules; the "Testing", "Domain invariants", and "Ask first" sections override anything here. `git-conventions` governs commits — bug-fix commits carry the Prove-It test in the same commit as the fix, and the body names the symptom.

---

## Common rationalizations

The thoughts that lead to the test getting skipped, and what's actually true.

| Rationalization | Reality |
|---|---|
| "I'll add tests after it works" | Tests written after the fact assert on *what the code does*, not *what it should do*. In a measurement tool that means calcifying a wrong number into the contract. |
| "This is too simple to test" | A classify rule or a count coercion is exactly where an off-by-one biases the benchmark. The test is the contract. |
| "I can't test the timing without hyperfine" | You can: `StubAdapter` + `_fake_checker` + a `monkeypatch` of `run_timing` cover every taxonomy class deterministically, no binary required. |
| "I tested it manually" | Manual tests do not persist. Tomorrow's refactor — real adapters, cgroup memory — has nothing to fall back on. |
| "The code is self-explanatory" | Code describes *how*. Tests describe *what*. They are not the same artifact. |
| "Mocking's fine, I'll fix it later" | Once `Mock`s proliferate, nothing removes them. Use the real `StubAdapter`/fake now. |
| "It's just the engine spine, real checkers come later" | The spine is what every later plan builds on. Its invariants are the foundation of trust in the numbers. |

---

## Red flags

If you catch yourself doing any of these, stop and course-correct:

- Writing production code without a corresponding test in the same change.
- Writing a test that passes on its first run without having seen it fail.
- A bug-fix commit with no reproduction test (especially a dropped-failure / mis-classification bug).
- Any async test machinery — typebench has no asyncio.
- A hyperfine- or signal-dependent test missing its `@pytest.mark.skipif` guard.
- Inventing a `unit`/`component`/`integration` marker.
- `unittest.mock.patch("typebench.…")` reaching through the module system instead of `monkeypatch.setattr` on the boundary.
- Mocking the checker outcome with a bare `Mock` instead of driving `StubAdapter` + `_fake_checker`.
- A schema field or flag added only so a test can inspect internals.
- A test whose assertions would pass for any successful execution of the code path.
- Asserting on a Pydantic message string instead of the raised `ValidationError`.
- "All tests pass" when you haven't actually run `uv run pytest`.
- Tests skipped or xfailed without a reason.

---

## Verification

A test change — or a feature change with tests — is done when:

- New or changed behavior has a test, and that test *failed* against the pre-change code.
- hyperfine- or signal-dependent tests carry the right `@pytest.mark.skipif` guard; everything else runs unconditionally.
- No async machinery anywhere (typebench is synchronous).
- No new marker invented; no `unittest.mock.patch` reaching through module paths where `monkeypatch.setattr` on the boundary would do.
- Every taxonomy-class assertion is reachable deterministically via `StubAdapter` + `_fake_checker` (no flaky dependence on a real checker's behavior).
- Models that touch disk are round-tripped through `model_dump_json` / `model_validate_json` and the `extra="forbid"` rejection is asserted.
- The verification floor passes clean, in order (AGENTS.md "Quality gates"):

  ```bash
  uv run ruff format
  uv run ruff check
  uv run pyrefly check    # preset = "strict", including tests/
  uv run pytest
  ```

- Test names read as sentences — `test_<unit>_<scenario>_<expected_behavior>`.
- Bug-fix commit includes the reproduction test; commit body names the symptom (`git-conventions`).
- No new `# pyrefly: ignore[<kind>]` without a reason comment — tests are held to strict.
- No new runtime dependency without AGENTS.md "Ask first" clearance; no schema field added casually.
- Coverage, if measured, is a signal — write for contracts and measurement invariants, not for a number.

---

## Examples

**Good TDD slice shape:**

- *One* new test, one new or changed function, both in the same commit.
- Test asserts on observable state (the `RunResult`, the written JSON, the parsed counts), not internal interactions.
- Inputs constructed inline via `StubAdapter(...)`; boundaries (`run_timing`, `shutil.which`) mocked with `monkeypatch.setattr` only where a real subprocess would otherwise be needed.
- Skip guard set if the test touches hyperfine or signals; docstring omitted (tests are exempt from `D`-style rules); types annotated (strict).
- `uv run ruff format && uv run ruff check && uv run pyrefly check && uv run pytest` clean.
- Commit body explains *why* the new behavior is wanted (and which honesty/measurement invariant it protects), not *what* the code does.

**Good Prove-It shape:**

- Test written first, named after the bug's observable symptom (e.g. `…timing_crash_marks_timing_phase`).
- Test fails against `HEAD~1`, passes against the fix.
- Same commit contains test + fix. Commit body: symptom → test → fix → verification.
- Only one behavioral change in the commit; unrelated cleanup deferred.

**Bad shape that passes the test command and is still wrong:**

```python
# tests/test_collector.py
from unittest.mock import Mock, patch


def test_run_single_records_a_result() -> None:
    fake_adapter = Mock()                                  # (1) mocks the adapter outcome
    fake_adapter.classify.return_value = ResultClass.CLEAN
    with patch("typebench.collector.run_timing") as mt:    # (2) patches via module path
        mt.return_value = Mock()
        result = run_single(fake_adapter, project="demo", thread_mode=ThreadMode.ALL_CORES,
                            warmup=1, runs=2, timeout=10)
    assert fake_adapter.classify.called                    # (3) interaction assertion
```

Three things are wrong at once:

1. `Mock()` for the adapter bypasses what `StubAdapter` + `_fake_checker` would prove — the test won't catch a regression where the collector mis-maps a real exit code to the wrong taxonomy class, only whether `classify` was *called*.
2. `patch("typebench.collector.run_timing")` is fine as a *target*, but pairing it with a fully-mocked adapter means nothing real runs end to end; prefer a real `StubAdapter` and `monkeypatch.setattr` so the collector's actual classification logic executes.
3. `assert fake_adapter.classify.called` is an interaction assertion — green against a refactor, green against the wrong `result_class`, green against a dropped failure. Asserting `result.result_class == ResultClass.CLEAN and result.timing is not None` would have caught all three.

This test is worse than no test: it occupies a file named after the right behavior, lulls reviewers into approving, and will silently pass through every refactor that matters — including one that quietly biases the numbers. Delete it and rewrite around a real `StubAdapter` and assertions on the returned `RunResult`.
