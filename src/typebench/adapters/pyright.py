"""pyright adapter (spec §4, §6). Node-based; reference adapter for Plan 2.
See docs/superpowers/research/2026-06-08-checker-cli-facts.md (pyright)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path  # runtime: used to derive venvPath (not annotation-only)
from typing import TYPE_CHECKING

from typebench.adapters.base import ParallelismCap, coerce_count
from typebench.models import ResultClass, ThreadMode
from typebench.wrapper import classify_with_map

if TYPE_CHECKING:
    from typebench.normalized_config import NormalizedConfig
    from typebench.wrapper import RawRun

# Exit codes (research doc): 0 clean, 1 errors, 2 fatal, 3 config, 4 bad-CLI/missing-path.
_EXIT_MAP: dict[int, ResultClass] = {
    0: ResultClass.CLEAN,
    1: ResultClass.DIAGNOSTICS,
    2: ResultClass.FAILED_CRASH,
    3: ResultClass.FAILED_ENV,
    4: ResultClass.FAILED_ENV,
}

# §6 platform (canonical lowercase) -> pyright's capitalized spelling.
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


def _node_version() -> str:
    """Node version (pyright is a Node app; `pyright --version` omits it). No-raise."""
    try:
        out = subprocess.run(["node", "--version"], capture_output=True, text=True, check=False)
    except OSError:
        return "unknown"
    return out.stdout.strip() or "unknown"


class PyrightAdapter:
    name = "pyright"

    def version(self) -> str:
        # No-raise: called during RunResult assembly even on the env-failure path,
        # so a missing binary must NOT crash and drop the record (repo invariant).
        try:
            out = subprocess.run(
                ["pyright", "--version"], capture_output=True, text=True, check=False
            )
        except OSError:
            return "unknown"
        return out.stdout.strip() or out.stderr.strip() or "unknown"

    def install(self) -> str:
        # `pyright --version` omits Node; record both for reproducibility (the §9
        # lock manifest consumes this). Node *pinning* is an env concern (later plan).
        return f"{self.version()} (node {_node_version()})"

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
    ) -> tuple[list[str], dict[str, str]]:
        platform = self._platform(config)
        # pyright resolves config `include` paths RELATIVE to the config-file dir
        # and silently DROPS absolute entries ("not relative" -> 0 files analyzed,
        # a false-clean). The config lives in `workdir`, so render each absolute
        # src_root as a workdir-relative path (pyright docs: Path Handling).
        includes = [_relative_to(root, workdir) for root in config.src_roots]
        pyright_config: dict[str, object] = {
            "include": includes,
            "exclude": list(config.exclude_globs),
            "typeCheckingMode": "standard",  # stock default (§6)
            "useLibraryCodeForTypes": True,  # resolve deps, report first-party only
            "pythonVersion": config.python_version,
            "pythonPlatform": platform,  # threaded from §6, not hardcoded
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
            "pyright",
            "--project",
            str(workdir),
            "--outputjson",
            "--pythonversion",
            config.python_version,
            "--pythonplatform",
            platform,
        ]
        if thread_mode is ThreadMode.ALL_CORES:
            argv.append("--threads")  # bare = auto-parallelism by logical CPUs (pyright docs)
        # ONE_CORE: omit --threads (default single main thread); affinity in Plan 4.
        return (argv, {})

    def parallelism_cap(self, thread_mode: ThreadMode) -> ParallelismCap:
        # pyright --threads is a hint, not an OS cap; affinity (Plan 4) makes it hard.
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
        # Parse-sanity (research doc): a CLEAN verdict is only honest if we can
        # confirm a positive file count. Promote to failed{env} when files is 0
        # (mis-scoped include) OR None (exit 0 but --outputjson was unparseable /
        # dropped summary.filesAnalyzed). Recording an unverifiable clean would let
        # a false-clean enter the data set -> record-honesty violation (§7/§12).
        if result is ResultClass.CLEAN:
            _diags, files = self.parse(raw.stdout, raw.stderr, raw.exit_code)
            if not files:  # 0 or None
                return ResultClass.FAILED_ENV
        return result

    def clear_cache(self, project: str) -> None:
        return None  # stateless single-shot (research doc)

    def prepare_command(self, project: str) -> str | None:
        return None
