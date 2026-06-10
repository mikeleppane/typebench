"""On-disk enums for result taxonomy and thread modes.

Deliberately pydantic-free: the exit-code wrapper imports ResultClass and runs
as hyperfine's per-run command, so importing pydantic here would add ~50ms of
startup to every timed run and bias the benchmark. Keep this module stdlib-only.
"""

from __future__ import annotations

from enum import StrEnum
from typing import assert_never


def _is_measured_success(value: ResultClass) -> bool:
    """clean and diagnostics are successes; only real failures are excluded.
    Exhaustive: a new ResultClass member trips assert_never at check time until
    it is explicitly classified here."""
    match value:
        case ResultClass.CLEAN | ResultClass.DIAGNOSTICS:
            return True
        case (
            ResultClass.FAILED_ENV
            | ResultClass.FAILED_CRASH
            | ResultClass.FAILED_TIMEOUT
            | ResultClass.FAILED_OOM
        ):
            return False
        case _ as unreachable:
            assert_never(unreachable)


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
        return _is_measured_success(self)


class ThreadMode(StrEnum):
    """Thread-mode labels. A literal '1 thread' is not claimed; the floor is 1-core."""

    CONSTRAINED = "constrained"
    ALL_CORES = "all-cores"


def is_constrained(mode: ThreadMode) -> bool:
    """Single mode->behavior chokepoint. A third ThreadMode member trips
    assert_never here at check time, forcing every call site to be revisited
    before this binary predicate can compile again."""
    match mode:
        case ThreadMode.CONSTRAINED:
            return True
        case ThreadMode.ALL_CORES:
            return False
        case _ as unreachable:
            assert_never(unreachable)


class FailurePhase(StrEnum):
    """Which pass produced a failure.

    PROBE  — the probe invocation itself failed; real_exit_code is its code.
    TIMING — the probe measured-succeeded but a timed run failed under hyperfine;
             real_exit_code is the *successful probe's*, not the failure's.
    """

    PROBE = "probe"
    TIMING = "timing"


class LocDenominator(StrEnum):
    """Which LOC count the headline kLOC/s used. String values are stable on disk."""

    CODE = "code"  # tokei reconciled code-LOC (blanks + comments excluded)
    PHYSICAL = "physical"  # fallback physical line count


class SizeBucket(StrEnum):
    """LOC bands that reveal scaling curves."""

    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    GIANT = "giant"
