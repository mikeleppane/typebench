"""typebench CLI (spec §5). Plan 1 exposes `run` for a single invocation."""

from __future__ import annotations

import os

# Runtime import (not TYPE_CHECKING): typer resolves option annotations at
# runtime via inspect.signature(eval_str=True), so `Path` in `Annotated[Path,
# typer.Option(...)]` must be importable then — else NameError. Hence noqa TC003.
from pathlib import Path  # noqa: TC003
from typing import Annotated

import typer

from typebench.adapters.stub import StubAdapter
from typebench.collector import run_single
from typebench.models import ThreadMode

app = typer.Typer(help="Neutral Python type-checker performance benchmark.")

# Adapter registry. Real checkers (mypy/pyright/pyrefly/ty) are added in Plan 2.
_ADAPTERS = {
    "stub": StubAdapter,
}


@app.callback()
def main() -> None:
    """Neutral Python type-checker performance benchmark.

    A no-op callback forces Typer to keep `run` as a named subcommand. Without
    it, a single-command app collapses the command name away and rejects an
    explicit `typebench run ...` invocation as an extra argument.
    """


@app.command()
def run(
    tool: Annotated[str, typer.Option(help="Checker to run (e.g. stub).")],
    project: Annotated[str, typer.Option(help="Project name or path.")],
    output: Annotated[Path, typer.Option(help="Where to write the results JSON.")],
    thread_mode: Annotated[ThreadMode, typer.Option(help="Thread track.")] = ThreadMode.ALL_CORES,
    runs: Annotated[int, typer.Option(help="hyperfine timed runs.")] = 10,
    warmup: Annotated[int, typer.Option(help="hyperfine warmup runs.")] = 3,
    timeout: Annotated[float, typer.Option(help="Per-invocation timeout (seconds).")] = 900.0,
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

    adapter = factory()
    result = run_single(
        adapter,
        project=project,
        thread_mode=thread_mode,
        warmup=warmup,
        runs=runs,
        timeout=timeout,
    )
    output.write_text(result.model_dump_json(indent=2))
    typer.echo(f"{tool} / {project} -> {result.result_class.value} -> {output}")


if __name__ == "__main__":
    app()
