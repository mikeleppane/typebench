"""Results schema — the on-disk contract (spec §7, §8, §9)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

# ResultClass / ThreadMode / FailurePhase live in a pydantic-free module so the
# exit-code wrapper (hyperfine's per-run command) can import them without paying
# pydantic's import cost on every timed run. Re-exported here for the stable
# `typebench.contracts.models` import path the rest of the package and tests use.
from typebench.contracts.taxonomy import FailurePhase, ResultClass, ThreadMode

__all__ = [
    "CalibrationStats",
    "EnvFingerprint",
    "FailurePhase",
    "MemoryStats",
    "PreflightReport",
    "PreparedProject",
    "ResultClass",
    "ResultsEnvelope",
    "RunResult",
    "ThreadMode",
    "TimingStats",
    "ToolPreflight",
]


class TimingStats(BaseModel):
    """Wall-time statistics from one hyperfine timing pass (spec §5.4)."""

    model_config = ConfigDict(extra="forbid")

    runs: int
    min_s: float
    median_s: float
    mean_s: float
    stddev_s: float
    max_s: float
    times_s: list[float]


class MemoryStats(BaseModel):
    """Peak cgroup-memory statistics from the resource pass (spec §5.5).

    "Peak cgroup memory," NOT RSS: cgroup v2 `memory.peak` is the max usage charged
    to the scope and ALL descendants (page cache, kernel structs, every child) — the
    right cross-tool number (it catches pyright's Node process and worker threads
    `/usr/bin/time -v` would miss). It also includes the in-scope Python wrapper: a
    ~10-15 MB interpreter baseline PLUS the captured checker stdout/stderr buffer.
    That buffer is small + output-dependent, not strictly constant — for normal
    type-check diagnostics (KBs) it is <1% of the checker's own analysis memory
    (tens-hundreds of MB) and does not set the peak, but a pathologically
    diagnostics-heavy run could inflate it. Known limitation (measure.main); a
    diagnostics-flood corpus entry would warrant streaming output to disk.
    `peak_bytes_*` are min/median/max over `runs` repeats. `memory_stat` is the
    `memory.stat` snapshot of the median-peak run (a data point, never a ranking)."""

    model_config = ConfigDict(extra="forbid")

    runs: int
    peak_bytes_min: int
    peak_bytes_median: int
    peak_bytes_max: int
    memory_stat: dict[str, int] | None = None


class CalibrationStats(BaseModel):
    """Per-run calibration baseline (spec §5.7). A fixed CPU-bound Python workload
    timed alongside the real run so weekly trends can be normalized against the
    VM-to-VM hardware lottery. RAW seconds only (min/median/max over `runs`):
    normalization to a reference is a render-time transform (Plan 5), so no factor
    is baked here — storing raw keeps the choice of reference open and honest.
    `workload_id` + `iterations` lock the workload identity for the manifest."""

    model_config = ConfigDict(extra="forbid")

    workload_id: str
    iterations: int
    runs: int
    raw_min_s: float
    raw_median_s: float
    raw_max_s: float


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


class RunResult(BaseModel):
    """One (project x tool x thread-mode) measurement record.

    `typebench run` writes ONE self-contained record. `typebench suite` wraps
    many records in ResultsEnvelope; the per-record schema_version versions this
    record shape independently of the envelope.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 3
    tool: str
    tool_version: str
    project: str
    thread_mode: ThreadMode
    # Honesty flag (spec §5.3), CONSTRAINED-specific: True only when the N-core
    # taskset affinity pin was actually applied. ALL_CORES is unconstrained by
    # design and leaves this False (it claims no pinning, so False is honest).
    # The record must never imply a methodology the engine did not run.
    thread_mode_enforced: bool = False
    # The CONSTRAINED track's pinned core count (taskset -c 0..cores-1) + the per-tool
    # worker cap (spec §5.3). None on ALL_CORES (unconstrained) and when affinity did
    # not actually run, so the record never claims a pin width it did not apply.
    cores: int | None = None
    # Per-tool worker-cap honesty (spec §5.3), from Adapter.parallelism_cap():
    # hard_cap True = a real worker cap (pyrefly/ty/mypy worker count), False =
    # best-effort (ty's soft TY_MAX_PARALLELISM, pyright --threads hint).
    # Recorded only for the CONSTRAINED track; None for ALL_CORES.
    hard_cap: bool | None = None
    cap_mechanism: str | None = None
    result_class: ResultClass
    # Which pass produced a failure: PROBE (real_exit_code is the failing
    # command's) or TIMING (real_exit_code is the *successful* probe's — the
    # failure was a flaky timed run). None on measured-success (spec §5.1 audit).
    failure_phase: FailurePhase | None = None
    real_exit_code: int
    # Failure metadata — enough to audit failed{env} vs failed{crash}/{oom}
    # after the fact (spec §5.1). None/False on success.
    signal: int | None = None
    timed_out: bool = False
    oom: bool = False
    error_detail: str | None = None
    diagnostics: int | None = None
    files: int | None = None
    timing: TimingStats | None = None
    # Resource pass (spec §5.5), None when measurement is unavailable (mac/CI):
    memory: MemoryStats | None = None
    cpu_time_s: float | None = None  # user+sys from cgroup cpu.stat
    parallel_efficiency: float | None = None  # cpu_time_s / wall median (~1 single-thread)
    calibration: CalibrationStats | None = None
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


class ResultsEnvelope(BaseModel):
    """The committed results file (spec §7/§11): many records + suite metadata.
    `typebench run` writes ONE self-contained RunResult; `typebench suite` writes
    this envelope as results/<date>.json (git history = the time-series)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    suite_version: str
    generated_at: str  # ISO-8601 UTC, stamped by the CLI
    runs: list[RunResult]


class PreparedProject(BaseModel):
    """An envman-prepared corpus project persisted as a cache sidecar."""

    model_config = ConfigDict(extra="forbid")

    name: str
    checkout: str
    venv_python: str
    src_roots: tuple[str, ...]
    exclude_globs: tuple[str, ...]
    python_version: str
    python_platform: str
    sha: str
    lock_hash: str
    frozen: tuple[str, ...]
    canonical_files: int
    canonical_loc: int
    canonical_code_loc: int | None = None  # tokei code-LOC; None when tokei unavailable
    fingerprint: str


class ToolPreflight(BaseModel):
    """One tool's preflight outcome on a prepared project."""

    model_config = ConfigDict(extra="forbid")

    tool: str
    version: str
    result_class: ResultClass
    real_exit_code: int
    signal: int | None = None
    timed_out: bool = False
    oom: bool = False
    error_detail: str | None = None
    self_reported_files: int | None = None
    files_divergence: int | None = None
    scope_ok: bool = True
    over_reports: bool = False


class PreflightReport(BaseModel):
    """Per-project preflight result (spec §12)."""

    model_config = ConfigDict(extra="forbid")

    project: str
    sha: str
    python_version: str
    lock_hash: str
    canonical_files: int
    canonical_loc: int
    ready: bool
    throughput_review_required: bool = False
    tools: list[ToolPreflight]
