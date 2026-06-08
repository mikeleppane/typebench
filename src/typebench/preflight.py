"""Preflight gate (spec §12).

Validates a prepared corpus project is checkable before timing, and records each
tool's self-reported file count against the canonical denominator (§8).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from typebench.models import PreflightReport, ResultClass, ThreadMode, ToolPreflight
from typebench.normalized_config import NormalizedConfig
from typebench.wrapper import RawRun, run_command

if TYPE_CHECKING:
    from typebench.adapters.base import Adapter
    from typebench.models import PreparedProject


class Probe(Protocol):
    def __call__(self, argv: list[str], timeout: float, env: dict[str, str] | None = ...) -> RawRun:
        """Run a preflight probe command."""
        ...


def _config_for(prepared: PreparedProject) -> NormalizedConfig:
    return NormalizedConfig(
        src_roots=prepared.src_roots,
        exclude_globs=prepared.exclude_globs,
        python_version=prepared.python_version,
        python_platform=prepared.python_platform,
        venv_python=prepared.venv_python or None,
    )


def _probe_one(
    adapter: Adapter,
    prepared: PreparedProject,
    config: NormalizedConfig,
    timeout: float,
    probe: Probe,
) -> ToolPreflight:
    """Probe one adapter and assemble its ToolPreflight."""
    with tempfile.TemporaryDirectory(prefix="typebench-preflight-") as tmp:
        try:
            argv, env = adapter.command(prepared.name, config, ThreadMode.ONE_CORE, Path(tmp))
        except (OSError, ValueError) as exc:
            return ToolPreflight(
                tool=adapter.name,
                version=adapter.version(),
                result_class=ResultClass.FAILED_ENV,
                real_exit_code=-1,
                error_detail=f"command construction failed: {exc}".strip()[-500:],
            )
        raw = probe(argv, timeout, env)

    result_class = adapter.classify(raw)
    self_files: int | None = None
    divergence: int | None = None
    scope_ok = True
    over_reports = False
    error_detail: str | None = None
    if result_class.is_measured_success:
        _diagnostics, self_files = adapter.parse(raw.stdout, raw.stderr, raw.exit_code)
        if self_files is not None:
            divergence = self_files - prepared.canonical_files
            scope_ok = self_files >= prepared.canonical_files
            over_reports = self_files > prepared.canonical_files
    else:
        error_detail = (raw.stderr.strip() or raw.stdout.strip())[-500:] or None

    return ToolPreflight(
        tool=adapter.name,
        version=adapter.version(),
        result_class=result_class,
        real_exit_code=raw.exit_code,
        signal=raw.signal,
        timed_out=raw.timed_out,
        oom=raw.oom,
        error_detail=error_detail,
        self_reported_files=self_files,
        files_divergence=divergence,
        scope_ok=scope_ok,
        over_reports=over_reports,
    )


def preflight_project(
    prepared: PreparedProject,
    adapters: list[Adapter],
    *,
    timeout: float,
    probe: Probe = run_command,
) -> PreflightReport:
    """Probe each adapter once on the prepared project."""
    config = _config_for(prepared)
    tools = [_probe_one(adapter, prepared, config, timeout, probe) for adapter in adapters]
    return PreflightReport(
        project=prepared.name,
        sha=prepared.sha,
        python_version=prepared.python_version,
        lock_hash=prepared.lock_hash,
        canonical_files=prepared.canonical_files,
        canonical_loc=prepared.canonical_loc,
        # Neutrality note: scope_ok is reliability-based, not tool-identity-based.
        # A tool whose parse() yields self_reported_files is None (ty, pyrefly —
        # both stderr-scraped) keeps scope_ok=True and is not gated on an
        # unverifiable count; mypy/pyright (reliable counts) are held to
        # self >= canonical. This None-tolerance is symmetric across the two Rust
        # tools and cannot favor pyrefly over ty. mypy/pyright independently
        # promote a 0/None-file CLEAN to FAILED_ENV in their own classify(), so
        # they cannot slip an unverifiable count through here either.
        ready=all(tool.result_class.is_measured_success and tool.scope_ok for tool in tools),
        throughput_review_required=any(tool.over_reports for tool in tools),
        tools=tools,
    )
