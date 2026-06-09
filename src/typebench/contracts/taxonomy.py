"""On-disk enums for result taxonomy and thread modes.

Deliberately pydantic-free: the exit-code wrapper imports ResultClass and runs
as hyperfine's per-run command, so importing pydantic here would add ~50ms of
startup to every timed run and bias the benchmark. Keep this module stdlib-only.
"""

from __future__ import annotations

from enum import StrEnum


class ResultClass(StrEnum):
    """Failure taxonomy. String values are stable on disk."""

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
    """Thread-mode labels. A literal '1 thread' is not claimed; the floor is 1-core."""

    CONSTRAINED = "constrained"
    ALL_CORES = "all-cores"


class FailurePhase(StrEnum):
    """Which pass produced a failure.

    PROBE  — the probe invocation itself failed; real_exit_code is its code.
    TIMING — the probe measured-succeeded but a timed run failed under hyperfine;
             real_exit_code is the *successful probe's*, not the failure's.
    """

    PROBE = "probe"
    TIMING = "timing"
