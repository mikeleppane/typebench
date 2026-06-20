---
name: improve-architecture
description: Explore the typebench codebase, surface architectural friction, and propose module-deepening refactors as actionable plan documents under docs/plans/. Use when asked to review architecture, "make this cleaner", "reduce coupling", "this module is doing too much", "the modules are just thrown there", "the tests are a junkyard", or to evaluate whether a module pulls its weight. Tuned to typebench's package layering (contracts ← engine ← {adapters, corpus} ← suite ← cli), its pydantic-free measured path, the Adapter Protocol grain, and the no-planning-jargon docstring norm. Produces a durable refactor plan, not edits — execution is a separate, approved step.
---

# Improve Architecture (typebench)

Explore the typebench codebase organically, surface architectural friction, and propose
module-deepening refactors as **durable plan documents** under `docs/plans/`. typebench is a
neutral, reproducible benchmark of Python type-checker performance (mypy, pyright, pyrefly, ty,
zuban); **the only product is trust in the numbers.** Architecture work here is in service of
that: a boundary that hides measurement logic badly is a boundary that lets a bias leak in.

A **deep module** (Ousterhout, *A Philosophy of Software Design*) has "a small interface
hiding a large implementation." Deep modules are more testable, more navigable for humans and
agents, and let you test at the boundary instead of patching internals. This skill finds
*shallow* modules — interface nearly as complex as implementation — and proposes ways to
deepen them. It also covers the plain-but-real case typebench hit: modules that are
individually fine but **dumped flat with no package boundary**, where the friction is
navigational, not depth.

Project-specific rules live in [AGENTS.md](../../../AGENTS.md) and override anything here.
Companion skills own adjacent moments and take precedence on their rules:

- [coding-guidance-python](../coding-guidance-python/SKILL.md) — the Python implementation
  contract (typing, illegal-states-unrepresentable, error handling, module boundaries). When a
  proposed interface needs a contract, *cite this skill* rather than re-deriving it.
- [code-review-and-quality](../code-review-and-quality/SKILL.md) — the review moment, with the
  benchmark-aware checks (measurement fidelity, record honesty, failure completeness). A
  refactor plan must not regress any of those.
- [test-driven-development](../test-driven-development/SKILL.md) — boundary tests replace
  internal patches as modules deepen; cite when the plan changes test seams.
- [git-conventions](../git-conventions/SKILL.md) — atomic commits with required scope. Each
  migration slice is one such commit.

This skill **produces a plan; it does not edit code.** Execution is a separate step the user
approves.

---

## typebench's architecture as it stands

The target layering — established and dependency-honest (no cycles) — is:

```text
contracts/   models · taxonomy · config            # shared vocabulary, no internal deps
engine/      collector · wrapper · timing                                    # produce one
             measure · calibration · env                                     # RunResult
adapters/    base · _support · mypy · pyright · pyrefly · ty · stub          # per-checker
corpus/      catalog · counting · envman            # what gets benchmarked
suite/       runner · preflight · renderer          # matrix, gating, rendering (app tier)
_internal/   fake_checker                           # private shipped stub backend
cli.py                                              # entry point
```

Dependency direction is **contracts ← engine ← {adapters, corpus} ← suite ← cli**. Any
proposal that points an arrow backward (e.g. `engine` importing `suite`) is wrong by
construction — say so and redraw it. When the live tree disagrees with this map (mid-refactor),
trust the import graph, not the diagram.

---

## Process

### 1. Explore the codebase

Use the Agent tool with `subagent_type=Explore` to navigate organically — like a developer
seeing the code for the first time. Note where friction shows up. **Do not trust a single
explorer's "looks clean" verdict** — typebench's modules are individually tidy yet were
collectively unnavigable; the user felt that before any tool did. Cross-check the explorer's
read against the user's stated pain and against direct greps (line counts per module, import
graph, docstring jargon density, test-file naming).

typebench-specific friction signals — hunt for these first:

- **Flat module dump.** Many modules in one directory with no package boundary, so
  understanding one concept means bouncing between files with no map. The filesystem split
  isn't pulling its weight. (This was the dominant friction; the fix was packaging by the
  layering above, even though packaging is reorganization, not deepening — be honest about
  which you're doing.)
- **Planning-jargon docstrings.** `spec §N`, `Plan N`, `Decision X`, `PLAN 2 TRAP`,
  `2B residual risk`, and pointers to `docs/superpowers/research/*` that no longer exist.
  These are dead references to a defunct GSD process. Flag them: the *rationale* is
  load-bearing, the *label* is noise. Rewrite to describe behavior and intent.
- **Test junk-drawer.** A flat `tests/` with no `conftest.py` (so `NormalizedConfig` /
  `EnvFingerprint` get rebuilt inline in many files), and inconsistent naming clusters
  (`test_e2e` vs `test_all_tools_e2e` vs `test_pyright_e2e`). Propose a mirror of the package
  tree, shared **named builders** (not magic autouse fixtures — keep scenario values visible
  at the assertion site), and normalized groups.
- **Adapter boilerplate.** The five adapters duplicate the no-raise `--version` probe and the
  CLEAN-honesty rule ("exit 0 is only clean with a positive file count, else FAILED_ENV"). The
  grain here is **free functions, not inheritance** — `base.py` already ships free helpers
  (`coerce_count`, `default_classify`); `wrapper.py` is all free functions. A base class or a
  declarative Spec both fight the tests (they patch `MypyAdapter.version` on the class and
  `<tool>_mod.subprocess.run` in each module). Prefer a tiny `adapters/_support.py`.
- **The `dict[str, Any]` exception that proves the rule.** typebench is well-typed; the only
  `Any` highway is `parse_hyperfine_json` (a justified external-JSON hydration boundary).
  Don't manufacture friction where the types are already honest.

Generic deep-module signals still apply: god modules, stringly-typed interfaces, `**kwargs`
seams, import-time side effects, late-import circular-dep workarounds, feature envy across
packages, Manager/Service/Handler method-bags, overextended inheritance, mutation hidden
behind `get_*`/`load_*` names.

The friction you encounter IS the signal. Record what felt hard; don't force categories.

### 2. Present candidates

A numbered list of opportunities. For each: the **cluster** (modules/classes/functions
involved), **why they're coupled**, the **typebench signal** it matches, the **dependency
category** (internal / crosses a package boundary / crosses an external I/O boundary —
subprocess, cgroup, filesystem, hyperfine), and the **test impact** (which internal patches
become boundary tests). Do NOT propose interfaces yet. Ask: *"Which would you like to explore?"*

Use `AskUserQuestion` for the choice when several candidates are live, and when a target
package shape or execution mode (plan-first vs execute-in-slices) needs a decision only the
user can make.

### 3. User picks a candidate

Wait. They may combine, reprioritize, or fold in adjacent cleanups — adapt.

### 4. Frame the problem space

Before designing, write the constraints any new interface must satisfy, the ownership/lifetime
relationships (who constructs, owns, disposes — crucial for the run-scoped `workdir`, cgroup
scopes, venvs, subprocess lifetimes), the dependencies it relies on, and a rough illustrative
sketch (NOT a proposal — a way to make constraints concrete). Show it, then proceed to Step 5
so the user reads while sub-agents work.

### 5. Design multiple interfaces in parallel

Spawn 3+ sub-agents (Agent tool, `subagent_type=Plan`) concurrently, each producing a
**radically different** interface. Brief each with file paths, coupling details, dependency
category, the complexity being hidden, and typebench constraints (Python 3.12+, full
annotations, stdlib-preferred, the relevant invariants below). Give each a distinct constraint:

- **Minimal surface** — 1–3 entry points; free functions over a class. Smallest interface wins.
- **Protocol-based** — `typing.Protocol` boundaries with fake/stub implementations for trivial
  testing. Avoid `abc.ABC` unless a runtime isinstance check is genuinely needed (the existing
  `Adapter` Protocol is `@runtime_checkable` and tested with `isinstance` — preserve that).
- **Caller-optimized** — make the common case one line; progressive disclosure via
  keyword-only options and sensible defaults.
- **Ports-and-adapters** (when crossing an external I/O boundary) — a port `Protocol` with
  production and test adapters; domain logic must not import the adapter.

Each sub-agent outputs: interface signature (fully annotated), usage example at the call site,
**what it hides** (the Ousterhout test), dependency strategy, and trade-offs. Present them
sequentially, compare in prose, then **give your own opinionated recommendation** — strongest
design and why; propose a hybrid if elements combine. The user wants a strong read, not a menu.

### 6. User picks an interface (or accepts the recommendation)

### 7. Write the refactor plan

Write to `docs/plans/<date>-<slug>.md` (default location). Make it **durable** — describe
responsibilities, boundaries, and contracts, not file paths that will move during the refactor.

Required sections:

- **Goal** — what the deepened/repackaged module encapsulates, one paragraph.
- **Scope of "no behavior change"** — state explicitly what is preserved (CLI surface, runtime
  benchmark behavior, every classification verdict, the on-disk JSON schema) vs. allowed to
  break (internal Python import paths are not a public API; no compat shims unless a real
  out-of-tree consumer is known).
- **Boundary** — the public interface in signature form, fully annotated.
- **Responsibilities** — what lives inside each package, what stays out (a table works well).
- **Migration slices** — ordered, **bottom-up through the layering**, each slice one atomic
  commit that keeps the repo green. Include the runtime-command-string migration (below) as an
  explicit item, not folded into import rewrites.
- **Test strategy** — which internal patches become boundary tests, which files move where,
  what new boundary coverage appears. Shared **named builders**, not over-centralized fixtures.
- **Known landmines** — the typebench-specific ones below, plus anything this refactor could
  break if rushed.
- **Done criteria** — specific and observable, grouped (structure, runtime correctness, dedup,
  tests & gates, documented-invariant preservation).

---

## typebench landmines any refactor plan must address

These have bitten before; a plan that ignores them is incomplete.

- **The pydantic-free measured path — the highest-risk invariant.** `wrapper`, `measure`, and
  `calibration` run as hot subprocesses and must NOT import pydantic (guarded by
  `test_wrapper_import_does_not_pull_pydantic`). Importing a submodule runs every ancestor
  package's `__init__.py`, so **package `__init__.py` files on the measured path must stay
  empty / version-only** — one convenience re-export of a pydantic model breaks it. `calibration`
  keeps its lazy in-function schema import for the same reason. Extend the guard to every
  measured-path module a refactor moves.
- **Runtime `python -m typebench.<module>` string literals.** `timing` invokes the wrapper,
  `measure` invokes itself under `systemd-run`, `stub` invokes the fake checker, and
  `wrapper`/`calibration`/`measure` set `argparse(prog=…)`. An import-rewrite sweep does NOT
  catch these — they are strings handed to a subprocess. Migrate them with their module and add
  a test that asserts the constructed hyperfine/systemd commands invoke the new paths.
- **Adapter test seams.** Version tests patch `subprocess.run` *as imported into each adapter
  module*; mypy tests patch `MypyAdapter.version` on the class and call `mypy._cache_dir`
  directly. Any adapter dedup must keep adapters as plain classes with module-local
  `import subprocess` and module-scope helpers — which is why free functions beat a base class
  or a declarative Spec here.
- **Record honesty / failure completeness.** "Never drop a record" and the CLEAN-confirmation
  rule are load-bearing measurement semantics. A refactor that changes when `parse`/`classify`
  run, or routes a new adapter through `confirm_clean`, can silently flip a verdict
  (`StubAdapter(exit_code=0)` is `CLEAN` today with `files=0`). Treat these as behavior, not
  cleanup.
- **Quality gate.** Per AGENTS.md, the floor is `uv run ruff format` · `uv run ruff check` ·
  `uv run pyrefly check` (strict, 0 errors) · `uv run pytest`. Run the **full** gate after each
  import-moving slice and as final acceptance — pytest alone is not enough for an import-path
  refactor, and pyrefly is strict.

---

## Anti-patterns to avoid in recommendations

- **Premature abstraction** — a `Protocol` with exactly one foreseeable implementation. A
  concrete class is simpler and refactorable later.
- **Indirection masquerading as design** — a factory wrapping a constructor, a "Manager" with
  three methods, an empty `interfaces/` dir. If it doesn't hide complexity, it adds it.
- **Renaming without deepening** — moving the same interface into a new directory and calling it
  done. The test is whether the interface shrank and the hidden implementation grew. (When the
  goal is genuinely navigational packaging, say so — don't dress reorg up as deepening.)
- **Single-module packages** — a `report/` holding only `renderer.py` is a layer with nothing
  to show. Co-locate with its sibling instead.
- **Framework-shaped fixes** — DI containers, event buses, plugin registries before the
  pressure warrants them. Solve the immediate coupling first.
- **Over-centralized test fixtures** — autouse fixtures that hide the values driving assertions.
  Prefer named builders the test calls with scenario-specific values.

---

## When *not* to use this skill

- One-off scripts or throwaway corpus probes where the design needn't last.
- A refactor with an already-agreed target interface — skip to writing the plan.
- Pure bug-fix work where the architecture is fine — use the debugging flow instead.
- Small cleanup inside a single function — that's code-level simplification, not architecture.
