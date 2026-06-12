"""pyright adapter.

Node-based checker with JSON output and explicit project config generation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path  # runtime: used to derive venvPath (not annotation-only)
from typing import TYPE_CHECKING

from typebench.adapters._support import confirm_clean, probe_version
from typebench.adapters.base import ParallelismCap, coerce_count
from typebench.contracts.policy import PRESETS, CheckerPosture, Policy
from typebench.contracts.taxonomy import ResultClass, ThreadMode, is_constrained
from typebench.engine.proc import SYSTEM_HOST
from typebench.engine.wrapper import classify_with_map

if TYPE_CHECKING:
    from typebench.contracts.config import NormalizedConfig
    from typebench.contracts.proc import ProcessHost, RawRun

# Exit codes: 0 clean, 1 errors, 2 fatal, 3 config, 4 bad-CLI/missing-path.
_EXIT_MAP: dict[int, ResultClass] = {
    0: ResultClass.CLEAN,
    1: ResultClass.DIAGNOSTICS,
    2: ResultClass.FAILED_CRASH,
    3: ResultClass.FAILED_ENV,
    4: ResultClass.FAILED_ENV,
}

# Canonical lowercase platform -> pyright's capitalized spelling.
_PYRIGHT_PLATFORM: dict[str, str] = {
    "linux": "Linux",
    "darwin": "Darwin",
    "win32": "Windows",
    "windows": "Windows",
}


def _relative_to(target: str, base: Path) -> str:
    """Render `target` (an absolute src_root) as a path relative to `base` (the
    workdir holding pyrightconfig.json). pyright drops absolute `include` entries
    ("not relative" -> 0 files -> false-clean). src_roots live elsewhere on disk,
    so this needs the `..` walk-up that Path.relative_to / PurePath lack; os.path
    .relpath is the only stdlib path op that provides it (no PTH equivalent)."""
    return os.path.relpath(target, base)


def _node_version(host: ProcessHost) -> str:
    """Node version (pyright is a Node app; `pyright --version` omits it). No-raise."""
    out = host.run(["node", "--version"], timeout=10)
    if out.env_error or out.timed_out:
        return "unknown"
    return out.stdout.strip() or "unknown"


def _posture_config(posture: CheckerPosture) -> dict[str, object]:
    """Render pyright config keys for the equalized checker posture."""
    if posture.strict:
        msg = "strict posture not yet implemented for pyright"
        raise NotImplementedError(msg)
    return {
        "typeCheckingMode": "standard",
        "useLibraryCodeForTypes": posture.resolve_deps_report_first_party,
    }


class PyrightAdapter:
    name = "pyright"
    install_source = "npm + Node"

    def __init__(self, host: ProcessHost = SYSTEM_HOST) -> None:
        self._host = host

    def version(self, binary: str | None = None) -> str:
        return probe_version([binary or "pyright", "--version"], host=self._host)

    def install(self) -> str:
        # `pyright --version` omits Node; record both for reproducibility. Node
        # pinning is an environment concern.
        return f"{self.version()} (node {_node_version(self._host)})"

    def _platform(self, config: NormalizedConfig) -> str:
        return _PYRIGHT_PLATFORM.get(
            config.python_platform.lower(), config.python_platform.capitalize()
        )

    def command(
        self,
        project: str,
        config: NormalizedConfig,
        thread_mode: ThreadMode,
        workdir: Path,
        binary: str | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        platform = self._platform(config)
        # pyright resolves config `include` paths RELATIVE to the config-file dir
        # and silently DROPS absolute entries ("not relative" -> 0 files analyzed,
        # a false-clean). The config lives in `workdir`, so render each absolute
        # src_root as a workdir-relative path (pyright docs: Path Handling).
        includes = [_relative_to(root, workdir) for root in config.src_roots]
        # pyright excludes are relative to the project root (workdir). Generic globs
        # like `**/tests/**` do NOT match under a `../../...` include tree — pyright
        # resolves them only within direct child paths. Scope each exclude glob under
        # each include so the exclusion contract holds regardless of include depth.
        excludes: list[str] = []
        for inc in includes:
            for glob in config.exclude_globs:
                excludes.append(f"{inc}/{glob}")
        pyright_config: dict[str, object] = {
            "include": includes,
            "exclude": excludes,
            **_posture_config(PRESETS[Policy.STANDARD]),
            "pythonVersion": config.python_version,
            "pythonPlatform": platform,  # threaded from normalized config, not hardcoded
        }
        if config.venv_python is not None:
            # pyright wants venvPath = dir CONTAINING the venv, venv = its name.
            # config.venv_python is <venv>/bin/python -> parent.parent is <venv>.
            # Derive LEXICALLY (parent.parent), never `.resolve()`: a real venv's
            # bin/python is a SYMLINK to the base interpreter, so resolving it walks
            # OUT of the venv (/tmp/v/bin/python -> /usr/bin/python3.12 -> venvPath=/,
            # venv=usr) -> deps unresolved -> spurious reportMissingImports inflate
            # diagnostics (non-neutral). The CLI passes an absolute path already.
            venv_dir = Path(config.venv_python).parent.parent
            pyright_config["venvPath"] = str(venv_dir.parent)
            pyright_config["venv"] = venv_dir.name
        (workdir / "pyrightconfig.json").write_text(json.dumps(pyright_config, indent=2))

        argv = [
            binary or "pyright",
            "--project",
            str(workdir),
            "--outputjson",
            "--pythonversion",
            config.python_version,
            "--pythonplatform",
            platform,
        ]
        if not is_constrained(thread_mode):
            argv.append("--threads")  # bare = auto-parallelism by logical CPUs (pyright docs)
        # CONSTRAINED: omit --threads (default single main thread); affinity is uniform.
        return (argv, {})

    def parallelism_cap(
        self, thread_mode: ThreadMode, cores: int, binary: str | None = None
    ) -> ParallelismCap:
        # pyright stays single-main-thread in CONSTRAINED regardless of cores;
        # affinity makes the cap hard. cores-independent mechanism.
        return ParallelismCap(mechanism="cpu-affinity + single-thread", hard_cap=False)

    def parse(self, stdout: str, stderr: str, exit_code: int) -> tuple[int | None, int | None]:
        try:
            payload = json.loads(stdout)
        except ValueError:
            return (None, None)
        if not isinstance(payload, dict):
            return (None, None)
        summary = payload.get("summary")
        if not isinstance(summary, dict):
            return (None, None)
        # coerce_count (base.py) rejects JSON bools/non-ints -> no garbage counts.
        return (
            coerce_count(summary.get("errorCount")),
            coerce_count(summary.get("filesAnalyzed")),
        )

    def classify(self, raw: RawRun) -> ResultClass:
        result = classify_with_map(raw, _EXIT_MAP)
        # Parse-sanity: a CLEAN verdict is only honest if we can
        # confirm a positive file count. Promote to failed{env} when files is 0
        # (mis-scoped include) OR None (exit 0 but --outputjson was unparsable /
        # dropped summary.filesAnalyzed). Recording an unverifiable clean would let
        # a false-clean enter the data set -> record-honesty violation.
        if result is ResultClass.CLEAN:
            _diags, files = self.parse(raw.stdout, raw.stderr, raw.exit_code)
            return confirm_clean(files, tolerate_unknown=False)
        return result

    def clear_cache(self, project: str) -> None:
        return None  # stateless single-shot

    def prepare_command(self, project: str) -> str | None:
        return None
