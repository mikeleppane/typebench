---
name: code-review-and-quality
description: Multi-axis code review for the typebench repo before merge — your own code, another agent's, or a teammate's PR. Adds benchmark-aware checks for measurement fidelity, record honesty, and failure completeness on top of the standard correctness/readability/architecture/security/performance review. Use whenever you are about to merge, or when asked "is this ready?", "review this", "check this change", "look this over". Reviewing AI-generated code is a stronger trigger, not a weaker one — false confidence is the dominant failure mode.
---

# Code Review & Quality (typebench)

Multi-axis code review for the typebench repo. typebench is a neutral, reproducible benchmark of Python type-checker performance (mypy, pyright, pyrefly, ty); **the only product is trust in the numbers.** The output of this skill is a **structured Markdown review report** with findings grouped by severity, each carrying a `file:line` reference and a quoted snippet, followed by a clear verdict.

This skill is for the *review* moment. Companion skills cover the *production* moments and they take precedence on the rules they own:

- [coding-guidance-python](../coding-guidance-python/SKILL.md) — Python implementation contract (security, typing, illegal-states-unrepresentable, error handling, module boundaries). When a finding is "this code violates the Python contract", *cite this skill* — don't restate its rules.
- [test-driven-development](../test-driven-development/SKILL.md) — failing-test-first and Prove-It for bugs. Cite when a change adds behavior without a test that would have failed first.
- [git-conventions](../git-conventions/SKILL.md) — commit message format, required scope, atomic commits, and the no-AI-attribution rule. When a finding is about commit hygiene or PR shape, cite this.

**Spot-checks vs. rule restatement.** The five axes below contain triage prompts of the form "did you check X?". Those are deliberate — they name *what* to look for during the walk-through. They are not the rule itself. When you file a finding, cite the source skill or AGENTS.md section rather than restating the rule content in the review; the source is authoritative and this skill cannot stay in sync with it forever.

Project-specific rules live in [AGENTS.md](../../../AGENTS.md) and override anything in this skill. The benchmark-aware checks below are this skill's highest-value contribution — a measurement tool has failure modes a generic reviewer misses.

---

## The approval standard

**Approve a change when it definitely improves overall code health, even if it isn't perfect.** Perfect code does not exist; the goal is continuous improvement. Don't block a change because it isn't exactly how you'd have written it. If it improves the codebase and follows the project's conventions, approve it — and file the cleanup ideas you spotted as `Suggestion` or as a separate issue.

The corollary: **don't rubber-stamp.** "LGTM" without evidence of actual review helps no one and trains the team to skip the review step. Every approval should be backed by something concrete — a command you ran, an axis you walked through, a deliberate "I checked X and Y, both clean."

For this repo there is one extra approval gate: **a change that can bias the numbers is never "good enough".** Measurement-fidelity, record-honesty, and failure-completeness defects (see *Benchmark-aware review checks*) block merge regardless of how clean the rest of the diff is.

---

## When to use this skill

- Before merging any PR or change.
- After completing a feature implementation, before declaring it done.
- When another agent or model produced code you need to evaluate.
- After any bug fix — review both the fix and the regression test.
- When a teammate asks "is this ready?", "look this over", "anything I missed?".

## When *not* to use it

- Trivial one-line changes (typo, import order, formatter churn) — the review is one line too. Skip the report format and just say "looks good" or "nope, X is wrong".
- A spec is missing and you don't yet know what the code is *supposed* to do — that's a spec problem, not a review problem. Reviewing code against an unstated intent produces opinion, not findings. The design spec (`docs/superpowers/specs/`) and the per-plan plan docs are the intent of record here.
- The change is part of an in-progress branch the author has explicitly marked as WIP. Wait until they're done.

---

## Severity vocabulary

Every finding gets a severity label. This is what makes a review actionable instead of an opinion blob. The labels match what `coding-guidance-python` already uses, with two additions from common review practice:

| Label | Meaning | Author Action |
|---|---|---|
| `Critical` | Blocks merge. Will crash the run, drop or fabricate a benchmark record, bias the measured interval, leak data, claim an unrun methodology, break a stable on-disk contract *without* a documented migration path, or violate AGENTS.md "do not violate" invariants or "Never" rules. | Must fix before merge. |
| `Important` | Should fix before merge. Bug on a less-likely path, AGENTS.md "Ask first" boundary crossed without checking, design issue that will compound, scope creep past the current plan. | Fix or explicitly defer with reason recorded. |
| `Suggestion` | Would improve the change. Refactor, clarification, missing test for an edge case. | Worth doing; reviewer doesn't block on it. |
| `Nit` | Optional polish. Naming, formatting (where the formatter doesn't already enforce), micro-style. | Author may ignore. Use sparingly — too many nits drown the real findings. |
| `FYI` | Informational. Context for future readers, related bug to file, observation. | No action needed. |

**Rule:** if a finding has no severity label, the author has to guess what's required. That makes the review unusable. Label every finding.

---

## The review process

Walk these five steps in order. Don't jump straight to "let me look at the code" — half the value of a review comes from steps 1 and 2.

### Step 1 — Understand the intent

Before reading code, find the answer to:

- What is this change trying to accomplish? (commit message, PR description, the design spec, the plan doc)
- What was the failing behavior, or what new behavior is being added?
- **Which plan does this belong to?** typebench is plan-staged (Plan 1 = engine spine + stub adapter; real checkers, cgroup memory, corpus pinning, renderer land later). Work that reaches past the current plan is a scope finding.
- Which AGENTS.md "Ask first" boundaries does it touch?

If you can't answer these from the artifacts the author provided, the change description is incomplete — that's the first finding (`Important: change description doesn't say what it does`). A reviewer who has to reconstruct intent from the diff is a reviewer who will miss things.

### Step 2 — Read the tests first

Tests reveal intent, coverage, and the author's mental model. They also tell you whether you can trust the implementation walk-through.

- Do tests exist for the change?
- Do they test *behavior* (what the code is supposed to do) rather than *implementation* (which functions get called)? Implementation tests calcify the code and offer a false sense of safety.
- Are edge cases covered (empty input, boundary values, error paths, every failure taxonomy class)?
- Do test names describe the scenario? AGENTS.md names `test_<unit>_<scenario>_<expected_behavior>` as the target style (e.g. `test_run_command_missing_binary_records_failed_env`) because it reads as a sentence in failure output. If the project hasn't adopted this in some corner yet, file at most a `Suggestion`, not an `Important`.
- **Are environment-specific tests gated?** No pytest markers are registered here. A test that needs the timing harness must carry `@pytest.mark.skipif(shutil.which("hyperfine") is None, ...)`; a test that exercises signals / process groups must carry `@pytest.mark.skipif(os.name != "posix", ...)`. An ungated test that silently passes (or hangs) where the binary/platform is absent is `Important`, often `Critical` in CI.
- Are taxonomy classes driven **deterministically through `StubAdapter` + `_fake_checker`** rather than against a real checker? A test that shells out to mypy/pyright/ty to assert a failure class is non-reproducible and out of plan scope — `Important`.
- Do on-disk models round-trip through JSON and assert `extra="forbid"`? A schema change without a round-trip + extra-forbidden test is `Important` (the on-disk contract is unverified).
- Would the tests catch a regression if someone changed the implementation tomorrow? (Mutation-test the test mentally — if you flipped the implementation, would the assertion catch it?)
- No production API, field, flag, branch, or export was added only to make a test or assertion possible.

### Step 3 — Walk the implementation through the five axes

Hold all five axes in mind for each file. Don't do five passes — do one pass with five lenses. The axes are:

1. **Correctness** — does it do what it claims?
2. **Readability & simplicity** — can a future agent or human understand it cold?
3. **Architecture** — does it fit the system's design and the layer boundaries?
4. **Security** — does it expose anything new?
5. **Performance** — does it introduce a bottleneck on a hot path?

Then walk the **Benchmark-aware review checks** — the typebench-specific axis that protects the numbers. Detail for each is below.

### Step 4 — Categorize findings

For every finding, attach:

- **Severity** — `Critical` / `Important` / `Suggestion` / `Nit` / `FYI`
- **File and line** — `src/typebench/collector.py:60`
- **Snippet** — the actual code, copied verbatim. If you can't quote it, you don't have a finding.
- **Problem** — 1–3 sentences in plain English. What is wrong and why.
- **Fix** — concrete replacement code or, if the fix is conceptual, the end state.

A finding without a quoted snippet is suspicious — it usually means the reviewer is recalling a pattern instead of reading the diff. Don't trust your memory of the file; quote the line.

### Step 5 — Verify the verification story

Check what the author actually ran:

- Did the quality gate pass, in order (floor per AGENTS.md)?
  - `uv run ruff format`
  - `uv run ruff check`
  - `uv run pyrefly check` (preset `strict`)
  - `uv run pytest`
  Ruff and pyrefly findings must be at **zero**.
- For schema changes — does a representative `RunResult` round-trip to JSON and back, and does an unknown on-disk field fail (`extra="forbid"`)?
- For wrapper / measured-path changes — was the import cost of the change considered? (See *Measurement fidelity*.)
- For failure-path changes — was every taxonomy class actually produced by a test, not just reasoned about?
- For CLI behavior — was `typebench run` actually invoked (`typer.testing.CliRunner`), or only type-checked?

A green gate is necessary, not sufficient. Type-check passing is not the same as the benchmark being honest.

---

## Benchmark-aware review checks

**This is the section that matters most.** typebench is a measurement instrument; a change can pass every generic axis and still silently corrupt the product, which is *trust in the numbers*. Walk these on every change that touches the measured path, the schema, or a failure path. A defect here is `Critical` by default — it doesn't crash, it lies.

### 1. Measurement fidelity — does the change bias the interval?

The wrapper (`src/typebench/wrapper.py`) is hyperfine's per-run command: **everything it imports runs on every single timed measurement.** A heavy import there (pydantic, or anything that transitively pulls it in) adds a constant per-run startup cost that biases comparative ratios between tools.

- Does any change put a heavy import on the measured path? The wrapper must import enums from `taxonomy.py` (pydantic-free, stdlib-only), **never from `models.py`** (which imports pydantic). The same applies to `taxonomy.py` itself — it must stay stdlib-only. A `from typebench.models import ...` in the wrapper is `Critical`.
- Does the change add fixed startup cost, output capture-and-rewrite of large buffers, extra shell parsing, or any work to the measured interval that lands *unevenly* across tools? Constant overhead biases ratios even when it looks "small". Flag it.
- Is timing measurement still delegated to hyperfine (wall-time) rather than reimplemented in-process? Hand-rolled timing inside the measured command is both less accurate and a fidelity risk.

### 2. Honesty of the record — does the schema claim a methodology that wasn't run?

The schema must never assert something the engine didn't actually do.

- `thread_mode_enforced` must stay `False` until CPU affinity / a hard cap is actually applied (Plan 4). A change that flips it `True` without the enforcement mechanism is `Critical` — it claims an unrun methodology.
- A failure record must not imply success metadata. If `result_class` is a `failed{...}` class, `failure_phase` must mark *which pass* failed (`PROBE` vs `TIMING`) so `real_exit_code` can't be misread as a clean command with a failed result. A `failed{crash}` next to a clean-looking `real_exit_code` with no `failure_phase` marker is `Critical`.
- New on-disk fields must round-trip through JSON and respect `extra="forbid"`. A field that serializes but doesn't deserialize (or vice versa), or that bypasses `extra="forbid"`, breaks the on-disk contract. Schema/taxonomy-string changes are an AGENTS.md "Ask first" boundary — surface it.

### 3. Failure completeness — is every failure recorded, never dropped?

A dropped or crashed failure silently biases the benchmark by removing a data point. The §7 taxonomy (`clean`, `diagnostics`, `failed{env|crash|timeout|oom}`) is the contract; every path must land in it.

- `run_command` **must never raise.** Timeout, signal death, and a missing/non-executable binary all become a recorded `RawRun` (`env_error=True` for the OSError case). A new code path in the wrapper that can propagate an exception out of `run_command` is `Critical`.
- The collector must catch harness failures and convert them to a record, never let them escape: `subprocess.CalledProcessError` from a flaky timed run → `failed{crash}` with `failure_phase=TIMING`; `OSError`/`ValueError`/`KeyError` from the timing harness (garbled hyperfine JSON, vanished export file, TOCTOU on `shutil.which`) → `failed{env}`. A new exception type from the timing path that isn't caught and recorded is `Critical`.
- A `parse()` that can raise on unexpected checker output, rather than degrading, drops the record's counts — see the Adapter contract check below.

### 4. Subprocess safety

- List-form `subprocess` only, **never `shell=True`.** A `shell=True` is `Critical` (AGENTS.md "Never").
- Any command string handed to hyperfine must go through `shlex.join` — never naive string concatenation of argv. Unquoted user/project paths into a hyperfine command string is `Critical`.
- Timeouts must kill the **whole process group**, not just the direct child (`start_new_session` + `os.killpg`), or grandchild stragglers steal CPU from later runs and contaminate them. A change that swaps `_terminate_tree` for a plain `proc.kill()` on POSIX is `Critical` (benchmark isolation).

### 5. Scope by plan

- Is this change staying inside the current plan? Plan 1 is the engine spine + stub adapter. Real-checker adapters, cgroup memory, corpus pinning, and the renderer belong to later plans. Spine work that starts building real-checker / cgroup / corpus / renderer behavior is `Important: out of plan scope` (often the right move is to split it out).
- Are the deferred Adapter Protocol methods (`install`, `parallelism_cap`) left as documented-but-unbuilt placeholders rather than half-implemented? Don't delete them; don't grow behavior behind them early. A half-built deferred method is `Important`.

### 6. Adapter contract

- New adapters must conform to the `Adapter` Protocol (`@runtime_checkable`, `src/typebench/adapters/base.py`) — `name`, `version`, `install`, `command`, `parallelism_cap`, `parse`, `classify`, `clear_cache`, `prepare_command`. A missing or mistyped method is `Critical` (it breaks the collector's pipeline).
- `parse()` must **degrade to `(None, None)` on unexpected output** rather than raising or guessing, and must coerce counts through `coerce_count` (which rejects bools and non-ints) rather than leaking a garbage value (or a JSON `true`) into the record as a count. A `parse()` that lets unvalidated parsed-JSON flow into `diagnostics`/`files` is `Important`.
- `classify()` may delegate to `default_classify`/`classify_default` for the generic `{0: clean, 1: diagnostics}` map, but a real tool whose diagnostics exit code is not 1 (e.g. ty) needs its own classify *and* the wrapper's success-exit gate must agree (see the "PLAN 2 TRAP" note in `wrapper.py`). A new adapter whose probe-phase classify and wrapper-gate classify disagree is `Critical` — the two phases will record conflicting results.

---

## The five axes

Each axis lists project-specific failure modes worth scanning for. These are *prompts*, not exhaustive checklists — the actual finding has to come from reading the code, not pattern-matching against the list.

When in doubt, prefer to cite `coding-guidance-python` and AGENTS.md rather than restate convention details inline — the source files own the rules and stay current.

### 1. Correctness — does the code do what it claims?

- Does it match the spec, plan, or task description?
- Are edge cases handled — empty input, missing keys, `None`, boundary values?
- Are error paths handled, not just the happy path? (For typebench, "error path" usually means "a failure taxonomy class" — see *Failure completeness*.)
- Off-by-one errors, state inconsistencies, mutation hidden in `get_*` / `parse_*` / `run_*` names?
- Exception context preserved with `raise NewError(...) from original`?
- Specific exception types caught, not bare `except Exception` outside a deliberate top-level boundary? (The collector catches *specific* harness exceptions on purpose; widening that to bare `Exception` would hide bugs as records.)
- For Pydantic models: `ConfigDict(extra="forbid")` so unknown on-disk fields fail fast at runtime *and* type-check?
- For the **classifier**: does the precedence order (env-error → oom → timeout → SIGKILL-as-oom heuristic → other-signal-as-crash → exit-code table) still hold after the change? A reorder changes which failure class a run gets recorded as.
- For new tests: do they actually fail when the code is broken?

### 2. Readability & simplicity — can it be understood without explanation?

- Are names descriptive and consistent with surrounding files? (No bare `temp`, `data`, `result` without context.)
- Is control flow straightforward? Early returns over deep nesting (>3 levels is the line).
- Function size — anything over ~40–50 lines is usually doing more than one thing.
- Parameter count — ruff caps `max-args` at 8 here; anything approaching that wants a dataclass / Pydantic model or a split.
- **Could this be done in fewer lines?** 1000 lines where 100 would suffice is a failure. But don't *force* compression for its own sake — clarity wins over brevity.
- **Are abstractions earning their complexity?** Don't generalize until the third use case. A `Protocol` with one implementation is premature — *except* where AGENTS.md deliberately pins a final-ish shape early (the Adapter Protocol). Know the difference before filing.
- **Is this extra surface area justified?** Unused option flags, speculative compatibility shims, generalized helpers with one caller, future-proof extension points — review findings when they make the next change harder. `Important` when they complicate reasoning or the on-disk/Protocol contracts; otherwise `Suggestion`.
- Are the comments doing real work? The codebase uses load-bearing "why" comments heavily (the "PLAN 2 TRAP", the precedence notes, the killpg rationale). Deleting one of those is a finding; adding a comment that merely restates well-named code is noise.
- Dead code artifacts — no-op variables, leftover shims, `# removed` comments? No commented-out code (AGENTS.md: `git log` remembers).

### 3. Architecture — does it fit the system?

- Does it respect the **pydantic-free boundary**? `taxonomy.py` and `wrapper.py` are on the measured path and must not import `models.py` / pydantic. A cross-boundary import there is `Critical` (see *Measurement fidelity*).
- Does the **schema live in one place** (`models.py` / `taxonomy.py`)? Reads of on-disk fields scattered across modules instead of going through the typed model are a smell.
- Does the **Adapter Protocol stay the only checker-specific surface**? Checker-specific branching leaking into the collector / wrapper / timing instead of into an adapter is an architecture finding.
- Does the **CLI stay a wiring layer** — Typer parsing, composition root, hand-off to `collector` / `timing` library code? Business logic creeping into `cli.py` is a smell.
- Does it follow existing patterns or invent a new one? A new "Manager" / "Service" / "Handler" suffix where the surrounding code uses named domain types (`RawRun`, `RunResult`, `Adapter`) is a smell.
- Are there shallow modules that should be deepened? (Interface nearly as complex as the implementation.)
- Is `dict[str, Any]`, `dict[str, object]`, or raw parsed JSON flowing through multiple layers? At the boundary, define a `TypedDict` / dataclass / Pydantic model so the contract is named. (`coerce_count` is the pattern for taming raw parsed-JSON counts — reuse it.)
- Is `object` used where the possible shapes are already known? Prefer a union or local `type` statement. Keep `object` for opaque inputs that are immediately narrowed (e.g. `coerce_count(value: object)`).
- Are the package's `__init__` / re-export surfaces growing into accidental public API? `models.py` re-exports the taxonomy enums *deliberately* (stable import path); new re-exports need the same justification.

### 4. Security — does it expose anything new?

`coding-guidance-python` "Security — priority 1" owns the rule set. This skill does not restate it. During the review walk, hold these triage prompts in mind and *cite the source* when you file:

- Any **`shell=True`**, **`eval`/`exec`/`pickle` on untrusted data**, **hardcoded secrets**, or **SQL/command string concatenation**? These are AGENTS.md "Never" / `coding-guidance-python` Security violations and file as `Critical` citing the source. (`shell=True` is also a benchmark-safety issue — see *Subprocess safety*.)
- Any **`assert` in production code** (anything outside `tests/`)? `assert` disappears under `python -O`; that's a `Critical`.
- Any **untrusted/external input crossing a boundary unvalidated** — checker stdout/stderr, hyperfine JSON, project paths, env vars? File a `Critical`/`Important` finding and point at the missing Pydantic model / `coerce_count` / path-resolve check in `coding-guidance-python` Security. (typebench has no secrets domain; there is no secret-redaction rule to apply — but "validate external input before trusting it" still applies fully to checker output and hyperfine's JSON.)
- Any **internal exception type leaking across a public boundary**, forcing callers to know the implementation? `Important` — translate into a domain exception per `coding-guidance-python` "Error handling". (Note: inside the collector, harness exceptions are *deliberately* caught and turned into records — that's the contract, not a leak.)

If a security finding is well-known (those above), cite `coding-guidance-python` rather than restate the rule. If it's a novel concern not covered there, file the finding *and* surface it as a candidate addition to `coding-guidance-python`'s Security section.

### 5. Performance — any bottleneck?

Performance findings only fire when the cost is *real and measurable*. "Could be slow" without numbers is speculation, not a finding. typebench is fully synchronous — there is no event loop to block — so the performance lens is dominated by **measured-path overhead** (see *Measurement fidelity*) and the usual hot-loop traps:

- **Heavy import on the measured path** — by far the most important perf concern here, because it biases the product, not just the tooling. Covered under *Measurement fidelity*; file there as `Critical`, not as a soft perf `Suggestion`.
- **Large objects allocated in hot paths** — re-creating Pydantic models, recompiling regexes, re-reading configuration per call instead of once. Relevant if a change moves such work into the per-run command.
- **N+1 subprocess spawns** — one process launch per item where one batched invocation would do, on a path that runs many times.
- **Capture-and-rewrite of large output buffers** on the measured path — adds non-trivial per-run cost. Cross-reference *Measurement fidelity*.

If you can't say roughly *how much* slower the current code is than the proposed fix (and, for the measured path, *how it biases the ratio*), demote a generic perf finding to `Suggestion` or `FYI`. Optimize after measurement is a project rule for a reason — but measured-path bias is the exception: it is a correctness-of-the-product issue, not a speed nicety.

---

## What makes a finding good (and what makes one bad)

The single most useful filter:

> **Would this finding cost the team something — or cost the *numbers* their credibility — the next time they touch this code?**

If the answer is no, the finding is noise. Even if it's *technically correct*. The cost of a noisy review is high — authors learn to skim past findings, and real issues hide in the noise.

### Bad finding example 1 — technically correct, zero impact

```
Suggestion: Use list comprehension
src/typebench/timing.py:88

  result = []
  for item in items:
      result.append(transform(item))

→ result = [transform(item) for item in items]
```

Technically correct. List comprehensions are idiomatic. But the loop is clear and "comprehension" doesn't prevent any future bug. The author will skim past, and one more `Suggestion` like this trains them to skim past the real ones. **Don't file it.**

### Bad finding example 2 — speculation stated as fact

```
Important: Possible crash if hyperfine JSON is malformed
src/typebench/collector.py:48

run_timing parses hyperfine's JSON. If the JSON is garbled this will blow up
and crash the whole benchmark run.
```

Looks like a real finding — names a function, names a scenario. But the reviewer hasn't read the code: the collector *already* catches `OSError`/`ValueError`/`KeyError` from the timing path and records `failed{env}`. The whole finding rests on an unverified assumption that the path is uncaught. **A hypothesis stated as a finding is a false positive.** Either trace the handling and prove the gap, or don't file it.

### Bad finding example 3 — proposes inconsistency

```
Suggestion: Use match/case for the classifier dispatch
src/typebench/wrapper.py:118

The if-chain in classify_default would be cleaner as a match statement.
```

The classifier is an ordered precedence chain (env → oom → timeout → …) where *order is the logic*; the early-return `if` ladder expresses that precedence directly and the surrounding code uses the same style. Proposing `match` introduces inconsistency for no clarity gain. **Match the file's existing patterns.**

### Bad finding example 4 — applies a public-API rule to a private helper

```
Important: Missing docstring
src/typebench/wrapper.py:32

def _terminate_tree(proc: subprocess.Popen[str]) -> None:
```

`_terminate_tree` is private (underscore prefix) — and in this case it *already* carries a load-bearing why-comment. Demanding a formal public-style docstring here is misapplying the rule.

### Good finding example

Following the per-finding format defined in *Output format* below:

````markdown
**Critical: Wrapper imports from models.py, putting pydantic on the measured path**
`src/typebench/wrapper.py:14`

```python
from typebench.models import ResultClass
```

`wrapper.py` is hyperfine's per-run command, so every import here runs on *every* timed measurement. `models.py` imports pydantic (~50ms startup); pulling it onto the measured path adds a constant per-run overhead that biases comparative ratios between tools. The enums live in the pydantic-free `taxonomy.py` precisely so the wrapper can import them cheaply. See AGENTS.md "Measurement fidelity".

**Fix:**

```python
from typebench.taxonomy import ResultClass
```
````

Quoted snippet. Names the specific invariant and cites AGENTS.md. Explains the consequence to the *numbers*. Points at the existing pattern. **This is the shape every finding should aim for.**

---

## Budget

**The cap exists to suppress noise, not to suppress defects.**

- `Critical` and `Important` findings are **uncapped**. File every one. They are real defects; suppressing them defeats the purpose of the review. Benchmark-aware defects in particular are never dropped.
- `Suggestion` + `Nit` combined are capped at **5–7 per review**. Past that, you're spending the author's attention on polish at the expense of the substance. Drop the marginal items or save them for a follow-up issue.
- `FYI` is uncapped but use it sparingly — every entry costs reading time.

**If a single change genuinely warrants more than ~5 Important findings, the change is too dense to merge safely.** The right response is not to drop findings, it is to file one top-level finding: *"this change is too large / too entangled to review properly — split it into reviewable slices"* — see [git-conventions](../git-conventions/SKILL.md). Then address the splits in follow-up reviews.

---

## Strengths section

End the review with up to 3 short positive observations. One sentence each, ≤120 chars.

```
What's good
  • wrapper.py imports only taxonomy.py — measured path stays pydantic-free
  • Every timing-harness exception is caught and recorded; no failure can drop a record
  • New schema field round-trips through JSON and a test asserts extra="forbid"
```

This isn't sycophancy. Naming what works:

- **Reinforces patterns** the author should keep using.
- **Calibrates** the harshness of the rest of the review — a critical bug-find lands differently when the rest of the change is acknowledged as solid.
- **Helps future readers** of the review history understand what the codebase considers "good", which is useful when AGENTS.md doesn't yet say.

Skip the section if you genuinely have nothing positive — fabricated strengths read worse than none. But before you skip, look again — a measured-path import kept clean, a failure path that records instead of crashing, a good commit message, deleting more code than it adds, are all worth a line.

---

## Reviewing AI-generated code

This is the dominant case in this repo. Treat it as a *stronger* trigger for this skill, not a weaker one. The failure modes:

- **False confidence.** AI-generated code reads as authoritative. It uses the right vocabulary, follows the right shape, looks plausible. *Plausible-looking code that's subtly wrong is the dominant defect* — and in a measurement tool, "subtly wrong" often means "biases the numbers without crashing".
- **Pattern transplant.** The model may import a pattern from another codebase that doesn't match this project — `os.path` instead of `pathlib.Path`, a convenience `from typebench.models import ...` in the wrapper that breaks the pydantic-free boundary, `shell=True` for "simplicity", a bare `except Exception` that swallows a record.
- **Hallucinated APIs.** Method names that look right but don't exist, kwargs the library doesn't accept, fields the Pydantic model doesn't have, Adapter Protocol methods with the wrong signature. Always verify import paths and signatures against the actual file.
- **Test theater.** Tests that assert what the implementation *does*, not what the contract *should* be. They pass, prove nothing, and lock the implementation in place.
- **Record/honesty theater.** A change that makes the schema *look* complete — flipping `thread_mode_enforced` to `True`, or filling a failure record with success-looking metadata — without the underlying methodology. This is the highest-stakes AI failure mode here: it produces a confident, plausible, *dishonest* record. Treat with maximum suspicion.
- **Extra-mile features.** Code that solves more than was asked, or reaches into a later plan. Unused options, premature config flags, half-built deferred Adapter methods, defensive code for impossible inputs. Pull these out — future maintenance cost for no current benefit, and a scope violation.
- **AI attribution smuggled into commits.** AGENTS.md and `git-conventions` are explicit: no AI/assistant attribution, co-author trailers, or marketing footers in commit messages or PR bodies — commits read as the author's own work. Scan the branch. `Critical` when they appear.

When the author *is* an AI agent, you have a special obligation: nobody else is going to push back. **Be more direct, not less.** Polite hedging — "this might be worth considering" — gets the wrong things merged. State problems plainly, with evidence, and ask for the fix.

---

## Change sizing & splitting

| Size | Verdict |
|---|---|
| ~100 lines | Easy to review in one sitting. Aim for this. |
| ~300 lines | Acceptable for a single logical change. |
| ~1000+ lines | Too large. Push back: ask the author to split. |

If a change is too large, the right *first* finding is: "this needs to be split before review", and the rest of the review can wait. Trying to review a 1500-line change properly is the path to LGTM-ing real bugs.

Splitting strategies (from `git-conventions`):

| Strategy | When |
|---|---|
| **Stack** | Submit a small change, base the next on it. Sequential dependencies. |
| **By file group** | Different reviewers for different concerns. |
| **Horizontal** | Shared types / `Protocol` first, consumers next. Layered changes. |
| **Vertical** | Smaller end-to-end slices of the feature. Most feature work. |

**Separate refactors from feature work.** A change that refactors *and* adds new behavior is two changes — file `Important: split refactor from feature` and ask for it. **Separate one plan's work from the next** for the same reason.

---

## Dependency review

AGENTS.md treats new runtime dependencies as an *Ask first* boundary. When a change adds anything to `pyproject.toml [project] dependencies`, file the boundary check explicitly:

- `Important: New runtime dependency added — was this asked about?` (Even if the answer is yes, the answer needs to appear in the PR description.)
- Is the dependency actively maintained? Last release date, open-issue count.
- License compatible?
- **Does it land on the measured path?** A dependency whose import touches `wrapper.py` / `taxonomy.py` biases every timed run — that's a `Critical` fidelity issue, not just an "ask first" one. The runtime deps today are `pydantic` and `typer`, and pydantic is deliberately kept *off* the measured path.
- Does the existing stack solve this? (`pydantic` for models, `typer` for the CLI, stdlib `subprocess`/`shlex` for process control, `hyperfine` for timing. The answer is almost always *use what's already here*.)

**Rule from `coding-guidance-python`:** prefer standard library and existing project utilities over new dependencies. Every dependency is a liability — and on the measured path, a measurement bias.

---

## Dead code hygiene

Refactors and feature changes often leave orphaned code. After the implementation walk-through, scan for:

- Functions and classes no longer called.
- Pydantic fields no longer read or written.
- Taxonomy values / enum members no longer referenced.
- Test fixtures no longer used.
- Constants whose only callers are deleted.

**Don't silently delete.** What looks orphaned to you may be in-progress work the author hasn't wired up yet, a deliberately-deferred Adapter Protocol method (`install`, `parallelism_cap`), or part of a planned next slice — AGENTS.md says *don't delete the deferred surface*. File a finding listing what *appears* unused and ask:

```
FYI: Apparent dead code after this change

  - _parse_legacy_summary() in src/typebench/timing.py — no remaining callers
  - LEGACY_PHASE constant in src/typebench/taxonomy.py — no references
  - test_legacy_timing() in tests/test_timing.py — covers a deleted path

Is removing these in scope for this change, or part of a follow-up? (Confirm none
are deliberately-deferred Adapter surface before deleting.)
```

This both prevents missed cleanup and respects scope discipline (`coding-guidance-python` "narrowest change that solves the problem").

---

## Output format — the review report

Render the review as Markdown using this exact template. Stable structure means the author can scan it predictably and the report copy-pastes cleanly into a PR comment.

````markdown
# Review — <PR title or short change description>

## Summary

<2–4 sentences: what the change does, what state it's in, headline verdict.>

## Verdict

<one of: Approve / Approve with suggestions / Request changes / Needs split>

<one-line rationale>

## Findings

### Critical

<each finding as: severity + title, then file:line, snippet block, problem, fix.
Omit the section heading if there are no findings at this severity.>

### Important

...

### Suggestion

...

### Nit

...

### FYI

...

## What's good

- <0–3 short positive observations, one line each>

## Verification

- [ ] Tests added / updated; env-specific tests gated with the right `skipif`
- [ ] `uv run ruff format` clean
- [ ] `uv run ruff check` passes (zero findings)
- [ ] `uv run pyrefly check` passes (strict, zero errors)
- [ ] `uv run pytest` passes
- [ ] Measured path stays pydantic-free (wrapper/taxonomy import no heavy deps)
- [ ] Schema/taxonomy changes round-trip through JSON and respect `extra="forbid"`
- [ ] Every failure path produces a recorded taxonomy result — nothing crashes or drops a record
- [ ] AGENTS.md "Ask first" boundaries: <none crossed | listed and answered>
- [ ] No AI attribution / co-author trailers in commit messages on this branch
````

### Verdict ↔ severity mapping

This is a guide for the reviewer when picking a verdict — it is *not* part of the rendered report. Apply mechanically; the verdict should follow from the findings, not from a judgment call.

| Verdict | Allowed when |
|---|---|
| Approve | Zero `Critical` and zero `Important` findings. May have any number of `Suggestion` / `Nit` / `FYI`. |
| Approve with suggestions | Same as Approve. Use this variant when there are non-blocking findings worth surfacing but the change is mergeable as-is. |
| Request changes | At least one `Critical` *or* `Important` finding. Author must fix or explicitly defer (with reason recorded) before merge. |
| Needs split | The change is too large or too entangled to review safely. Re-review after the split. |

If the verdict says Approve and the report contains an unresolved `Important`, the verdict is wrong — fix one or the other before sending.

### Per-finding format

Each finding follows this structure. The outer block uses **4-backtick** fences so the inner ` ```python ` blocks render correctly inside it; copy this same shape if you reproduce the format elsewhere.

`````markdown
**Critical: <short title>**
`src/typebench/collector.py:60`

```python
result_class = ResultClass.FAILED_CRASH
timing = None
```

<1–3 sentence problem statement explaining what is wrong and why it matters.>

**Fix:**

```python
result_class = ResultClass.FAILED_CRASH
failure_phase = FailurePhase.TIMING
timing = None
```
`````

If you can't quote the line, you don't have a finding. If you can't write a concrete fix, the finding is too vague — sharpen it or drop it.

---

## Pre-send checklist

Before delivering the report, sanity-check it against this list. Each item is something a bad review gets wrong. Once authors learn to distrust your reviews, they stop reading them — keep the bar high.

**Findings:**

- [ ] Every finding has a severity label.
- [ ] Every finding quotes a specific file, line, and snippet from the actual diff.
- [ ] No finding is speculation ("might crash if..." / "could possibly...") — each is grounded in the code, including the existing error handling.
- [ ] No finding fails the future-change filter (would this cost the team — or the numbers — next time?).
- [ ] No finding proposes inconsistency with the surrounding file's patterns.
- [ ] No finding is restated under two different axes — pick one place to file it.

**Coverage:**

- [ ] AGENTS.md "Ask first" / "do not violate" boundaries surfaced explicitly (or noted as not present).
- [ ] Measured-path purity: `wrapper.py` and `taxonomy.py` import no pydantic / heavy deps.
- [ ] On-disk models have `ConfigDict(extra="forbid")`; schema/taxonomy-string changes flagged as an Ask-first boundary.
- [ ] Record honesty: `thread_mode_enforced` not falsely `True`; failure records carry `failure_phase`.
- [ ] Failure completeness: `run_command` can't raise; collector catches timing-harness failures into recorded classes.
- [ ] Subprocess safety: list-form only, no `shell=True`, `shlex.join` for hyperfine strings, process-group kill on timeout.
- [ ] Adapter contract: new adapters conform to the Protocol; `parse()` degrades to `(None, None)` and uses `coerce_count`.
- [ ] Scope by plan: no out-of-plan real-checker / cgroup / corpus / renderer work; deferred Protocol methods untouched.
- [ ] Env-specific tests gated with `skipif`; taxonomy classes driven through StubAdapter + `_fake_checker`.
- [ ] Verification story is checked, not assumed.
- [ ] No AI attribution / co-author trailers / marketing footers in commit messages on the branch under review.

**Shape:**

- [ ] `Critical` findings appear at the top of Findings, not buried in a long Suggestions section.
- [ ] `Suggestion` + `Nit` combined ≤ 7. (`Critical` and `Important` are uncapped.)
- [ ] Strengths section reflects something genuinely worth naming, or is omitted (don't fabricate).
- [ ] An empty "What's good" section paired with a long Critical/Important list is a prompt to re-read for fairness before sending — possibly accurate, but check.
- [ ] The verdict matches the findings — `Approve` doesn't co-exist with any unaddressed `Critical` or `Important`.
- [ ] Approval is backed by something concrete (axes walked, verification confirmed) — never just "LGTM".

---

## Common rationalizations

The thoughts that lead to a bad review. Notice them, reverse course.

| Rationalization | Reality |
|---|---|
| "It works, that's good enough" | Working code that biases the numbers, is unreadable, or is architecturally wrong creates debt that compounds. The review is the gate. |
| "Tests pass, so it's good" | Tests are necessary, not sufficient. They don't catch a measured-path import, a dishonest record, or an architecture problem. |
| "The benchmark still produced a number" | A *wrong* number is worse than no number — it carries false authority. The product is trust, not output. |
| "The author probably already thought about this" | If you can't see the answer in the code or the description, neither can the next reader. Ask. |
| "I wrote this, so it's correct" | Authors are blind to their own assumptions. Self-review is necessary; it is not a substitute for another set of eyes. |
| "AI wrote this, it's probably fine" | AI-generated code needs *more* scrutiny, not less — it's confident and plausible even when it's quietly biasing the measurement. |
| "I'll soften this — they might take it personally" | Sycophancy in reviews is a failure mode. Be respectful, name the code (not the person), but say what is true. |
| "I'll add a Suggestion for everything I noticed" | A 30-finding review gets ignored. Cap to 5–7; surface the rest as a follow-up issue if they matter. |
| "I'll approve and they can fix it later" | Later rarely comes. If it needs fixing, request changes. If it doesn't, drop the finding. |
| "It's almost the same as how I'd write it, so close enough" | "Definitely improves overall code health" is the bar, not "matches my style". Approve. |
| "This change is too big to review properly, so I'll skim it" | Skimming a 1500-line change is how real bugs reach main. Push back: split it. |

---

## Anti-patterns in typebench reviews

Things that are easy to get wrong here. Add to this list when a real review miss happens.

- **Letting a `from typebench.models import ...` (or any heavy import) into `wrapper.py` / `taxonomy.py`** — it puts pydantic on the measured path and biases every timed run; invisible to anyone not looking for it.
- **Approving a flip of `thread_mode_enforced` to `True` without the CPU-affinity mechanism** — the record then claims a methodology the engine never ran.
- **Approving a failure record with no `failure_phase`** — `real_exit_code` can be misread as a clean command with a failed result.
- **Letting a new code path raise out of `run_command`, or escape the collector's timing-harness `except`** — a dropped or crash-causing failure silently biases the benchmark.
- **Approving a `subprocess` call without auditing for `shell=True`, for `shlex.join` on hyperfine strings, and for process-group kill on timeout** — each is a benchmark-isolation or safety break.
- **Approving a new adapter whose `parse()` can raise, or whose counts skip `coerce_count`** — garbage (or a JSON `true`) leaks into the record as a count.
- **Approving an adapter whose probe-phase `classify` disagrees with the wrapper's success-exit gate** (the "PLAN 2 TRAP") — the two phases record conflicting results.
- **Approving a schema or taxonomy-string change without round-trip + `extra="forbid"` tests, or without the AGENTS.md "Ask first"** — the on-disk contract is a stability promise.
- **Approving out-of-plan work** — spine work that starts building real-checker / cgroup / corpus / renderer behavior, or half-builds a deferred Adapter method.
- **Approving a PR whose commit messages contain AI attribution or marketing footers** — AGENTS.md and `git-conventions` forbid them; they need to come out before merge.

---

## Examples

**Good review shape:**

````markdown
# Review — feat(adapters): add coerce_count guard to stub parse()

## Summary

Routes the stub adapter's parsed diagnostics/files counts through `coerce_count`
so a malformed `_fake_checker` summary line yields `(None, None)` instead of a
garbage count. Adds two tests (non-int value, JSON `true`) asserting the count is
dropped. ~30 lines, single concern, Plan 1 scope.

## Verdict

Approve with suggestions — change is mergeable; one non-blocking suggestion worth folding in.

## Findings

### Suggestion

**Suggestion: Assert the recorded RunResult, not just the parse() return**
`tests/test_adapters.py:22`

```python
assert adapter.parse(bad, "", 0) == (None, None)
```

The test checks `parse()` in isolation. A round-trip through `run_single` would
also prove the `None` counts survive into the recorded `RunResult` and serialize
cleanly — the property the benchmark actually depends on.

**Fix:** add one `run_single` + JSON round-trip assertion; keep this unit test too.

## What's good

- coerce_count rejects bools and non-ints — a JSON `true` can't masquerade as a count
- parse() degrades to (None, None) rather than raising, so no record is ever dropped
- Tests drive the path through StubAdapter + _fake_checker — fully reproducible

## Verification

- [x] `uv run ruff format` / `ruff check` clean
- [x] `uv run pyrefly check` passes (strict)
- [x] `uv run pytest` passes
- [x] Measured path untouched (no new imports in wrapper.py / taxonomy.py)
- [x] AGENTS.md "Ask first" boundaries: none crossed (Plan 1 adapter scope)
- [x] No AI attribution in commit messages on this branch
````

**Bad review shape (drop these patterns):**

```
LGTM

A few small things:
- could maybe use match in the classifier
- run_command is a bit long
- might crash if hyperfine output is weird
- nit: prefer single quotes
```

No severity, no file:line, no snippet, speculation that ignores the existing
`except` handling, contradicts the project's double-quote rule, and the LGTM up
top makes the comments meaningless.
