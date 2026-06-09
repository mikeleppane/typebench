"""ty adapter (spec §4, §6). ty has NO JSON output: diagnostics come from the
`concise` stdout summary, file count only from `-v` stderr (fragile -> may be
None). See docs/superpowers/research/2026-06-08-checker-cli-facts.md (ty)."""

from __future__ import annotations

import json
import re
import subprocess
from typing import TYPE_CHECKING

from typebench.adapters._support import confirm_clean, probe_version
from typebench.adapters.base import ParallelismCap, coerce_count
from typebench.contracts.models import ResultClass, ThreadMode
from typebench.engine.wrapper import classify_with_map

if TYPE_CHECKING:
    from pathlib import Path

    from typebench.contracts.config import NormalizedConfig
    from typebench.engine.wrapper import RawRun

_FOUND_RE = re.compile(r"Found (\d+) diagnostics?")
_INDEXED_RE = re.compile(r"Indexed (\d+) file\(s\)")

# Exit codes (research doc): 0 clean, 1 diagnostics, 2 config/IO/CLI, 101 panic.
_EXIT_MAP: dict[int, ResultClass] = {
    0: ResultClass.CLEAN,
    1: ResultClass.DIAGNOSTICS,
    2: ResultClass.FAILED_ENV,
    101: ResultClass.FAILED_CRASH,
}


class TyAdapter:
    name = "ty"
    install_source = "PyPI wheel (Rust)"

    def version(self) -> str:
        return probe_version(["ty", "--version"], runner=subprocess.run)

    def install(self) -> str:
        return self.version()

    def command(
        self,
        project: str,
        config: NormalizedConfig,
        thread_mode: ThreadMode,
        workdir: Path,
    ) -> tuple[list[str], dict[str, str]]:
        # Hand-render the tiny ty.toml (tomllib is read-only — no writer; the test
        # reads it back, the adapter only writes it). [environment] carries
        # version/platform; [src].exclude carries the §6 excludes in ty's
        # gitignore-style syntax (anchored to the project root). json.dumps quotes
        # the string values safely; keys are fixed literals (no user strings).
        exclude_items = ", ".join(json.dumps(g) for g in config.exclude_globs)
        lines = [
            "[environment]",
            f'python-version = "{config.python_version}"',
            f'python-platform = "{config.python_platform}"',
            "",
            "[src]",
            f"exclude = [{exclude_items}]",
        ]
        config_path = workdir / "ty.toml"
        config_path.write_text("\n".join(lines) + "\n")

        argv = [
            "ty",
            "check",
            *config.src_roots,
            "--config-file",
            str(config_path),  # suppress project [tool.ty] discovery
            # CRITICAL (ty docs, locally verified): gitignore-style excludes do NOT
            # apply to paths passed on the command line unless --force-exclude is set.
            # Without it a nested tests/ dir under an absolute src_root is still
            # checked -> breaks the §6 excludes contract and inflates ty's
            # diagnostics/file-count vs the others (neutrality leak).
            "--force-exclude",
            # The normalized file set must come ONLY from src_roots + exclude_globs.
            # By default ty honors the project's .gitignore (inside a git repo), so a
            # project that git-ignores some first-party Python would have those files
            # SKIPPED by ty while the other tools still analyze them -> different file
            # set -> a false-clean / undercount (neutrality leak). Disable it.
            "--no-respect-ignore-files",
            "--python-version",
            config.python_version,
            "--python-platform",
            config.python_platform,
            "--output-format",
            "concise",
            "-v",  # emits "Indexed N file(s)" on stderr (the only files source)
            "--no-progress",
            "--color",
            "never",
        ]
        for glob in config.exclude_globs:
            argv += ["--exclude", glob]  # belt-and-suspenders alongside [src].exclude
        if config.venv_python is not None:
            argv += ["--python", config.venv_python]  # resolve third-party from venv

        env: dict[str, str] = {}
        if thread_mode is ThreadMode.CONSTRAINED:
            # SOFT cap to the configured core count (ty may still spawn threads).
            env["TY_MAX_PARALLELISM"] = str(config.cores)
        return (argv, env)

    def parallelism_cap(self, thread_mode: ThreadMode, cores: int) -> ParallelismCap:
        # TY_MAX_PARALLELISM is a soft task cap, not a hard thread cap. Always set in
        # CONSTRAINED (incl. cores=1), so the mechanism is cores-independent.
        return ParallelismCap(mechanism="TY_MAX_PARALLELISM + cpu-affinity", hard_cap=False)

    def parse(self, stdout: str, stderr: str, exit_code: int) -> tuple[int | None, int | None]:
        files = None
        idx = _INDEXED_RE.search(stderr)
        if idx is not None:
            files = coerce_count(int(idx.group(1)))
        if "All checks passed!" in stdout:
            return (0, files)
        found = _FOUND_RE.search(stdout)
        if found is not None:
            return (coerce_count(int(found.group(1))), files)
        return (None, files)

    def classify(self, raw: RawRun) -> ResultClass:
        result = classify_with_map(raw, _EXIT_MAP)
        if result is ResultClass.CLEAN:
            _diags, files = self.parse(raw.stdout, raw.stderr, raw.exit_code)
            # ty's files count is best-effort (stderr -v). Only a CONFIRMED 0 is a
            # mis-scoped false-clean; files None is tolerated (unknowable, not broken)
            # -> stays CLEAN. (Contrast pyright/mypy, whose counts are reliable.)
            return confirm_clean(files, tolerate_unknown=True)
        return result

    def clear_cache(self, project: str) -> None:
        return None  # stateless `check`

    def prepare_command(self, project: str) -> str | None:
        return None
