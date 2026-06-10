"""Structural ports for suite orchestration.

The runner owns matrix and failure-record policy. Filesystem, checker-env, and
benchmark execution details live behind these small protocols so the runner can
be tested without a long list of callables.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from typebench.adapters.base import CheckerHandle
    from typebench.contracts.config import MeasurementPlan, NormalizedConfig
    from typebench.contracts.identity import CheckerSpec
    from typebench.contracts.models import (
        CalibrationStats,
        PreflightReport,
        PreparedProject,
        RunResult,
    )
    from typebench.contracts.policy import Policy
    from typebench.contracts.taxonomy import ThreadMode
    from typebench.corpus.catalog import CorpusProject
    from typebench.engine.collector import RunManifest


class CorpusSource(Protocol):
    """Source of selected corpus entries and prepared project environments."""

    def entries(self) -> list[CorpusProject]: ...

    def version(self) -> str: ...

    def prepare(self, entry: CorpusProject) -> PreparedProject: ...


class CheckerResolver(Protocol):
    """Resolve a checker spec into an adapter plus optional prepared runtime."""

    def resolve(self, spec: CheckerSpec) -> CheckerHandle: ...


class BenchEngine(Protocol):
    """Benchmark execution surface used by suite orchestration."""

    def calibrate(self, runs: int) -> CalibrationStats: ...

    def preflight(
        self,
        prepared: PreparedProject,
        checkers: list[CheckerHandle],
        *,
        timeout: float,
        policy: Policy,
    ) -> PreflightReport: ...

    def run_cell(
        self,
        checker: CheckerHandle,
        *,
        project: str,
        config: NormalizedConfig,
        thread_mode: ThreadMode,
        plan: MeasurementPlan,
        manifest: RunManifest,
        calibration: CalibrationStats | None,
        policy: Policy,
    ) -> RunResult: ...
