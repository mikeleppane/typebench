"""A/B orchestration: compare a candidate binary against a baseline spec over
arbitrary local targets, wall-time only. The middle layer between run_single
(no canonical LOC) and run_suite (assumes a corpus clone)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from typebench.contracts.config import DEFAULT_EXCLUDES, NormalizedConfig
from typebench.contracts.identity import CheckerSpec, Source
from typebench.contracts.models import ResolvedChecker, ResultsEnvelope
from typebench.contracts.policy import Policy
from typebench.engine.collector import RunManifest, run_single
from typebench.engine.proc import SYSTEM_HOST
from typebench.suite.services import PathResolver

if TYPE_CHECKING:
    from pathlib import Path

    from typebench.contracts.config import MeasurementPlan
    from typebench.contracts.models import RunResult
    from typebench.contracts.proc import ProcessHost
    from typebench.contracts.taxonomy import ThreadMode
    from typebench.suite.ports import CheckerResolver


def run_ab(  # noqa: PLR0913 — distinct A/B orchestration knobs (candidate binary, baseline spec, targets, plan, thread/cores, resolver seam) threaded from the action; collapsing them into a config object would only relocate the same surface
    *,
    checker: str,
    candidate_bin: str,
    candidate_label: str,
    baseline_spec: CheckerSpec,
    targets: list[Path],
    plan: MeasurementPlan,
    thread_mode: ThreadMode,
    cores: int,
    generated_at: str,
    baseline_resolver: CheckerResolver,
    host: ProcessHost = SYSTEM_HOST,
) -> ResultsEnvelope:
    """Resolve candidate (PathResolver) + baseline (injected resolver), run both
    arms over each manual target alternating candidate->baseline, return one
    wall-only envelope. measure_enabled comes from plan.measure (the action
    passes --no-measure -> False)."""
    candidate_spec = CheckerSpec(tool=checker, label=candidate_label, source=Source.PATH)
    candidate = PathResolver(candidate_bin, host=host).resolve(candidate_spec)
    baseline = baseline_resolver.resolve(baseline_spec)

    results: list[RunResult] = []
    for target in targets:
        project = target.name
        config = NormalizedConfig(
            src_roots=(str(target.resolve()),),
            exclude_globs=DEFAULT_EXCLUDES,
            python_version="3.12",
            python_platform="linux",
            venv_python=None,
            cores=cores,
        )
        for handle in (candidate, baseline):
            results.append(
                run_single(
                    handle.adapter,
                    project=project,
                    config=config,
                    thread_mode=thread_mode,
                    plan=plan,
                    calibration=None,
                    manifest=RunManifest(tool_install_source=handle.install_source),
                    binary=handle.binary,
                    checker_id=handle.checker_id,
                    policy=Policy.STANDARD,
                    headline_eligible=False,
                )
            )

    resolved = tuple(
        ResolvedChecker(
            checker_id=h.checker_id,
            tool=h.tool,
            version=h.runtime.version if h.runtime is not None else "unknown",
            lock_hash=h.runtime.lock_hash if h.runtime is not None else "",
            install_source=h.install_source,
        )
        for h in (candidate, baseline)
    )
    return ResultsEnvelope(
        suite_version=f"ab-{generated_at[:10]}",
        generated_at=generated_at,
        runs=results,
        resolved_checkers=resolved,
    )
