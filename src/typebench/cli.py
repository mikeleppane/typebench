"""typebench CLI (spec §5). Plan 1 exposes `run` for a single invocation."""

from __future__ import annotations

import os

# Runtime import (not TYPE_CHECKING): typer resolves option annotations at
# runtime via inspect.signature(eval_str=True), so `Path` in `Annotated[Path,
# typer.Option(...)]` must be importable then; `run` also calls Path() at runtime.
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from typebench.adapters.mypy import MypyAdapter
from typebench.adapters.pyrefly import PyreflyAdapter
from typebench.adapters.pyright import PyrightAdapter
from typebench.adapters.stub import StubAdapter
from typebench.adapters.ty import TyAdapter
from typebench.calibration import calibrate
from typebench.collector import RunManifest, run_single
from typebench.corpus import load_suite
from typebench.envman import PrepareError, prepare_project
from typebench.models import ThreadMode
from typebench.normalized_config import DEFAULT_EXCLUDES, NormalizedConfig, config_hash
from typebench.preflight import preflight_project

if TYPE_CHECKING:
    from collections.abc import Callable

    from typebench.adapters.base import Adapter
    from typebench.corpus import CorpusProject
    from typebench.models import PreparedProject

app = typer.Typer(help="Neutral Python type-checker performance benchmark.")

# Default location for prepared clones/venvs. MUST be a non-hidden directory:
# pyrefly skips dot-directories during file discovery, so a hidden cache (e.g.
# `.typebench-cache`) makes the corpus invisible to pyrefly ALONE — it would see
# 0 files, fail{env}, and be excluded from headline aggregates (recorded, not
# dropped) while the other tools run. A tool-asymmetric cache location is a
# neutrality defect, so the default is plain.
DEFAULT_CACHE_ROOT = Path("typebench-cache")

# Adapter registry. All four real checkers + the controllable stub.
_ADAPTERS: dict[str, Callable[[], Adapter]] = {
    "mypy": MypyAdapter,
    "pyright": PyrightAdapter,
    "pyrefly": PyreflyAdapter,
    "stub": StubAdapter,
    "ty": TyAdapter,
}


@app.callback()
def main() -> None:
    """Neutral Python type-checker performance benchmark.

    A no-op callback forces Typer to keep `run` as a named subcommand. Without
    it, a single-command app collapses the command name away and rejects an
    explicit `typebench run ...` invocation as an extra argument.
    """


def _adapters_for(tools: list[str]) -> list[Adapter]:
    """Resolve tool names to adapter instances, erroring on an unknown tool."""
    out: list[Adapter] = []
    for name in tools:
        factory = _ADAPTERS.get(name)
        if factory is None:
            typer.echo(f"Unknown tool: {name!r}. Known: {sorted(_ADAPTERS)}", err=True)
            raise typer.Exit(code=2)
        out.append(factory())
    return out


def _lookup_project(corpus: Path, name: str) -> CorpusProject:
    for entry in load_suite(corpus):
        if entry.name == name:
            return entry
    typer.echo(f"Unknown corpus project: {name!r} in {corpus}", err=True)
    raise typer.Exit(code=2)


@app.command()
def run(  # noqa: PLR0913 — each parameter is a distinct user-facing CLI option, not a code smell
    tool: Annotated[str, typer.Option(help="Checker to run (e.g. stub).")],
    output: Annotated[Path, typer.Option(help="Where to write the results JSON.")],
    project: Annotated[
        str | None,
        typer.Option(help="Project name/path (omit when using --corpus-project)."),
    ] = None,
    thread_mode: Annotated[ThreadMode, typer.Option(help="Thread track.")] = ThreadMode.ALL_CORES,
    runs: Annotated[int, typer.Option(help="hyperfine timed runs.")] = 10,
    warmup: Annotated[int, typer.Option(help="hyperfine warmup runs.")] = 3,
    mem_runs: Annotated[
        int,
        typer.Option(
            help=(
                "Resource-pass repeats (peak memory variance, spec §5.5). >=1; >=3 for "
                "official numbers."
            )
        ),
    ] = 3,
    measure: Annotated[
        bool,
        typer.Option(help="Run the cgroup memory/CPU pass (auto-skips if unavailable)."),
    ] = True,
    calibrate_baseline: Annotated[
        bool,
        typer.Option(
            "--calibrate/--no-calibrate", help="Time the calibration workload (spec §5.7)."
        ),
    ] = True,
    calib_runs: Annotated[int, typer.Option(help="Calibration workload repeats (>=1).")] = 5,
    timeout: Annotated[float, typer.Option(help="Per-invocation timeout (seconds).")] = 900.0,
    src_root: Annotated[
        list[str] | None,
        typer.Option(
            help="First-party source dir to analyze (repeatable). Required for real tools."
        ),
    ] = None,
    python_version: Annotated[str, typer.Option(help="Target Python version.")] = "3.12",
    python_platform: Annotated[str, typer.Option(help="Target platform.")] = "linux",
    venv: Annotated[
        str | None, typer.Option(help="Project venv interpreter for dep resolution.")
    ] = None,
    corpus: Annotated[
        Path | None,
        typer.Option(help="suite.toml; with --corpus-project, derive config from it."),
    ] = None,
    corpus_project: Annotated[
        str | None, typer.Option(help="Corpus project name to run (requires --corpus).")
    ] = None,
    cache_root: Annotated[
        Path, typer.Option(help="Where prepared clones/venvs are cached.")
    ] = DEFAULT_CACHE_ROOT,
) -> None:
    factory = _ADAPTERS.get(tool)
    if factory is None:
        typer.echo(f"Unknown tool: {tool!r}. Known: {sorted(_ADAPTERS)}", err=True)
        raise typer.Exit(code=2)

    # Fail fast on a bad --output: a single run can take many minutes, so a
    # non-existent or read-only output directory must not surface only after all
    # the measurement work is already done and unrecoverable.
    out_dir = output.parent
    if not out_dir.exists() or not os.access(out_dir, os.W_OK):
        typer.echo(f"Output directory not writable: {out_dir}", err=True)
        raise typer.Exit(code=2)
    if mem_runs < 1:
        typer.echo("--mem-runs must be >= 1 (>= 3 recommended, spec §5.5).", err=True)
        raise typer.Exit(code=2)
    if calib_runs < 1:
        typer.echo("--calib-runs must be >= 1.", err=True)
        raise typer.Exit(code=2)

    prepared: PreparedProject | None = None
    entry: CorpusProject | None = None
    if corpus_project is not None:
        if corpus is None:
            typer.echo("--corpus-project requires --corpus.", err=True)
            raise typer.Exit(code=2)
        if src_root or venv:
            typer.echo("--corpus-project cannot be combined with --src-root/--venv.", err=True)
            raise typer.Exit(code=2)
        entry = _lookup_project(corpus, corpus_project)
        try:
            prepared = prepare_project(entry, cache_root)
        except PrepareError as exc:
            # Same controlled-failure posture as the `preflight` command: a
            # clone/install/lock-drift failure is a CLI error, not a traceback.
            typer.echo(f"prepare failed for {corpus_project!r}: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        project = entry.name
        src_root = list(prepared.src_roots)
        python_version = prepared.python_version
        python_platform = prepared.python_platform
        venv = prepared.venv_python or None
    if project is None:
        typer.echo("Provide --project (manual) or --corpus-project (corpus mode).", err=True)
        raise typer.Exit(code=2)

    src_roots = src_root or []
    # Fail fast: a real checker with no source roots yields an empty `include`
    # -> 0 files analyzed -> a false "clean". The stub legitimately ignores src
    # roots, so it is exempt.
    if tool != "stub" and not src_roots:
        typer.echo("--src-root is required for real tools (got none).", err=True)
        raise typer.Exit(code=2)

    config = NormalizedConfig(
        src_roots=tuple(str(Path(s).resolve()) for s in src_roots),
        exclude_globs=(prepared.exclude_globs if prepared is not None else DEFAULT_EXCLUDES),
        python_version=python_version,
        python_platform=python_platform,
        # abspath, NOT resolve(): a venv's bin/python is a symlink to the base
        # interpreter; resolving it would walk out of the venv and break pyright's
        # venvPath/venv derivation. abspath makes it absolute without following the
        # symlink. (src_roots are dirs, so .resolve() above is fine for them.)
        venv_python=os.path.abspath(venv) if venv is not None else None,  # noqa: PTH100 - need non-symlink-following abspath; Path.resolve() follows symlinks
    )
    adapter = factory()
    manifest: RunManifest | None = None
    if prepared is not None and corpus_project is not None and entry is not None:
        manifest = RunManifest(
            project_sha=prepared.sha,
            lock_hash=prepared.lock_hash,
            config_hash=config_hash(
                entry.src_roots,
                entry.effective_excludes(),
                entry.python_version,
                entry.python_platform,
            ),
            canonical_files=prepared.canonical_files,
            canonical_loc=prepared.canonical_loc,
            canonical_code_loc=prepared.canonical_code_loc,
            tool_install_source=adapter.install_source,
            over_reports=None,
        )
    calibration = calibrate(runs=calib_runs) if calibrate_baseline else None
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
    output.write_text(result.model_dump_json(indent=2))
    typer.echo(f"{tool} / {project} -> {result.result_class.value} -> {output}")


@app.command()
def preflight(
    corpus: Annotated[Path, typer.Option(help="Path to suite.toml.")],
    project: Annotated[str, typer.Option(help="Corpus project name to preflight.")],
    output: Annotated[Path, typer.Option(help="Where to write the PreflightReport JSON.")],
    tool: Annotated[
        list[str] | None,
        typer.Option(help="Tools to probe (repeatable). Default: all four real checkers."),
    ] = None,
    cache_root: Annotated[
        Path, typer.Option(help="Where prepared clones/venvs are cached.")
    ] = DEFAULT_CACHE_ROOT,
    timeout: Annotated[float, typer.Option(help="Per-probe timeout (seconds).")] = 900.0,
) -> None:
    """Prepare a corpus project and probe each tool once."""
    entry = _lookup_project(corpus, project)
    tools = tool or ["mypy", "pyright", "ty", "pyrefly"]
    adapters = _adapters_for(tools)
    out_dir = output.parent
    if not out_dir.exists() or not os.access(out_dir, os.W_OK):
        typer.echo(f"Output directory not writable: {out_dir}", err=True)
        raise typer.Exit(code=2)
    try:
        prepared = prepare_project(entry, cache_root)
    except PrepareError as exc:
        typer.echo(f"preflight: prepare failed for {project!r}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    report = preflight_project(prepared, adapters, timeout=timeout)
    output.write_text(report.model_dump_json(indent=2))
    status = "ready" if report.ready else "NOT READY"
    typer.echo(f"preflight {project} -> {status} ({report.canonical_files} files) -> {output}")
    if not report.ready:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
