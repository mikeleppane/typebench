"""typebench command-line interface."""

from __future__ import annotations

import json
import os
import webbrowser
from datetime import UTC, datetime

# Runtime import (not TYPE_CHECKING): typer resolves option annotations at
# runtime via inspect.signature(eval_str=True), so `Path` in `Annotated[Path,
# typer.Option(...)]` must be importable then; `run` also calls Path() at runtime.
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from pydantic import ValidationError
from rich import box
from rich.console import Console
from rich.table import Table

from typebench.adapters.base import CheckerHandle
from typebench.adapters.registry import (
    UnknownToolError,
    create_adapter,
    default_checker_specs,
    default_tools,
    validate_specs,
)
from typebench.contracts.config import (
    DEFAULT_EXCLUDES,
    MeasurementPlan,
    NormalizedConfig,
    config_hash,
)
from typebench.contracts.configfile import (
    RunCliOverrides,
    discover_config,
    load_config,
    merge_cli,
    resolve_corpus,
)
from typebench.contracts.identity import CheckerSpec, Source
from typebench.contracts.models import ResultsEnvelope
from typebench.contracts.policy import Policy
from typebench.contracts.runconfig import RunConfig, merge_tool_override
from typebench.contracts.taxonomy import SizeBucket, ThreadMode
from typebench.corpus.catalog import load_suite
from typebench.corpus.checkerenv import cache_status as checker_cache_status
from typebench.corpus.envman import PrepareError, prepare_project
from typebench.engine.calibration import calibrate
from typebench.engine.collector import RunManifest, run_single
from typebench.engine.doctor import Tier, run_doctor
from typebench.suite.ab import run_ab
from typebench.suite.preflight import preflight_project
from typebench.suite.renderer import (
    build_report_html,
    build_trends,
    render_ab,
    render_compare,
    render_readme,
    render_terminal,
)
from typebench.suite.runner import run_suite
from typebench.suite.selection import SelectionError, resolve_selection
from typebench.suite.services import (
    CorpusCache,
    LocalBenchEngine,
    PathResolver,
    UvCheckerResolver,
)

if TYPE_CHECKING:
    from typebench.contracts.models import PreparedProject
    from typebench.corpus.catalog import CorpusProject
    from typebench.suite.ports import CheckerResolver

app = typer.Typer(help="Neutral Python type-checker performance benchmark.")
config_app = typer.Typer(help="Inspect and scaffold typebench configuration.")
app.add_typer(config_app, name="config")

# Default location for prepared clones/venvs. MUST be a non-hidden directory:
# pyrefly skips dot-directories during file discovery, so a hidden cache (e.g.
# `.typebench-cache`) makes the corpus invisible to pyrefly ALONE — it would see
# 0 files, fail{env}, and be excluded from headline aggregates (recorded, not
# dropped) while the other tools run. A tool-asymmetric cache location is a
# neutrality defect, so the default is plain.
DEFAULT_CACHE_ROOT = Path("typebench-cache")
_README_BEGIN = "<!-- TYPEBENCH:BEGIN -->"
_README_END = "<!-- TYPEBENCH:END -->"
_DEFAULT_TOOLS = default_tools()
_MIN_COMPARE_CHECKERS = 2
# run_ab emits resolved_checkers as (candidate, baseline); index 1 is the baseline.
_AB_ARMS = 2
_INIT_FALLBACKS = {
    "mypy": "1.18.2",
    "pyright": "1.1.410",
    "pyrefly": "0.36.2",
    "ty": "0.0.1",
}
_INIT_TEMPLATE = """\
# typebench run configuration. Layered: built-in defaults < this file < CLI flags.
policy = "standard"
# corpus = "corpus/suite.toml"   # optional; default resolves to corpus/suite.toml

# projects = ["httpx", "sqlalchemy"]   # explicit names
# buckets = ["large"]                  # OR/ALSO whole buckets; both empty = whole corpus

[tracks]
thread_modes = ["all-cores", "constrained"]
cores = [1]                            # constrained sweep, e.g. [1, 4, 8]

[[checker]]
tool = "mypy"
version = "{mypy}"

[[checker]]
tool = "pyright"
version = "{pyright}"

[[checker]]
tool = "pyrefly"
version = "{pyrefly}"

[[checker]]
tool = "ty"
version = "{ty}"

[run]
runs = 10
warmup = 3
mem_runs = 3
"""


def _assert_output_writable(output: Path) -> None:
    # Fail fast on a bad --output: a single run can take many minutes, so a
    # non-existent or read-only output directory must not surface only after all
    # the measurement work is already done and unrecoverable.
    out_dir = output.parent
    if not out_dir.exists() or not os.access(out_dir, os.W_OK):
        typer.echo(f"Output directory not writable: {out_dir}", err=True)
        raise typer.Exit(code=2)


def _available_cores() -> int:
    """Cores this process may actually use — the CPU-affinity mask size on Linux
    (honors container/cpuset limits), else the logical CPU count. The upper bound
    for `--cores`: asking for more would tell a checker to spawn more workers than
    exist (mypy `--num-workers 999` self-crashes with an INTERNAL ERROR) and the
    `taskset` pin could never be applied anyway."""
    getaffinity = getattr(os, "sched_getaffinity", None)
    if getaffinity is not None:
        return len(getaffinity(0))
    return os.cpu_count() or 1


def _validate_cores(cores: int) -> int:
    """Validate + clamp `--cores`. Below 1 is a hard error; above the available
    core count is clamped down (with a notice) rather than crashed — the recorded
    `cores` then honestly reflects what actually ran, and the same command stays
    portable across machines with different core counts."""
    if cores < 1:
        typer.echo("--cores must be >= 1.", err=True)
        raise typer.Exit(code=2)
    available = _available_cores()
    if cores > available:
        typer.echo(
            f"--cores {cores} exceeds {available} usable cores; clamping to {available}.",
            err=True,
        )
        return available
    return cores


def _parse_cores_list(spec: str) -> list[int]:
    """Parse a comma-separated constrained-core sweep."""
    try:
        values = [int(piece.strip()) for piece in spec.split(",") if piece.strip()]
    except ValueError:
        typer.echo(
            f"--cores-list must be comma-separated ints (e.g. 1,4,8), got {spec!r}",
            err=True,
        )
        raise typer.Exit(code=2) from None
    if not values or any(value < 1 for value in values):
        typer.echo("--cores-list values must each be >= 1.", err=True)
        raise typer.Exit(code=2)
    return [_validate_cores(value) for value in values]


def _print_dry_run(run_config: RunConfig, selected: list[str], corpus: Path) -> None:
    """Print the effective plan without executing the suite."""
    checker_ids = [spec.checker_id() for spec in run_config.checkers]
    per_pair = sum(
        1 if mode is ThreadMode.ALL_CORES else len(run_config.cores)
        for mode in run_config.thread_modes
    )
    matrix = len(selected) * len(checker_ids) * per_pair
    typer.echo(f"policy: {run_config.policy.value}")
    typer.echo(f"corpus: {corpus}")
    typer.echo(f"checkers: {checker_ids}")
    typer.echo(f"selection ({len(selected)}): {selected}")
    typer.echo(
        f"thread_modes: {[mode.value for mode in run_config.thread_modes]}  "
        f"cores: {list(run_config.cores)}"
    )
    typer.echo(f"matrix size: {matrix} cells")
    typer.echo(f"headline-eligible: {run_config.policy is Policy.STANDARD}")
    typer.echo("checker caches:")
    for spec in run_config.checkers:
        state, version = checker_cache_status(spec, DEFAULT_CACHE_ROOT)
        resolved = f" (resolved {version})" if version else ""
        typer.echo(f"  {spec.checker_id():<24}  {state}{resolved}")


def _version_token(text: str, fallback: str) -> str:
    """Best-effort extraction for checker `--version` output."""
    for token in text.split():
        stripped = token.strip("(),")
        if stripped and stripped[0].isdigit():
            return stripped
    return fallback


def _probe_init_version(tool: str, fallback: str) -> str:
    """Probe one adapter version for config init, falling back offline."""
    try:
        return _version_token(create_adapter(tool).version(), fallback)
    except Exception:
        return fallback


@app.callback()
def main() -> None:
    """Neutral Python type-checker performance benchmark.

    A no-op callback forces Typer to keep `run` as a named subcommand. Without
    it, a single-command app collapses the command name away and rejects an
    explicit `typebench run ...` invocation as an extra argument.
    """


@config_app.command("init")
def config_init(
    path: Annotated[Path, typer.Argument(help="Where to write typebench.toml.")],
) -> None:
    """Scaffold a commented typebench.toml pinning the four standard checkers."""
    versions = {tool: _probe_init_version(tool, _INIT_FALLBACKS[tool]) for tool in _DEFAULT_TOOLS}
    path.write_text(_INIT_TEMPLATE.format(**versions), encoding="utf-8")
    typer.echo(str(path))


@config_app.command("show")
def config_show(
    config: Annotated[
        Path | None,
        typer.Option("-c", "--config", help="typebench.toml to show; defaults to cwd discovery."),
    ] = None,
) -> None:
    """Print the effective file config."""
    path = config or discover_config(Path.cwd())
    if path is None:
        typer.echo("No typebench.toml found; pass -c/--config.", err=True)
        raise typer.Exit(code=2)
    run_config = _load_config_or_exit(path)
    typer.echo(f"policy: {run_config.policy.value}")
    typer.echo("checkers:")
    for spec in run_config.checkers:
        typer.echo(f"  {spec.checker_id()}")
    typer.echo(f"thread_modes: {[mode.value for mode in run_config.thread_modes]}")
    typer.echo(f"cores: {list(run_config.cores)}")
    if run_config.projects:
        selection = list(run_config.projects)
    elif run_config.buckets:
        selection = [bucket.value for bucket in run_config.buckets]
    else:
        selection = "(whole corpus)"
    typer.echo(f"selection: {selection}")


def _validate_timing(runs: int, warmup: int, timeout: float) -> None:
    """Reject timing args that would hang or corrupt measurement before any work
    starts: `hyperfine --runs 0` spins forever, and a negative warmup / non-positive
    timeout is meaningless. Mirrors the existing mem_runs/calib_runs guards."""
    if runs < 1:
        typer.echo("--runs must be >= 1.", err=True)
        raise typer.Exit(code=2)
    if warmup < 0:
        typer.echo("--warmup must be >= 0.", err=True)
        raise typer.Exit(code=2)
    if timeout <= 0:
        typer.echo("--timeout must be > 0.", err=True)
        raise typer.Exit(code=2)


def _validation_message(exc: ValidationError) -> str:
    details = "; ".join(error["msg"] for error in exc.errors())
    return details or str(exc)


def _load_suite_or_exit(corpus: Path) -> list[CorpusProject]:
    """Load the corpus, turning a missing/unreadable/malformed suite.toml into a
    clean CLI error rather than a raw traceback (load_suite's read_text has no
    existence check)."""
    try:
        return load_suite(corpus)
    except (OSError, ValueError) as exc:
        typer.echo(f"Could not read corpus {corpus}: {exc}", err=True)
        raise typer.Exit(code=2) from exc


def _load_config_or_exit(path: Path) -> RunConfig:
    """Load typebench.toml, translating file/validation failures to a CLI error."""
    try:
        return load_config(path)
    except (OSError, ValueError) as exc:
        typer.echo(f"Could not read config {path}: {exc}", err=True)
        raise typer.Exit(code=2) from exc


def _lookup_project(corpus: Path, name: str) -> CorpusProject:
    for entry in _load_suite_or_exit(corpus):
        if entry.name == name:
            return entry
    typer.echo(f"Unknown corpus project: {name!r} in {corpus}", err=True)
    raise typer.Exit(code=2)


def _parse_shard(spec: str) -> tuple[int, int]:
    """Parse 'i/n' (e.g. '0/4'); validate 0 <= i < n. typer.Exit(2) on bad input."""
    try:
        index_str, total_str = spec.split("/", 1)
        index, total = int(index_str), int(total_str)
    except ValueError:
        typer.echo(f"--shard must be 'index/total' (e.g. 0/4), got {spec!r}", err=True)
        raise typer.Exit(code=2) from None
    if total < 1 or not 0 <= index < total:
        typer.echo(
            f"--shard out of range: {spec!r} (need 0 <= index < total, total >= 1)",
            err=True,
        )
        raise typer.Exit(code=2)
    return index, total


def _replace_readme_block(readme_text: str, block: str) -> str:
    """Swap the TYPEBENCH marker block while preserving hand-written prose."""
    start = readme_text.find(_README_BEGIN)
    end = readme_text.find(_README_END)
    if start != -1 and end != -1 and end > start:
        return readme_text[:start] + block + readme_text[end + len(_README_END) :]
    return readme_text.rstrip() + "\n\n## Latest results\n\n" + block + "\n"


def _empty_readme_block() -> str:
    """Placeholder block for when no official envelopes exist yet, so the publish
    flow (and CI) stays green from day one instead of failing on an empty store."""
    return (
        f"{_README_BEGIN}\n\n"
        "_No official results published yet. Run a suite and add the envelope "
        "to `data/official/`._\n\n"
        f"{_README_END}"
    )


@app.command()
def run(  # noqa: PLR0913, PLR0915 — many user-facing CLI options + linear arg-validation/manifest/run setup; one command by design
    tool: Annotated[str, typer.Option(help="Checker to run (e.g. stub).")],
    output: Annotated[Path, typer.Option(help="Where to write the results JSON.")],
    config: Annotated[
        Path | None,
        typer.Option("-c", "--config", help="typebench.toml (checker version source)."),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the effective plan; execute nothing.")
    ] = False,
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
            help=("Resource-pass repeats (peak memory variance). >=1; >=3 for official numbers.")
        ),
    ] = 3,
    measure: Annotated[
        bool,
        typer.Option(help="Run the cgroup memory/CPU pass (auto-skips if unavailable)."),
    ] = True,
    calibrate_baseline: Annotated[
        bool,
        typer.Option("--calibrate/--no-calibrate", help="Time the calibration workload."),
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
    cores: Annotated[
        int,
        typer.Option(
            help=(
                "CPU cores for the 'constrained' thread track. Default 1 = single-threaded; "
                "multithreading is opt-in — raise it (e.g. --cores 8) to pin the checker to N "
                "cores and let it use N worker threads, kept comparable across machines. The "
                "'all-cores' track ignores this and uses every core."
            )
        ),
    ] = 1,
) -> None:
    """Run ONE checker on ONE project and write a single RunResult (manual/single-cell)."""
    cores = _validate_cores(cores)
    if config is not None and not dry_run:
        typer.echo(
            "run -c is preview-only for now: pass --dry-run, or use `suite`/`compare` "
            "for version-resolved execution.",
            err=True,
        )
        raise typer.Exit(code=2)

    # `run` only reads -c in the preview path, so it deliberately has no cwd discovery seam.
    if dry_run:
        base = _load_config_or_exit(config) if config is not None else RunConfig(checkers=())
        spec = merge_tool_override(base.checkers, [tool])[0]
        state, version = checker_cache_status(spec, cache_root)
        resolved = f" (resolved {version})" if version else ""
        typer.echo(f"checker: {spec.checker_id()}  {state}{resolved}")
        typer.echo(f"project: {project or corpus_project or '(none)'}")
        typer.echo(f"thread_mode: {thread_mode.value}  cores: {cores}")
        raise typer.Exit(code=0)

    try:
        adapter = create_adapter(tool)
    except UnknownToolError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    _assert_output_writable(output)
    if mem_runs < 1:
        typer.echo("--mem-runs must be >= 1 (>= 3 recommended).", err=True)
        raise typer.Exit(code=2)
    if calib_runs < 1:
        typer.echo("--calib-runs must be >= 1.", err=True)
        raise typer.Exit(code=2)
    _validate_timing(runs, warmup, timeout)

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

    normalized_config = NormalizedConfig(
        src_roots=tuple(str(Path(s).resolve()) for s in src_roots),
        exclude_globs=(prepared.exclude_globs if prepared is not None else DEFAULT_EXCLUDES),
        python_version=python_version,
        python_platform=python_platform,
        # abspath, NOT resolve(): a venv's bin/python is a symlink to the base
        # interpreter; resolving it would walk out of the venv and break pyright's
        # venvPath/venv derivation. abspath makes it absolute without following the
        # symlink. (src_roots are dirs, so .resolve() above is fine for them.)
        venv_python=os.path.abspath(venv) if venv is not None else None,  # noqa: PTH100 - need non-symlink-following abspath; Path.resolve() follows symlinks
        cores=cores,
    )
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
        config=normalized_config,
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
def suite(  # noqa: PLR0913 — each parameter is a distinct user-facing CLI option
    output: Annotated[Path, typer.Option(help="Where to write the results envelope JSON.")],
    corpus: Annotated[
        Path | None,
        typer.Option(help="Path to suite.toml (else config `corpus`, else corpus/suite.toml)."),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option("-c", "--config", help="typebench.toml (else auto-discovered in cwd)."),
    ] = None,
    tool: Annotated[
        list[str] | None,
        typer.Option(help="Tools to run (repeatable). Default: all four real checkers."),
    ] = None,
    project: Annotated[
        list[str] | None,
        typer.Option(help="Project name(s); replaces file selection."),
    ] = None,
    bucket: Annotated[
        list[str] | None,
        typer.Option(help="Size bucket(s); replaces file selection."),
    ] = None,
    thread_mode: Annotated[
        list[ThreadMode] | None,
        typer.Option(help="Thread tracks (repeatable). Default: both."),
    ] = None,
    cores_list: Annotated[
        str | None,
        typer.Option("--cores-list", help="Comma-separated constrained cores sweep, e.g. 1,4,8."),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the effective plan; execute nothing.")
    ] = False,
    shard: Annotated[str, typer.Option(help="Shard selector 'index/total' (e.g. 0/4).")] = "0/1",
    runs: Annotated[
        int | None, typer.Option(help="hyperfine timed runs (else config [run].runs).")
    ] = None,
    warmup: Annotated[
        int | None, typer.Option(help="hyperfine warmup runs (else config [run].warmup).")
    ] = None,
    mem_runs: Annotated[
        int | None, typer.Option(help="Resource-pass repeats (>=1; else config [run].mem_runs).")
    ] = None,
    measure: Annotated[
        bool | None,
        typer.Option("--measure/--no-measure", help="Run the cgroup memory/CPU pass."),
    ] = None,
    calibrate_baseline: Annotated[
        bool | None,
        typer.Option("--calibrate/--no-calibrate", help="Time the calibration workload."),
    ] = None,
    calib_runs: Annotated[
        int | None,
        typer.Option(help="Calibration workload repeats (else config [run].calib_runs)."),
    ] = None,
    timeout: Annotated[
        float | None,
        typer.Option(help="Per-invocation timeout (else config [run].timeout)."),
    ] = None,
    cache_root: Annotated[
        Path, typer.Option(help="Where prepared clones/venvs are cached.")
    ] = DEFAULT_CACHE_ROOT,
    cores: Annotated[
        int | None,
        typer.Option(
            help=(
                "CPU cores for the 'constrained' thread track. Default 1 = single-threaded; "
                "multithreading is opt-in — raise it (e.g. --cores 8) to pin the checker to N "
                "cores and let it use N worker threads, kept comparable across machines. The "
                "'all-cores' track ignores this and uses every core."
            )
        ),
    ] = None,
) -> None:
    """Run the (project x tool x thread-mode) matrix and write a results envelope."""
    _assert_output_writable(output)
    config_path = config or discover_config(Path.cwd())
    base = (
        _load_config_or_exit(config_path)
        if config_path is not None
        else RunConfig(
            checkers=merge_tool_override(default_checker_specs(), tool)
            if tool is not None
            else default_checker_specs()
        )
    )
    cores_sweep = _parse_cores_list(cores_list) if cores_list else None
    # Scalar --cores back-compat: --cores-list wins; then explicit scalar; else file/default sweep.
    effective_cores = cores_sweep or ([_validate_cores(cores)] if cores is not None else None)
    try:
        run_config = merge_cli(
            base,
            tools=tool,
            projects=project,
            buckets=bucket,
            thread_modes=thread_mode,
            cores=effective_cores,
            run_overrides=RunCliOverrides(
                runs=runs,
                warmup=warmup,
                mem_runs=mem_runs,
                timeout=timeout,
                measure=measure,
                calibrate=calibrate_baseline,
                calib_runs=calib_runs,
            ),
        )
    except ValidationError as exc:
        typer.echo(_validation_message(exc), err=True)
        raise typer.Exit(code=2) from exc
    try:
        validate_specs(run_config.checkers)
    except UnknownToolError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    # strict posture is deferred this slice: adapters render STANDARD only, so a
    # strict run would stamp policy="strict" onto records that actually ran standard.
    # Reject it before any record is produced (covers --dry-run too).
    if run_config.policy is not Policy.STANDARD:
        typer.echo(
            f"policy '{run_config.policy.value}' is not supported yet (standard only).", err=True
        )
        raise typer.Exit(code=2)
    effective_corpus = resolve_corpus(run_config, corpus, Path("corpus/suite.toml"))
    corpus_entries = _load_suite_or_exit(effective_corpus)
    try:
        selected = resolve_selection(run_config, corpus_entries)
    except SelectionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    if dry_run:
        _print_dry_run(run_config, selected, effective_corpus)
        raise typer.Exit(code=0)

    shard_index, shard_total = _parse_shard(shard)
    envelope = run_suite(
        config=run_config,
        corpus=CorpusCache(effective_corpus, cache_root, projects=selected),
        resolver=UvCheckerResolver(cache_root),
        engine=LocalBenchEngine(),
        generated_at=datetime.now(UTC).isoformat(),
        shard_index=shard_index,
        shard_total=shard_total,
    )
    output.write_text(envelope.model_dump_json(indent=2))
    measured = sum(1 for r in envelope.runs if r.result_class.is_measured_success)
    if envelope.runs:
        typer.echo(render_terminal(envelope))
    typer.echo(f"suite {shard} -> {measured}/{len(envelope.runs)} measured -> {output}")


@app.command()
def compare(  # noqa: PLR0913 — distinct user-facing CLI options for one command
    output: Annotated[Path, typer.Option(help="Where to write the results envelope JSON.")],
    checker: Annotated[
        list[str],
        typer.Option(help="Checker spec name[@version[+label]] (repeatable; >=2 to compare)."),
    ],
    corpus: Annotated[
        Path | None,
        typer.Option(help="Path to suite.toml (else corpus/suite.toml)."),
    ] = None,
    project: Annotated[
        list[str] | None, typer.Option(help="Project name(s) to compare over.")
    ] = None,
    bucket: Annotated[
        list[str] | None, typer.Option(help="Size bucket(s) to compare over.")
    ] = None,
    cores: Annotated[int, typer.Option(help="Constrained core count for the compare run.")] = 1,
    thread_mode: Annotated[ThreadMode, typer.Option(help="Thread track.")] = (
        ThreadMode.CONSTRAINED
    ),
    runs: Annotated[int, typer.Option(help="hyperfine timed runs.")] = 10,
    warmup: Annotated[int, typer.Option(help="hyperfine warmup runs.")] = 3,
    mem_runs: Annotated[int, typer.Option(help="Resource-pass repeats (>=1).")] = 3,
    measure: Annotated[bool, typer.Option(help="Run the cgroup memory/CPU pass.")] = True,
    calibrate_baseline: Annotated[
        bool,
        typer.Option("--calibrate/--no-calibrate", help="Time the calibration workload."),
    ] = True,
    calib_runs: Annotated[int, typer.Option(help="Calibration workload repeats (>=1).")] = 5,
    timeout: Annotated[float, typer.Option(help="Per-invocation timeout (seconds).")] = 900.0,
    cache_root: Annotated[
        Path, typer.Option(help="Where prepared clones/venvs are cached.")
    ] = DEFAULT_CACHE_ROOT,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the effective plan; execute nothing.")
    ] = False,
) -> None:
    """Compare N checker specs over one selected corpus slice."""
    if len(checker) < _MIN_COMPARE_CHECKERS:
        typer.echo("compare needs >=2 --checker specs.", err=True)
        raise typer.Exit(code=2)
    _assert_output_writable(output)
    cores = _validate_cores(cores)

    try:
        specs = merge_tool_override(default_checker_specs(), checker)
        buckets = tuple(SizeBucket(value) for value in (bucket or ()))
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    try:
        validate_specs(specs)
    except UnknownToolError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    try:
        run_config = RunConfig(
            checkers=specs,
            projects=tuple(project or ()),
            buckets=buckets,
            thread_modes=(thread_mode,),
            cores=(cores,),
            runs=runs,
            warmup=warmup,
            timeout=timeout,
            mem_runs=mem_runs,
            measure=measure,
            calibrate=calibrate_baseline,
            calib_runs=calib_runs,
        )
    except ValidationError as exc:
        typer.echo(_validation_message(exc), err=True)
        raise typer.Exit(code=2) from exc
    effective_corpus = resolve_corpus(run_config, corpus, Path("corpus/suite.toml"))
    corpus_entries = _load_suite_or_exit(effective_corpus)
    try:
        selected = resolve_selection(run_config, corpus_entries)
    except SelectionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    if dry_run:
        _print_dry_run(run_config, selected, effective_corpus)
        raise typer.Exit(code=0)

    envelope = run_suite(
        config=run_config,
        corpus=CorpusCache(effective_corpus, cache_root, projects=selected),
        resolver=UvCheckerResolver(cache_root),
        engine=LocalBenchEngine(),
        generated_at=datetime.now(UTC).isoformat(),
    )
    output.write_text(envelope.model_dump_json(indent=2), encoding="utf-8")
    # Baseline = the FIRST spec's RESOLVED checker_id (records are keyed on the
    # resolved id, so a declared `mypy@latest` would match no row and zero the deltas).
    baseline_id = (
        envelope.resolved_checkers[0].checker_id
        if envelope.resolved_checkers
        else specs[0].checker_id()
    )
    typer.echo(render_compare(envelope, baseline=baseline_id))


def _parse_baseline(baseline: str) -> str | None:
    """'latest' (or empty) -> None (uv installs newest). '==X.Y' or 'X.Y' -> 'X.Y'."""
    token = baseline.strip()
    if token in ("", "latest"):
        return None
    return token[2:].strip() if token.startswith("==") else token


@app.command()
def ab(  # noqa: PLR0913 — distinct user-facing CLI options for one command
    checker: Annotated[str, typer.Option(help="Adapter id (pyrefly|ty|mypy|pyright).")],
    candidate_bin: Annotated[Path, typer.Option(help="Path to the PR-built checker binary.")],
    output: Annotated[Path, typer.Option(help="Where to write the A/B results envelope JSON.")],
    target: Annotated[
        list[Path], typer.Option(help="Project path to type-check (repeatable; >=1).")
    ],
    baseline: Annotated[
        str, typer.Option(help="Baseline uv spec: 'latest' or '==X.Y'.")
    ] = "latest",
    baseline_bin: Annotated[
        Path | None,
        typer.Option(help="Baseline binary path (offline; overrides --baseline / uv)."),
    ] = None,
    candidate_label: Annotated[str, typer.Option(help="Candidate row label.")] = "pr",
    baseline_label: Annotated[str, typer.Option(help="Baseline row label.")] = "release",
    runs: Annotated[int, typer.Option(help="hyperfine timed runs.")] = 5,
    warmup: Annotated[int, typer.Option(help="hyperfine warmup runs.")] = 1,
    timeout: Annotated[float, typer.Option(help="Per-invocation timeout (seconds).")] = 900.0,
    thread_mode: Annotated[ThreadMode, typer.Option(help="Thread track.")] = ThreadMode.ALL_CORES,
    cores: Annotated[int, typer.Option(help="Constrained-track core pin.")] = 1,
    measure: Annotated[
        bool, typer.Option("--measure/--no-measure", help="Run the cgroup memory pass.")
    ] = False,
    cache_root: Annotated[
        Path, typer.Option(help="Where the baseline uv checker cache lives.")
    ] = DEFAULT_CACHE_ROOT,
) -> None:
    """A/B-compare a candidate checker binary against a released baseline (wall-only)."""
    if not target:
        typer.echo("ab needs >=1 --target.", err=True)
        raise typer.Exit(code=2)
    _assert_output_writable(output)
    try:
        create_adapter(checker)
    except UnknownToolError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    if not candidate_bin.is_file():
        typer.echo(f"--candidate-bin is not a file: {candidate_bin}", err=True)
        raise typer.Exit(code=2)
    if baseline_bin is not None and not baseline_bin.is_file():
        typer.echo(f"--baseline-bin is not a file: {baseline_bin}", err=True)
        raise typer.Exit(code=2)

    if baseline_bin is not None:
        baseline_spec = CheckerSpec(tool=checker, label=baseline_label, source=Source.PATH)
        baseline_resolver: CheckerResolver = PathResolver(str(baseline_bin))
    else:
        if baseline.strip().startswith("path:"):
            typer.echo("--baseline does not accept 'path:'; use --baseline-bin instead.", err=True)
            raise typer.Exit(code=2)
        baseline_spec = CheckerSpec(
            tool=checker, version=_parse_baseline(baseline), label=baseline_label
        )
        baseline_resolver = UvCheckerResolver(cache_root)

    plan = MeasurementPlan(runs=runs, warmup=warmup, timeout_s=timeout, measure=measure)
    envelope = run_ab(
        checker=checker,
        candidate_bin=str(candidate_bin),
        candidate_label=candidate_label,
        baseline_spec=baseline_spec,
        targets=list(target),
        plan=plan,
        thread_mode=thread_mode,
        cores=cores,
        generated_at=datetime.now(UTC).isoformat(),
        baseline_resolver=baseline_resolver,
    )
    output.write_text(envelope.model_dump_json(indent=2))
    baseline_id = (
        envelope.resolved_checkers[1].checker_id
        if len(envelope.resolved_checkers) >= _AB_ARMS
        else None
    )
    typer.echo(render_ab(envelope, baseline=baseline_id))


def _load_results_history(results_dir: Path) -> list[ResultsEnvelope]:
    """Load every results/<date>.json envelope, oldest-first by generated_at.
    A single corrupt/half-written envelope fails loudly (exit 1) rather than
    silently emitting garbage; an empty dir is not an error and returns []."""
    history: list[ResultsEnvelope] = []
    for file in sorted(results_dir.glob("*.json")):
        try:
            history.append(ResultsEnvelope.model_validate_json(file.read_text()))
        except (ValidationError, ValueError, OSError) as exc:
            reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            typer.echo(f"Malformed results envelope {file}: {reason}", err=True)
            raise typer.Exit(code=1) from exc
    history.sort(key=lambda envelope: envelope.generated_at)
    return history


@app.command()
def render(
    results_dir: Annotated[Path, typer.Option(help="Directory of results/<date>.json envelopes.")],
    readme: Annotated[Path, typer.Option(help="README.md to update between the markers.")],
    trends: Annotated[Path, typer.Option(help="Where to write site/data/trends.json.")],
) -> None:
    """Regenerate the README table (latest envelope) and trends.json (full history)."""
    history = _load_results_history(results_dir)

    # An empty store is not an error: the publish workflow runs before the first
    # official envelope exists. Emit a placeholder README block + empty trends so
    # CI stays green and the site renders an honest "nothing yet" state.
    block = render_readme(history[-1]) if history else _empty_readme_block()
    readme.write_text(_replace_readme_block(readme.read_text(), block))

    trends.parent.mkdir(parents=True, exist_ok=True)
    trends.write_text(json.dumps(build_trends(history), indent=2))
    summary = f"{len(history)} envelopes" if history else "no official results yet"
    typer.echo(f"render -> {readme} (latest) + {trends} ({summary})")


@app.command()
def report(
    output: Annotated[
        Path, typer.Option(help="Where to write the self-contained HTML report.")
    ] = Path("typebench-report.html"),
    results_dir: Annotated[
        Path, typer.Option(help="Directory of results/<date>.json envelopes.")
    ] = Path("results"),
    site_dir: Annotated[
        Path, typer.Option(help="Site assets to inline (index.html, app.js, vendor/).")
    ] = Path("site"),
    open_browser: Annotated[
        bool, typer.Option("--open/--no-open", help="Open the report in your browser when done.")
    ] = False,
) -> None:
    """Build a self-contained local HTML report from your own results history.

    Unlike `render`, this touches neither README.md nor the published site — it
    folds the site assets + your local trends into one portable file you can open
    with no server. Use it to view your own runs without publishing anything."""
    history = _load_results_history(results_dir)
    index_html = site_dir / "index.html"
    app_js = site_dir / "app.js"
    chart_js = site_dir / "vendor" / "chart.umd.min.js"
    missing = [str(asset) for asset in (index_html, app_js, chart_js) if not asset.exists()]
    if missing:
        typer.echo(f"Missing site asset(s): {', '.join(missing)}", err=True)
        raise typer.Exit(code=2)

    html = build_report_html(
        index_html.read_text(),
        app_js=app_js.read_text(),
        chart_js=chart_js.read_text(),
        trends=build_trends(history),
    )
    output.write_text(html)
    typer.echo(f"report -> {output} ({len(history)} envelopes)")
    if open_browser:
        webbrowser.open(output.resolve().as_uri())


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
    tools = tool or list(_DEFAULT_TOOLS)
    specs = tuple(CheckerSpec(tool=name) for name in tools)
    try:
        validate_specs(specs)
    except UnknownToolError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    checkers = tuple(CheckerHandle(spec=spec, adapter=create_adapter(spec.tool)) for spec in specs)
    _assert_output_writable(output)
    try:
        prepared = prepare_project(entry, cache_root)
    except PrepareError as exc:
        typer.echo(f"preflight: prepare failed for {project!r}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    report = preflight_project(prepared, list(checkers), timeout=timeout)
    output.write_text(report.model_dump_json(indent=2))
    status = "ready" if report.ready else "NOT READY"
    typer.echo(f"preflight {project} -> {status} ({report.canonical_files} files) -> {output}")
    if not report.ready:
        raise typer.Exit(code=1)


@app.command()
def doctor(
    check: Annotated[
        bool,
        typer.Option(help="Exit nonzero if a REQUIRED-tier tool is missing (CI gate)."),
    ] = False,
    config: Annotated[
        Path | None,
        typer.Option("-c", "--config", help="typebench.toml to validate configured checkers."),
    ] = None,
) -> None:
    """Report external-tool availability, versions, roles, and consequence-if-absent.

    Default exits 0 (pure report, matching the graceful-degradation design). With
    --check, a missing REQUIRED tool (uv/git) exits 1; PER_TOOL/OPTIONAL misses
    only warn.
    """
    checks = run_doctor()
    console = Console()
    # One table per tier, in escalating-importance order, so a REQUIRED miss
    # (breaks the run) is never buried among OPTIONAL ones (degrade gracefully).
    for tier in (Tier.REQUIRED, Tier.PER_TOOL, Tier.OPTIONAL):
        rows = [c for c in checks if c.tier is tier]
        if not rows:
            continue
        table = Table(title=str(tier), title_justify="left", box=box.SIMPLE, title_style="bold")
        table.add_column("tool", no_wrap=True)
        table.add_column("status", no_wrap=True)
        table.add_column("version")
        table.add_column("role")
        for c in rows:
            if not c.present:
                status = "[red]✗ MISSING[/red]"
            elif c.healthy:
                status = "[green]✓ OK[/green]"
            else:
                status = "[yellow]⚠ DEGRADED[/yellow]"
            table.add_row(c.name, status, c.version or "—", c.role)
        console.print(table)

    n_healthy = sum(1 for c in checks if c.healthy)
    n_degraded = sum(1 for c in checks if c.present and not c.healthy)
    n_missing = sum(1 for c in checks if not c.present)
    console.print(f"{n_healthy} healthy · {n_degraded} degraded · {n_missing} missing")

    # Remediation: a doctor that says MISSING but not how to fix it is half a tool.
    # Carry the consequence here too, since the per-tier tables stay uncluttered.
    unhealthy = [c for c in checks if not c.healthy]
    if unhealthy:
        typer.echo("\nto fix:")
        for c in unhealthy:
            typer.echo(f"  {c.name:<12}  {c.install_hint}   (else: {c.if_absent})")

    unhealthy_required = [c.name for c in checks if c.tier is Tier.REQUIRED and not c.healthy]
    if unhealthy_required:
        typer.echo(f"\nmissing/unhealthy required: {', '.join(unhealthy_required)}", err=True)
    if config is not None:
        run_config = _load_config_or_exit(config)
        typer.echo("\nconfigured checkers:")
        for spec in run_config.checkers:
            state, version = checker_cache_status(spec, DEFAULT_CACHE_ROOT)
            resolved = f" (resolved {version})" if version else ""
            typer.echo(f"  {spec.checker_id():<24}  {state}{resolved}")
    if check and unhealthy_required:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
