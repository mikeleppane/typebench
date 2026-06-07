"""Results schema — the on-disk contract (spec §7, §8, §9)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ResultClass(StrEnum):
    """Spec §7 failure taxonomy. String values are stable on disk."""

    CLEAN = "clean"
    DIAGNOSTICS = "diagnostics"
    FAILED_ENV = "failed{env}"
    FAILED_CRASH = "failed{crash}"
    FAILED_TIMEOUT = "failed{timeout}"
    FAILED_OOM = "failed{oom}"

    @property
    def is_measured_success(self) -> bool:
        """clean and diagnostics are successes; only real failures are excluded."""
        return self in (ResultClass.CLEAN, ResultClass.DIAGNOSTICS)


class ThreadMode(StrEnum):
    """Spec §5.3. A literal '1 thread' is not claimed; the floor is 1-core."""

    ONE_CORE = "1-core-constrained"
    ALL_CORES = "all-cores"


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
