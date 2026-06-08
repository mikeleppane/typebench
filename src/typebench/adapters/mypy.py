"""mypy adapter (spec §4, §6). Text-summary parse (mypy JSON has no files count
and is empty on clean). See docs/superpowers/research/2026-06-08-checker-cli-facts.md."""

from __future__ import annotations

import re
import subprocess
from typing import TYPE_CHECKING

from typebench.adapters.base import ParallelismCap
from typebench.models import ResultClass, ThreadMode
from typebench.wrapper import universal_failure_prefix

if TYPE_CHECKING:
    from pathlib import Path

    from typebench.normalized_config import NormalizedConfig
    from typebench.wrapper import RawRun

# Text summaries (research doc). errors+files, and the clean form.
_FOUND_RE = re.compile(r"Found (\d+) errors? in \d+ files? \(checked (\d+) source files?\)")
_CLEAN_RE = re.compile(r"Success: no issues found in (\d+) source files?")

# Exit codes (research doc): 0 clean, 1 diagnostics, 2 overloaded (usage/crash).
_EXIT_DIAGNOSTICS = 1
_EXIT_OVERLOADED = 2


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
            "--no-incremental",
            "--cache-dir=/dev/null",  # write no cache (cold single-shot)
            "--exclude",
            _globs_to_exclude_regex(config.exclude_globs),
        ]
        if config.venv_python is not None:
            # Resolve installed third-party from the project venv (else mypy uses
            # its own interpreter env). First-party-only stays via follow-imports.
            argv += ["--python-executable", config.venv_python]
        argv += list(config.src_roots)
        return (argv, {})

    def parallelism_cap(self, thread_mode: ThreadMode) -> ParallelismCap:
        # mypy is single-process by default (--num-workers is experimental and can
        # change diagnostics). We never enable workers, so the cap is effectively
        # hard via single-process; affinity (Plan 4) pins the core.
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
        return None  # --cache-dir=/dev/null + --no-incremental -> stateless

    def prepare_command(self, project: str) -> str | None:
        return None
