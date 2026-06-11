"""Production services for suite orchestration ports."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from typebench.adapters.base import CheckerHandle
from typebench.adapters.registry import create_adapter
from typebench.contracts.identity import CheckerRuntime, CheckerSpec
from typebench.contracts.policy import Policy
from typebench.corpus.catalog import load_suite, load_suite_version
from typebench.corpus.checkerenv import prepare_checker
from typebench.corpus.envman import prepare_project
from typebench.engine.calibration import calibrate
from typebench.engine.collector import run_single
from typebench.engine.proc import SYSTEM_HOST
from typebench.suite.preflight import preflight_project

if TYPE_CHECKING:
    from typebench.contracts.config import MeasurementPlan, NormalizedConfig
    from typebench.contracts.models import (
        CalibrationStats,
        PreflightReport,
        PreparedProject,
        RunResult,
    )
    from typebench.contracts.proc import ProcessHost
    from typebench.contracts.taxonomy import ThreadMode
    from typebench.corpus.catalog import CorpusProject
    from typebench.engine.collector import RunManifest


class CorpusCache:
    """Filesystem-backed corpus source using the prepared-project cache."""

    def __init__(
        self,
        suite_path: Path,
        cache_root: Path,
        *,
        projects: list[str] | None = None,
        host: ProcessHost = SYSTEM_HOST,
    ) -> None:
        self._suite_path = suite_path
        self._cache_root = cache_root
        self._host = host
        entries = load_suite(suite_path)
        self._entries = (
            [entry for entry in entries if entry.name in set(projects)]
            if projects is not None
            else entries
        )

    def entries(self) -> list[CorpusProject]:
        return list(self._entries)

    def version(self) -> str:
        return load_suite_version(self._suite_path)

    def prepare(self, entry: CorpusProject) -> PreparedProject:
        return prepare_project(entry, self._cache_root, host=self._host)


class UvCheckerResolver:
    """Resolve checker specs through the adapter registry and uv checker cache."""

    def __init__(self, cache_root: Path, *, host: ProcessHost = SYSTEM_HOST) -> None:
        self._cache_root = cache_root
        self._host = host

    def resolve(self, spec: CheckerSpec) -> CheckerHandle:
        adapter = create_adapter(spec.tool, host=self._host)
        runtime = prepare_checker(
            spec,
            self._cache_root,
            install_source=adapter.install_source,
            host=self._host,
        )
        return CheckerHandle(spec=spec, adapter=adapter, runtime=runtime)


class PathResolver:
    """Resolve a checker spec to an explicit prebuilt binary (Source.PATH).

    Unlike UvCheckerResolver, this never installs: it stamps the given binary as
    the runtime, with lock_hash = sha256 of the binary's bytes (the reproducibility
    anchor for a build-from-source artifact). Used by `run_ab` for the PR candidate.
    """

    def __init__(self, binary: str, *, host: ProcessHost = SYSTEM_HOST) -> None:
        self._binary = binary
        self._host = host

    def resolve(self, spec: CheckerSpec) -> CheckerHandle:
        adapter = create_adapter(spec.tool, host=self._host)
        resolved = Path(self._binary).resolve()
        binary = str(resolved)
        try:
            version = adapter.version(binary)
        except (OSError, ValueError):
            version = "path"
        # Adapters probe versions through a no-raise ProcessHost helper that
        # returns the "unknown" sentinel on a failed launch rather than raising,
        # so degrade that sentinel to "path" as well.
        if version == "unknown":
            version = "path"
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        checker_id = f"{spec.tool}@{version}"
        if spec.label:
            checker_id = f"{checker_id}+{spec.label}"
        runtime = CheckerRuntime(
            checker_id=checker_id,
            tool=spec.tool,
            binary=binary,
            version=version,
            lock_hash=f"sha256:{digest}",
            install_source="prebuilt binary (path)",
        )
        return CheckerHandle(spec=spec, adapter=adapter, runtime=runtime)


class LocalBenchEngine:
    """Production benchmark engine used by `typebench suite` and `compare`."""

    def calibrate(self, runs: int) -> CalibrationStats:
        return calibrate(runs)

    def preflight(
        self,
        prepared: PreparedProject,
        checkers: list[CheckerHandle],
        *,
        timeout: float,
        policy: Policy,
    ) -> PreflightReport:
        return preflight_project(prepared, checkers, timeout=timeout, policy=policy)

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
    ) -> RunResult:
        return run_single(
            checker.adapter,
            project=project,
            config=config,
            thread_mode=thread_mode,
            warmup=plan.warmup,
            runs=plan.runs,
            timeout=plan.timeout_s,
            mem_runs=plan.mem_runs,
            measure_enabled=plan.measure,
            calibration=calibration,
            manifest=manifest,
            binary=checker.binary,
            checker_id=checker.checker_id,
            policy=policy,
            headline_eligible=policy is Policy.STANDARD,
        )
