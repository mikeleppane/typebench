# Plan 5 — Results Envelope · Suite Orchestration · Lock Manifest · Renderer · GH Pages — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the single-record engine into a publishable benchmark: a results **envelope** wrapping many records, a **suite orchestrator** that runs the (project × tool × thread-mode) matrix behind a preflight gate, the last **lock-manifest** schema enrichment so every record is reproducible, and a **renderer** that emits the auto-generated README tables + a GitHub Pages trend site from the committed JSON history.

**Architecture:** A new pydantic `ResultsEnvelope` wraps `list[RunResult]` + suite metadata. `RunResult` gains lean per-record reproducibility scalars (SHA, lock/config hashes, tool install source, canonical counts, over-report flag) — the frozen dep *contents* stay in the committed `corpus/locks/*.txt` (pinned by `lock_hash`), not duplicated per record. `EnvFingerprint` expands with node/npm/uv versions, total memory, and a cgroup-v2 flag. Code-LOC for the headline kLOC/s comes from **tokei** counted over the *exact* canonical file set `counting.py` already walks. A new `suite.py` loops the matrix (shardable via `--shard i/n`), gates each project on `preflight`, stamps a `RunManifest` onto every record, and writes `results/<date>.json`. A new `renderer.py` (pure, golden-tested) renders the README markdown block and `site/data/trends.json` (raw + calibration-normalized + inter-checker-ratio series); normalization uses a **fixed per-CPU-model calibration anchor** so published chart points never move retroactively. The GH Pages site is committed static assets (`index.html` + vanilla JS + vendored Chart.js) that fetch `trends.json`.

**Tech Stack:** Python 3.12, pydantic v2, Typer, `tokei` (Rust code-counter, availability-gated with a physical-LOC fallback), the existing `hyperfine`/cgroup/`taskset` measurement stack, Chart.js (vendored), pytest. No new **pip** dependency; `tokei` is a system tool like `hyperfine`.

---

## Scope (locked during brainstorm — 6 confirmed decisions, 2026-06-08)

**Confirmed by user (AskUserQuestion, 2026-06-08):**
1. **Single Plan 5** — one plan/branch covering data layer + renderer (not split 5A/5B).
2. **Lean per-record manifest + committed locks** — `RunResult` stays self-contained with scalar hashes + runtime versions; the full frozen dep list is NOT duplicated per record (it lives in committed `corpus/locks/*.txt`, pinned by `lock_hash`). The envelope wraps records + suite metadata. `env` stays ON `RunResult` (no breaking move).
3. **Fixed per-CPU-model calibration anchor** — the first official run on each CPU model freezes that model's calibration anchor; `normalized = raw × (anchor_calib / run_calib)`. Published points never move retroactively; hardware cancels within a model.
4. **tokei** (not scc) for code-LOC. The spec text said "scc"; the tool brand is not a locked policy. Documented as a minor deviation.
5. **Minimal `--shard i/n`** matrix partition built now (deterministic round-robin; stub-testable; CI-ready), strategy documented.
6. **Vendored Chart.js** for GH Pages (committed, not CDN — offline + no third-party network dep on a credibility site).

**IN:**
- `ResultsEnvelope` model + `RunResult` enrichment scalars + `EnvFingerprint` expansion; `schema_version` **2 → 3**.
- `config_hash` — machine-independent hash of the *logical* normalized config (repo-relative roots, excludes, py-version, platform), never abspaths/venv.
- `detect_env` populates `node_version`/`npm_version`/`uv_version`/`mem_total_bytes`/`cgroup_v2`.
- tokei code-LOC over the exact canonical file set + file-set reconciliation + physical-LOC fallback + `loc_denominator` honesty flag; `PreparedProject.canonical_code_loc`.
- Per-adapter `install_source`.
- `RunManifest` + collector stamping; `typebench run` corpus-mode stamps it.
- `suite.py`: matrix builder, `--shard i/n`, `run_suite` orchestrator (prepare → preflight gate → run cells → envelope), suite-version from `suite.toml`.
- `typebench suite` + `typebench render` CLI commands.
- `renderer.py`: README markdown block + `trends.json` (raw + per-CPU-model-anchor-normalized + inter-checker-ratio) + corpus-bump markers; throughput uses code-LOC denominator with over-report withholding; diagnostics kept out of headline; `parallel_efficiency` labelled cross-pass.
- GH Pages static assets (`site/index.html`, `site/app.js`, vendored `site/vendor/chart.umd.min.js`); README between `<!-- TYPEBENCH:BEGIN/END -->` markers.
- Real httpx ×4 suite bench → envelope → render, before "done."

**OUT (later plans / not this one):**
- **GH Pages *deployment* (the Actions workflow that publishes `site/`)** — that is CI automation = **Plan 6**. Plan 5 builds the site assets + the generated `trends.json`; local preview is `python -m http.server` in `site/`.
- **Monthly corpus-bump job** + weekly scheduled run (Plan 6).
- **Warm/incremental track, more checkers, per-PR regression alerts** (later milestones, spec §14).
- **New `taxonomy.py` enum values** — none needed; ASK-FIRST if ever.
- **Larger corpus entries** — incremental, post-Plan-5 (corpus stays httpx for the verification bench).

## Key decisions (carry into every task)

- **Decision A — Lean per-record manifest; deps via committed locks.** `RunResult` gains only scalars (`project_sha`, `lock_hash`, `config_hash`, `tool_install_source`, `canonical_files`, `canonical_loc`, `canonical_code_loc`, `loc_denominator`, `over_reports`) — all `| None`, defaulting None so every existing `RunResult(...)` in tests stays valid and manual `typebench run` (no corpus) leaves them None. The frozen dep *contents* are NOT in the record; `lock_hash` + the committed `corpus/locks/*.txt` is the source of truth. This keeps weekly git diffs small and avoids re-duplicating a project's dep list across its 8 (4 tools × 2 modes) records.
- **Decision B — `env` stays on `RunResult`.** The envelope wraps records; it does NOT lift `env` up (that would break the self-contained single-record `typebench run` output). `EnvFingerprint` is *expanded in place* with new optional fields.
- **Decision C — `config_hash` is machine-independent.** It hashes the *logical* config: **repo-relative** src_roots (from the `CorpusProject`, never the absolute checkout path), effective excludes, python_version, python_platform, and a `NORMALIZED_POLICY_VERSION` tag (bumped if the locked §6 policy ever changes). Hashing absolute paths or the venv would make the hash machine-dependent and useless for cross-run comparison.
- **Decision D — tokei counts the EXACT canonical file set.** `counting.py` owns the canonical `.py` walk (`first_party_files`, applying §6 dir-segment excludes). tokei is invoked over *that explicit file list* (chunked to stay under argv limits for giant projects), NOT tokei's own gitignore/exclude logic (`--no-ignore`). `count_code_loc` reconciles tokei's reported Python file count against `len(files)`; on divergence it returns `None` → physical-LOC fallback (`loc_denominator="physical"`), a visible honest degradation, never a silent wrong denominator. The §8-locked denominator (the canonical file set) is therefore identical for the file count and the code-LOC.
- **Decision E — `loc_denominator` honesty flag.** `"code"` when tokei produced a reconciled `canonical_code_loc`, else `"physical"`. The renderer uses code-LOC for the headline kLOC/s and footnotes any `"physical"` row.
- **Decision F — tokei is a soft system dep, gated with fallback.** Like `hyperfine`/`systemd-run`, tokei is probed via `shutil.which`; absent → `canonical_code_loc=None`, `loc_denominator` resolves to `"physical"`. Never a hard requirement; never raises into prepare. (Real bench installs tokei to exercise the code path.)
- **Decision G — Project-level preflight gate (spec §12).** `run_suite` excludes a project from a run iff `preflight_project(...).ready` is False (any tool not measured-success, or a reliable-count tool under-scoped). An excluded project still EMITS one `FAILED_ENV` record per cell carrying the preflight detail — "didn't compete" must be *visible*, never silently absent (§7/§12). `throughput_review_required`/`over_reports` does NOT block readiness (it only flags the kLOC/s caveat).
- **Decision H — One calibration per suite invocation.** The calibration baseline is the per-VM hardware scalar for the whole run; `run_suite` calibrates ONCE and attaches the same `CalibrationStats` to every record (running it per-cell would be wasteful and noisier). Matches §5.7 "each weekly run lands on a VM."
- **Decision I — Fixed per-CPU-model anchor, computed at render time over history.** `cpu_model_anchors(history)` = for each CPU model, the calibration `raw_median_s` of the *earliest* envelope (by `generated_at`) that has a run on that model. `normalized = raw × (anchor / run_calib)`. A single historical point normalizes to itself (factor ≈ 1.0). Anchors only ever ADD (new CPU model seeds its own), so a published point's normalized value never changes when later data arrives. **Operational invariant (enforced by Plan 6's scheduled job):** this guarantee holds ONLY for append-only, monotonically-dated history. Never introduce an envelope whose `generated_at` predates an existing envelope for the same CPU model — backfilling an earlier-dated run reseats that model's anchor and retroactively shifts every published normalized point on it.
- **Decision J — `schema_version` 2 → 3 (RunResult) + new `ResultsEnvelope.schema_version = 1`.** RunResult's record shape changes (new scalars), so bump its version. The envelope is a NEW container with its own version (starts at 1). Update EVERY existing `schema_version == 2` assertion to `== 3` in the same task or the gate fails (grep step included).
- **Decision K — Renderer is pure + golden-tested.** `renderer.py` functions take models and return `str`/`dict`; no filesystem I/O inside them (the CLI does I/O). Golden tests pin the exact README markdown and `trends.json` shape. Diagnostics count is NEVER a column in headline tables (§8). `parallel_efficiency` is labelled "(cross-pass: cold-cpu ÷ warm-wall)", not a within-run figure (collector comment).
- **Decision L — GH Pages *assets* in, *deploy* out.** Plan 5 commits `site/` (static) + generates `site/data/trends.json`. The Pages-publishing GitHub Action is Plan 6. README auto-block lives between `<!-- TYPEBENCH:BEGIN -->`/`<!-- TYPEBENCH:END -->`; hand-written methodology prose stays OUTSIDE the markers so regeneration never clobbers it (§11).
- **Measured path stays pydantic-free.** No task touches `wrapper.py`/`taxonomy.py`/`measure.py`/`calibration.py` import graphs. `counting.py` gains a `subprocess` tokei call but is NOT on the measured (hyperfine per-run) path, so this is fine. The existing import-guard tests must stay green.

## File structure

- **Create `src/typebench/suite.py`** — `SuiteCell`, `build_matrix`, `shard`, `RunManifest` consumers, `run_suite` orchestrator (injectable `prepare`/`preflight`/`run_one`/`calibrate_fn`/`adapter_factory` seams). Pydantic via `models` (off the measured path).
- **Create `src/typebench/renderer.py`** — `render_readme(envelope)`, `cpu_model_anchors(history)`, `build_trends(history)`, plus small formatters (`_kloc_s`, `_peak_mem_mb`). Pure; no I/O.
- **Create GH Pages assets** — `site/index.html`, `site/app.js`, `site/vendor/chart.umd.min.js` (vendored), `site/data/.gitkeep`.
- **Modify `src/typebench/models.py`** — expand `EnvFingerprint`; add `RunResult` scalars + bump `schema_version` to 3; add `PreparedProject.canonical_code_loc`; add `ResultsEnvelope`.
- **Modify `src/typebench/normalized_config.py`** — `NORMALIZED_POLICY_VERSION` + `config_hash(...)`.
- **Modify `src/typebench/env.py`** — populate the expanded fingerprint.
- **Modify `src/typebench/counting.py`** — `first_party_files()` (the canonical walk, reused) + `count_code_loc(files)` (tokei).
- **Modify `src/typebench/envman.py`** — compute + persist `canonical_code_loc`.
- **Modify `src/typebench/adapters/base.py` + the 5 adapters** — `install_source` class attribute on the Protocol + each adapter.
- **Modify `src/typebench/collector.py`** — `RunManifest` dataclass + stamp it onto `RunResult` (incl. derived `loc_denominator`).
- **Modify `src/typebench/corpus.py`** — `load_suite_version(path)` reading `[suite] version`.
- **Modify `src/typebench/cli.py`** — `run` corpus-mode stamps a `RunManifest`; new `suite` + `render` commands.
- **Modify `corpus/suite.toml`** — `[suite] version`.
- **Modify `README.md`** — seed the `<!-- TYPEBENCH:BEGIN/END -->` markers + a Methodology section + a "Trends" link.
- **Modify `AGENTS.md`** — layout (suite/renderer/site), scope-by-plan (Plan 5 done), commit scopes (`suite, renderer, site`), tokei note, schema now v3.
- **Tests:** `tests/test_suite.py` (new), `tests/test_renderer.py` (new), `tests/test_models.py`, `tests/test_normalized_config.py`, `tests/test_env.py`, `tests/test_counting.py`, `tests/test_envman.py`, `tests/test_collector.py`, `tests/test_corpus.py`, `tests/test_cli.py`, `tests/test_cli_suite.py` (new), `tests/test_cli_render.py` (new), and a small `tests/test_adapters_install_source.py` (new).

---

### Conventions for EVERY test edit below (the gate enforces these)

Snippets use an "append to the test file" style for readability. When applying:

1. **Imports go at the TOP of the file, merged + sorted** (ruff `E402`/`I001`). Hoist every `import` shown in a snippet into the file's existing top import block. Imports *inside a function body* shown intentionally (e.g. lazy) stay local.
2. **Annotate every new helper, fixture, inner function** — pyrefly `preset = "strict"` runs over `src` + `tests`. Use `monkeypatch: pytest.MonkeyPatch`, `tmp_path: Path`, concrete return types.
3. **`monkeypatch.setattr(..., raising=True)` (default) for seams that already exist.**
4. **Magic numbers in tests are fine** (`PLR2004` is per-file-ignored for `tests/**`); unused fixture args are fine (`ARG001`). In **`src/`**, justify any `# noqa: CODE — reason` against the existing `cli.run`/`run_single` precedent (e.g. `PLR0913` for many CLI options, `PLC0415` for a lazy import).

---

### Task 1: Schema — `EnvFingerprint` expansion, `RunResult` enrichment, `ResultsEnvelope`, bump to v3

**Files:**
- Modify: `src/typebench/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_models.py` (hoist imports of `ResultsEnvelope` to the top import block):

```python
def test_env_fingerprint_expands_with_optional_runtime_fields() -> None:
    # New §9 fields default so existing hand-built fingerprints stay valid.
    base = EnvFingerprint(
        os="Linux", kernel="6.6", cpu_model="x", core_count=8, python_version="3.12.0"
    )
    assert base.node_version is None
    assert base.npm_version is None
    assert base.uv_version is None
    assert base.mem_total_bytes is None
    assert base.cgroup_v2 is False
    full = EnvFingerprint(
        os="Linux", kernel="6.6", cpu_model="x", core_count=8, python_version="3.12.0",
        node_version="v20.1.0", npm_version="10.2.0", uv_version="uv 0.4.0",
        mem_total_bytes=16_000_000_000, cgroup_v2=True,
    )
    assert EnvFingerprint.model_validate_json(full.model_dump_json()) == full


def test_run_result_enrichment_scalars_default_none_and_round_trip() -> None:
    base = RunResult(
        tool="mypy", tool_version="1.0", project="httpx",
        thread_mode=ThreadMode.ALL_CORES, result_class=ResultClass.CLEAN,
        real_exit_code=0, env=_env(),
    )
    assert base.schema_version == 3
    for field in (
        base.project_sha, base.lock_hash, base.config_hash, base.tool_install_source,
        base.canonical_files, base.canonical_loc, base.canonical_code_loc,
        base.loc_denominator, base.over_reports,
    ):
        assert field is None
    rich = RunResult(
        tool="mypy", tool_version="1.0", project="httpx",
        thread_mode=ThreadMode.ALL_CORES, result_class=ResultClass.CLEAN,
        real_exit_code=0, env=_env(),
        project_sha="80960fa", lock_hash="abc123", config_hash="def456",
        tool_install_source="PyPI wheel (mypyc-compiled)",
        canonical_files=23, canonical_loc=4000, canonical_code_loc=3200,
        loc_denominator="code", over_reports=False,
    )
    assert RunResult.model_validate_json(rich.model_dump_json()) == rich


def test_results_envelope_wraps_records() -> None:
    rec = RunResult(
        tool="stub", tool_version="0", project="demo",
        thread_mode=ThreadMode.ALL_CORES, result_class=ResultClass.CLEAN,
        real_exit_code=0, env=_env(),
    )
    env = ResultsEnvelope(suite_version="2026-06-08", generated_at="2026-06-08T00:00:00Z", runs=[rec])
    assert env.schema_version == 1
    restored = ResultsEnvelope.model_validate_json(env.model_dump_json())
    assert restored == env
    assert len(restored.runs) == 1


def test_results_envelope_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ResultsEnvelope.model_validate(
            {"suite_version": "v", "generated_at": "t", "runs": [], "bogus": 1}
        )
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_models.py -k "enrichment or envelope or expands" -v`
Expected: FAIL (`ImportError: ResultsEnvelope`, `AttributeError`/`ValidationError` on new fields, `schema_version == 3` mismatch).

- [ ] **Step 3: Implement the schema changes**

In `src/typebench/models.py`, expand `EnvFingerprint`:

```python
class EnvFingerprint(BaseModel):
    """Minimal environment stamp (spec §9). Runtime versions + memory + cgroup
    availability added in Plan 5 for full reproducibility + CPU-model trend
    segmentation. New fields are optional so a non-Linux/CI fingerprint stays valid."""

    model_config = ConfigDict(extra="forbid")

    os: str
    kernel: str
    cpu_model: str
    core_count: int
    python_version: str
    node_version: str | None = None  # pyright's Node runtime (spec §9)
    npm_version: str | None = None
    uv_version: str | None = None
    mem_total_bytes: int | None = None  # /proc/meminfo MemTotal
    cgroup_v2: bool = False  # whether the memory pass could run here (spec §5.5/§15)
```

Add the enrichment scalars to `RunResult` (bump default to 3 and update its docstring), placed after `env`:

```python
    schema_version: int = 3
    ...
    env: EnvFingerprint
    # --- Lock manifest (spec §9), Plan 5. Lean per-record scalars: the frozen dep
    # CONTENTS live in the committed corpus/locks/*.txt, pinned by lock_hash; only
    # the hashes + identifying versions are duplicated here. All None in manual
    # `typebench run` (no corpus); stamped by the suite orchestrator / corpus-mode run.
    project_sha: str | None = None
    lock_hash: str | None = None
    config_hash: str | None = None  # machine-independent logical-config hash (§6)
    tool_install_source: str | None = None  # "PyPI wheel (mypyc)", "npm + Node", ...
    # Canonical analyzed-set denominator (§8), identical across tools. canonical_code_loc
    # is tokei code-LOC (blanks+comments excluded); loc_denominator records which the
    # headline kLOC/s used ("code" when tokei reconciled, else "physical").
    canonical_files: int | None = None
    canonical_loc: int | None = None
    canonical_code_loc: int | None = None
    loc_denominator: str | None = None  # "code" | "physical"
    # From preflight (§12): self-reported files > canonical -> withhold/caveat kLOC/s.
    over_reports: bool | None = None
```

Add the `canonical_code_loc` field to `PreparedProject` (after `canonical_loc`):

```python
    canonical_loc: int
    canonical_code_loc: int | None = None  # tokei code-LOC; None when tokei unavailable
```

Add the envelope class and export it:

```python
class ResultsEnvelope(BaseModel):
    """The committed results file (spec §7/§11): many records + suite metadata.
    `typebench run` writes ONE self-contained RunResult; `typebench suite` writes
    this envelope as results/<date>.json (git history = the time-series)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    suite_version: str
    generated_at: str  # ISO-8601 UTC, stamped by the CLI
    runs: list[RunResult]
```

Add `"ResultsEnvelope"` to `__all__`.

- [ ] **Step 4: Update every stale `schema_version == 2` assertion**

Run: `grep -rn "schema_version == 2" tests/ src/`
Expected matches in `tests/test_models.py` (3 places: the v2 round-trip, `test_run_result_v2_carries_*`, `test_run_result_v2_defaults_are_none`). Change each `== 2` to `== 3`. Re-run the grep until it returns nothing.

- [ ] **Step 5: Run the model tests**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (all, including the updated v2→v3 assertions).

- [ ] **Step 6: Commit**

```bash
git add src/typebench/models.py tests/test_models.py
git commit -m "feat(models): RunResult v3 lock-manifest scalars + ResultsEnvelope + EnvFingerprint expansion

Lean per-record reproducibility (sha/lock/config hashes, install source, canonical
counts, over_reports) keeps frozen deps in committed locks (pinned by lock_hash).
EnvFingerprint gains node/npm/uv/mem/cgroup_v2. New envelope wraps records. Bumps
RunResult schema_version 2 -> 3; updates round-trip assertions."
```

---

### Task 2: `config_hash` — machine-independent logical-config hash

**Files:**
- Modify: `src/typebench/normalized_config.py`
- Test: `tests/test_normalized_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_normalized_config.py` (hoist imports):

```python
from typebench.normalized_config import NORMALIZED_POLICY_VERSION, config_hash


def test_config_hash_is_stable_and_order_independent() -> None:
    a = config_hash(("httpx", "src"), ("**/tests/**", "**/vendor/**"), "3.12", "linux")
    b = config_hash(("src", "httpx"), ("**/vendor/**", "**/tests/**"), "3.12", "linux")
    assert a == b  # sorted inputs -> order independent
    assert len(a) == 64  # sha256 hex


def test_config_hash_ignores_absolute_paths() -> None:
    # The hash must be machine-independent: a different checkout prefix is irrelevant
    # because callers pass REPO-RELATIVE roots, never absolute checkout paths.
    rel = config_hash(("httpx",), ("**/tests/**",), "3.12", "linux")
    assert rel == config_hash(("httpx",), ("**/tests/**",), "3.12", "linux")


def test_config_hash_changes_on_policy_or_inputs() -> None:
    base = config_hash(("httpx",), ("**/tests/**",), "3.12", "linux")
    assert base != config_hash(("httpx",), ("**/tests/**",), "3.13", "linux")
    assert base != config_hash(("httpx",), ("**/tests/**",), "3.12", "darwin")
    assert base != config_hash(("other",), ("**/tests/**",), "3.12", "linux")
    assert NORMALIZED_POLICY_VERSION  # non-empty tag is part of the payload
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_normalized_config.py -k config_hash -v`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement**

In `src/typebench/normalized_config.py`, add (with `import hashlib` at the top):

```python
# Bump when the LOCKED §6 policy set (flags/posture) changes, so config_hash
# distinguishes pre/post-policy runs even at identical inputs.
NORMALIZED_POLICY_VERSION = "v1"


def config_hash(
    src_roots: tuple[str, ...],
    exclude_globs: tuple[str, ...],
    python_version: str,
    python_platform: str,
) -> str:
    """Stable, machine-independent hash of the resolved normalized config (spec §6).

    Callers MUST pass REPO-RELATIVE src_roots (e.g. CorpusProject.src_roots), never
    the absolute checkout path or the venv — those are machine-specific and would
    make the hash non-comparable across runs/VMs. Inputs are sorted so ordering is
    irrelevant; NORMALIZED_POLICY_VERSION folds the locked policy revision in."""
    payload = "\n".join(
        [
            NORMALIZED_POLICY_VERSION,
            "\x00".join(sorted(src_roots)),
            "\x00".join(sorted(exclude_globs)),
            python_version,
            python_platform,
        ]
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_normalized_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/typebench/normalized_config.py tests/test_normalized_config.py
git commit -m "feat(config): machine-independent config_hash over the logical normalized config

Hashes repo-relative roots + excludes + py-version/platform + a policy-version tag;
never absolute paths/venv, so the hash is comparable across VMs (spec §6/§9)."
```

---

### Task 3: `detect_env` populates the expanded fingerprint

**Files:**
- Modify: `src/typebench/env.py`
- Test: `tests/test_env.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_env.py` (hoist imports of `pytest`, `env` module):

```python
def test_detect_env_populates_runtime_and_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    # Stub the seams so the test is hermetic (no real node/npm/uv required).
    monkeypatch.setattr(env, "_cmd_version", lambda argv: f"{argv[0]}-9.9", raising=True)
    monkeypatch.setattr(env, "_mem_total_bytes", lambda: 16_000_000_000, raising=True)
    monkeypatch.setattr(env, "_cgroup_v2", lambda: True, raising=True)
    fp = env.detect_env()
    assert fp.node_version == "node-9.9"
    assert fp.npm_version == "npm-9.9"
    assert fp.uv_version == "uv-9.9"
    assert fp.mem_total_bytes == 16_000_000_000
    assert fp.cgroup_v2 is True


def test_mem_total_bytes_parses_meminfo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       16384000 kB\nMemFree: 100 kB\n")
    monkeypatch.setattr(env, "_MEMINFO", meminfo, raising=True)
    assert env._mem_total_bytes() == 16384000 * 1024
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_env.py -v`
Expected: FAIL (`AttributeError: _cmd_version` / new fields absent).

- [ ] **Step 3: Implement**

Replace `src/typebench/env.py` body's `detect_env` and add helpers (add `import subprocess`):

```python
_MEMINFO = Path("/proc/meminfo")
_CGROUP_CONTROLLERS = Path("/sys/fs/cgroup/cgroup.controllers")


def _cmd_version(argv: list[str]) -> str | None:
    """First line of `<tool> --version`, or None if the tool is missing. No-raise:
    detect_env runs during RunResult assembly and must never crash a record."""
    try:
        out = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    text = out.stdout.strip() or out.stderr.strip()
    return text.splitlines()[0] if text else None


def _mem_total_bytes() -> int | None:
    if not _MEMINFO.exists():
        return None
    for line in _MEMINFO.read_text().splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1]) * 1024  # kB -> bytes
    return None


def _cgroup_v2() -> bool:
    return _CGROUP_CONTROLLERS.exists()


def detect_env() -> EnvFingerprint:
    return EnvFingerprint(
        os=platform.system(),
        kernel=platform.release(),
        cpu_model=_cpu_model(),
        core_count=os.cpu_count() or 1,
        python_version=platform.python_version(),
        node_version=_cmd_version(["node", "--version"]),
        npm_version=_cmd_version(["npm", "--version"]),
        uv_version=_cmd_version(["uv", "--version"]),
        mem_total_bytes=_mem_total_bytes(),
        cgroup_v2=_cgroup_v2(),
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_env.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/typebench/env.py tests/test_env.py
git commit -m "feat(env): expand fingerprint with node/npm/uv versions, mem total, cgroup_v2 (spec §9)"
```

---

### Task 4: tokei code-LOC over the exact canonical file set

**Files:**
- Modify: `src/typebench/counting.py`
- Test: `tests/test_counting.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_counting.py` (hoist imports of `pytest`, `shutil`, and `counting` module / `count_code_loc`, `first_party_files`):

```python
def test_first_party_files_returns_the_canonical_set(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    _write(pkg / "a.py", "x = 1\n")
    _write(pkg / "tests" / "t.py", "assert True\n")
    files = first_party_files([pkg], DEFAULT_EXCLUDES)
    assert [f.name for f in files] == ["a.py"]  # tests/ excluded


@pytest.mark.skipif(shutil.which("tokei") is None, reason="tokei not installed")
def test_count_code_loc_excludes_comments_and_blanks(tmp_path: Path) -> None:
    f = tmp_path / "m.py"
    f.write_text("# comment\n\nx = 1\ny = 2\n")  # 1 comment, 1 blank, 2 code
    assert count_code_loc([f]) == 2


def test_count_code_loc_returns_none_without_tokei(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "m.py"
    f.write_text("x = 1\n")
    monkeypatch.setattr(counting.shutil, "which", lambda _name: None, raising=True)
    assert count_code_loc([f]) is None


def test_count_code_loc_none_on_empty_input(tmp_path: Path) -> None:
    assert count_code_loc([]) is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_counting.py -v`
Expected: FAIL (`ImportError: first_party_files`/`count_code_loc`).

- [ ] **Step 3: Implement**

Refactor `src/typebench/counting.py` so the canonical walk is shared, and add the tokei counter (add `import json`, `import shutil`, `import subprocess` at the top). Keep `Path` under `TYPE_CHECKING` exactly as the existing module does: the new functions use `Path` only in annotations (no `Path(...)` construction at runtime), so with `from __future__ import annotations` a runtime import would trip ruff `TC003` (the `TC` ruleset is enabled — `pyproject.toml`):

```python
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

# argv chunk size so a giant project's file list stays under OS argv limits.
_TOKEI_CHUNK = 500


def first_party_files(roots: list[Path], exclude_globs: tuple[str, ...]) -> list[Path]:
    """The canonical first-party `.py` set (spec §6/§8): walk `roots`, drop any path
    under an excluded dir-segment. This single walk is the ONE denominator basis —
    both count_first_party (file/physical-LOC) and count_code_loc (tokei) consume it,
    so the file count and the code-LOC describe the identical file set (§8 locked)."""
    excluded = _excluded_dir_names(exclude_globs)
    out: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if excluded & set(path.relative_to(root).parts):
                continue
            out.append(path)
    return out


def count_first_party(roots: list[Path], exclude_globs: tuple[str, ...]) -> FileCount:
    """Count `.py` files and physical lines under `roots`."""
    files = first_party_files(roots, exclude_globs)
    loc = sum(
        len(path.read_text(encoding="utf-8", errors="replace").splitlines()) for path in files
    )
    return FileCount(files=len(files), loc=loc)


def count_code_loc(files: list[Path]) -> int | None:
    """tokei Python code-LOC (blanks+comments excluded) over EXACTLY `files`.

    Returns None — caller falls back to physical-LOC — when tokei is absent, the
    input is empty, tokei errors, or its reported Python file count does not match
    `len(files)` (a set mismatch would mean the code-LOC and the file-count
    denominator describe different sets, a §8 neutrality defect; fail to None loudly
    rather than publish a mismatched denominator). `--no-ignore` so .gitignore can't
    silently drop files; `--types Python` selects the Python bucket."""
    if not files or shutil.which("tokei") is None:
        return None
    total_code = 0
    total_reports = 0
    for start in range(0, len(files), _TOKEI_CHUNK):
        chunk = files[start : start + _TOKEI_CHUNK]
        try:
            out = subprocess.run(
                ["tokei", "--output", "json", "--no-ignore", "--types", "Python",
                 *[str(p) for p in chunk]],
                capture_output=True, text=True, check=False, timeout=300,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if out.returncode != 0:
            return None
        try:
            data = json.loads(out.stdout)
        except ValueError:
            return None
        python = data.get("Python")
        if not isinstance(python, dict):
            return None
        code = python.get("code")
        reports = python.get("reports")
        if not isinstance(code, int) or not isinstance(reports, list):
            return None
        total_code += code
        total_reports += len(reports)
    if total_reports != len(files):
        return None  # set mismatch -> physical fallback (visible via loc_denominator)
    return total_code
```

(Keep the existing `FileCount` dataclass and `_excluded_dir_names`, and keep `Path` under the `TYPE_CHECKING` block it already lives in — it stays annotation-only. Update the module docstring's "scc-style code-LOC for headline kLOC/s is the renderer's job in Plan 5" line to "tokei code-LOC is computed here in Plan 5 and consumed by the renderer.")

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_counting.py -v`
Expected: PASS (the comment/blank test runs because tokei is installed; if a runner lacks tokei it skips).

- [ ] **Step 5: Commit**

```bash
git add src/typebench/counting.py tests/test_counting.py
git commit -m "feat(counting): tokei code-LOC over the exact canonical file set + physical fallback

first_party_files() is the single canonical walk both the file count and tokei
consume, so the §8 denominator is one set. count_code_loc reconciles tokei's Python
file count against len(files); divergence/absence -> None (physical fallback)."
```

---

### Task 5: envman persists `canonical_code_loc`

**Files:**
- Modify: `src/typebench/envman.py`
- Test: `tests/test_envman.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_envman.py` (reuse the file's existing fake `Runner`/fixtures; hoist imports). This asserts prepare wires code-LOC through to the sidecar:

```python
def test_prepare_records_code_loc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Drive prepare with a stub Runner (existing pattern in this file) and stub the
    # tokei seam so the test is hermetic and asserts wiring, not tokei itself.
    monkeypatch.setattr(envman, "count_code_loc", lambda _files: 1234, raising=True)
    prepared = _prepare_with_stub_runner(tmp_path)  # existing helper building a fake checkout
    assert prepared.canonical_code_loc == 1234
```

> Implementation note for the executor: `tests/test_envman.py` already builds prepared projects through an injected `Runner` and a fake checkout. Reuse that machinery for `_prepare_with_stub_runner` (or inline it); the only new assertion is `canonical_code_loc`. If the existing happy-path test already returns a `PreparedProject`, simply add `monkeypatch.setattr(envman, "count_code_loc", ...)` to it and assert the field instead of writing a brand-new fixture.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_envman.py -k code_loc -v`
Expected: FAIL (`AttributeError`/`count_code_loc` not imported in envman).

- [ ] **Step 3: Implement**

In `src/typebench/envman.py`:
- Import: `from typebench.counting import count_code_loc, count_first_party, first_party_files`
- In `prepare_project`, after computing `counted = count_first_party(roots, excludes)` and the `counted.files == 0` guard, add:

```python
        code_loc = count_code_loc(first_party_files(roots, excludes))
```

- Pass it into the `PreparedProject(...)` construction:

```python
            canonical_files=counted.files,
            canonical_loc=counted.loc,
            canonical_code_loc=code_loc,
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_envman.py -v`
Expected: PASS (all envman tests; the new field is optional so existing sidecar fixtures stay valid).

- [ ] **Step 5: Commit**

```bash
git add src/typebench/envman.py tests/test_envman.py
git commit -m "feat(envman): persist tokei canonical_code_loc on PreparedProject (spec §4/§8)"
```

---

### Task 6: per-adapter `install_source`

**Files:**
- Modify: `src/typebench/adapters/base.py`, `mypy.py`, `pyright.py`, `pyrefly.py`, `ty.py`, `stub.py`
- Test: `tests/test_adapters_install_source.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_adapters_install_source.py`:

```python
from typebench.adapters.mypy import MypyAdapter
from typebench.adapters.pyrefly import PyreflyAdapter
from typebench.adapters.pyright import PyrightAdapter
from typebench.adapters.stub import StubAdapter
from typebench.adapters.ty import TyAdapter


def test_each_adapter_declares_an_install_source() -> None:
    assert MypyAdapter().install_source == "PyPI wheel (mypyc-compiled)"
    assert PyrightAdapter().install_source == "npm + Node"
    assert PyreflyAdapter().install_source == "PyPI wheel (Rust)"
    assert TyAdapter().install_source == "PyPI wheel (Rust)"
    assert StubAdapter().install_source == "builtin"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_adapters_install_source.py -v`
Expected: FAIL (`AttributeError: install_source`).

- [ ] **Step 3: Implement**

In `src/typebench/adapters/base.py`, add to the `Adapter` Protocol (after `name: str`):

```python
    name: str
    install_source: str  # §9 manifest: "PyPI wheel (mypyc)", "npm + Node", ...
```

Add the class attribute to each adapter, next to `name`:
- `mypy.py`: `install_source = "PyPI wheel (mypyc-compiled)"`
- `pyright.py`: `install_source = "npm + Node"`
- `pyrefly.py`: `install_source = "PyPI wheel (Rust)"`
- `ty.py`: `install_source = "PyPI wheel (Rust)"`
- `stub.py`: `install_source = "builtin"`

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_adapters_install_source.py -v && uv run pyrefly check`
Expected: PASS + pyrefly 0 errors (the Protocol attribute is satisfied by every adapter).

- [ ] **Step 5: Commit**

```bash
git add src/typebench/adapters/ tests/test_adapters_install_source.py
git commit -m "feat(adapters): declare install_source per adapter for the §9 lock manifest"
```

---

### Task 7: `RunManifest` + collector stamping

**Files:**
- Modify: `src/typebench/collector.py`
- Test: `tests/test_collector.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_collector.py` (hoist `RunManifest` import from `typebench.collector`):

```python
def test_run_single_stamps_manifest_fields() -> None:
    adapter = StubAdapter(exit_code=0, diagnostics=0, files=1)
    manifest = RunManifest(
        project_sha="80960fa", lock_hash="lh", config_hash="ch",
        canonical_files=23, canonical_loc=4000, canonical_code_loc=3200,
        tool_install_source="builtin", over_reports=False,
    )
    result = run_single(
        adapter, project="httpx", config=NormalizedConfig(),
        thread_mode=ThreadMode.ALL_CORES, warmup=1, runs=1, timeout=10,
        manifest=manifest,
    )
    assert result.project_sha == "80960fa"
    assert result.lock_hash == "lh"
    assert result.config_hash == "ch"
    assert result.tool_install_source == "builtin"
    assert result.canonical_files == 23
    assert result.canonical_code_loc == 3200
    assert result.loc_denominator == "code"  # code_loc present
    assert result.over_reports is False


def test_run_single_loc_denominator_physical_when_no_code_loc() -> None:
    manifest = RunManifest(canonical_files=10, canonical_loc=500, canonical_code_loc=None)
    result = run_single(
        StubAdapter(exit_code=0, files=1), project="demo", config=NormalizedConfig(),
        thread_mode=ThreadMode.ALL_CORES, warmup=1, runs=1, timeout=10, manifest=manifest,
    )
    assert result.loc_denominator == "physical"
    assert result.canonical_code_loc is None


def test_run_single_no_manifest_leaves_scalars_none() -> None:
    result = run_single(
        StubAdapter(exit_code=0, files=1), project="demo", config=NormalizedConfig(),
        thread_mode=ThreadMode.ALL_CORES, warmup=1, runs=1, timeout=10,
    )
    assert result.project_sha is None
    assert result.loc_denominator is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_collector.py -k manifest -v`
Expected: FAIL (`ImportError: RunManifest` / unexpected kwarg).

- [ ] **Step 3: Implement**

In `src/typebench/collector.py`:
- Add `from dataclasses import dataclass` and define the bundle near the top:

```python
@dataclass(frozen=True)
class RunManifest:
    """Per-cell reproducibility data the collector stamps onto a RunResult (spec §9).
    Built by the suite orchestrator (and corpus-mode `typebench run`); None in manual
    mode. Carries scalars only — frozen dep CONTENTS stay in the committed lockfile."""

    project_sha: str | None = None
    lock_hash: str | None = None
    config_hash: str | None = None
    canonical_files: int | None = None
    canonical_loc: int | None = None
    canonical_code_loc: int | None = None
    tool_install_source: str | None = None
    over_reports: bool | None = None
```

- Add a `manifest: RunManifest | None = None` parameter to `run_single` (the existing `# noqa: PLR0913, PLR0915` already covers the extra arg — keep the noqa).
- Before the final `return RunResult(...)`, derive the denominator and the stamped values:

```python
        # Lock-manifest stamp (spec §9). loc_denominator records which throughput
        # denominator the headline kLOC/s should use: "code" when tokei produced a
        # reconciled code-LOC, else "physical". None when no canonical denominator
        # is known at all (manual run without a corpus project).
        man = manifest or RunManifest()
        if man.canonical_files is None:
            loc_denominator = None
        elif man.canonical_code_loc is not None:
            loc_denominator = "code"
        else:
            loc_denominator = "physical"
```

- Extend the `return RunResult(...)` with:

```python
            calibration=calibration,
            env=detect_env(),
            project_sha=man.project_sha,
            lock_hash=man.lock_hash,
            config_hash=man.config_hash,
            tool_install_source=man.tool_install_source,
            canonical_files=man.canonical_files,
            canonical_loc=man.canonical_loc,
            canonical_code_loc=man.canonical_code_loc,
            loc_denominator=loc_denominator,
            over_reports=man.over_reports,
```

Apply the same stamping on the EARLY `return RunResult(...)` failure path (the `command construction failed` branch near the top): add the same manifest-derived kwargs there so a `failed{env}` record from a corpus run still carries its repro scalars. Compute `man`/`loc_denominator` once at the top of `run_single` (right after the `mem_runs` guard) so both return sites use it.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_collector.py -v`
Expected: PASS (all collector tests; existing calls without `manifest` still work, scalars None).

- [ ] **Step 5: Commit**

```bash
git add src/typebench/collector.py tests/test_collector.py
git commit -m "feat(collector): stamp RunManifest (sha/lock/config/install/canonical/over_reports) onto RunResult

Derives loc_denominator (code|physical|None) from the manifest. Both return sites
(success + early failed{env}) carry the repro scalars so a corpus failure record is
still reproducible (spec §9/§12)."
```

---

### Task 8: `typebench run` corpus-mode stamps the manifest

**Files:**
- Modify: `src/typebench/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`. Capture the `manifest` passed to `run_single` in corpus mode by stubbing `prepare_project` + `run_single`:

```python
def test_run_corpus_mode_builds_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from typebench.collector import RunManifest
    from typebench.corpus import CorpusProject
    from typebench.models import PreparedProject

    entry = CorpusProject(
        name="httpx", repo_url="https://x", sha="80960fa", tag="0.28.0",
        size_bucket="small", python_version="3.12", src_roots=("httpx",),
        install=("uv pip install .",),
    )
    prepared = PreparedProject(
        name="httpx", checkout=str(tmp_path / "repo"),
        venv_python=str(tmp_path / "venv/bin/python"), src_roots=(str(tmp_path / "repo/httpx"),),
        exclude_globs=("**/tests/**",), python_version="3.12", python_platform="linux",
        sha="80960fa", lock_hash="LH", frozen=("httpx==0.28.0",),
        canonical_files=23, canonical_loc=4000, canonical_code_loc=3200, fingerprint="fp",
    )
    monkeypatch.setattr(cli, "_lookup_project", lambda _c, _n: entry, raising=True)
    monkeypatch.setattr(cli, "prepare_project", lambda _e, _c: prepared, raising=True)

    captured: dict[str, object] = {}

    def fake_run_single(adapter: object, **kwargs: object) -> RunResult:
        captured.update(kwargs)
        return _fake_result()

    monkeypatch.setattr(cli, "run_single", fake_run_single)
    out = tmp_path / "r.json"
    suite = tmp_path / "suite.toml"
    suite.write_text("")  # _lookup_project is stubbed, contents unused
    result = _invoke_run(
        ["--tool", "mypy", "--corpus", str(suite), "--corpus-project", "httpx",
         "--output", str(out), "--no-calibrate", "--no-measure"]
    )
    assert result.exit_code == 0, result.output
    man = captured["manifest"]
    assert isinstance(man, RunManifest)
    assert man.project_sha == "80960fa"
    assert man.lock_hash == "LH"
    assert man.canonical_code_loc == 3200
    assert man.tool_install_source == "PyPI wheel (mypyc-compiled)"
    assert man.config_hash is not None and len(man.config_hash) == 64
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_cli.py -k corpus_mode_builds_manifest -v`
Expected: FAIL (`run_single` called without `manifest`).

- [ ] **Step 3: Implement**

In `src/typebench/cli.py`:
- Imports: `from typebench.collector import RunManifest, run_single` and `from typebench.normalized_config import DEFAULT_EXCLUDES, NormalizedConfig, config_hash`.
- Hoist `entry` so it is always bound: change the existing `prepared: PreparedProject | None = None` declaration (just before the `if corpus_project is not None:` branch) to also declare `entry: CorpusProject | None = None`, then inside the branch keep the existing `entry = _lookup_project(...)` assignment (no annotation). `CorpusProject` stays a `TYPE_CHECKING`-only import — with `from __future__ import annotations` the hoisted annotation is a string, so no runtime import is added.
- In `run`, the corpus branch already sets `entry`/`prepared`. Build a manifest there; default `manifest = None` for manual mode. After the `config = NormalizedConfig(...)` block and `adapter = factory()`:

```python
    manifest: RunManifest | None = None
    if entry is not None and prepared is not None:
        # Guard on `entry is not None` (NOT `corpus_project is not None`): pyrefly
        # strict does not correlate corpus_project-not-None with entry-bound, so it
        # would flag entry possibly-unbound. config_hash uses the REPO-RELATIVE
        # src_roots from the corpus entry (not prepared's absolute roots) so it is
        # machine-independent (Decision C). `run` does not preflight, so over_reports
        # is unknown here (None); the suite orchestrator stamps it.
        manifest = RunManifest(
            project_sha=prepared.sha,
            lock_hash=prepared.lock_hash,
            config_hash=config_hash(
                entry.src_roots, entry.effective_excludes(),
                entry.python_version, entry.python_platform,
            ),
            canonical_files=prepared.canonical_files,
            canonical_loc=prepared.canonical_loc,
            canonical_code_loc=prepared.canonical_code_loc,
            tool_install_source=adapter.install_source,
            over_reports=None,
        )
    result = run_single(
        adapter,
        project=project,
        config=config,
        thread_mode=thread_mode,
        warmup=warmup,
        runs=runs,
        timeout=timeout,
        mem_runs=mem_runs,
        measure_enabled=measure,
        calibration=calibration,
        manifest=manifest,
    )
```

> `entry` must remain referenceable after the corpus branch. The hoist above (`entry: CorpusProject | None = None` before the branch) makes it always bound, and the `if entry is not None and prepared is not None:` guard is what pyrefly strict narrows on — a guard of `corpus_project is not None` alone does NOT narrow `entry` and will fail `pyrefly check`.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/typebench/cli.py tests/test_cli.py
git commit -m "feat(cli): stamp a RunManifest in corpus-mode run (sha/lock/config_hash/install/canonical)"
```

---

### Task 9: suite matrix, `--shard`, and suite-version loader

**Files:**
- Create: `src/typebench/suite.py`
- Modify: `src/typebench/corpus.py`, `corpus/suite.toml`
- Test: `tests/test_suite.py` (new), `tests/test_corpus.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_suite.py`:

```python
import pytest

from typebench.models import ThreadMode
from typebench.suite import SuiteCell, build_matrix, shard


def test_build_matrix_is_project_major() -> None:
    cells = build_matrix(["a", "b"], ["mypy", "ty"], [ThreadMode.ALL_CORES, ThreadMode.ONE_CORE])
    assert len(cells) == 8
    assert cells[0] == SuiteCell("a", "mypy", ThreadMode.ALL_CORES)
    assert all(isinstance(c, SuiteCell) for c in cells)


def test_shard_partitions_disjointly_and_covers_all() -> None:
    cells = build_matrix(["a", "b", "c"], ["mypy", "ty"], [ThreadMode.ALL_CORES])
    s0 = shard(cells, 0, 3)
    s1 = shard(cells, 1, 3)
    s2 = shard(cells, 2, 3)
    assert set(s0) | set(s1) | set(s2) == set(cells)
    assert not (set(s0) & set(s1))
    assert len(s0) + len(s1) + len(s2) == len(cells)


def test_shard_total_one_is_identity() -> None:
    cells = build_matrix(["a"], ["mypy"], [ThreadMode.ALL_CORES])
    assert shard(cells, 0, 1) == cells


def test_shard_rejects_bad_index() -> None:
    cells = build_matrix(["a"], ["mypy"], [ThreadMode.ALL_CORES])
    with pytest.raises(ValueError):
        shard(cells, 3, 3)
    with pytest.raises(ValueError):
        shard(cells, 0, 0)
```

Append to `tests/test_corpus.py`:

```python
def test_load_suite_version_reads_suite_table(tmp_path: Path) -> None:
    from typebench.corpus import load_suite_version

    suite = tmp_path / "suite.toml"
    suite.write_text('[suite]\nversion = "2026-06-08"\n\n[[project]]\nname="x"\n')
    assert load_suite_version(suite) == "2026-06-08"


def test_load_suite_version_defaults_when_absent(tmp_path: Path) -> None:
    from typebench.corpus import load_suite_version

    suite = tmp_path / "suite.toml"
    suite.write_text("[[project]]\nname='x'\n")
    assert load_suite_version(suite) == "unversioned"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_suite.py tests/test_corpus.py -k "shard or matrix or suite_version" -v`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement**

Create `src/typebench/suite.py` (matrix + shard portion only for this task; the orchestrator lands in Task 10):

```python
"""Suite orchestration (spec §10/§11). Loops the (project × tool × thread-mode)
matrix behind the §12 preflight gate and writes a ResultsEnvelope. Off the measured
path; pydantic via `models` is fine here."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typebench.models import ThreadMode


@dataclass(frozen=True)
class SuiteCell:
    """One unit of the benchmark matrix."""

    project: str
    tool: str
    thread_mode: ThreadMode


def build_matrix(
    projects: list[str], tools: list[str], thread_modes: list[ThreadMode]
) -> list[SuiteCell]:
    """Project-major matrix so a project's clone/venv is reused across its cells."""
    return [
        SuiteCell(project, tool, mode)
        for project in projects
        for tool in tools
        for mode in thread_modes
    ]


def shard(cells: list[SuiteCell], index: int, total: int) -> list[SuiteCell]:
    """Deterministic round-robin partition (spec §10 sharding). `total=1` is the
    identity. Round-robin (not contiguous slices) spreads heavy/light cells evenly
    across shards so no single CI job inherits all the giant-bucket work."""
    if total < 1:
        raise ValueError(f"shard total must be >= 1, got {total}")
    if not 0 <= index < total:
        raise ValueError(f"shard index {index} out of range for total {total}")
    return [cell for position, cell in enumerate(cells) if position % total == index]
```

In `src/typebench/corpus.py`, add:

```python
def load_suite_version(path: Path) -> str:
    """Read `[suite] version` from suite.toml; 'unversioned' when absent (spec §9)."""
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    suite = raw.get("suite", {})
    version = suite.get("version") if isinstance(suite, dict) else None
    return str(version) if version else "unversioned"
```

(`Path` is currently a `TYPE_CHECKING`-only import in `corpus.py`; `load_suite_version` uses it only as an annotation, so no runtime import is needed — `from __future__ import annotations` is already present.)

In `corpus/suite.toml`, add at the very top (before the first `[[project]]`):

```toml
[suite]
version = "2026-06-08"
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_suite.py tests/test_corpus.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/typebench/suite.py src/typebench/corpus.py corpus/suite.toml tests/test_suite.py tests/test_corpus.py
git commit -m "feat(suite): matrix builder + round-robin --shard partition + suite-version loader (spec §10)"
```

---

### Task 10: `run_suite` orchestrator

**Files:**
- Modify: `src/typebench/suite.py`
- Test: `tests/test_suite.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_suite.py`. Drive the orchestrator entirely through injected seams + `StubAdapter` so it is hermetic (no clone/preflight/hyperfine):

```python
from pathlib import Path

from typebench.adapters.stub import StubAdapter
from typebench.models import (
    CalibrationStats, PreflightReport, PreparedProject, ResultClass, ResultsEnvelope,
    RunResult, ThreadMode, ToolPreflight,
)
from typebench.suite import run_suite


def _prepared(name: str) -> PreparedProject:
    return PreparedProject(
        name=name, checkout="/x/repo", venv_python="/x/venv/bin/python",
        src_roots=("/x/repo/pkg",), exclude_globs=("**/tests/**",),
        python_version="3.12", python_platform="linux", sha="SHA1",
        lock_hash="LH", frozen=("pkg==1.0",), canonical_files=10, canonical_loc=500,
        canonical_code_loc=400, fingerprint="fp",
    )


def _ready_report(name: str, tools: list[str]) -> PreflightReport:
    return PreflightReport(
        project=name, sha="SHA1", python_version="3.12", lock_hash="LH",
        canonical_files=10, canonical_loc=500, ready=True,
        tools=[
            ToolPreflight(tool=t, version="1", result_class=ResultClass.CLEAN,
                          real_exit_code=0, self_reported_files=10, over_reports=False)
            for t in tools
        ],
    )


def _calib() -> CalibrationStats:
    return CalibrationStats(
        workload_id="calib-pyloop-v1", iterations=1, runs=1,
        raw_min_s=0.3, raw_median_s=0.3, raw_max_s=0.3,
    )


def test_run_suite_runs_ready_cells_and_builds_envelope() -> None:
    captured: list[object] = []

    def fake_run_one(adapter: object, **kwargs: object) -> RunResult:
        captured.append(kwargs.get("manifest"))
        from typebench.env import detect_env
        return RunResult(
            tool=getattr(adapter, "name", "stub"), tool_version="1", project=str(kwargs["project"]),
            thread_mode=ThreadMode(kwargs["thread_mode"]) if not isinstance(kwargs["thread_mode"], ThreadMode) else kwargs["thread_mode"],
            result_class=ResultClass.CLEAN, real_exit_code=0, env=detect_env(),
        )

    envelope = run_suite(
        suite_path=Path("/x/suite.toml"), cache_root=Path("/x/cache"),
        tools=["stub"], thread_modes=[ThreadMode.ALL_CORES, ThreadMode.ONE_CORE],
        generated_at="2026-06-08T00:00:00Z",
        runs=1, warmup=1, timeout=10, mem_runs=1, measure_enabled=False, calib_runs=1,
        load_projects=lambda _p: ["demo"],
        load_version=lambda _p: "2026-06-08",
        adapter_factory=lambda _name: StubAdapter(),
        lookup_entry=lambda _p, name: _prepared(name),  # entry stand-in (see note)
        prepare=lambda _entry, _cache: _prepared("demo"),
        preflight=lambda _prepared, _adapters, timeout: _ready_report("demo", ["stub"]),
        run_one=fake_run_one,
        calibrate_fn=lambda runs: _calib(),
    )
    assert isinstance(envelope, ResultsEnvelope)
    assert envelope.suite_version == "2026-06-08"
    assert envelope.generated_at == "2026-06-08T00:00:00Z"
    assert len(envelope.runs) == 2  # 1 project × 1 tool × 2 modes
    assert all(r.result_class == ResultClass.CLEAN for r in envelope.runs)
    # over_reports stamped from the (ready) preflight report
    assert all(m is not None for m in captured)


def test_run_suite_excluded_project_emits_failed_records() -> None:
    not_ready = PreflightReport(
        project="demo", sha="SHA1", python_version="3.12", lock_hash="LH",
        canonical_files=10, canonical_loc=500, ready=False,
        tools=[ToolPreflight(tool="stub", version="1", result_class=ResultClass.FAILED_ENV,
                             real_exit_code=3, error_detail="pyrefly env error")],
    )

    def boom_run_one(adapter: object, **kwargs: object) -> RunResult:
        raise AssertionError("run_one must NOT be called for an excluded project")

    envelope = run_suite(
        suite_path=Path("/x/suite.toml"), cache_root=Path("/x/cache"),
        tools=["stub"], thread_modes=[ThreadMode.ALL_CORES],
        generated_at="t", runs=1, warmup=1, timeout=10, mem_runs=1,
        measure_enabled=False, calib_runs=1,
        load_projects=lambda _p: ["demo"], load_version=lambda _p: "v",
        adapter_factory=lambda _name: StubAdapter(),
        lookup_entry=lambda _p, name: _prepared(name),
        prepare=lambda _entry, _cache: _prepared("demo"),
        preflight=lambda _prepared, _adapters, timeout: not_ready,
        run_one=boom_run_one, calibrate_fn=lambda runs: _calib(),
    )
    assert len(envelope.runs) == 1  # 1 cell, emitted as a FAILED record (never dropped)
    rec = envelope.runs[0]
    assert rec.result_class == ResultClass.FAILED_ENV
    assert rec.error_detail is not None and "pyrefly env error" in rec.error_detail
```

> Seam note for the executor: the orchestrator takes injectable callables so tests never touch disk. In production wiring (Task 11), `lookup_entry` resolves a `CorpusProject` from the suite file, `prepare`/`preflight`/`run_one`/`calibrate_fn` default to the real `prepare_project`/`preflight_project`/`run_single`/`calibrate`, and `adapter_factory` comes from the CLI registry. The two tests above pin: (a) ready project → cells run, manifest stamped, envelope assembled; (b) not-ready project → one `FAILED_ENV` record per cell, `run_one` never called (§12 gate + "never drop a record").

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_suite.py -k run_suite -v`
Expected: FAIL (`ImportError: run_suite`).

- [ ] **Step 3: Implement**

Append to `src/typebench/suite.py` (add the imports it needs at the top; keep `Adapter`, `CorpusProject`, `PreparedProject`, `PreflightReport`, `Callable` under `TYPE_CHECKING` where used only as annotations, but `RunManifest`/`RunResult`/`ResultsEnvelope`/`ResultClass`/`FailurePhase`/`detect_env` are runtime):

```python
from typebench.collector import RunManifest, run_single
from typebench.corpus import load_suite, load_suite_version
from typebench.env import detect_env
from typebench.envman import prepare_project
from typebench.models import (
    FailurePhase, PreflightReport, ResultClass, ResultsEnvelope, RunResult,
)
from typebench.normalized_config import config_hash
from typebench.preflight import preflight_project

# ... (TYPE_CHECKING block) ...
if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from typebench.adapters.base import Adapter
    from typebench.calibration import CalibrationStats
    from typebench.corpus import CorpusProject
    from typebench.models import PreparedProject, ThreadMode


def _excluded_record(
    cell: SuiteCell, prepared: PreparedProject | None, entry: CorpusProject | None,
    install_source: str, detail: str, calibration: CalibrationStats | None,
) -> RunResult:
    """A FAILED_ENV record for a cell whose project was excluded by preflight or a
    prepare failure. The bar must read 'didn't compete', never be silently absent
    (spec §7/§12). Carries whatever repro scalars are known."""
    ch = (
        config_hash(entry.src_roots, entry.effective_excludes(),
                    entry.python_version, entry.python_platform)
        if entry is not None else None
    )
    return RunResult(
        tool=cell.tool, tool_version="unknown", project=cell.project,
        thread_mode=cell.thread_mode, result_class=ResultClass.FAILED_ENV,
        failure_phase=FailurePhase.PROBE, real_exit_code=-1,
        error_detail=detail.strip()[-500:] or None,
        project_sha=prepared.sha if prepared else (entry.sha if entry else None),
        lock_hash=prepared.lock_hash if prepared else None,
        config_hash=ch,
        tool_install_source=install_source,
        canonical_files=prepared.canonical_files if prepared else None,
        canonical_loc=prepared.canonical_loc if prepared else None,
        canonical_code_loc=prepared.canonical_code_loc if prepared else None,
        calibration=calibration, env=detect_env(),
    )


def run_suite(  # noqa: PLR0913 — distinct orchestration knobs + injectable seams, mirrors run_single's noqa precedent
    *,
    suite_path: Path,
    cache_root: Path,
    tools: list[str],
    thread_modes: list[ThreadMode],
    generated_at: str,
    runs: int,
    warmup: int,
    timeout: float,
    mem_runs: int,
    measure_enabled: bool,
    calib_runs: int,
    shard_index: int = 0,
    shard_total: int = 1,
    projects: list[str] | None = None,
    load_projects: Callable[[Path], list[str]] = lambda p: [e.name for e in load_suite(p)],
    load_version: Callable[[Path], str] = load_suite_version,
    lookup_entry: Callable[[Path, str], CorpusProject] | None = None,
    adapter_factory: Callable[[str], Adapter] | None = None,
    prepare: Callable[..., PreparedProject] = prepare_project,
    preflight: Callable[..., PreflightReport] = preflight_project,
    run_one: Callable[..., RunResult] = run_single,
    calibrate_fn: Callable[[int], CalibrationStats] | None = None,
) -> ResultsEnvelope:
    """Run the sharded matrix behind the §12 preflight gate -> ResultsEnvelope.

    Per project (project-major, so the clone/venv is reused): prepare -> preflight;
    if the project is not ready (or prepare fails), emit one FAILED_ENV record per
    cell (visible 'didn't compete', §12) and skip running. Otherwise run each ready
    cell via run_one with a stamped RunManifest. One calibration per invocation
    (Decision H) attached to every record."""
    if adapter_factory is None:
        raise ValueError("adapter_factory is required")
    if lookup_entry is None:
        raise ValueError("lookup_entry is required")

    all_projects = projects if projects is not None else load_projects(suite_path)
    suite_version = load_version(suite_path)
    cells = shard(build_matrix(all_projects, tools, thread_modes), shard_index, shard_total)

    calibration: CalibrationStats | None = None
    if calibrate_fn is not None:
        calibration = calibrate_fn(calib_runs)

    # Group sharded cells by project, preserving matrix order.
    by_project: dict[str, list[SuiteCell]] = {}
    for cell in cells:
        by_project.setdefault(cell.project, []).append(cell)

    results: list[RunResult] = []
    for project, project_cells in by_project.items():
        entry = lookup_entry(suite_path, project)
        project_tools = sorted({c.tool for c in project_cells})
        adapters = [adapter_factory(name) for name in project_tools]
        adapter_by_name = {a.name: a for a in adapters}

        try:
            prepared = prepare(entry, cache_root)
        except Exception as exc:  # noqa: BLE001 — prepare_project raises PrepareError; any failure must still emit records, never abort the suite
            for cell in project_cells:
                src = getattr(adapter_by_name.get(cell.tool), "install_source", "unknown")
                results.append(_excluded_record(cell, None, entry, src, f"prepare failed: {exc}", calibration))
            continue

        report = preflight(prepared, adapters, timeout=timeout)
        if not report.ready:
            detail = "; ".join(
                f"{t.tool}: {t.result_class.value} {t.error_detail or ''}".strip()
                for t in report.tools
                if not (t.result_class.is_measured_success and t.scope_ok)
            )
            for cell in project_cells:
                src = adapter_by_name[cell.tool].install_source
                results.append(_excluded_record(cell, prepared, entry, src, detail or "preflight not ready", calibration))
            continue

        over_by_tool = {t.tool: t.over_reports for t in report.tools}
        ch = config_hash(
            entry.src_roots, entry.effective_excludes(),
            entry.python_version, entry.python_platform,
        )
        config = _suite_config(prepared)
        for cell in project_cells:
            adapter = adapter_by_name[cell.tool]
            manifest = RunManifest(
                project_sha=prepared.sha, lock_hash=prepared.lock_hash, config_hash=ch,
                canonical_files=prepared.canonical_files, canonical_loc=prepared.canonical_loc,
                canonical_code_loc=prepared.canonical_code_loc,
                tool_install_source=adapter.install_source,
                over_reports=over_by_tool.get(cell.tool, False),
            )
            results.append(
                run_one(
                    adapter, project=project, config=config, thread_mode=cell.thread_mode,
                    warmup=warmup, runs=runs, timeout=timeout, mem_runs=mem_runs,
                    measure_enabled=measure_enabled, calibration=calibration, manifest=manifest,
                )
            )

    return ResultsEnvelope(suite_version=suite_version, generated_at=generated_at, runs=results)
```

Add a small `_suite_config` helper (mirrors `preflight._config_for`):

```python
def _suite_config(prepared: PreparedProject) -> NormalizedConfig:
    return NormalizedConfig(
        src_roots=prepared.src_roots,
        exclude_globs=prepared.exclude_globs,
        python_version=prepared.python_version,
        python_platform=prepared.python_platform,
        venv_python=prepared.venv_python or None,
    )
```

(Import `NormalizedConfig` at the top runtime block.)

> Note: the two hermetic tests inject `prepare`/`preflight`/`run_one`, so the real `prepare_project`/`preflight_project`/`run_single` defaults are never exercised in unit tests — they are wired live by the CLI (Task 11) and proven by the real bench (Task 16). The `# noqa: BLE001` on the bare `except Exception` is deliberate: a prepare exception of ANY kind must still produce records (never drop a project's bars, never abort the whole suite) — same "never drop a record" posture as the collector's defensive fallback.

- [ ] **Step 2 (re-run): verify it passes**

Run: `uv run pytest tests/test_suite.py -v`
Expected: PASS.

- [ ] **Step 3: Gate the new module**

Run: `uv run ruff check src/typebench/suite.py && uv run pyrefly check`
Expected: clean (fix any `TC`/import-placement findings: annotation-only imports under `TYPE_CHECKING`, runtime ones at top).

- [ ] **Step 4: Commit**

```bash
git add src/typebench/suite.py tests/test_suite.py
git commit -m "feat(suite): run_suite orchestrator — preflight gate, manifest stamping, excluded-as-FAILED records

Project-major loop: prepare -> preflight; not-ready/prepare-failure emits one
FAILED_ENV record per cell (visible 'didn't compete', §12), never dropped and never
aborting the suite. Ready cells run via run_single with a stamped RunManifest. One
calibration per invocation (Decision H). Fully seam-injected for hermetic tests."
```

---

### Task 11: `typebench suite` CLI command

**Files:**
- Modify: `src/typebench/cli.py`
- Test: `tests/test_cli_suite.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_suite.py`:

```python
from pathlib import Path

import pytest
from typer.testing import CliRunner

from typebench import cli
from typebench.cli import app
from typebench.models import ResultsEnvelope

runner = CliRunner()


def test_suite_writes_envelope(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Stub run_suite so the CLI is tested for wiring + file write, not orchestration.
    from typebench.env import detect_env
    from typebench.models import ResultClass, RunResult, ThreadMode

    def fake_run_suite(**kwargs: object) -> ResultsEnvelope:
        rec = RunResult(
            tool="stub", tool_version="0", project="demo", thread_mode=ThreadMode.ALL_CORES,
            result_class=ResultClass.CLEAN, real_exit_code=0, env=detect_env(),
        )
        return ResultsEnvelope(suite_version="v", generated_at=str(kwargs["generated_at"]), runs=[rec])

    monkeypatch.setattr(cli, "run_suite", fake_run_suite)
    suite = tmp_path / "suite.toml"
    suite.write_text('[suite]\nversion="v"\n')
    out = tmp_path / "results" / "2026-06-08.json"
    out.parent.mkdir()
    result = runner.invoke(
        app,
        ["suite", "--corpus", str(suite), "--output", str(out),
         "--tool", "stub", "--shard", "0/1", "--no-calibrate", "--no-measure", "--runs", "1"],
    )
    assert result.exit_code == 0, result.output
    envelope = ResultsEnvelope.model_validate_json(out.read_text())
    assert len(envelope.runs) == 1


def test_suite_rejects_bad_shard(tmp_path: Path) -> None:
    suite = tmp_path / "suite.toml"
    suite.write_text("[[project]]\nname='x'\n")
    out = tmp_path / "r.json"
    result = runner.invoke(
        app, ["suite", "--corpus", str(suite), "--output", str(out), "--shard", "3/2"]
    )
    assert result.exit_code == 2
    assert "shard" in result.output.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_cli_suite.py -v`
Expected: FAIL (no `suite` command).

- [ ] **Step 3: Implement**

In `src/typebench/cli.py`:
- Imports: `from datetime import UTC, datetime`, `from typebench.suite import run_suite`, `from typebench.calibration import calibrate`.
- Add a `_parse_shard` helper and the command:

```python
def _parse_shard(spec: str) -> tuple[int, int]:
    """Parse 'i/n' (e.g. '0/4'); validate 0 <= i < n. typer.Exit(2) on bad input."""
    try:
        index_str, total_str = spec.split("/", 1)
        index, total = int(index_str), int(total_str)
    except ValueError:
        typer.echo(f"--shard must be 'index/total' (e.g. 0/4), got {spec!r}", err=True)
        raise typer.Exit(code=2) from None
    if total < 1 or not 0 <= index < total:
        typer.echo(f"--shard out of range: {spec!r} (need 0 <= index < total, total >= 1)", err=True)
        raise typer.Exit(code=2)
    return index, total


@app.command()
def suite(  # noqa: PLR0913 — each parameter is a distinct user-facing CLI option
    corpus: Annotated[Path, typer.Option(help="Path to suite.toml.")],
    output: Annotated[Path, typer.Option(help="Where to write the results envelope JSON.")],
    tool: Annotated[
        list[str] | None,
        typer.Option(help="Tools to run (repeatable). Default: all four real checkers."),
    ] = None,
    thread_mode: Annotated[
        list[ThreadMode] | None,
        typer.Option(help="Thread tracks (repeatable). Default: both."),
    ] = None,
    shard: Annotated[str, typer.Option(help="Shard selector 'index/total' (e.g. 0/4).")] = "0/1",
    runs: Annotated[int, typer.Option(help="hyperfine timed runs.")] = 10,
    warmup: Annotated[int, typer.Option(help="hyperfine warmup runs.")] = 3,
    mem_runs: Annotated[int, typer.Option(help="Resource-pass repeats (>=1; >=3 official).")] = 3,
    measure: Annotated[bool, typer.Option(help="Run the cgroup memory/CPU pass.")] = True,
    calibrate_baseline: Annotated[
        bool, typer.Option("--calibrate/--no-calibrate", help="Time the calibration workload.")
    ] = True,
    calib_runs: Annotated[int, typer.Option(help="Calibration workload repeats (>=1).")] = 5,
    timeout: Annotated[float, typer.Option(help="Per-invocation timeout (seconds).")] = 900.0,
    cache_root: Annotated[
        Path, typer.Option(help="Where prepared clones/venvs are cached.")
    ] = DEFAULT_CACHE_ROOT,
) -> None:
    """Run the (project × tool × thread-mode) matrix and write a results envelope."""
    out_dir = output.parent
    if not out_dir.exists() or not os.access(out_dir, os.W_OK):
        typer.echo(f"Output directory not writable: {out_dir}", err=True)
        raise typer.Exit(code=2)
    if mem_runs < 1 or calib_runs < 1:
        typer.echo("--mem-runs and --calib-runs must be >= 1.", err=True)
        raise typer.Exit(code=2)
    shard_index, shard_total = _parse_shard(shard)
    tools = tool or ["mypy", "pyright", "pyrefly", "ty"]
    modes = thread_mode or [ThreadMode.ALL_CORES, ThreadMode.ONE_CORE]
    envelope = run_suite(
        suite_path=corpus,
        cache_root=cache_root,
        tools=tools,
        thread_modes=modes,
        generated_at=datetime.now(UTC).isoformat(),
        runs=runs,
        warmup=warmup,
        timeout=timeout,
        mem_runs=mem_runs,
        measure_enabled=measure,
        calib_runs=calib_runs,
        shard_index=shard_index,
        shard_total=shard_total,
        lookup_entry=_lookup_project,
        adapter_factory=lambda name: _adapters_for([name])[0],
        calibrate_fn=calibrate if calibrate_baseline else None,
    )
    output.write_text(envelope.model_dump_json(indent=2))
    measured = sum(1 for r in envelope.runs if r.result_class.is_measured_success)
    typer.echo(f"suite {shard} -> {measured}/{len(envelope.runs)} measured -> {output}")
```

> `_lookup_project(corpus: Path, name: str) -> CorpusProject` already exists in `cli.py` and matches the `lookup_entry` seam signature `(Path, str) -> CorpusProject`. Pass it directly.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_cli_suite.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/typebench/cli.py tests/test_cli_suite.py
git commit -m "feat(cli): typebench suite — sharded matrix run -> results envelope (spec §10/§11)"
```

---

### Task 12: renderer — README markdown table

**Files:**
- Create: `src/typebench/renderer.py`
- Test: `tests/test_renderer.py` (new)

- [ ] **Step 1: Write the failing test (golden)**

Create `tests/test_renderer.py`:

```python
from typebench.env import EnvFingerprint
from typebench.models import (
    MemoryStats, ResultClass, ResultsEnvelope, RunResult, ThreadMode, TimingStats,
)
from typebench.renderer import render_readme


def _env(cpu: str = "Test CPU") -> EnvFingerprint:
    return EnvFingerprint(os="Linux", kernel="6.6", cpu_model=cpu, core_count=8,
                          python_version="3.12.0")


def _record(tool: str, wall: float, peak: int, over: bool = False) -> RunResult:
    return RunResult(
        tool=tool, tool_version="1.0", project="httpx", thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.CLEAN, real_exit_code=0,
        timing=TimingStats(runs=3, min_s=wall, median_s=wall, mean_s=wall, stddev_s=0.0,
                           max_s=wall, times_s=[wall]),
        memory=MemoryStats(runs=3, peak_bytes_min=peak, peak_bytes_median=peak, peak_bytes_max=peak),
        cpu_time_s=wall, parallel_efficiency=1.0,
        canonical_files=23, canonical_loc=4000, canonical_code_loc=3200,
        loc_denominator="code", over_reports=over, env=_env(),
    )


def test_render_readme_table_is_fastest_first_and_excludes_diagnostics() -> None:
    env = ResultsEnvelope(
        suite_version="2026-06-08", generated_at="2026-06-08T00:00:00Z",
        runs=[_record("mypy", 2.0, 200_000_000), _record("ty", 0.5, 400_000_000)],
    )
    md = render_readme(env)
    # fastest-first: ty (0.5s) before mypy (2.0s)
    assert md.index("| ty ") < md.index("| mypy ")
    # diagnostics is NOT a column (spec §8)
    assert "diagnostics" not in md.lower()
    # code-LOC throughput present (3200 LOC / 0.5 s = 6.4 kLOC/s)
    assert "6.4" in md
    # cross-pass label on parallel efficiency
    assert "cross-pass" in md.lower()


def test_render_readme_withholds_throughput_for_over_reporters() -> None:
    env = ResultsEnvelope(
        suite_version="v", generated_at="t", runs=[_record("ty", 0.5, 1, over=True)]
    )
    md = render_readme(env)
    # over_reports -> kLOC/s withheld with the asterisk caveat, not a number
    assert "—*" in md or "n/a*" in md


def test_render_readme_shows_failed_cells_as_didnt_compete() -> None:
    failed = RunResult(
        tool="pyright", tool_version="1.0", project="httpx", thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.FAILED_ENV, real_exit_code=3, env=_env(),
    )
    env = ResultsEnvelope(suite_version="v", generated_at="t", runs=[failed])
    md = render_readme(env)
    assert "failed{env}" in md
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_renderer.py -v`
Expected: FAIL (`ImportError: render_readme`).

- [ ] **Step 3: Implement**

Create `src/typebench/renderer.py`:

```python
"""Renderer (spec §8/§11) — pure functions from results models to the README
markdown block and the GH Pages trends.json. No filesystem I/O here (the CLI does
it); golden-tested. Hard rules: diagnostics counts are NEVER a headline column
(§8); kLOC/s uses the canonical code-LOC denominator and is withheld for
over-reporting tools; parallel_efficiency is labelled cross-pass (cold-cpu ÷
warm-wall), not a within-run figure."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typebench.models import ResultsEnvelope, RunResult

_README_BEGIN = "<!-- TYPEBENCH:BEGIN -->"
_README_END = "<!-- TYPEBENCH:END -->"


def _peak_mem_mb(record: RunResult) -> str:
    if record.memory is None:
        return "—"
    return f"{record.memory.peak_bytes_median / 1_000_000:.1f}"


def _kloc_s(record: RunResult) -> str:
    """Headline throughput = canonical code-LOC / wall median. Withheld (—*) for
    over-reporters (their analyzed set diverges from the canonical denominator, §8).
    Physical-denominator rows are footnoted by the caller via loc_denominator."""
    if record.over_reports:
        return "—*"
    loc = record.canonical_code_loc if record.loc_denominator == "code" else record.canonical_loc
    if loc is None or record.timing is None or record.timing.median_s <= 0:
        return "—"
    return f"{(loc / 1000) / record.timing.median_s:.1f}"


def _wall(record: RunResult) -> str:
    return f"{record.timing.median_s:.3f}" if record.timing is not None else "—"


def _sort_key(record: RunResult) -> tuple[int, float, str]:
    # Measured-success first (fastest wall first); failures sink to the bottom,
    # then alphabetical by tool for stable ordering.
    if record.timing is not None:
        return (0, record.timing.median_s, record.tool)
    return (1, float("inf"), record.tool)


def _table(records: list[RunResult]) -> str:
    header = (
        "| Tool | Result | Wall median (s) | Peak cgroup mem (MB) | "
        "CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |\n"
        "|------|--------|-----------------|----------------------|"
        "--------------|----------------------------|---------------|\n"
    )
    rows = []
    for r in sorted(records, key=_sort_key):
        cpu = f"{r.cpu_time_s:.3f}" if r.cpu_time_s is not None else "—"
        peff = f"{r.parallel_efficiency:.2f}" if r.parallel_efficiency is not None else "—"
        rows.append(
            f"| {r.tool} | {r.result_class.value} | {_wall(r)} | {_peak_mem_mb(r)} | "
            f"{cpu} | {peff} | {_kloc_s(r)} |"
        )
    return header + "\n".join(rows) + "\n"


def render_readme(envelope: ResultsEnvelope) -> str:
    """Markdown block (between the TYPEBENCH markers) — one table per
    (project, thread-mode), ordered fastest-first (ranking by the measured metric,
    §11). Includes the suite version, generated timestamp, and the caveat footnotes."""
    groups: dict[tuple[str, str], list[RunResult]] = {}
    for record in envelope.runs:
        groups.setdefault((record.project, record.thread_mode.value), []).append(record)

    parts = [
        _README_BEGIN,
        f"\n_Suite `{envelope.suite_version}` · generated {envelope.generated_at}_\n",
    ]
    for (project, mode) in sorted(groups):
        parts.append(f"\n#### {project} — {mode}\n")
        parts.append(_table(groups[(project, mode)]))
    parts.append(
        "\n> kLOC/s denominator is the canonical analyzed code-LOC (tokei; blanks+"
        "comments excluded), identical across tools. `—*` = throughput withheld "
        "because the tool over-reports its analyzed set vs the canonical denominator. "
        "Parallel efficiency is cross-pass (cold cgroup CPU-time ÷ warm hyperfine wall). "
        "Diagnostics counts are intentionally omitted — they are not comparable across "
        "tools and are not a ranking (spec §8).\n"
    )
    parts.append(_README_END)
    return "\n".join(parts)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_renderer.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/typebench/renderer.py tests/test_renderer.py
git commit -m "feat(renderer): README table block — fastest-first, code-LOC kLOC/s, over-report withholding

Diagnostics kept out of headline (§8); parallel efficiency labelled cross-pass;
failed cells render as their failure class ('didn't compete'). Pure + golden-tested."
```

---

### Task 13: renderer — calibration normalization + trends.json

**Files:**
- Modify: `src/typebench/renderer.py`
- Test: `tests/test_renderer.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_renderer.py` (hoist `CalibrationStats`, `cpu_model_anchors`, `build_trends` imports):

```python
def _record_for_trends(tool: str, wall: float, calib_med: float, cpu: str) -> RunResult:
    return RunResult(
        tool=tool, tool_version="1.0", project="httpx", thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.CLEAN, real_exit_code=0,
        timing=TimingStats(runs=1, min_s=wall, median_s=wall, mean_s=wall, stddev_s=0.0,
                           max_s=wall, times_s=[wall]),
        memory=MemoryStats(runs=1, peak_bytes_min=1, peak_bytes_median=200_000_000, peak_bytes_max=1),
        canonical_code_loc=3200, loc_denominator="code", over_reports=False,
        calibration=CalibrationStats(workload_id="calib-pyloop-v1", iterations=1, runs=1,
                                     raw_min_s=calib_med, raw_median_s=calib_med, raw_max_s=calib_med),
        env=_env(cpu),
    )


def _envelope(gen: str, *records: RunResult) -> ResultsEnvelope:
    return ResultsEnvelope(suite_version="v", generated_at=gen, runs=list(records))


def test_cpu_model_anchors_take_earliest_per_model() -> None:
    history = [
        _envelope("2026-02-01", _record_for_trends("mypy", 1.0, 0.40, "CPU-A")),
        _envelope("2026-03-01", _record_for_trends("mypy", 1.0, 0.20, "CPU-A")),
        _envelope("2026-03-01", _record_for_trends("mypy", 1.0, 0.50, "CPU-B")),
    ]
    anchors = cpu_model_anchors(history)
    assert anchors["CPU-A"] == 0.40  # earliest envelope's calib for CPU-A
    assert anchors["CPU-B"] == 0.50


def test_build_trends_normalizes_against_anchor() -> None:
    history = [
        _envelope("2026-02-01", _record_for_trends("mypy", 1.0, 0.40, "CPU-A")),  # anchor
        _envelope("2026-03-01", _record_for_trends("mypy", 1.0, 0.20, "CPU-A")),  # 2x faster VM
    ]
    trends = build_trends(history)
    points = [p for p in trends["points"] if p["date"] == "2026-03-01"]
    assert len(points) == 1
    p = points[0]
    assert p["wall_median_s"] == 1.0
    # normalized = raw * anchor / run_calib = 1.0 * 0.40 / 0.20 = 2.0
    assert abs(p["wall_median_s_norm"] - 2.0) < 1e-9
    # single anchor point normalizes to ~itself
    anchor_point = [p for p in trends["points"] if p["date"] == "2026-02-01"][0]
    assert abs(anchor_point["wall_median_s_norm"] - 1.0) < 1e-9


def test_build_trends_includes_kloc_and_corpus_markers() -> None:
    history = [_envelope("2026-02-01", _record_for_trends("mypy", 2.0, 0.40, "CPU-A"))]
    trends = build_trends(history)
    p = trends["points"][0]
    assert abs(p["kloc_s"] - 1.6) < 1e-9  # 3200/1000 / 2.0
    assert trends["corpus_markers"][0]["suite_version"] == "v"
    assert "CPU-A" in trends["cpu_models"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_renderer.py -k "anchor or trends" -v`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement**

Append to `src/typebench/renderer.py`:

```python
def _calib_median(record: RunResult) -> float | None:
    return record.calibration.raw_median_s if record.calibration is not None else None


def cpu_model_anchors(history: list[ResultsEnvelope]) -> dict[str, float]:
    """Fixed per-CPU-model calibration anchor (Decision I): for each CPU model, the
    calibration raw_median_s of the EARLIEST envelope (by generated_at) that has a
    run on that model with a calibration. Anchors only ever add, so a published
    point's normalized value never changes when later data arrives."""
    anchors: dict[str, float] = {}
    for envelope in sorted(history, key=lambda e: e.generated_at):
        for record in envelope.runs:
            calib = _calib_median(record)
            if calib is None or calib <= 0:
                continue
            anchors.setdefault(record.env.cpu_model, calib)
    return anchors


def _kloc_value(record: RunResult) -> float | None:
    if record.over_reports or record.timing is None or record.timing.median_s <= 0:
        return None
    loc = record.canonical_code_loc if record.loc_denominator == "code" else record.canonical_loc
    return (loc / 1000) / record.timing.median_s if loc is not None else None


def build_trends(history: list[ResultsEnvelope]) -> dict[str, object]:
    """Flatten history to fully-labelled points + per-CPU-model-normalized variants.
    The GH Pages app groups points into series and derives inter-checker ratios
    client-side (slowest per date/project/mode/metric). Only measured-success records
    contribute points; failures are visible in the README, not the trend lines."""
    anchors = cpu_model_anchors(history)
    points: list[dict[str, object]] = []
    markers: list[dict[str, object]] = []
    for envelope in sorted(history, key=lambda e: e.generated_at):
        date = envelope.generated_at[:10]
        markers.append({"date": date, "suite_version": envelope.suite_version})
        for record in envelope.runs:
            if not record.result_class.is_measured_success or record.timing is None:
                continue
            calib = _calib_median(record)
            anchor = anchors.get(record.env.cpu_model)
            wall = record.timing.median_s
            wall_norm = (
                wall * anchor / calib
                if anchor is not None and calib is not None and calib > 0
                else None
            )
            peak_mb = record.memory.peak_bytes_median / 1_000_000 if record.memory else None
            points.append(
                {
                    "date": date,
                    "suite_version": envelope.suite_version,
                    "project": record.project,
                    "thread_mode": record.thread_mode.value,
                    "tool": record.tool,
                    "cpu_model": record.env.cpu_model,
                    "wall_median_s": wall,
                    "wall_median_s_norm": wall_norm,
                    "peak_mem_mb": peak_mb,
                    "kloc_s": _kloc_value(record),
                    "calib_median_s": calib,
                    "calib_anchor_s": anchor,
                }
            )
    return {
        "cpu_models": sorted(anchors),
        "points": points,
        "corpus_markers": markers,
    }
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_renderer.py -v`
Expected: PASS (all renderer tests).

- [ ] **Step 5: Commit**

```bash
git add src/typebench/renderer.py tests/test_renderer.py
git commit -m "feat(renderer): trends.json with fixed per-CPU-model-anchor normalization (spec §5.7)

normalized = raw * anchor/run_calib; anchors are earliest-per-model so published
points never move retroactively. Emits labelled points + kLOC/s + corpus markers;
inter-checker ratios are derived client-side."
```

---

### Task 14: `typebench render` CLI

**Files:**
- Modify: `src/typebench/cli.py`
- Test: `tests/test_cli_render.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_render.py`:

```python
import json
from pathlib import Path

from typer.testing import CliRunner

from typebench.cli import app
from typebench.env import EnvFingerprint
from typebench.models import ResultClass, ResultsEnvelope, RunResult, ThreadMode, TimingStats

runner = CliRunner()


def _envelope_file(path: Path, gen: str) -> None:
    rec = RunResult(
        tool="mypy", tool_version="1.0", project="httpx", thread_mode=ThreadMode.ALL_CORES,
        result_class=ResultClass.CLEAN, real_exit_code=0,
        timing=TimingStats(runs=1, min_s=1.0, median_s=1.0, mean_s=1.0, stddev_s=0.0,
                           max_s=1.0, times_s=[1.0]),
        canonical_code_loc=3200, loc_denominator="code",
        env=EnvFingerprint(os="Linux", kernel="6.6", cpu_model="CPU-A", core_count=8,
                           python_version="3.12.0"),
    )
    env = ResultsEnvelope(suite_version="2026-06-08", generated_at=gen, runs=[rec])
    path.write_text(env.model_dump_json())


def test_render_updates_readme_markers_and_writes_trends(tmp_path: Path) -> None:
    results = tmp_path / "results"
    results.mkdir()
    _envelope_file(results / "2026-06-08.json", "2026-06-08T00:00:00Z")
    readme = tmp_path / "README.md"
    readme.write_text(
        "# typebench\n\nIntro prose.\n\n<!-- TYPEBENCH:BEGIN -->\nOLD\n<!-- TYPEBENCH:END -->\n\nFooter prose.\n"
    )
    trends = tmp_path / "site" / "data" / "trends.json"
    trends.parent.mkdir(parents=True)
    result = runner.invoke(
        app, ["render", "--results-dir", str(results), "--readme", str(readme),
              "--trends", str(trends)],
    )
    assert result.exit_code == 0, result.output
    text = readme.read_text()
    assert "Intro prose." in text and "Footer prose." in text  # prose preserved
    assert "OLD" not in text  # block replaced
    assert "| mypy " in text
    data = json.loads(trends.read_text())
    assert data["points"][0]["tool"] == "mypy"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_cli_render.py -v`
Expected: FAIL (no `render` command).

- [ ] **Step 3: Implement**

In `src/typebench/cli.py`:
- Imports: `import json`, `from typebench.renderer import build_trends, render_readme`, and the marker constants (re-declare locally or import from renderer): `from typebench.renderer import _README_BEGIN, _README_END` is private; instead define module-level constants in `cli.py`:

```python
_README_BEGIN = "<!-- TYPEBENCH:BEGIN -->"
_README_END = "<!-- TYPEBENCH:END -->"


def _replace_readme_block(readme_text: str, block: str) -> str:
    """Swap the content between the TYPEBENCH markers; append a fresh block (with a
    heading) if the markers are absent, so hand-written prose is never clobbered."""
    start = readme_text.find(_README_BEGIN)
    end = readme_text.find(_README_END)
    if start != -1 and end != -1 and end > start:
        return readme_text[:start] + block + readme_text[end + len(_README_END):]
    return readme_text.rstrip() + "\n\n## Latest results\n\n" + block + "\n"


@app.command()
def render(
    results_dir: Annotated[Path, typer.Option(help="Directory of results/<date>.json envelopes.")],
    readme: Annotated[Path, typer.Option(help="README.md to update between the markers.")],
    trends: Annotated[Path, typer.Option(help="Where to write site/data/trends.json.")],
) -> None:
    """Regenerate the README table (latest envelope) + the GH Pages trends.json (full
    history). Hand-written prose outside the TYPEBENCH markers is preserved (§11)."""
    files = sorted(results_dir.glob("*.json"))
    if not files:
        typer.echo(f"No results/*.json found under {results_dir}", err=True)
        raise typer.Exit(code=1)
    history = [ResultsEnvelope.model_validate_json(f.read_text()) for f in files]
    history.sort(key=lambda e: e.generated_at)

    block = render_readme(history[-1])  # README shows the latest run
    readme.write_text(_replace_readme_block(readme.read_text(), block))

    trends.parent.mkdir(parents=True, exist_ok=True)
    trends.write_text(json.dumps(build_trends(history), indent=2))
    typer.echo(f"render -> {readme} (latest) + {trends} ({len(history)} envelopes)")
```

(Add `from typebench.models import ResultsEnvelope` to the cli imports.)

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_cli_render.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/typebench/cli.py tests/test_cli_render.py
git commit -m "feat(cli): typebench render — README block (latest) + trends.json (history), prose preserved"
```

---

### Task 15: GH Pages static site assets

**Files:**
- Create: `site/index.html`, `site/app.js`, `site/vendor/chart.umd.min.js`, `site/data/.gitkeep`
- Modify: `README.md` (seed markers + Methodology + Trends link)

- [ ] **Step 1: Vendor Chart.js**

Download a pinned Chart.js UMD build and commit it (offline + no third-party network dep, Decision F/6):

```bash
mkdir -p site/vendor site/data
curl -fsSL https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js -o site/vendor/chart.umd.min.js
touch site/data/.gitkeep
test -s site/vendor/chart.umd.min.js && echo "vendored chart.js OK"
```

Expected: `vendored chart.js OK` and a non-empty `site/vendor/chart.umd.min.js`.

- [ ] **Step 2: Create `site/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>typebench — Python type-checker performance trends</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; }
    h1 { margin-bottom: 0; }
    .sub { color: #666; margin-top: .25rem; }
    .controls { margin: 1rem 0; display: flex; gap: 1rem; flex-wrap: wrap; align-items: center; }
    canvas { max-width: 100%; }
    .note { color: #666; font-size: .9rem; }
  </style>
</head>
<body>
  <h1>typebench</h1>
  <p class="sub">Neutral, reproducible Python type-checker performance trends. Raw JSON is the source of truth; this page renders it.</p>
  <div class="controls">
    <label>Metric:
      <select id="metric">
        <option value="wall_median_s_norm">Wall (calibration-normalized)</option>
        <option value="wall_median_s">Wall (raw)</option>
        <option value="peak_mem_mb">Peak cgroup memory (MB)</option>
        <option value="kloc_s">Throughput (kLOC/s)</option>
      </select>
    </label>
    <label>Thread mode: <select id="mode"></select></label>
    <label>Project: <select id="project"></select></label>
  </div>
  <canvas id="chart" height="160"></canvas>
  <p class="note">Normalized series divide each run by its per-CPU-model calibration anchor, so VM hardware variance does not masquerade as a checker change (spec §5.7). Dotted vertical lines mark corpus-version changes.</p>
  <script src="./vendor/chart.umd.min.js"></script>
  <script src="./app.js"></script>
</body>
</html>
```

- [ ] **Step 3: Create `site/app.js`**

```javascript
// Fetch the committed trends.json and render a per-tool line chart. Inter-checker
// ratios and grouping are derived client-side; trends.json is the source of truth.
const COLORS = { mypy: "#3572A5", pyright: "#178600", pyrefly: "#DEA584", ty: "#000000", stub: "#999" };

async function main() {
  const res = await fetch("./data/trends.json");
  const data = await res.json();
  const points = data.points || [];

  const modes = [...new Set(points.map(p => p.thread_mode))].sort();
  const projects = [...new Set(points.map(p => p.project))].sort();
  fill("mode", modes);
  fill("project", projects);

  const ctx = document.getElementById("chart").getContext("2d");
  let chart = null;

  function render() {
    const metric = document.getElementById("metric").value;
    const mode = document.getElementById("mode").value;
    const project = document.getElementById("project").value;
    const sel = points.filter(p => p.thread_mode === mode && p.project === project);
    const dates = [...new Set(sel.map(p => p.date))].sort();
    const tools = [...new Set(sel.map(p => p.tool))].sort();
    const datasets = tools.map(tool => ({
      label: tool,
      borderColor: COLORS[tool] || "#555",
      backgroundColor: COLORS[tool] || "#555",
      spanGaps: true,
      data: dates.map(d => {
        const hit = sel.find(p => p.date === d && p.tool === tool);
        return hit ? hit[metric] : null;
      }),
    }));
    if (chart) chart.destroy();
    chart = new Chart(ctx, {
      type: "line",
      data: { labels: dates, datasets },
      options: { responsive: true, interaction: { mode: "index", intersect: false },
                 scales: { y: { beginAtZero: true } } },
    });
  }

  for (const id of ["metric", "mode", "project"]) {
    document.getElementById(id).addEventListener("change", render);
  }
  render();
}

function fill(id, values) {
  const el = document.getElementById(id);
  el.innerHTML = values.map(v => `<option value="${v}">${v}</option>`).join("");
}

main();
```

- [ ] **Step 4: Seed the README markers + Methodology + Trends link**

In `README.md`, add a Methodology section (hand-written, OUTSIDE the markers) and the marker block. If the README has no results section yet, append:

```markdown
## Results

<!-- TYPEBENCH:BEGIN -->
_No results yet. Run `typebench suite` then `typebench render`._
<!-- TYPEBENCH:END -->

## Trends

Interactive trend charts (calibration-normalized) live in [`site/`](site/) — open
locally with `python -m http.server -d site` and visit <http://localhost:8000>.
GitHub Pages deployment is wired in a later milestone.

## Methodology

Each run records peak **cgroup** memory (not RSS), CPU-time, wall-time
(min/median/mean/stddev + dispersion), and throughput against the canonical
analyzed code-LOC denominator (identical across tools). Diagnostics counts are
reported as data only, never a ranking. Full methodology: `docs/superpowers/specs/`.
```

- [ ] **Step 4b: Reconcile the existing README's target-state prose**

The current README describes several Plan-5/Plan-6 features in the present tense as if shipped. Since this task already edits `README.md` and Task 16 stamps "Plan 5 done," fix the drift in the SAME change so the README is honest about what now ships vs. what is still upcoming:

- **Now-true after Plan 5 (keep present tense, verify wording matches the implementation):** per-combo throughput (kLOC/s), `tokei` as a soft dependency, the results envelope, and the per-record lock manifest (sha/lock/config hashes, install source, canonical counts, expanded env). These land in Tasks 1/4/5/7/8/10/12.
- **Still future (must NOT read as shipped):** the preflight gate applies to `typebench suite` (Task 10), NOT to a single `typebench run` — a single run is prepare → measure → collect. Reword any "preflight → prepare → measure → … is the engine pipeline" / "corpus health is a gate" prose to scope the gate to suite orchestration.
- **Evergreen-wrong (wrong now and after Plan 5 — fix regardless):**
  - The quality-gate section must not claim the full gate is enforced "in CI." There is no `.github/workflows`; pre-commit runs `ruff` + `pyrefly` only (not `pytest`). State: run the gate locally before done; pre-commit enforces ruff/pyrefly; CI automation is Plan 6.
  - The corpus bullet's `typebench run <repo>` positional form is not the CLI. Describe the real contract: manual mode = `--project` + repeated `--src-root` (+ optional `--venv` for real tools); curated mode = `--corpus` + `--corpus-project`.
  - The `--cache-root` rationale says a mis-located cache would make pyrefly "silently excluded" — contradicts the "failures are always recorded" taxonomy. Say "recorded as `failed{env}` and excluded from headline aggregates." (The same phrasing in the `cli.py` `DEFAULT_CACHE_ROOT` comment is being corrected too.)

Prefer the structure "What ships now → How to run it → Methodology contract → Upcoming (Plan 5/6)" if the surrounding prose makes interleaving hard to fix in place. Keep all edits OUTSIDE the `<!-- TYPEBENCH:BEGIN/END -->` markers (the renderer owns the block between them).

- [ ] **Step 5: Smoke-check the assets exist + the page references the data**

Run:
```bash
test -s site/vendor/chart.umd.min.js && grep -q 'data/trends.json' site/app.js && grep -q 'TYPEBENCH:BEGIN' README.md && echo "site assets OK"
```
Expected: `site assets OK`.

- [ ] **Step 6: Commit**

```bash
git add site/ README.md
git commit -m "feat(site): GH Pages trend assets — vendored Chart.js, index.html, app.js + README markers

Static site fetches the committed trends.json; calibration-normalized series default.
Pages *deployment* (Action) is a later milestone; preview via python -m http.server."
```

---

### Task 16: AGENTS.md + tokei note + real httpx suite bench (verification)

**Files:**
- Modify: `AGENTS.md`
- Verification only: real corpus run (no new src)

- [ ] **Step 1: Update `AGENTS.md`**

- **Status line:** Plan 5 done; `RunResult` is **v3**; results **envelope** + renderer + GH Pages assets landed; CI/bump is Plan 6.
- **Layout:** add `suite.py` (orchestration → envelope), `renderer.py` (README + trends.json, pure), `site/` (GH Pages assets, vendored Chart.js). Note `counting.py` now also computes tokei code-LOC.
- **Conventions / deps:** add a line — "**tokei** is a soft system dependency (like hyperfine): code-LOC counting gates on `shutil.which('tokei')` with a physical-LOC fallback; never a hard requirement."
- **Commit scopes:** add `suite, renderer, site` to the scope list.
- **Scope discipline by plan:** add a Plan 5 paragraph (envelope/orchestration/manifest/renderer/site done; schema is now **v3**; the lock-manifest enrichment that was deferred is complete). Note Plan 6 = CI automation + GH Pages deploy + monthly corpus bump.
- **Ask first:** note the schema is now v3 and the lock-manifest enrichment is complete — further `RunResult`/`ResultsEnvelope` field or taxonomy changes remain ASK-FIRST.

- [ ] **Step 2: Run the FULL gate**

Run:
```bash
UV_CACHE_DIR=/tmp/uv-cache uv run ruff format && uv run ruff check && uv run pyrefly check && uv run pytest
```
Expected: ruff clean, pyrefly strict 0 errors, all tests pass (the 2 prior skips remain capability-gated). Fix any finding before proceeding.

- [ ] **Step 3: Commit the docs**

```bash
git add AGENTS.md
git commit -m "docs(agents): Plan 5 done — envelope/suite/renderer/site, schema v3, tokei soft dep"
```

- [ ] **Step 4: Real httpx ×4 suite bench (the bug-catching step — REQUIRED before 'done')**

tokei is installed (verified). Run the corpus-driven suite over the real httpx project for all four tools, both thread modes, then render. Use a NON-hidden cache (`typebench-cache`) so pyrefly's dot-dir skip does not make the corpus invisible (neutrality):

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run typebench suite \
  --corpus corpus/suite.toml \
  --cache-root typebench-cache \
  --tool mypy --tool pyright --tool pyrefly --tool ty \
  --thread-mode all-cores --thread-mode 1-core-constrained \
  --runs 3 --warmup 1 --mem-runs 3 \
  --output /tmp/typebench-suite.json
```

Verify the envelope:
```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python - <<'PY'
import json
e = json.load(open("/tmp/typebench-suite.json"))
print("schema_version", e["schema_version"], "suite", e["suite_version"], "runs", len(e["runs"]))
for r in e["runs"]:
    assert r["canonical_files"] == 23 or r["result_class"].startswith("failed"), r  # neutrality: same file set
    assert r["loc_denominator"] in ("code", None) or r["result_class"].startswith("failed")
    print(r["tool"], r["thread_mode"], r["result_class"],
          "code_loc", r["canonical_code_loc"], "kloc?", r["loc_denominator"], "over", r["over_reports"])
PY
```
Expected: 8 records (4 tools × 2 modes), `canonical_code_loc` populated (tokei ran), `canonical_files == 23` for every measured record (neutrality holds — same denominator as Plan 4's reference), `loc_denominator == "code"`.

Then render into a scratch README + trends file:
```bash
mkdir -p /tmp/tb-results /tmp/tb-site/data && cp /tmp/typebench-suite.json /tmp/tb-results/2026-06-08.json
printf '# scratch\n<!-- TYPEBENCH:BEGIN -->\nold\n<!-- TYPEBENCH:END -->\n' > /tmp/tb-README.md
UV_CACHE_DIR=/tmp/uv-cache uv run typebench render \
  --results-dir /tmp/tb-results --readme /tmp/tb-README.md --trends /tmp/tb-site/data/trends.json
echo "--- README block ---"; sed -n '/TYPEBENCH:BEGIN/,/TYPEBENCH:END/p' /tmp/tb-README.md
echo "--- trends points ---"; UV_CACHE_DIR=/tmp/uv-cache uv run python -c "import json;d=json.load(open('/tmp/tb-site/data/trends.json'));print(len(d['points']),'points',d['cpu_models'])"
```
Expected: a fastest-first markdown table per (httpx, mode) with real kLOC/s numbers (sanity vs Plan 4: par_eff ≈1.0 on 1-core; pyright peak ~2 GB on all-cores), and a trends.json with measured points + the box's CPU model. Investigate ANY divergence (a tokei file-set mismatch → `loc_denominator: "physical"`; a missing record; a wrong denominator) before declaring done — the real bench has caught a bug every plan.

- [ ] **Step 5: Final whole-suite gate (catches cross-file breakage the per-file gate misses)**

Run:
```bash
UV_CACHE_DIR=/tmp/uv-cache uv run ruff format && uv run ruff check && uv run pyrefly check && uv run pytest
```
Expected: fully green. Plan 5 is done only when this passes AND the real httpx suite bench above produced a clean envelope + rendered table.

---

## Self-Review (against the spec, post-write)

**Spec coverage**
- §7 envelope / §11 data flow → Tasks 1 (`ResultsEnvelope`), 10/11 (`suite` writes `results/<date>.json`), 14 (`render` → README + Pages data). ✓
- §8 metrics → Task 12 table (wall/peak-mem/cpu/parallel-eff/kLOC-s), code-LOC denominator (Tasks 4/5/12), diagnostics excluded (Task 12), over-report withholding (Tasks 7/12). ✓
- §5.6 stats → already in `TimingStats` (Plan 3/4); renderer surfaces median (min/dispersion available in JSON for the site). ✓ (No new stats math required; flagged: README shows median only — raw JSON carries the rest.)
- §5.7 calibration normalization → Task 13 fixed per-CPU-model anchor + inter-checker ratios (client-side) + CPU-model field. ✓
- §9 lock manifest → Tasks 1/2/3/5/6/7/8/10 (sha, lock_hash, config_hash, install_source, canonical counts, expanded env, suite_version). Frozen-dep contents intentionally in committed locks (Decision A). ✓
- §10 cost/sharding → Task 9 `--shard` + documented round-robin strategy. ✓ (Per-bucket wall-time budget numbers: the corpus is 1 project today; the budget table is filled when buckets are added — flagged, not blocking, matches spec "numbers filled during planning" with a single-project corpus.)
- §12 preflight gate → Task 10 project-level gate, excluded-as-FAILED records. ✓
- §13 testing → golden README/trends, normalization units, envelope round-trip, tokei fallback, suite via stub, import-guards untouched, real bench. ✓

**Placeholder scan:** no "TBD"/"add error handling"/"similar to Task N" — every code step shows full code. One executor-discretion note in Task 5 (reuse the existing envman fixture) is explicit about what to write. ✓

**Type/name consistency:** `RunManifest` fields (Task 7) match the stamping in `run_suite` (Task 10) and `cli.run` (Task 8). `ResultsEnvelope(suite_version, generated_at, runs)` consistent across Tasks 1/10/11/13/14. `count_code_loc`/`first_party_files` (Task 4) consumed in Tasks 5/12/13. `config_hash(src_roots, exclude_globs, python_version, python_platform)` signature identical in Tasks 2/8/10. `_lookup_project(Path, str) -> CorpusProject` matches the `lookup_entry` seam (Tasks 10/11). `loc_denominator` derivation (Task 7) matches renderer consumption (Tasks 12/13). ✓

**Known deferrals (intentional, documented):** GH Pages deploy Action + per-bucket budget numbers + larger corpus → Plan 6 / incremental. Inter-checker ratio is computed client-side (renderer emits raw+normalized points; the JS groups) to keep `renderer.py` responsibilities tight — flagged so a reviewer expecting a `ratio` field in `trends.json` knows it is by design.
