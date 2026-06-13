"""pyrefly adapter.

Project-mode check driven by a generated pyrefly.toml (preset="default"; the
loose-file fallback to basic silences errors). JSON stdout errors[] plus
--summary=full stderr module count. Exit 1 is overloaded (diagnostics vs fatal
config). pyrefly is treated identically to every other entrant: no favorable
defaults.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from typebench.adapters._support import confirm_clean, probe_version
from typebench.adapters.base import ParallelismCap, coerce_count
from typebench.contracts.policy import PRESETS, CheckerPosture, Policy
from typebench.contracts.taxonomy import ResultClass, ThreadMode, is_constrained
from typebench.engine.proc import SYSTEM_HOST
from typebench.engine.wrapper import universal_failure_prefix

if TYPE_CHECKING:
    from pathlib import Path

    from typebench.contracts.config import NormalizedConfig
    from typebench.contracts.proc import ProcessHost, RawRun

# Match the digit group WITH thousands separators: pyrefly's --summary=full prints
# "8,792 modules" once a project exceeds 999 files, so a comma-blind \d+ would
# capture only the trailing "792". Commas are stripped before int() in _files.
_MODULES_RE = re.compile(r"([\d,]+) modules?")  # singular "1 module" is real output

# Exit codes where the meaning is unambiguous (exit 1 is overloaded — handled in classify).
_EXIT_ENV = 3
_EXIT_CRASH = 101


def _toml_str_list(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(json.dumps(v) for v in values) + "]"


def _posture_lines(posture: CheckerPosture) -> list[str]:
    """Render pyrefly config lines for the equalized checker posture."""
    if posture.strict:
        msg = "strict posture not yet implemented for pyrefly"
        raise NotImplementedError(msg)
    unannotated = "true" if posture.analyze_untyped_defs else "false"
    return [
        'preset = "default"',
        f"check-unannotated-defs = {unannotated}",
    ]


class PyreflyAdapter:
    name = "pyrefly"
    install_source = "PyPI wheel (Rust)"

    def __init__(self, host: ProcessHost = SYSTEM_HOST) -> None:
        self._host = host

    def version(self, binary: str | None = None) -> str:
        return probe_version([binary or "pyrefly", "--version"], host=self._host)

    def install(self) -> str:
        return self.version()

    def command(
        self,
        project: str,
        config: NormalizedConfig,
        thread_mode: ThreadMode,
        workdir: Path,
        binary: str | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        # kebab-case keys. preset="default" is the stock-neutral
        # policy — NOT basic (under-reports -> false-clean) and NOT strict
        # (over-reports). Explicit project-includes prevents the loose-file
        # fallback to basic. json.dumps quotes string values safely.
        # pyrefly project-excludes needs absolute globs (relative **/dir/** patterns
        # are silently ignored in pyrefly 1.0.0). Scope each glob under each src_root.
        excludes: tuple[str, ...] = tuple(
            f"{root}/{glob}" for root in config.src_roots for glob in config.exclude_globs
        )
        # search-path = src_roots: the generated config lives in the run-scoped
        # workdir (/tmp/...), so pyrefly would otherwise infer its import root from
        # THAT temp dir and fail to resolve first-party imports in a src-layout
        # project -> spurious `missing-import` diagnostics + skewed timing (a
        # neutrality leak the other tools don't have). Pinning search-path to the
        # source tree makes first-party imports resolve from where the code lives.
        posture_lines = _posture_lines(PRESETS[Policy.STANDARD])
        preset_line, unannotated_line = posture_lines[0], posture_lines[1]
        lines = [
            preset_line,
            f"project-includes = {_toml_str_list(config.src_roots)}",
            f"search-path = {_toml_str_list(config.src_roots)}",
            f"project-excludes = {_toml_str_list(excludes)}",
            f'python-version = "{config.python_version}"',
            f'python-platform = "{config.python_platform}"',
            unannotated_line,
            'infer-return-types = "checked"',
        ]
        if config.venv_python is not None:
            lines.append(f"python-interpreter-path = {json.dumps(config.venv_python)}")
        config_path = workdir / "pyrefly.toml"
        config_path.write_text("\n".join(lines) + "\n")

        argv = [
            binary or "pyrefly",
            "check",
            "--config",
            str(config_path),  # short-circuits discovery (suppress project cfg)
            "--output-format",
            "json",
            "--summary=full",  # emits "N modules" on stderr (the files source)
        ]
        if is_constrained(thread_mode):
            argv += ["--threads", str(config.cores)]  # HARD cap (rayon pool = N)
        return (argv, {})

    def parallelism_cap(
        self, thread_mode: ThreadMode, cores: int, binary: str | None = None
    ) -> ParallelismCap:
        # --threads N is a HARD cap (rayon num_threads(N)); RAYON_NUM_THREADS is
        # NOT honored. Always set in CONSTRAINED (incl. cores=1), so the mechanism
        # is cores-independent. Affinity pins the cores on top.
        return ParallelismCap(mechanism="--threads (rayon) + cpu-affinity", hard_cap=True)

    def _files(self, stderr: str) -> int | None:
        m = _MODULES_RE.search(stderr)
        return coerce_count(int(m.group(1).replace(",", ""))) if m is not None else None

    def parse(self, stdout: str, stderr: str, exit_code: int) -> tuple[int | None, int | None]:
        files = self._files(stderr)
        try:
            payload = json.loads(stdout)
        except ValueError:
            return (None, files)
        if not isinstance(payload, dict):
            return (None, files)
        errors = payload.get("errors")
        if not isinstance(errors, list):
            return (None, files)
        # The array includes non-error directives; count severity == "error" only.
        count = sum(1 for e in errors if isinstance(e, dict) and e.get("severity") == "error")
        return (count, files)

    def classify(self, raw: RawRun) -> ResultClass:
        prefix = universal_failure_prefix(raw)
        if prefix is not None:
            return prefix
        code = raw.exit_code
        diags, files = self.parse(raw.stdout, raw.stderr, raw.exit_code)
        if code == 0:
            # Clean only if confirmed by a positive module count; 0 = mis-scoped
            # includes (false-clean). files None tolerated (stderr-scraped).
            return confirm_clean(files, tolerate_unknown=True)
        if code == 1:
            # Overloaded: parseable JSON with >=1 error -> diagnostics; otherwise a
            # fatal config/IO error reported via anyhow -> failed{env}.
            return (
                ResultClass.DIAGNOSTICS
                if (diags is not None and diags > 0)
                else ResultClass.FAILED_ENV
            )
        if code == _EXIT_ENV:
            return ResultClass.FAILED_ENV
        return ResultClass.FAILED_CRASH

    def clear_cache(self, project: str) -> None:
        return None  # stateless `check`

    def prepare_command(self, project: str) -> str | None:
        return None
