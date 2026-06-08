"""Results schema — the on-disk contract (spec §7, §8, §9)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

# ResultClass / ThreadMode / FailurePhase live in a pydantic-free module so the
# exit-code wrapper (hyperfine's per-run command) can import them without paying
# pydantic's import cost on every timed run. Re-exported here for the stable
# `typebench.models` import path the rest of the package and tests use.
from typebench.taxonomy import FailurePhase, ResultClass, ThreadMode

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


class EnvFingerprint(BaseModel):
    """Minimal environment stamp (spec §9 — expanded in later plans)."""

    model_config = ConfigDict(extra="forbid")

    os: str
    kernel: str
    cpu_model: str
    core_count: int
    python_version: str


class RunResult(BaseModel):
    """One (project x tool x thread-mode) measurement record.

    Plan 1 writes ONE record per file. The eventual results file (Plan 5)
    wraps many records in an envelope ({schema_version, runs: [...]}); the
    per-record schema_version below versions the record shape until then.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    tool: str
    tool_version: str
    project: str
    thread_mode: ThreadMode
    # Honesty flag (spec §5.3): True only once CPU affinity / a hard cap is
    # actually applied (Plan 4). Plan 1 never enforces, so it stays False — the
    # record must not imply a methodology the engine did not run.
    thread_mode_enforced: bool = False
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
    env: EnvFingerprint


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
