---
name: coding-guidance-python
description: Python implementation and review skill for the typebench repo — a neutral, reproducible Python type-checker performance benchmark. Use every time you write, modify, refactor, or review Python code in this codebase — contracts, type safety, testability, security, measurement fidelity, and module boundaries all matter here. Apply on every Python edit, not only when the user explicitly asks for "clean code" or "best practices".
---

# Python Coding Guidance (typebench)

Python implementation, refactoring, and review guidance for typebench. The goal is modern, clean, robust, well-architected, readable Python — code that reads like it was written once on purpose, not accreted over time. typebench is a measurement tool, so the bar is higher in one specific way: **the only product is trust in the numbers**, and every rule below ultimately serves that.

## Project conventions — source of truth

Project-specific rules live in [AGENTS.md](../../../AGENTS.md) and take precedence over anything in this skill. Re-read AGENTS.md when in doubt; the highlights:

- **Tooling** — `uv` for everything; there is no `make`. `uv sync` sets up the env. The floor before declaring a change done is these four gates, run in order:

  ```bash
  uv run ruff format
  uv run ruff check
  uv run pyrefly check
  uv run pytest
  ```

  Pre-commit runs `ruff check --fix`, `ruff format`, and `pyrefly check` (strict) on commit. Never bypass with `--no-verify`.

- **Python 3.12+**, `pyrefly` with `preset = "strict"` (`project-includes = ["src", "tests"]`, `search-path = ["src"]`, `python-version = "3.12"`, `python-platform = "linux"`). typebench **dogfoods** pyrefly — it is one of the checkers being benchmarked — so keep it at 0 errors. Every function — including tests — gets annotations.
- **Line length 100**, double-quoted strings, formatter-enforced. Ruff lint set: `E,W,F,I,N,UP,B,C4,SIM,PTH,RET,ARG,TID,TC,PL,RUF`, pylint `max-args = 8`. `docstring-code-format` is on. Per-file ignores exist for `tests/**` (`PLR2004,ARG001,ARG002`) and `src/typebench/adapters/**` (`ARG002` — Protocol-required args the stub doesn't use yet); don't widen them casually.
- **`pathlib.Path`, not `os.path`.**
- **No commented-out code** — `git log` remembers.
- **Pydantic models: `ConfigDict(extra="forbid")`** on every model — unknown on-disk fields must fail at runtime *and* type-check. The on-disk schema (`RunResult` fields, taxonomy string values) is a stability contract; changing it is an "Ask first" boundary.
- **Security rules on** — no `subprocess` with `shell=True` (list-form only; strings handed to hyperfine go through `shlex.join`), no hardcoded secrets, no `eval`/`exec`/`pickle` of untrusted data, no `assert` in production code paths.
- **Package lives under `src/typebench/`** (src layout, hatchling); tests live under `tests/`. Docs and phase plans live under `docs/superpowers/{specs,plans}/`.
- **Fully synchronous.** There is no asyncio in the engine spine. If async is ever introduced it gets its own clearly-named boundary; until then, don't reach for it.
- **No logging layer.** typebench has no `structlog` / stdlib `logging` setup; failures are recorded into the result schema, not logged. Don't introduce a logging dependency.

## Design taste

Good Python in this repo is small, typed, explicit, and boring. The agent is not rewarded for filling space; it is rewarded for making behavior easier to prove and easier to change — and, here, for keeping the measured path honest and cheap.

- Prefer the smallest change that makes the tested behavior correct.
- Use clear functions and plain data flow before classes, registries, factories, or framework-style indirection.
- Add an abstraction only when it reduces real complexity, protects a contract, or makes a likely change safer. One caller is usually not enough evidence.
- Treat DRY as protection for measurement rules, schema rules, and fragile assumptions. Do not abstract harmless repetition just to remove repeated lines.
- Keep functions focused, but do not split a readable flow into tiny helper chains that force the reader to jump around.
- Choose obvious control flow and familiar project patterns. Clever code must earn its keep through correctness, clarity, or safety.
- Prefer deletion over accommodation when code is unused or speculative — but note the pinned Adapter Protocol methods (`install`, `parallelism_cap`) are implemented, normal methods with tests; don't delete them.
- Comments explain why the obvious path was not taken. They are not a place to restate well-named code. (The existing measurement-fidelity comments and the `universal_failure_prefix` / `classify_with_map` hazard note in `engine/wrapper.py` are the model: they explain a non-obvious hazard.)

## Domain values

typebench-specific invariants. These are not style preferences — violating one corrupts the numbers, which is the whole product. They map to AGENTS.md "Domain invariants."

- **Honesty by construction.** The schema must never claim a methodology that wasn't run. `thread_mode_enforced` must reflect whether CPU affinity was actually applied for the run — never set `True` when it wasn't. `FailurePhase` disambiguates a probe failure (`real_exit_code` is its own code) from a flaky timed-run failure (`real_exit_code` is the *successful probe's*), so a record can't be misread.
- **Record every failure, never drop one.** The failure taxonomy (`ResultClass`: `clean`, `diagnostics`, `failed{env|crash|timeout|oom}`) is the contract. A dropped failure silently biases the benchmark. `run_command` **never raises** — nonzero exit, timeout, signal death, and missing-binary all become a recorded `RawRun`.
- **Measurement fidelity.** The wrapper is hyperfine's per-run command, so everything it imports runs on *every* timed measurement. Keep `engine/wrapper`, `engine/measure`, `engine/calibration`, and `contracts/taxonomy` free of heavy imports — pydantic on the measured path adds constant startup overhead that biases comparative ratios. Import enums from `contracts/taxonomy`, not `contracts/models`.
- **Benchmark isolation.** Subprocess in list-form only; `shlex.join` for hyperfine command strings. On timeout, kill the whole **process group** (`start_new_session` + `killpg`), not just the direct child, or stragglers contaminate later runs.
- **Scope discipline.** Keep changes as narrow as possible. The "Ask first" boundaries in AGENTS.md define the hard edges: on-disk schema, quality gates, runtime dependencies, and heavy imports on the measured path. When a change approaches any of those boundaries, stop and check before continuing.

## Implementation workflow

1. **Read before editing** — touched modules, entrypoints, tests, `AGENTS.md`, the design spec / phase plan under `docs/superpowers/`.
2. **Infer intent** from existing code, imports, and tests when the request is only partially specified. Ask only when multiple plausible designs would change semantics or cross an AGENTS.md "Ask first" boundary (quality gate, on-disk schema, runtime dependency, heavy import on the measured path).
3. **Choose the narrowest change** that keeps contracts, side effects, error handling, and the on-disk schema shape explicit.
4. **Implement** with simple functions, clear module boundaries, explicit types, production-safe behavior at I/O boundaries (filesystem, subprocess) — and on the measured path, cheap imports.
5. **Add or update tests** close to the changed behavior; gate environment-specific tests with the right `skipif`.
6. **Verify** — the four gates (`uv run ruff format`, `uv run ruff check`, `uv run pyrefly check`, `uv run pytest`), in order, at minimum.

## Refactoring workflow

Use this instead of the default implementation workflow when the task is primarily cleanup or restructuring:

1. Capture current behavior, side effects, hidden globals, import shape, and mutation hotspots. On the wrapper/taxonomy path, also capture the import cost.
2. Break the refactor into small slices that preserve behavior.
3. Remove long functions, muddled responsibilities, implicit coupling, and anti-patterns one step at a time.
4. Keep the four-gate verification (`ruff format`, `ruff check`, `pyrefly check`, `pytest`) green after each slice. Add characterization coverage first when behavior is unclear.
5. Stop when the code is simpler, more explicit, easier to test, and easier to operate.

## When reviewing instead of implementing

Use the [code-review-and-quality](../code-review-and-quality/SKILL.md) skill — it owns the review process, severity vocabulary, output format, and project-specific review checks. This skill remains the source of truth for the *Python rules* below; the review skill cites them rather than restating them.

---

## Python rules

### Security — priority 1

Security rules come first because the cost of a violation is highest. typebench enables ruff's broad rule set and runs subprocesses on every benchmark; these are the patterns to internalize regardless:

- **Never hardcode secrets, API keys, credentials** — route through config or environment.
- **Never pass `shell=True`** to `subprocess` — always use the list form. `shell=True` with interpolated values is command injection. typebench runs checkers via `subprocess.Popen(argv, ...)` and hands command *strings* to hyperfine only through `shlex.join`.
- **Never `eval`, `exec`, or `pickle` untrusted data** — they execute arbitrary code. Use `json`, `tomllib`, or a typed parser. Checker stdout/stderr is untrusted input: parse it, don't execute it.
- **Use `secrets` (not `random`) for cryptographic randomness** — tokens, session IDs, salts. (typebench has no crypto domain today; keep this rule if one ever appears.)
- **Never `assert` in production code paths.** `assert` is compiled out under `python -O`; rules that disappear under an optimization flag are not rules. Use real validation that raises explicit exceptions. Tests keep their asserts and `S101` does not apply there.
- **Validate user-provided or externally-supplied file paths** before opening:

  ```python
  from pathlib import Path

  def safe_read(user_path: str, allowed_root: Path) -> str:
      resolved = Path(user_path).resolve()
      if not resolved.is_relative_to(allowed_root.resolve()):
          raise ValueError(f"Path outside allowed root: {user_path}")
      return resolved.read_text(encoding="utf-8")
  ```

### First tier — causes bugs

- Keep module side effects minimal; no import-time network calls, filesystem mutation, or heavy initialization unless the module is an entrypoint. On the measured path (`engine/wrapper`, `engine/measure`, `engine/calibration`, `contracts/taxonomy`) this extends to import *cost*: no pydantic, no heavy third-party imports.
- Prefer explicit parameters and return values over hidden globals, ambient context, or module-level mutation.
- Do not use mutable default arguments.
- Treat `None`, optional fields, and missing keys as contract design, not caller cleanup. (`timing=None`, `failure_phase=None`, and `diagnostics/files=None` in `RunResult` are deliberate contract states, not laziness.)
- Preserve exception context — `raise NewError(...) from original` when translating. Never swallow without a domain-specific reason. The collector's `except` blocks are the model: each one reclassifies into the taxonomy and records, rather than dropping.
- Use context managers for files, locks, and subprocess pipes (`with proc:` as in `run_command`).
- Be explicit about text vs. bytes and timezone-aware vs. naive datetimes. Subprocesses run with `text=True`; keep stdout/stderr handling consistent.

### Second tier — prevents mistakes

- Prefer small functions and plain data flow before introducing classes.
- Use classes when they model stateful domain objects or a stable behavior boundary, not just to group helpers.
- Prefer `pathlib.Path`.
- Prefer standard-library types — `dataclass`, `TypedDict`, `Protocol`, `Enum`/`StrEnum`, `Literal`, `NamedTuple` — when they clarify contracts. typebench uses `@dataclass(frozen=True)` for the measured-path `RawRun`/`ParallelismCap`, `StrEnum` for the on-disk taxonomy, and a `runtime_checkable` `Protocol` for `Adapter`. For validated external/on-disk data, use Pydantic with `ConfigDict(extra="forbid")`.
- **`Final` for constants** — `MAX_RETRIES: Final = 3`. No unexplained literals; if a number has meaning, name it (e.g. the `_SIGKILL = 9` OOM-heuristic constant in `engine/wrapper.py`).
- Early returns over deep nesting.
- Keyword-only arguments (after `*`) for optional parameters in public APIs.
- Use comprehensions and built-ins when they clarify intent; avoid dense one-liners that hide control flow.
- Avoid boolean flag parameters — split functions or use a small config type.
- Keep lint and pyrefly findings at zero. Pyrefly here rejects legacy `# type: ignore` — suppress only with `# pyrefly: ignore[<kind>]` plus a reason comment.

### Typing and interfaces

- Type-annotate every function (AGENTS.md requires this, including in tests).
- Prefer concrete types at boundaries and `Protocol` for substitution seams rather than broad `Any`. The `Adapter` Protocol is the substitution seam between the collector and any checker; the stub and real adapters both satisfy it structurally.
- Use `Any` only when the repo truly needs a dynamic escape hatch; never in public signatures.
- Do not use `object` as a lazy stand-in for "several known shapes." Use a real union (`int | None`, `str | None`, `Path | str`, etc.), a named PEP 695 alias, or a typed container. Keep `object` for intentionally opaque raw input — e.g. `coerce_count(value: object)` in `adapters/base.py`, which takes a parsed-JSON field precisely so it can reject bools and non-ints before narrowing to `int | None`.
- Don't let untyped `json.loads` results or `dict[str, Any]` flow across modules. Narrow parsed JSON at the boundary (the `coerce_count` pattern) before it spreads.
- Use Python 3.12 `type` statements for repeated or hard-to-read unions, callables, tuple shapes, and container contracts. Prefer module-local aliases; promote upward only when the shape crosses module boundaries.
- Prefer modern union syntax (`X | Y`, `T | None`) and type narrowing.
- If a function takes more than 2–3 meaningful parameters, prefer a named type, config object, or split responsibility. (`run_single` carries the run knobs explicitly because they are the run's identity; a config object is the move once they grow.)
- Make mutation visible in names and signatures.
- Don't weaken pyrefly strictness (preset, sub-config) to get a commit through — tighten the seam instead. Changing the pyrefly preset is an "Ask first" boundary.

### Make illegal states unrepresentable

The strongest lever in the typing toolbox (Yaron Minsky, popularized via Rust). If the types cannot represent an invalid state, you cannot write code that has to handle one. Push invariants into the type system at construction, then the rest of the code reads what is already known to be true. In a measurement tool this is also an honesty mechanism: a state the schema can't represent is a claim the benchmark can't accidentally make.

Patterns in Python 3.12, grounded in typebench:

- **`StrEnum` / `Literal` instead of `str`** for closed sets. `ResultClass`, `ThreadMode`, and `FailurePhase` are `StrEnum`s with stable on-disk string values — a call site that invents a class fails at type-check, not at runtime, and the string never drifts.
- **Push invariants onto the enum.** `ResultClass.is_measured_success` lives on the enum, so "clean and diagnostics are successes; only real failures are excluded" is stated once and every consumer reads the same truth.
- **Discriminated unions** — a `Literal`/enum tag + `match`. typebench's `RawRun` → `ResultClass` mapping in `classify_default` is a precedence-ordered classifier over the failure dimensions (env-error, oom, timeout, signal, exit code). When real adapters add variants, an exhaustive `match` over `ResultClass` (pyrefly's strict preset flags non-exhaustive matches) keeps every consumer honest:

  ```python
  from typebench.contracts.taxonomy import ResultClass

  def label(rc: ResultClass) -> str:
      match rc:
          case ResultClass.CLEAN:
              return "clean"
          case ResultClass.DIAGNOSTICS:
              return "diagnostics"
          case ResultClass.FAILED_ENV | ResultClass.FAILED_CRASH | ResultClass.FAILED_TIMEOUT | ResultClass.FAILED_OOM:
              return "failure"
  ```

- **The `hard_cap` honesty flag** — `ParallelismCap(mechanism, hard_cap)` makes "is this a real worker cap or best-effort?" a typed field, not a comment. The benchmark cannot silently claim a constraint it didn't enforce.
- **Validate once at construction** — Pydantic validators on the on-disk models, dataclass `__post_init__` for internal containers. After construction the instance is known-valid; no repeated defensive checks deeper in the call graph.
- **Immutable data containers** — `@dataclass(frozen=True)` for internal data (as `RawRun` and `ParallelismCap` already are); Pydantic `frozen=True` when validation is in play.
- **Required fields stay required** — don't hedge with `Optional[X] = None` for fields that must be set. `Optional`/`None` is a claim that absence is legitimate (e.g. `timing=None` means "not measured"), not a shortcut for lazy construction.

### Modern Python (3.12)

Use these features when they make the contract clearer. typebench is **fully synchronous** — the async/`TaskGroup`/`except*`/`to_thread`/`httpx` features from the general Python toolbox are deliberately out of scope for the engine spine. Revisit only if async is ever introduced, behind a clearly-named boundary.

- **`StrEnum` (3.11)** — string-valued enums for stable on-disk tokens (used throughout `contracts/taxonomy.py`).
- **`typing.Self` (3.11)** — for methods returning self (builder chains, fluent APIs).
- **`@override` (3.12)** — annotates intentional overrides; pyrefly flags silent divergence when a base method is renamed.
- **PEP 695 type alias (3.12)** — `type Counts = tuple[int | None, int | None]`. Cleaner than `TypeAlias`.
- **PEP 695 generic syntax (3.12)** — `def first[T](items: list[T]) -> T: ...` without a separate `TypeVar`.
- **`match` / `case`** — pattern matching, especially over the `ResultClass` taxonomy.

### Tests and verification

The [test-driven-development](../test-driven-development/SKILL.md) skill owns the TDD process (failing test first; Prove-It for bugs) — follow it; this section only states typebench's test *conventions*.

- Pytest-style, arrange–act–assert.
- **Name tests `test_<unit>_<scenario>_<expected_behavior>`** — reads as a sentence in failure output (e.g. `test_run_command_missing_binary_records_failed_env`).
- **No pytest markers are registered.** Don't invent `unit`/`integration`/`component` markers, and don't add `@pytest.mark.asyncio` (no async). Gate environment-specific tests with `skipif` instead:
  - `@pytest.mark.skipif(shutil.which("hyperfine") is None, ...)` for timing tests.
  - `@pytest.mark.skipif(os.name != "posix", ...)` for signals / process-group tests.
- Test the Typer CLI with `typer.testing.CliRunner`.
- Stub boundary seams (`run_timing`, `shutil.which`) with `monkeypatch.setattr` — mock the external boundary, not the internal behavior you're trying to prove.
- **Drive every taxonomy class deterministically** through `StubAdapter` + `fake_checker` (`typebench._internal.fake_checker`) — no real checker required. The fake checker is controllable, so each `ResultClass` (clean / diagnostics / each failure mode) gets exercised on purpose.
- **Round-trip pydantic models through JSON** and assert `extra="forbid"` rejects unknown fields — the on-disk schema is a stability contract, so prove it both serializes and refuses junk.
- Parameterize when the same contract holds across multiple inputs. Use fixtures for reusable setup, not to hide meaning.
- Do not distort production APIs for tests. Test-only fields, flags, branches, or exports are design regressions unless the runtime contract explicitly needs them.

### Data and state discipline

- **Default shape for internal data containers:** `@dataclass(frozen=True)` (add `slots=True`, `kw_only=True` where it helps). Immutable, safe from positional-arg mistakes. `RawRun` and `ParallelismCap` are the in-repo examples. Add `__post_init__` for validation when needed.
- **For externally-derived / on-disk data** (the result schema, env fingerprint, anything parsed from JSON): Pydantic model with `ConfigDict(extra="forbid")`. Validate at the boundary; downstream code trusts the type.
- **Pydantic v2 idioms** — `model_config = ConfigDict(extra="forbid")` on the class (not the v1 `class Config:` inner class). `field_validator` / `model_validator` for custom rules (not the v1 `@validator`). `Model.model_validate_json(raw)` for parsing (not `parse_raw`). Use `Field(..., ge=0)` for built-in constraints before reaching for a validator (counts are non-negative).
- **Push structural rules into the model.** Allowed-value sets, range limits, and format constraints belong in `field_validator` / `model_validator` on the model — not as repeated `if` checks at every consumer. One validator, every call site safe.
- Keep validation, serialization, and the measurement logic separate enough to test each directly. Crucially: keep pydantic *off* the measured path — `engine/wrapper`, `engine/measure`, `engine/calibration`, and `contracts/taxonomy` produce plain dataclasses/enums; the pydantic `RunResult` is assembled later in the collector.
- Be suspicious of `dict[str, Any]` or raw parsed JSON flowing through many layers — narrow it at the boundary (`coerce_count`).
- Prefer immutable or append-only data flow where shared mutation would make behavior harder to reason about.
- Cache only when measurement justifies it; make scope and invalidation explicit. (Checker caches are the opposite concern: the collector deliberately *clears* them before every timed run so each run is cold.)
- Treat env-var reads, CWD assumptions, and process-global configuration as boundary concerns. `run_command` merges injected `env` over `os.environ` explicitly rather than mutating the global.

### Modules, structure, and packaging

- `src/typebench/` holds the package (src layout, hatchling). The layering, lowest to highest:
  - `contracts/` — stdlib-only on-disk enums (`taxonomy`), plain dataclass schemas (`models`, `proc`, `runconfig`), and the `Adapter` Protocol identity. **Stays free of heavy imports.**
  - `engine/` — `wrapper` (`RawRun`, `run_command`, `classify_default`, the hyperfine per-run command), `measure` (cgroup memory), `calibration`, `timing`, `env`, `collector`. **The measured-path modules (`wrapper`, `measure`, `calibration`, `timing`) stay free of heavy imports.**
  - `adapters/` — real checker adapters (`mypy`, `pyright`, `pyrefly`, `ty`), `base` (the `Adapter` Protocol helpers, `coerce_count`), `stub` (`StubAdapter`), `_support` (`probe_version`, `confirm_clean`), `registry`.
  - `corpus/` — corpus catalog, checker-env management, counting, `envman`.
  - `suite/` — A/B suite ports, preflight, renderer, runner, selection, services.
  - `cli.py` — the Typer app (`typebench run`).
- **The Typer CLI is a wiring layer**: parse args, build the composition root (adapter, run knobs), and hand off to `collector.run_single`. No business logic or module-level state at import time.
- Avoid circular imports by moving shared contracts to a lower layer, not by using late imports as a default escape hatch. The legitimate `TYPE_CHECKING` imports in `adapters/base.py` and `engine/collector.py` exist to keep the *runtime* import graph (and the measured path) clean — follow that pattern.
- Keep CLI, subprocess/timing, schema, and measurement logic separated.
- Prefer shallow directory structures unless there is a real sub-domain split (`adapters/` is one).
- **Prefer modules under ~300 lines.**
- Expose public package APIs deliberately — do not leak internal helpers or Pydantic internals as public contracts by accident.

### Architecture and anti-patterns

- Prefer composition over inheritance unless inheritance matches the real domain model. The `Adapter` Protocol is structural substitution, not a base class to inherit from.
- Wait for repeated pressure before extracting abstractions; avoid registries, factories, or framework indirection that don't buy real clarity.
- Keep subprocess/timing I/O and classification logic separated enough that classification (`classify_default`) can be tested without spawning a process.
- Do not duplicate retry, timeout, or classification behavior at multiple layers. Classification lives in one place per phase; the `universal_failure_prefix` / `classify_with_map` mechanism in `engine/wrapper.py` exists precisely because the wrapper's generic success-exit gate and the adapter's `classify` must not silently disagree — an adapter whose probe-phase `classify` conflicts with the wrapper's gate records contradictory results.
- Do not hard-code environment-specific settings — route through config / the run knobs.
- Do not swallow exceptions, ignore partial failures silently, or hide a recorded failure behind a benign name. Every failure becomes a recorded `ResultClass`.

### Error handling

typebench is small and currently uses `ValueError` at boundaries plus pydantic `ValidationError` for on-disk data — it has not yet grown a domain-exception hierarchy, and doesn't need one for the spine. The principles still apply:

- **Reach for a domain-specific exception class** when an error condition maps to a distinct recovery strategy or CLI exit code. Until then, `ValueError` at boundaries and pydantic `ValidationError` for schema violations are the honest floor — don't over-engineer an exception tree the spine won't use.
- **Chain with `raise ... from e`** to preserve the original cause and traceback.
- **Include actionable context** in the message — the user-visible fact, not `"error"`.
- **Catch specific exceptions.** Avoid `except Exception`. The collector's `except subprocess.CalledProcessError` / `except (OSError, ValueError, KeyError)` blocks are the model: each catches a specific failure class and *records* it into the taxonomy rather than crashing or dropping the run.
- **On the measured path, never raise.** `run_command` converts every failure mode into a `RawRun`. This is the load-bearing exception: a raise here would drop a measurement.

```python
# The shape that matters here: a boundary failure becomes a recorded result,
# not an exception that escapes and drops the run.
try:
    timing = run_timing(argv, ...)
except subprocess.CalledProcessError as exc:
    result_class = ResultClass.FAILED_CRASH
    failure_phase = FailurePhase.TIMING
    timing_error = (exc.stderr or "").strip()[-500:] or "timing run failed under hyperfine"
```

---

## Decision heuristics

Use these when the right choice is not obvious:

- **Scope check** — if a change touches more than 3 modules or public entrypoints, stop and plan before continuing; the change is bigger than it looks. Also check it against the AGENTS.md "Ask first" boundaries (on-disk schema, quality gates, runtime deps, heavy imports on the measured path).
- **Measured-path check** — if the change touches `engine/wrapper`, `engine/measure`, `engine/calibration`, or `contracts/taxonomy`, ask "does this add an import?" A heavy import on the measured path is an "Ask first" boundary because it biases the numbers.
- **Function size** — keep functions ≤40–50 lines. Longer usually means mixed responsibilities.
- **Nesting depth** — if nesting exceeds 3 levels, use early returns or extract a helper.
- **Parameter count** — public function with more than 5 meaningful parameters? Introduce a dataclass/Pydantic model or split. (ruff's pylint `max-args` is 8; treat >2–3 as a yellow flag.)
- **State visibility** — if mutation or side effects are hard to see from the signature, redesign the interface.
- **Typing pressure** — if `Any` or untyped dicts start spreading, introduce a clearer type boundary (the `coerce_count` move) before adding more code.
- **Schema pressure** — if a change adds or renames a `RunResult` field or a taxonomy string value, stop: that's the on-disk stability contract ("Ask first").
- **I/O boundary pressure** — if an external boundary (subprocess, hyperfine, a missing binary) can fail, time out, or vanish, make sure it becomes a *recorded* outcome, not an escaping exception.
- **Narrowness vs. quality** — narrowest change that solves the problem. When narrowness conflicts with correctness or measurement honesty, prefer correctness. When it conflicts with style alone, prefer narrowness unless the task is explicit cleanup.
- **Refactor boundary** — outside explicit refactor work, fix at most one small adjacent issue while you are in the file.
- **Abstraction threshold** — three similar code blocks or repeated data-shaping pain is a pattern; check whether a helper function, named type, or boundary cleanup is the simpler move before extracting.
- **Performance rule** — typebench *measures* performance but its own code optimizes only after measurement, except for obvious algorithmic/allocation/I/O mistakes — and except for the measured-path import budget, which is a correctness concern, not a micro-optimization.

---

## Anti-patterns (with why)

Four classics that keep appearing. The *why* matters more than the rule:

```python
# BAD: mutable default argument
def f(items=[]):        # WHY: one list shared across every call —
    items.append(1)     # surprise cross-call state; classic footgun
    return items

# GOOD: default None, construct inside
def f(items: list[int] | None = None) -> list[int]:
    items = list(items) if items is not None else []
    items.append(1)
    return items
```

```python
# BAD: bare except
try:
    risky()
except:                 # WHY: catches SystemExit and KeyboardInterrupt,
    pass                # hides bugs, prevents graceful shutdown

# GOOD: specific exception — and here, record it instead of dropping it
try:
    timing = run_timing(argv, ...)
except subprocess.CalledProcessError:
    result_class = ResultClass.FAILED_CRASH  # never drop a measurement
```

```python
# BAD: string path manipulation
path = "/var/data/" + project + "/" + filename
# WHY: breaks cross-platform (separators), vulnerable to traversal,
# no automatic normalization

# GOOD: pathlib
path = Path("/var/data") / project / filename
```

```python
# BAD: subprocess shell=True with interpolation
subprocess.run(f"check {user_input}", shell=True)
# WHY: command injection — user_input="; rm -rf /" executes

# GOOD: list form (shell=False is the default); shlex.join only when a
# string is genuinely required (e.g. hyperfine's command argument)
subprocess.Popen(["mypy", project], ...)
hyperfine_cmd = shlex.join(["mypy", project])
```

---

## Validation

A change is done when:

- `uv run ruff format` leaves the tree unchanged (or was run).
- `uv run ruff check` passes with no new findings.
- `uv run pyrefly check` passes with no new findings (strict preset; typebench dogfoods this).
- `uv run pytest` passes.
- New behavior has test coverage driven deterministically through `StubAdapter` + `fake_checker` (`typebench._internal.fake_checker`) (or the lack of coverage is called out with a concrete reason), and any on-disk schema change round-trips through JSON with `extra="forbid"` asserted.
- No new `# pyrefly: ignore[<kind>]` without a reason comment; legacy `# type: ignore` is not accepted.
- No import-time side effects added — and no new heavy import on the measured path (`engine/wrapper`, `engine/measure`, `engine/calibration`, `contracts/taxonomy`).
- No new `Any` in public signatures, no broad `object` where a union or named alias would state the contract.
- The schema never claims an un-run methodology (`thread_mode_enforced` reflects actual affinity application, `FailurePhase` correctness) and no failure path can drop a run.
- On-disk schema and taxonomy string values are unchanged, or the change was raised as an "Ask first" decision.
- Review findings at `Critical` and `Important` severity are addressed.
- Commit message explains the *why* and uses a required scope (see the [git-conventions](../git-conventions/SKILL.md) skill).

---

## Examples

**Good task shapes:**

- `Add a real exit-code map for a checker adapter in src/typebench/adapters, keeping the wrapper's measured path import-light and tests green`
- `Refactor classify_default into a precedence-ordered table without changing the recorded ResultClass for any RawRun`
- `Review this collector change for correctness — does every failure path still record a RunResult instead of dropping it?`

**Shape of a small, well-formed change:**

- One narrow concern; one scope in the commit.
- Types tightened or unchanged, never loosened.
- No new `Any`, no new untyped seams, no new `# type: ignore`.
- No import-time side effects, and no new heavy import on the measured path.
- Tests updated or added in the same commit, driven through the stub + fake checker.
- The four gates clean (`ruff format`, `ruff check`, `pyrefly check`, `pytest`).
- Commit message explains the *why*, with a required scope.
