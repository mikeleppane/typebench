"""pyrefly adapter (spec §4, §6). Project-mode check driven by a generated
pyrefly.toml (preset="default" — the loose-file fallback to basic silences
errors). JSON stdout errors[] + --summary=full stderr module count. Exit 1 is
overloaded (diagnostics vs fatal config). pyrefly is treated identically to every
other entrant — no favorable defaults. See the research doc (pyrefly)."""

from __future__ import annotations

import json
import re
import subprocess
from typing import TYPE_CHECKING

from typebench.adapters.base import ParallelismCap, coerce_count
from typebench.models import ResultClass, ThreadMode
from typebench.wrapper import universal_failure_prefix

if TYPE_CHECKING:
    from pathlib import Path

    from typebench.normalized_config import NormalizedConfig
    from typebench.wrapper import RawRun

_MODULES_RE = re.compile(r"(\d+) modules?")  # singular "1 module" is real output

# Exit codes where the meaning is unambiguous (exit 1 is overloaded — handled in classify).
_EXIT_ENV = 3
_EXIT_CRASH = 101


def _toml_str_list(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(json.dumps(v) for v in values) + "]"


class PyreflyAdapter:
    name = "pyrefly"
    install_source = "PyPI wheel (Rust)"

    def version(self) -> str:
        try:
            out = subprocess.run(
                ["pyrefly", "--version"], capture_output=True, text=True, check=False
            )
        except OSError:
            return "unknown"
        return out.stdout.strip() or out.stderr.strip() or "unknown"

    def install(self) -> str:
        return self.version()

    def command(
        self,
        project: str,
        config: NormalizedConfig,
        thread_mode: ThreadMode,
        workdir: Path,
    ) -> tuple[list[str], dict[str, str]]:
        # kebab-case keys (research doc). preset="default" is the stock-neutral
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
        lines = [
            'preset = "default"',
            f"project-includes = {_toml_str_list(config.src_roots)}",
            f"search-path = {_toml_str_list(config.src_roots)}",
            f"project-excludes = {_toml_str_list(excludes)}",
            f'python-version = "{config.python_version}"',
            f'python-platform = "{config.python_platform}"',
            "check-unannotated-defs = true",
            'infer-return-types = "checked"',
        ]
        if config.venv_python is not None:
            lines.append(f"python-interpreter-path = {json.dumps(config.venv_python)}")
        config_path = workdir / "pyrefly.toml"
        config_path.write_text("\n".join(lines) + "\n")

        argv = [
            "pyrefly",
            "check",
            "--config",
            str(config_path),  # short-circuits discovery (suppress project cfg)
            "--output-format",
            "json",
            "--summary=full",  # emits "N modules" on stderr (the files source)
        ]
        if thread_mode is ThreadMode.ONE_CORE:
            argv += ["--threads", "1"]  # HARD cap (rayon pool = 1)
        return (argv, {})

    def parallelism_cap(self, thread_mode: ThreadMode) -> ParallelismCap:
        # --threads 1 is a HARD cap (rayon num_threads(1)); RAYON_NUM_THREADS is
        # NOT honored. Affinity (Plan 4) pins the core on top.
        return ParallelismCap(mechanism="--threads (rayon) + cpu-affinity", hard_cap=True)

    def _files(self, stderr: str) -> int | None:
        m = _MODULES_RE.search(stderr)
        return coerce_count(int(m.group(1))) if m is not None else None

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
            return ResultClass.FAILED_ENV if files == 0 else ResultClass.CLEAN
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
