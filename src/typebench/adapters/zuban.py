"""zuban adapter.

zuban (github.com/zubanls/zuban) is a Rust, mypy-compatible type checker. We run
its stock `check` in its OWN `default` mode — zuban's recommended preset, the
neutral analogue of pyrefly's `preset="default"` and ty's defaults (NOT a
mypy-imitation; see the mode note below). Two equalized posture knobs are
satisfied NATIVELY by default mode, both verified locally, so they need no flags:

- `analyze_untyped_defs`: default mode checks the bodies of untyped functions
  (mypy mode would skip them and emit an `annotation-unchecked` note instead).
- `resolve_deps_report_first_party`: zuban resolves third-party types from the
  venv (`--python-executable`) but reports diagnostics on the passed first-party
  files ONLY — it does not descend into and flag errors inside dependencies.

Output is mypy-IDENTICAL text on stdout (zuban has no JSON output): the same
`Found N errors in M files (checked K source files)` / `Success: no issues found
in N source files` summary, so parse() reuses mypy's exact regexes. Exit codes:
0 clean, 1 diagnostics, 2 env/usage (e.g. "No Python files found", bad flag,
config error), Rust panic (101) otherwise — a clean, non-overloaded map (unlike
mypy's exit 2 or pyrefly's exit 1).

MODE + config suppression (`suppress_project_config`). zuban discovers config by
reading `[mypy]` sections from mypy.ini/pyproject/setup.cfg searched UPWARD FROM
CWD, plus a stray home-dir `~/.mypy.ini`. The harness runs from a neutral cwd, but
to suppress config DETERMINISTICALLY (incl. a home-dir file) we pass an explicit
generated `[mypy]` config. Two non-obvious consequences, both VERIFIED:
  1. Providing a `[mypy]` config FLIPS zuban into mypy-compatible mode (untyped
     defs no longer checked). `--mode default` is therefore passed EXPLICITLY to
     pin zuban's stock default preset back on despite the config file.
  2. An out-of-tree `--config-file` makes zuban resolve no module base ("No
     Python files found"). The config sets `mypy_path` to each src_root's PARENT
     — which both fixes discovery AND lets a package src_root (e.g. .../repo/httpx)
     resolve its OWN absolute first-party imports (`httpx._client`) instead of
     flooding import-not-found. (Same first-party-root concern ty solves with
     --extra-search-path; here it rides on mypy_path.)

Threading: zuban is rayon-parallel by default (no thread flag). It honors
`RAYON_NUM_THREADS` (verified: =1 -> 1 core, uncapped -> many), so the constrained
track sets it to the configured core count as a HARD rayon-pool cap, with
cpu-affinity pinned on top.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from typebench.adapters._support import confirm_clean, probe_version
from typebench.adapters.base import ParallelismCap
from typebench.contracts.policy import PRESETS, CheckerPosture, Policy
from typebench.contracts.taxonomy import ResultClass, ThreadMode, is_constrained
from typebench.engine.proc import SYSTEM_HOST
from typebench.engine.wrapper import classify_with_map

if TYPE_CHECKING:
    from typebench.contracts.config import NormalizedConfig
    from typebench.contracts.proc import ProcessHost, RawRun

# mypy-identical summaries: errors+files, and the clean form (zuban prints these
# byte-for-byte like mypy, so the same patterns parse both).
_FOUND_RE = re.compile(r"Found (\d+) errors? in \d+ files? \(checked (\d+) source files?\)")
_CLEAN_RE = re.compile(r"Success: no issues found in (\d+) source files?")

# Exit codes: 0 clean, 1 diagnostics, 2 env/usage (no files / bad flag / config),
# 101 Rust panic. Clean and non-overloaded -> a plain classify_with_map suffices.
_EXIT_MAP: dict[int, ResultClass] = {
    0: ResultClass.CLEAN,
    1: ResultClass.DIAGNOSTICS,
    2: ResultClass.FAILED_ENV,
    101: ResultClass.FAILED_CRASH,
}


def _globs_to_exclude_regex(globs: tuple[str, ...]) -> str:
    """zuban --exclude takes a mypy-style REGEX (matched against discovered paths
    during recursive directory walk), not globs. Render the normalized exclude
    globs as an alternation of their dir-name segments, e.g. '**/tests/**' ->
    'tests', anchored on path separators so a segment matches, not a substring.
    (Mirrors mypy's rendering because zuban shares mypy's exclude semantics; kept
    local so the adapter stays self-contained and independently auditable.)"""
    names = sorted({g.strip("*/ ").split("/")[0] for g in globs if g.strip("*/ ")})
    if not names:
        return r"(?!)"  # match nothing
    return r"(^|/)(" + "|".join(re.escape(n) for n in names) + r")(/|$)"


def _posture_args(posture: CheckerPosture) -> list[str]:
    """Render zuban's native flags for the equalized checker posture.

    Under STANDARD, both analyze_untyped_defs and resolve_deps_report_first_party
    are satisfied natively by `--mode default` (see module docstring), so no extra
    flags are emitted. strict is deferred until a verified translation + golden."""
    if posture.strict:
        msg = "strict posture not yet implemented for zuban"
        raise NotImplementedError(msg)
    return []


class ZubanAdapter:
    name = "zuban"
    install_source = "PyPI wheel (Rust)"

    def __init__(self, host: ProcessHost = SYSTEM_HOST) -> None:
        self._host = host
        self._version_cache: dict[str | None, str] = {}

    def version(self, binary: str | None = None) -> str:
        cached = self._version_cache.get(binary)
        if cached is not None:
            return cached
        version = probe_version([binary or "zuban", "--version"], host=self._host)
        self._version_cache[binary] = version
        return version

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
        # Generated neutral [mypy] config (see module docstring): suppresses the
        # project's own config AND a stray ~/.mypy.ini, and carries mypy_path =
        # each src_root's PARENT so an out-of-tree config still discovers files and
        # resolves first-party absolute imports. Dedup parents in first-seen order.
        seen: set[str] = set()
        parents: list[str] = []
        for root in config.src_roots:
            parent = str(Path(root).parent)
            if parent not in seen:
                seen.add(parent)
                parents.append(parent)
        # mypy_path is a comma-separated list in the [mypy] .ini section (verified
        # zuban accepts the list form). Checkout paths under typebench-cache carry no
        # commas, so plain join is safe; .ini values are unquoted (NOT TOML).
        mypy_path_value = ", ".join(parents)
        config_path = workdir / "zuban.ini"
        config_path.write_text(f"[mypy]\nmypy_path = {mypy_path_value}\n")

        argv = [
            binary or "zuban",
            "check",
            # Pin zuban's OWN default preset: the [mypy] config below would
            # otherwise flip zuban into mypy-compatible mode (untyped defs skipped).
            "--mode",
            "default",
            "--config-file",
            str(config_path),
            "--python-version",
            config.python_version,
            "--platform",
            config.python_platform,
            "--exclude",
            _globs_to_exclude_regex(config.exclude_globs),
            *_posture_args(PRESETS[Policy.STANDARD]),
        ]
        if config.venv_python is not None:
            # Resolve installed third-party from the project venv (else zuban uses
            # its own interpreter env). First-party-only reporting stays native.
            argv += ["--python-executable", config.venv_python]
        argv += list(config.src_roots)

        env: dict[str, str] = {}
        if is_constrained(thread_mode):
            # HARD cap: zuban's rayon pool honors RAYON_NUM_THREADS (verified).
            # Always set in CONSTRAINED (incl. cores=1); affinity pins on top.
            env["RAYON_NUM_THREADS"] = str(config.cores)
        return (argv, env)

    def parallelism_cap(
        self, thread_mode: ThreadMode, cores: int, binary: str | None = None
    ) -> ParallelismCap:
        # RAYON_NUM_THREADS sets the rayon worker-pool size -> a real worker cap.
        # Always set in CONSTRAINED (incl. cores=1), so the mechanism is
        # cores-independent. Affinity pins the cores on top.
        return ParallelismCap(mechanism="RAYON_NUM_THREADS (rayon) + cpu-affinity", hard_cap=True)

    def parse(self, stdout: str, stderr: str, exit_code: int) -> tuple[int | None, int | None]:
        m = _FOUND_RE.search(stdout)
        if m is not None:
            return (int(m.group(1)), int(m.group(2)))
        c = _CLEAN_RE.search(stdout)
        if c is not None:
            return (0, int(c.group(1)))
        return (None, None)

    def classify(self, raw: RawRun) -> ResultClass:
        result = classify_with_map(raw, _EXIT_MAP)
        if result is ResultClass.CLEAN:
            _diags, files = self.parse(raw.stdout, raw.stderr, raw.exit_code)
            # exit 0 must carry a positive checked-files count, else the target was
            # mis-scoped (false clean). zuban's "Success: no issues found in N
            # source files" always reports the count, so None here means broken
            # output -> failed{env} (reliable count, like mypy/pyright; unlike ty).
            return confirm_clean(files, tolerate_unknown=False)
        return result

    def clear_cache(self, project: str) -> None:
        return None  # stateless `check` (no on-disk cache; verified)

    def prepare_command(self, project: str) -> str | None:
        return None
