---
name: git-conventions
description: Git commit, branch, and PR conventions for the typebench repo — Conventional Commits with required scopes, atomic commits, meaningful bodies, a commit-as-save-point working pattern, and a hard no-AI-attribution rule. Use every time you create a git commit, write a commit message, stage changes, open a PR, or resolve a merge conflict in this repo. Apply these rules on every commit — a sloppy git history compounds faster than sloppy code.
---

# Git Workflow & Commit Conventions (typebench)

> Format: Conventional Commits | Scopes: Required | Breaking changes: `!` + footer
> Atomic commits, imperative mood, explain the *why* in the body.
> **No AI/assistant attribution anywhere — commits read as the author's own work.**

A clean git history is a durable asset. Six months from now, `git log` and `git blame` are the first things anyone reads when diagnosing a regression, tracing a methodology decision, or onboarding. typebench's only product is trust in the numbers; the history is part of how that trust is audited. Follow these conventions on *every* commit so history stays useful.

This skill is the **general-purpose git contract** for this repo. Project-specific rules (quality gates, on-disk schema stability, "Ask first" boundaries) live in [AGENTS.md](../../../AGENTS.md) and take precedence.

---

## Commit as Save Point

Treat commits as save points, branches as sandboxes, and history as documentation. With AI agents generating code at high speed, disciplined version control is the mechanism that keeps changes manageable, reviewable, and reversible.

**Working pattern:**

```
Implement slice → run the verification floor (ruff format/check + pyrefly check + pytest) → Verify → Commit → Next slice
```

Not:

```
Implement everything → Hope it works → Giant commit
```

Each successful increment gets its own commit. If the next change breaks something, you revert to the last known-good state instantly — you never lose more than one increment. If the agent goes off the rails, `git reset --hard HEAD` takes you back to the last successful state.

---

## Commit Message Format

```
<type>(<scope>): <subject>

<body>

<footer>
```

All three parts carry weight: the subject catches attention, the body records motivation, the footer preserves breaking-change migrations and references.

---

## Subject Line

```
feat(cli): typebench run command writing a results record
```

- **Max 72 characters** — truncated in `git log --oneline` and the GitHub UI otherwise.
- **Lowercase** after the colon.
- **Imperative mood** — "add" not "added" or "adds". Read it as: *"this commit will <subject>"*.
- **No trailing period.**
- **Be specific** — describe *what changed*, not *what you did*. `record timing-phase failure instead of crashing the run` beats `fix bug`.

---

## Types

Use exactly these — no others:

| Type | When to use |
|------|-------------|
| `feat` | New user-facing feature or capability |
| `fix` | Bug fix — something was broken, now it works |
| `refactor` | Restructure with no behavior change |
| `perf` | Measurable performance improvement, no behavior change |
| `docs` | Documentation only — README, docstrings, specs, plans, AGENTS.md |
| `test` | Test-only change, no production code change |
| `build` | Build system, dependencies (`pyproject.toml`, `uv.lock`) |
| `ci` | CI/CD pipeline configuration (`.github/workflows/`) |
| `chore` | Maintenance — tooling, formatter churn, pre-commit config |

Choosing the right type:

- Behavior changed? → `feat` or `fix`
- Same behavior, different structure? → `refactor`
- Same behavior, faster? → `perf`
- Only tests? → `test`. Only docs? → `docs`

---

## Scopes (Required)

Every commit must carry a scope identifying the area of the codebase affected. Scopes are short, lowercase, and consistent.

The scope vocabulary tracks the package layout, so **read `src/typebench/*` before picking one** and keep the list honest as modules emerge. Current scopes:

| Scope | Area |
|-------|------|
| `scaffold` | Package skeleton, src layout, uv/hatchling, tooling bootstrap |
| `models` | `models.py` — pydantic schemas (`RunResult`, `TimingStats`, `EnvFingerprint`) |
| `taxonomy` | `taxonomy.py` — pydantic-free on-disk enums (`ResultClass`, `ThreadMode`, `FailurePhase`) |
| `env` | `env.py` — environment fingerprint |
| `wrapper` | `wrapper.py` — hyperfine's per-run command, `run_command`, `classify_default` |
| `timing` | `timing.py` — hyperfine pass + `parse_hyperfine_json` |
| `adapters` | `adapters/*` — `Adapter` Protocol, `StubAdapter`, shared helpers, `_fake_checker` |
| `collector` | `collector.py` — `run_single`, the probe→time pipeline |
| `cli` | `cli.py` — Typer app (`typebench run`) |
| `e2e` | End-to-end / cross-module pipeline tests and the rendered README |
| `engine` | Genuinely cross-cutting spine changes that span the measured path (use sparingly) |
| `ruff` | Ruff config / per-file ignores and lint-rule churn |
| `plan` | Phase plans under `docs/superpowers/plans/` |
| `spec` | Design spec under `docs/superpowers/specs/` |
| `docs` | Documentation only (README, AGENTS.md, docstrings) |
| `deps` | Dependency bumps when not better expressed as `build` |
| `ci` | CI pipeline configuration |

`engine` is reserved for changes that legitimately touch the spine across several modules at once (for example, threading a new failure-phase concept through the wrapper, taxonomy, and collector together). If a change really only touches one module, use that module's scope.

Scopes evolve as the project grows. Add a new scope when a new module emerges under `src/typebench/` that doesn't fit one above. Keep the list short — if half the commits land under one scope, split it.

---

## Body

The body explains **why** this change exists. The diff shows what.

```
fix(collector): record timing-phase failure instead of crashing the run

run_single caught only CalledProcessError, so a garbled hyperfine JSON,
a vanished export file, or a which() TOCTOU crashed the run with no
results.json — silently dropping a measurement.

The failure taxonomy contract is record, never drop: catch the harness
failures too and record failed{env} with FailurePhase=timing, so a flaky
timed run becomes an auditable record instead of a lost one.
```

- Wrap at **72 characters** — respects `git log` formatting in terminals.
- **Blank line** between subject and body.
- **First paragraph:** the problem or motivation.
- **Second paragraph (optional):** the approach. Include when methodology choices, tradeoffs, or rejected alternatives matter — and they usually do in a measurement tool.
- **Skip the body** only for truly trivial changes (typo, import order, dependency bump, formatter churn). If you're tempted to skip, ask whether the commit is too small or whether you're underestimating the future reader.

---

## No AI Attribution — Hard Rule

**Commit messages and PR bodies must contain NO AI or assistant attribution of any kind.** The maintainer strips such trailers from history; don't make them do it.

Specifically forbidden:

- `Co-Authored-By: Claude <...>` — or any AI/assistant co-author trailer (`Claude`, `Anthropic`, `ChatGPT`, `Copilot`, `Gemini`, or any other).
- "Generated with Claude Code" / "🤖 Generated with Claude Code" / "Made with Claude" / any similar trailer or marketing line.
- The robot emoji 🤖 used as a "an AI wrote this" marker.
- Process references that point at an assistant — "as discussed", "per review feedback", "Claude suggested", "as the agent recommended".

Authorship attribution is for humans who can be reached. An AI co-author trailer pollutes `git log`, `git blame`, and `git shortlog` with a signature nobody can email and nobody can ask follow-up questions to. The repo's history must read as if a careful human wrote every line — because *you*, the person reviewing and merging, are accountable for it.

If you used an agent to draft the change, that's fine — write the commit message in your own voice, take responsibility for the diff, and leave the tooling out of the message entirely.

---

## Breaking Changes

For changes that break a public API or change user-facing behavior incompatibly (CLI flag rename or removal, exit-code remapping, importable-API breaks, on-disk schema-format shifts):

1. **Add `!` after the scope** — for scanning `git log`.
2. **Add a `BREAKING CHANGE:` footer** — for migration details.

```
feat(cli)!: rename --out to --output

Aligns the results-path flag with the noun used in messages and docs.

BREAKING CHANGE: `--out` is no longer accepted. Update every invocation
(scripts, shell aliases) to use `--output`. The CLI exits non-zero with
a clear error on the old flag instead of silently parsing it.
```

Both the `!` and the footer are required.

Per [AGENTS.md](../../../AGENTS.md), changes to the on-disk schema (`RunResult` fields, taxonomy string values), CLI flags, exit-code mapping, or quality-gate config need to be asked about *before* the commit — not discovered in review. The schema is a stability contract.

---

## Atomic Commits

Each commit should be a single logical change that passes the verification floor (`ruff format`/`check`, `pyrefly check`, `pytest`) on the relevant scope. If you're writing "and" in the subject, split.

**Good — one concern per commit:**

```
feat(timing): hyperfine timing pass with pure JSON parser
feat(collector): probe-then-time pipeline producing a RunResult
test(cli): assert exact exit code 2 for unknown tool
```

**Bad — bundled concerns:**

```
feat(collector): add timing pass, refactor parser, and write CLI tests
```

Atomic commits make `git bisect` usable, `git revert` safe, and code review tractable.

**Keep concerns separate.** Don't combine formatter-only changes with behavior changes. Don't combine refactors with features. Small cleanups (renaming one variable) can ride along with a feature commit; anything larger should be a separate commit, and ideally a separate PR.

---

## Change Size

Target roughly:

| Size | Verdict |
|------|---------|
| ~100 lines | Easy to review, easy to revert — aim for this |
| ~300 lines | Acceptable for a single logical change |
| ~1000+ lines | Split before submitting, not after |

Large changes are harder to review, riskier to deploy, and harder to revert. If a single change exceeds ~1000 lines, it is almost always two or more changes wearing a trench coat — split it into reviewable slices that each stand on their own.

---

## Commit the *Why*, Not the *What*

The diff shows exactly what changed. The commit message should answer: *"six months from now, when someone reads `git blame` on this line, what context will they need?"* In a benchmark, that context is often a fidelity or honesty argument — record it.

---

## Things to Avoid

- **Vague subjects** — "fix bug", "update code", "WIP", "misc".
- **AI/assistant attribution** — no `Co-Authored-By: Claude` (or any AI), no "Generated with Claude Code", no 🤖 trailer, no "as discussed / per review feedback / Claude suggested". See the hard rule above. This is the single most important thing to keep out of every message.
- **Referencing process** — "as discussed", "per review feedback".
- **Temporal language** — "now we do X", "previously Y". Write in steady-state tense.
- **Implementation narration** — "first I changed X, then Y".
- **Emoji** in commit messages.
- **Ticket numbers in the subject** — put them in the footer: `Refs: #42`.
- **`--amend` on shared history.**
- **`--no-verify`** — pre-commit runs `ruff check --fix`, `ruff format`, and `pyrefly check` (strict) for a reason. If a hook is wrong, fix the hook. AGENTS.md is explicit that lint + typecheck + unit tests are the floor before declaring a change done.

---

## Footer

Blank line between body and footer. Only include footers when they carry information.

```
BREAKING CHANGE: <description and migration path>
Refs: #123, #456
Closes: #789
```

Do **not** add `Co-Authored-By:` for AI agents (see the hard no-AI-attribution rule above) — and don't slip an assistant credit in as a footer either. Co-authoring a real human teammate who pair-programmed the change is fine; that's the trailer's intended use.

---

## Commit Workflow

1. **Review staged changes** — `git diff --staged`. Does the diff match a single logical change? If not, split.
2. **Pre-commit hygiene:**

   ```bash
   # What is about to be committed?
   git diff --staged

   # Any obvious secrets? (quick first-pass scan)
   git diff --staged | grep -iE "password|secret|api[_-]?key|token|bearer"

   # Verification floor — required before declaring a change done
   uv run ruff format
   uv run ruff check
   uv run pyrefly check
   uv run pytest
   ```

   Never commit code that fails format, lint, typecheck, or unit tests. Pre-commit runs `ruff check --fix`, `ruff format`, and `pyrefly check` (strict) automatically — never bypass it with `--no-verify`. A secret-scan grep is not a substitute for a proper scanner but catches the common mistakes.
3. **Check for never-commit paths** — virtualenvs, generated `results.json` artifacts, `__pycache__`, anything with secrets, anything under a gitignored directory.
4. **Choose the right type.**
5. **Pick the scope** from the list above — read `src/typebench/*` if you're unsure which module owns the change; reserve `engine` for genuinely cross-cutting spine changes.
6. **Write a specific subject in imperative mood.**
7. **Write the body** — why does this change exist?
8. **Check for breaking changes.**
9. **Confirm no AI/assistant attribution** snuck into the message — no co-author trailer, no "Generated with" line, no 🤖.

---

## Change Summaries

After a non-trivial edit, give a structured summary. This surfaces scope discipline, catches wrong assumptions, and gives reviewers a clear map of the change:

```
CHANGES MADE:
- src/typebench/collector.py: run_single now catches harness failures
  (bad hyperfine JSON, missing export, which() TOCTOU) and records
  failed{env} with FailurePhase=timing instead of crashing.
- tests/test_collector.py: Added cases covering each dropped-failure
  path, asserting a RunResult is still produced.

THINGS I DIDN'T TOUCH (intentionally):
- src/typebench/wrapper.py: classify_default precedence is correct as-is;
  this change is about the collector's harness boundary, not the wrapper.
- src/typebench/models.py: No schema field added — the existing
  failure_phase field already carries this; out of scope to widen it.

POTENTIAL CONCERNS:
- This changes which records appear on a flaky run (previously: none).
  Behavior change at the harness boundary, but it upholds the
  record-never-drop contract.
- No "Ask first" boundary crossed: no schema field, CLI flag, exit-code,
  or gate-config change.
```

The `DIDN'T TOUCH` section is the important one. It shows you exercised scope discipline instead of going on an unsolicited renovation, and it gives the user a quick way to say "actually, fix that too" when the adjacent issue matters.

**Always call out AGENTS.md "Ask first" boundaries you crossed** in `POTENTIAL CONCERNS` — on-disk schema change (`RunResult` fields, taxonomy string values), CLI flag change, exit-code remap, a new runtime dependency, a heavy import on the measured (wrapper) path, or edits to `[tool.pyrefly]`, `[tool.ruff]`, or `[tool.pytest.ini_options]`. The check belongs in the summary whether or not you already asked.

Skip this pattern for trivial one-liners. Use it whenever the edit touched more than one file or could plausibly have touched more than one file.

---

## Branch and PR Hygiene

- **Branch name:** short, hyphenated, type-prefixed — `feat/cli-run-command`, `fix/collector-harness-failures`. Avoid long personal prefixes.
- **Keep branches short-lived** — aim to merge within 1–3 days. Long-lived branches accumulate merge risk and delay integration.
- **Rebase vs merge:** prefer rebase to keep your local branch in sync with main; follow the repo's policy for merging reviewed work.
- **Force-push:** only on your own feature branch, never on shared branches or `main`.
- **Delete branches after merge.**
- **PR title:** mirror a commit subject — `type(scope): imperative subject`.
- **PR description:** mirror a commit body — problem, approach, anything a reviewer needs that the diff won't show. Call out any AGENTS.md "Ask first" boundaries you crossed and the answer you got. **No AI/assistant attribution in the PR body either** — no co-author trailer, no "Generated with Claude Code", no 🤖.

---

## Recovery and Correction

- **Wrong subject on the last commit, not yet pushed:** `git commit --amend`.
- **Wrong file staged:** `git restore --staged <file>`.
- **Accidental commit on main:** branch from HEAD, reset main to the upstream, push the branch. *Do not* force-push main.
- **Accidentally discarded work:** `git reflog` — work lives there for ~90 days by default.

When recovery is ambiguous, stop and ask before running destructive commands (`reset --hard`, `push --force`, `clean -f`). Reversibility is cheap; recovery after a bad destructive command is expensive.

---

## Git for Debugging

Useful commands when diagnosing a regression:

```bash
# Find which commit introduced a bug (binary search)
git bisect start
git bisect bad HEAD
git bisect good <known-good-commit>
# At each midpoint: run `uv run pytest` (or the narrowest failing test)
# When done: git bisect reset

# What changed in a given area recently?
git log --oneline -20 -- src/typebench/collector.py
git diff HEAD~5..HEAD -- src/typebench/

# Who last changed a specific line, and why?
git blame -L <start>,<end> src/typebench/wrapper.py
# Then: git show <commit-hash> for the full context

# Find commits by keyword in the message
git log --grep="taxonomy" --oneline

# Discarded work recovery
git reflog
```

Atomic commits with descriptive messages are what make these tools effective. A history of "fix stuff" commits makes `bisect` useless.

---

## Common Rationalizations

Red-flag thoughts — when you notice them, reverse course:

| Rationalization | Reality |
|-----------------|---------|
| "I'll commit when the feature is done" | One giant commit is impossible to review, debug, or revert. Commit each slice. |
| "The message doesn't matter, the diff is obvious" | Messages are documentation. In six months nobody will read the diff first — they'll `git log --grep` and read the subject. |
| "I'll squash it all later" | Squashing destroys the development narrative. Prefer clean incremental commits from the start. |
| "Branches add overhead" | Short-lived branches are free and prevent conflicting work from colliding. Long-lived branches are the problem. |
| "I'll split this change later" | Large changes are harder to review, riskier to deploy, harder to revert. Split before submitting, not after. |
| "`--no-verify` just this once" | Every "just this once" is how broken code reaches main. AGENTS.md is explicit: ruff + pyrefly-strict + pytest are the floor. If a hook is wrong, fix the hook. |
| "`--amend` is fine, nobody pulled it yet" | That assumption breaks the moment CI pulls, a teammate pulls, or a mirror pulls. Amend only on truly private commits. |
| "A Co-Authored-By trailer is just harmless attribution" | It's noise the maintainer will strip by hand. You are the author. Own the change and leave the tooling out. |

---

## Red Flags

Stop and reconsider if you notice any of these:

- Large uncommitted changes accumulating (more than a few hundred lines unstaged)
- Commit messages like "fix", "update", "misc", "WIP"
- Formatter-only changes mixed with behavior changes in one commit
- Virtualenvs, `__pycache__`, or generated `results.json` artifacts in `git status`
- Long-lived branches that have diverged significantly from main
- Force-pushing to a shared branch
- `--no-verify` used to bypass a failing hook
- **Any AI/assistant attribution in the staged message — `Co-Authored-By: Claude`, "Generated with Claude Code", a 🤖 trailer, or "Claude suggested"**
- A `# type: ignore` instead of `# pyrefly: ignore[<kind>]` with a reason
- A heavy import (pydantic, etc.) added to `wrapper.py` or `taxonomy.py` — it biases every timed measurement

---

## Verification Checklist

Before every commit:

- [ ] Commit does one logical thing
- [ ] Message subject follows `type(scope): imperative subject`, ≤72 chars, lowercase, no trailing period
- [ ] Scope is one of the scopes above (and `engine` only for genuinely cross-cutting spine changes)
- [ ] Body explains the *why* (or the change is trivial enough to skip it)
- [ ] Breaking changes carry both `!` and a `BREAKING CHANGE:` footer
- [ ] **No AI/assistant attribution anywhere in the message — no `Co-Authored-By: Claude` (or any AI), no "Generated with Claude Code", no 🤖 trailer, no "Claude suggested"**
- [ ] `uv run ruff format` leaves no changes
- [ ] `uv run ruff check` passes
- [ ] `uv run pyrefly check` passes (strict)
- [ ] `uv run pytest` passes
- [ ] Staged diff contains no obvious secrets
- [ ] No formatter-only changes mixed with behavior changes
- [ ] No never-commit paths in the diff (virtualenvs, `__pycache__`, generated `results.json`, gitignored output)

Before opening a PR:

- [ ] Branch is short-lived (days, not weeks)
- [ ] PR title mirrors a commit subject
- [ ] PR description explains the problem and approach, not just the diff
- [ ] Any AGENTS.md "Ask first" boundary crossings are called out with the answer
- [ ] History is a sensible sequence of atomic commits
- [ ] **PR description contains no AI/assistant attribution** — no co-author trailer, no "Generated with Claude Code", no 🤖

---

## Examples

**Good:**

```
fix(wrapper): capture real outcome without aborting hyperfine on diagnostics

A checker that exits non-zero only because it found diagnostics is a
clean measurement, not a harness failure — but hyperfine treats any
non-zero exit as a failed run and discards the timing.

Normalize the wrapper's exit code so diagnostics-only runs report
success to hyperfine while the real exit code and result_class are
preserved in the recorded RawRun. The measurement survives; the truth
is kept in the record.
```

**Good (refactor, fidelity argument in the body):**

```
refactor(models): extract pydantic-free taxonomy so the wrapper stays lightweight

The exit-code wrapper is hyperfine's per-run command, so every import it
pulls runs on every timed measurement. Importing the enums from models
dragged pydantic (~50ms) onto the measured path and biased comparative
ratios.

Move ResultClass, ThreadMode, and FailurePhase into a stdlib-only
taxonomy module and import them there. No behavior change; the measured
path is now free of pydantic.
```

**Good (trivial, body skipped):**

```
build(deps): bump pyrefly to 0.16
```

**Bad:**

```
fix stuff

updated a bunch of files to fix the thing we discussed

Co-Authored-By: Claude <noreply@anthropic.com>
🤖 Generated with Claude Code
```

Avoid this shape entirely — vague subject, narration body, and the two things this repo forbids outright: an AI co-author trailer and a "Generated with Claude Code" line.
