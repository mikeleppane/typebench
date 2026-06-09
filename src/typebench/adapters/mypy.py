"""mypy adapter (spec §4, §6). Text-summary parse (mypy JSON has no files count
and is empty on clean). See docs/superpowers/research/2026-06-08-checker-cli-facts.md."""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path  # runtime: used to build the parallel cache dir path
from typing import TYPE_CHECKING

from typebench.adapters.base import ParallelismCap
from typebench.models import ResultClass, ThreadMode
from typebench.wrapper import universal_failure_prefix

if TYPE_CHECKING:
    from typebench.normalized_config import NormalizedConfig
    from typebench.wrapper import RawRun

# Text summaries (research doc). errors+files, and the clean form.
_FOUND_RE = re.compile(r"Found (\d+) errors? in \d+ files? \(checked (\d+) source files?\)")
_CLEAN_RE = re.compile(r"Success: no issues found in (\d+) source files?")

# Exit codes (research doc): 0 clean, 1 diagnostics, 2 overloaded (usage/crash).
_EXIT_DIAGNOSTICS = 1
_EXIT_OVERLOADED = 2

# Parallel type-checking landed in mypy 2.0 (--num-workers N / -nN; 0 disables).
# It needs --local-partial-types (default since 2.0) and auto-enables the native
# parser; it composes with --no-incremental + --cache-dir=/dev/null (mypy docs,
# verified via context7). Below 2.0 the flag does not exist, so we MUST version-
# guard or a <2.0 binary in the gate would hard-fail on an unknown option.
_MIN_PARALLEL_MAJOR = 2
_VERSION_RE = re.compile(r"\bmypy\s+(\d+)\.")  # "mypy 2.1.0 (compiled: yes)"

_CACHE_PREFIX = "typebench-mypy-cache"


def _major_version(version_str: str) -> int | None:
    m = _VERSION_RE.search(version_str)
    return int(m.group(1)) if m is not None else None


def _cache_dir(project: str) -> str:
    """Deterministic per-project cache dir for mypy's parallel mode. Keyed by a
    hash of the project so command()/clear_cache()/prepare_command() agree on the
    path WITHOUT threading the run-scoped workdir through the Adapter protocol
    (they only share `project`). Hashed so an arbitrary project name or path is
    filesystem-safe. Assumes the harness never runs two cells for the SAME project
    concurrently (it is sequential today); the wipe-before-every-run keeps it cold."""
    digest = hashlib.sha1(project.encode(), usedforsecurity=False).hexdigest()[:16]
    return str(Path(tempfile.gettempdir()) / f"{_CACHE_PREFIX}-{digest}")


def _globs_to_exclude_regex(globs: tuple[str, ...]) -> str:
    """mypy --exclude takes a REGEX (matched against discovered paths), not globs.
    Render the §6 exclude globs as an alternation of their dir-name segments, e.g.
    '**/tests/**' -> 'tests'. Anchored on path separators so it matches a segment,
    not a substring."""
    names = sorted({g.strip("*/ ").split("/")[0] for g in globs if g.strip("*/ ")})
    if not names:
        return r"(?!)"  # match nothing
    return r"(^|/)(" + "|".join(re.escape(n) for n in names) + r")(/|$)"


class MypyAdapter:
    name = "mypy"
    install_source = "PyPI wheel (mypyc-compiled)"

    def version(self) -> str:
        # No-raise (runs during RunResult assembly even on the env-failure path).
        try:
            out = subprocess.run(["mypy", "--version"], capture_output=True, text=True, check=False)
        except OSError:
            return "unknown"
        return out.stdout.strip() or out.stderr.strip() or "unknown"

    def install(self) -> str:
        # Records the version string, which carries "(compiled: yes)" for the §9
        # lock manifest (mypyc-compiled wheels are the default distribution).
        return self.version()

    def _supports_parallel(self) -> bool:
        """True when this mypy is >= 2.0 (the version that added --num-workers).
        Parses the resolved --version; an unparsable/unknown version reads as
        no-support so we never pass an unknown flag to an older binary."""
        major = _major_version(self.version())
        return major is not None and major >= _MIN_PARALLEL_MAJOR

    def _num_workers(self, cores: int, thread_mode: ThreadMode) -> int:
        """Worker count for --num-workers. CONSTRAINED uses the configured core
        count (taskset pins those same cores on top); ALL_CORES uses every logical
        CPU. Returns the raw count; the caller only emits the flag when it is > 1
        (1 == single-process == mypy's default, so passing -n1 would only add
        parallel-runtime overhead for no benefit)."""
        if thread_mode is ThreadMode.CONSTRAINED:
            return cores
        return os.cpu_count() or 1

    def command(
        self,
        project: str,
        config: NormalizedConfig,
        thread_mode: ThreadMode,
        workdir: Path,
    ) -> tuple[list[str], dict[str, str]]:
        argv = [
            "mypy",
            "--python-version",
            config.python_version,
            "--platform",
            config.python_platform,
            "--check-untyped-defs",  # analyze all bodies (§6)
            "--follow-imports=silent",  # resolve dep types, report first-party only
            "--config-file=",  # empty value suppresses the project's own config (§6)
        ]
        # Parallel pass (mypy >= 2.0). CONSTRAINED -> config.cores, ALL_CORES ->
        # every logical CPU. Only parallelize when workers > 1: -n1 is single-
        # process like the default but pays parallel-runtime overhead, and the
        # cores=1 default must stay byte-identical to the pre-parallel command.
        workers = self._num_workers(config.cores, thread_mode) if self._supports_parallel() else 1
        if workers > 1:
            # mypy parallel mode REQUIRES the cache ("Cache must be enabled in
            # parallel mode") — incompatible with --no-incremental / --cache-dir=
            # /dev/null. Point it at a FRESH per-run cache dir instead: clear_cache
            # + the hyperfine --prepare empty it before every run, so each measured
            # run still starts COLD (empty cache -> full recompute). The cache-write
            # cost is intrinsic to mypy's parallel mode and fairly counted (§5.2/§5.3).
            argv += ["--cache-dir", _cache_dir(project), "--num-workers", str(workers)]
        else:
            # Cold single-shot: write no cache at all (the default headline path).
            argv += ["--no-incremental", "--cache-dir=/dev/null"]
        argv += ["--exclude", _globs_to_exclude_regex(config.exclude_globs)]
        if config.venv_python is not None:
            # Resolve installed third-party from the project venv (else mypy uses
            # its own interpreter env). First-party-only stays via follow-imports.
            argv += ["--python-executable", config.venv_python]
        argv += list(config.src_roots)
        return (argv, {})

    def parallelism_cap(self, thread_mode: ThreadMode, cores: int) -> ParallelismCap:
        # Report the mechanism actually applied for this (thread_mode, cores) — the
        # honesty contract. command() emits --num-workers ONLY when workers > 1, so
        # mypy >= 2.0 with workers > 1 is the "--num-workers" hard cap; otherwise
        # (cores=1, or pre-2.0) it runs single-process — also a hard cap. Mirrors
        # the exact predicate in command() so the recorded mechanism never claims a
        # worker cap that was not passed (e.g. the default constrained N=1 headline).
        if self._supports_parallel() and self._num_workers(cores, thread_mode) > 1:
            return ParallelismCap(mechanism="--num-workers + cpu-affinity", hard_cap=True)
        return ParallelismCap(mechanism="single-process + cpu-affinity", hard_cap=True)

    def parse(self, stdout: str, stderr: str, exit_code: int) -> tuple[int | None, int | None]:
        m = _FOUND_RE.search(stdout)
        if m is not None:
            return (int(m.group(1)), int(m.group(2)))
        c = _CLEAN_RE.search(stdout)
        if c is not None:
            return (0, int(c.group(1)))
        return (None, None)

    def classify(self, raw: RawRun) -> ResultClass:
        prefix = universal_failure_prefix(raw)
        if prefix is not None:
            return prefix
        code = raw.exit_code
        if code == 0:
            _diags, files = self.parse(raw.stdout, raw.stderr, raw.exit_code)
            # exit 0 must come with a positive checked-files count, else the target
            # was mis-scoped (false clean). mypy text always carries the count on
            # success, so None here also means broken output -> failed{env}.
            return ResultClass.CLEAN if files else ResultClass.FAILED_ENV
        if code == _EXIT_DIAGNOSTICS:
            return ResultClass.DIAGNOSTICS
        if code == _EXIT_OVERLOADED:
            # Overloaded: a mypy crash also exits 2 but prints INTERNAL ERROR;
            # everything else at exit 2 is a usage/config/unreadable-target env error.
            blob = raw.stdout + raw.stderr
            return ResultClass.FAILED_CRASH if "INTERNAL ERROR" in blob else ResultClass.FAILED_ENV
        return ResultClass.FAILED_CRASH

    def clear_cache(self, project: str) -> None:
        # Single-process path is stateless (--cache-dir=/dev/null + --no-incremental).
        # Parallel path writes a real cache; wipe it so each cold repeat starts empty
        # (§5.2). Idempotent + harmless when the cache was never created.
        shutil.rmtree(_cache_dir(project), ignore_errors=True)

    def prepare_command(self, project: str) -> str | None:
        # hyperfine --prepare: wipe the parallel cache before EVERY timed run so each
        # starts cold (§5.2). hyperfine does not time --prepare, so zero measurement
        # impact; a no-op rm for the single-process path (cache dir never exists).
        return f"rm -rf {shlex.quote(_cache_dir(project))}"
