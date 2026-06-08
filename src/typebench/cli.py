"""typebench CLI (spec §5). Plan 1 exposes `run` for a single invocation."""

from __future__ import annotations

import os

# Runtime import (not TYPE_CHECKING): typer resolves option annotations at
# runtime via inspect.signature(eval_str=True), so `Path` in `Annotated[Path,
# typer.Option(...)]` must be importable then; `run` also calls Path() at runtime.
from pathlib import Path
from typing import Annotated

import typer

from typebench.adapters.pyright import PyrightAdapter
from typebench.adapters.stub import StubAdapter
from typebench.collector import run_single
from typebench.models import ThreadMode
from typebench.normalized_config import NormalizedConfig

app = typer.Typer(help="Neutral Python type-checker performance benchmark.")

# Adapter registry. Real checkers (mypy/pyright/pyrefly/ty) are added in Plan 2.
_ADAPTERS = {
    "stub": StubAdapter,
    "pyright": PyrightAdapter,
}


@app.callback()
def main() -> None:
    """Neutral Python type-checker performance benchmark.

    A no-op callback forces Typer to keep `run` as a named subcommand. Without
    it, a single-command app collapses the command name away and rejects an
    explicit `typebench run ...` invocation as an extra argument.
    """


@app.command()
def run(  # noqa: PLR0913 — each parameter is a distinct user-facing CLI option, not a code smell
    tool: Annotated[str, typer.Option(help="Checker to run (e.g. stub).")],
    project: Annotated[str, typer.Option(help="Project name or path.")],
    output: Annotated[Path, typer.Option(help="Where to write the results JSON.")],
    thread_mode: Annotated[ThreadMode, typer.Option(help="Thread track.")] = ThreadMode.ALL_CORES,
    runs: Annotated[int, typer.Option(help="hyperfine timed runs.")] = 10,
    warmup: Annotated[int, typer.Option(help="hyperfine warmup runs.")] = 3,
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

    src_roots = src_root or []
    # Fail fast: a real checker with no source roots yields an empty `include`
    # -> 0 files analyzed -> a false "clean". The stub legitimately ignores src
    # roots, so it is exempt.
    if tool != "stub" and not src_roots:
        typer.echo("--src-root is required for real tools (got none).", err=True)
        raise typer.Exit(code=2)

    config = NormalizedConfig(
        src_roots=tuple(str(Path(s).resolve()) for s in src_roots),
        python_version=python_version,
        python_platform=python_platform,
        venv_python=str(Path(venv).resolve()) if venv is not None else None,
    )
    adapter = factory()
    result = run_single(
        adapter,
        project=project,
        config=config,
        thread_mode=thread_mode,
        warmup=warmup,
        runs=runs,
        timeout=timeout,
    )
    output.write_text(result.model_dump_json(indent=2))
    typer.echo(f"{tool} / {project} -> {result.result_class.value} -> {output}")


if __name__ == "__main__":
    app()
