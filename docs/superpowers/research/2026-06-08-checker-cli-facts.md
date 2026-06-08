# Checker CLI Fact Sheets (for Plan 2 adapters)

**Date:** 2026-06-08
**Method:** per-tool research agents — context7 docs + empirical `--help`/runs (mypy 2.1.0, pyright 1.1.410 + Node 22, ty 0.0.44) and pyrefly local source (`~/personal/dev/pyrefly`, main @ 2026-06-07).
**Status:** authoritative as of the versions above. **ty is preview (0.0.x) and mypy is 2.x — re-verify on every version bump.**

This doc backs `2026-06-08-real-adapters-normalized-config.md` (Plan 2). It maps the LOCKED §6 normalized-config policies to concrete per-tool flags, plus output parsing, exit-code maps, thread control, and cache behavior.

---

## Cross-tool summary

| Concern | mypy | pyright | ty | pyrefly |
|---|---|---|---|---|
| Distribution | PyPI wheel (mypyc-compiled) | npm `pyright` + Node, or PyPI wrapper | PyPI `ty` (Rust bin) | PyPI `pyrefly` (Rust bin) |
| Verify build | `--version` → `(compiled: yes)` | `--version` (pyright only; Node separate) | `ty --version` | `pyrefly --version`; `pyrefly dump-config` validates config |
| Diagnostics count | text summary `Found N errors…` | JSON `summary.errorCount` | `concise` → `Found N diagnostics` | JSON `errors[severity==error]` len |
| Files-checked count | text summary `checked K source files` | JSON `summary.filesAnalyzed` | **`-v` stderr** `Indexed N file(s)` | **`--summary=full` stderr** `N modules` |
| Machine output | `-O json` (JSONL; **no files count, empty on clean**) → prefer text | `--outputjson` (clean summary) | **no JSON**; `concise`/`gitlab`/`junit` | `--output-format json` |
| Suppress project config | `--config-file=` (empty) | `--project <our-cfg-dir>` | `--config-file <our.toml>` (+ explicit CLI flags) | `--config <our.toml>` |
| 1-core control | omit / `--num-workers 1` | omit / `--threads 1` | `TY_MAX_PARALLELISM=1` | `--threads 1` / `PYREFLY_THREADS=1` |
| 1-core hard cap? | single-proc default (pair taskset) | hint, not hard | **soft** (task cap, not threads) | **HARD** (rayon pool=1) |
| Cache (cold)? | writes `.mypy_cache` unless `--cache-dir=/dev/null` | stateless | stateless | stateless |
| Analyze all bodies | `--check-untyped-defs` (off by default) | default on (don't pass `--skipunannotated`) | default on | `check-unannotated-defs=true` (default) |

**Universal:** affinity (`taskset -c 0` / cgroup, Plan 4) is the uniform 1-core mechanism; per-tool caps layer on top with an honesty flag. OOM/timeout are detected by the runner (signal/wall-clock), never by a tool's exit code.

---

## mypy (verified 2.1.0)

- **Distribution:** default `pip install mypy` is mypyc-compiled. Verify: `mypy --version` ends with `(compiled: yes)`. Pin exact `mypy==X.Y.Z`. **Version skew: `-O json`, `--num-workers`, `MYPY_NUM_WORKERS` are 2.x — absent in 1.x.**
- **Normalized argv (single-core, cold, text-parse):**
  ```
  mypy --python-version X.Y --platform linux --check-untyped-defs \
       --follow-imports=silent --config-file= \
       --no-incremental --cache-dir=/dev/null \
       --exclude '<tests|vendor|generated regex>' \
       --python-executable <project-venv-python> \
       <first-party-src-dir>
  ```
  - `--follow-imports=silent` = resolve dep types but suppress dep errors (= "report first-party only"). Do NOT use `skip` (loses types).
  - `--config-file=` (empty) suppresses the project's config (no `--no-config` flag exists). No plugins load.
  - `--python-executable <venv>` makes mypy resolve installed third-party from the project venv (else it uses its own interpreter env).
  - `--exclude` only filters directory discovery — give it the src dir, not individual files; ensure followed first-party imports don't pull tests back in.
- **Cold:** `--no-incremental` alone STILL creates `.mypy_cache`; add `--cache-dir=/dev/null` to write nothing. `clear_cache`/`prepare_command`: `rm -rf .mypy_cache` (+ any `MYPY_CACHE_DIR`).
- **Parse (prefer TEXT, not JSON — JSON has no files count & is empty on clean):**
  - errors+files: `Found (\d+) errors? in \d+ files? \(checked (\d+) source files?\)` → (diagnostics, files).
  - clean: `Success: no issues found in (\d+) source files?` → diagnostics=0, files=group1.
  - aborted: `errors prevented further checking` → no files count (failure path).
  - Do NOT pass `--no-error-summary`.
- **Exit codes:** 0 clean · 1 diagnostics · **2 overloaded** (usage error / unreadable target / config) → `failed_env`. Crash = `INTERNAL ERROR` in output or signal (139/negative) → `failed_crash`. 137/SIGKILL → `failed_oom` (runner).
- **Threads:** single-process by default (`--num-workers 0`). 1-core: omit or `--num-workers 1`. Multi-core track: `--num-workers N` is **experimental and can change diagnostics** — prefer mypyc compilation as the "fast" lever; if used, expect minor result deltas.
- **Parse-sanity:** assert `checked K source files`, K>0 on success (text mode only).

## pyright (verified 1.1.410 / Node 22)

- **Distribution:** Node app. Prefer **npm `pyright` pinned + pinned Node** (container/nvm) for reproducibility. PyPI wrapper picks ambient Node by default (`PYRIGHT_PYTHON_GLOBAL_NODE=true`) → non-deterministic unless you pin `PYRIGHT_PYTHON_FORCE_VERSION` + Node via `[nodejs]` wheel or `PYRIGHT_PYTHON_NODE_VERSION`. `pyright --version` prints pyright only — check `node --version` separately.
- **Normalized config:** generate a `pyrightconfig.json` in a tool-controlled dir; run `pyright --project <that-dir> --outputjson --pythonversion 3.X --pythonplatform Linux`. `--project` **replaces** discovery (the project's own config is ignored — verified). Config:
  ```json
  {
    "include": ["<abs path to first-party src>"],
    "exclude": ["**/node_modules","**/__pycache__","**/tests/**","**/_generated/**","**/_vendor/**"],
    "typeCheckingMode": "standard",
    "useLibraryCodeForTypes": true,
    "venvPath": "<dir containing venv>", "venv": "<venv name>",
    "pythonVersion": "3.X", "pythonPlatform": "Linux"
  }
  ```
  - Stock default mode = `standard` (verified). Don't use basic/strict.
  - Analyze all bodies = default; **do NOT pass `--skipunannotated`**.
  - First-party-only: `include`=src; deps resolved via venv + `useLibraryCodeForTypes=true`, not error-reported. (excluded-but-imported files are still analyzed, not counted/reported.)
  - Flag spellings are single tokens: `--pythonversion`, `--pythonplatform`, `--outputjson`, `--skipunannotated`.
- **Parse (JSON):** `summary.errorCount` (+ optional warning/info), `summary.filesAnalyzed`. (`filesAnalyzed` counts targeted first-party files, not typeshed — don't use `--stats`.) Parse defensively with `.get`.
- **Exit codes:** 0 clean · 1 errors (diagnostics) · 2 fatal → `failed_crash` · 3 config unreadable → `failed_env` · 4 bad CLI / missing target path → `failed_env`. Don't pass `--warnings` (keeps exit 1 = errors only).
- **Threads:** single main thread by default; `--threads N` is experimental, not a hard cap. 1-core: omit / `--threads 1` + affinity.
- **Cache:** stateless single-shot → `clear_cache`/`prepare_command` no-ops.
- **Parse-sanity:** `summary.filesAnalyzed > 0` (exit 0 + filesAnalyzed==0 = mis-scoped include → treat as failure, not clean).

## ty (verified 0.0.44 — PREVIEW, churns)

- **Distribution:** PyPI `ty`; `uv tool install ty==0.0.44` / `pip install ty==0.0.44`. Verify `ty --version` → `ty 0.0.44`. Pin exact.
- **Normalized command:**
  ```
  TY_MAX_PARALLELISM=1 ty check <src dirs> \
     --config-file <our ty.toml> \
     --python <project-venv> --python-version 3.X --python-platform linux \
     --exclude 'tests/**' --output-format concise -v --no-progress --color never
  ```
  - `--config-file` replaces `[tool.ty.rules]` discovery (verified); pass explicit CLI flags too (belt-and-suspenders — full sub-table suppression not exhaustively proven).
  - `--python <venv>` resolves third-party from that env. First-party-only reporting is default (no flag).
  - Analyze all bodies = default. Config schema key is `python-platform` (not `platform`); selection under `[src]`.
- **Parse:** **NO JSON.** `--output-format concise` → clean `All checks passed!` / non-clean `Found N diagnostic(s)` (parse int after `Found `). Files count ONLY via `-v` stderr `INFO Indexed (\d+) file\(s\)` (fragile). `gitlab` = JSON array (count length) / `junit` `tests=N` are structured fallbacks.
- **Exit codes:** 0 clean · 1 diagnostics · 2 config/IO/CLI → `failed_env` · 101 panic → `failed_crash`. Don't pass `--exit-zero`/`--error-on-warning`. Invalid `TY_MAX_PARALLELISM` is silently ignored — pass a valid int.
- **Threads:** `TY_MAX_PARALLELISM=1` = **soft** task cap (ty may still spawn threads) → honesty flag false. No `-j`/`--threads`.
- **Cache:** stateless `check` (incrementality only in `--watch`); `~/.cache/ty` holds only vendored typeshed. `clear_cache` no-op.
- **Parse-sanity:** files>0 only via `-v Indexed N`; else `files=None` (don't gate). `WARN No python files found` = empty target signal.

## pyrefly (local source, main @ 2026-06-07)

- **Distribution:** PyPI `pyrefly` (maturin bin wheel). Verify `pyrefly --version`. `pyrefly dump-config --config <cfg>` validates the config loads (good install/config check).
- **Normalized config + command:** generate a `pyrefly.toml`; run **project mode** (no positional file args) so `project-includes` drives the file set:
  ```
  pyrefly check --config <our.toml> --output-format json --summary=full --threads 1
  ```
  (cwd = project root, or use absolute `project-includes`). `--config` short-circuits discovery → project's own config ignored.
  ```toml
  preset = "default"                      # stock-neutral (NOT basic[under-reports]/strict[over-reports])
  project-includes = ["<abs src dir>"]
  project-excludes = ["**/tests", "**/_vendor/**", "**/generated/**", "**/__pycache__"]
  python-version = "3.X"
  python-platform = "linux"
  python-interpreter-path = "<project venv>/bin/python"   # resolves third-party deps
  check-unannotated-defs = true
  infer-return-types = "checked"
  ```
  - **CRITICAL gotcha (verified):** a loose file with no project root falls back to `preset=basic` which silences almost all errors → false-clean. ALWAYS supply our `--config` with explicit `project-includes` + `preset="default"`. Verify with `dump-config`.
  - File-mode (positional args) IGNORES `project-includes`/`project-excludes` — use project mode.
  - First-party-only: only `project-includes` handles are error-reported; deps resolved via `site-package-path`/`python-interpreter-path`. **Do NOT pass `--check-all`/`-a`** (that would report deps too).
  - Keys are kebab-case.
- **Parse (JSON, stdout):** `{"errors":[{...,"name","severity","path",...}]}`. diagnostics = count of `severity=="error"` (the array includes directives like reveal_type; filter by severity). `code` is a dummy `-2`, ignore. Files count NOT in JSON → use `--summary=full` stderr `"N modules"`.
- **Exit codes:** 0 clean · **1 overloaded** (diagnostics OR fatal config/IO via anyhow) · 3 infra → `failed_env` · 101 panic → `failed_crash`. Disambiguate exit 1: parseable JSON with ≥1 error → `diagnostics`; no parseable JSON / stderr has "Fatal configuration error"/"finding Python interpreter" → `failed_env`. (Exit 2 reserved Meta-internal, won't appear OSS.)
- **Threads:** `--threads 1` / `PYREFLY_THREADS=1` = **HARD** cap (rayon pool num_threads(1)). `RAYON_NUM_THREADS` is NOT honored. honesty flag true.
- **Cache:** stateless `check` (incremental only in `--watch`). `clear_cache`/`prepare_command` no-ops.
- **Parse-sanity:** files>0 via `--summary=full` stderr `N modules`; `exit 0 + 0 files` = false-clean (mis-scoped includes) → treat as failure.

---

## Implications for the adapter design (Plan 2)

1. **`parse()` is genuinely per-tool** — some read JSON stdout, some must read a stderr summary line; `files` may be `None` (ty without `-v` gate, etc.). The interface stays `(diagnostics, files)` but each adapter implements its own extraction, version-guarded.
2. **`classify_exit()` is genuinely per-tool** — overloaded codes (mypy 2, pyrefly 1) need output/stderr disambiguation, not just the integer.
3. **Normalized config is generated to a temp/tool-controlled location per project** and injected via each tool's suppression hook — none rely on the project's own config. A small `NormalizedConfig` value object (python_version, platform, src roots, exclude globs, venv path) feeds each adapter's config/flag renderer.
4. **`install()` verifies the expected distribution** (mypy compiled; pyright+Node pinned; ty/pyrefly version) and records it for the lock manifest (§9).
5. **`parallelism_cap()` honesty flags:** pyrefly hard, mypy hard-ish (single-proc), pyright soft, ty soft.
6. **Version pinning is load-bearing** — record every tool's resolved version + (pyright) Node version in the result; re-verify flags on bump (mypy 2.x vs 1.x; ty 0.0.x churn).
7. **Plan 2 tests** run against a tiny in-repo fixture project (stdlib-only, clean + error variants) — no third-party deps / envman needed yet (that's Plan 3). Third-party resolution flags are implemented now, exercised fully in Plan 3.
