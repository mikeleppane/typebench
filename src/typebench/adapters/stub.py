"""StubAdapter — drives typebench._fake_checker. Exercises the full pipeline
deterministically: chosen exit code, diagnostics, files, duration, a signal
death, or a missing-binary environment failure."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from typebench.adapters.base import ParallelismCap, coerce_count, default_classify

if TYPE_CHECKING:
    from typebench.models import ResultClass, ThreadMode
    from typebench.wrapper import RawRun


class StubAdapter:
    name = "stub"

    def __init__(
        self,
        exit_code: int = 0,
        diagnostics: int = 0,
        files: int = 0,
        sleep: float = 0.0,
        signal: int | None = None,
        missing_binary: bool = False,
        fail_after_runs: int | None = None,
        state_file: str | None = None,
    ) -> None:
        self._exit_code = exit_code
        self._diagnostics = diagnostics
        self._files = files
        self._sleep = sleep
        self._signal = signal
        self._missing_binary = missing_binary
        self._fail_after_runs = fail_after_runs
        self._state_file = state_file

    def version(self) -> str:
        return "stub-1.0"

    def install(self) -> str:
        # No distribution to verify; real checks land in Plan 2.
        return self.version()

    def command(self, project: str, thread_mode: ThreadMode) -> tuple[list[str], dict[str, str]]:
        if self._missing_binary:
            # Nonexistent executable -> run_command raises OSError -> failed{env}.
            return (["typebench-nonexistent-checker-xyz"], {})
        argv = [
            sys.executable,
            "-m",
            "typebench._fake_checker",
            "--exit-code",
            str(self._exit_code),
            "--diagnostics",
            str(self._diagnostics),
            "--files",
            str(self._files),
            "--sleep",
            str(self._sleep),
        ]
        if self._fail_after_runs is not None and self._state_file is not None:
            argv += [
                "--fail-after-runs",
                str(self._fail_after_runs),
                "--state-file",
                self._state_file,
            ]
        if self._signal is not None:
            argv += ["--signal", str(self._signal)]
        return (argv, {})

    def parallelism_cap(self, thread_mode: ThreadMode) -> ParallelismCap:
        # Single process: CPU affinity is the only lever and is a true cap.
        return ParallelismCap(mechanism="cpu-affinity", hard_cap=True)

    def parse(self, stdout: str, stderr: str, exit_code: int) -> tuple[int | None, int | None]:
        try:
            payload = json.loads(stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return (None, None)
        if not isinstance(payload, dict):
            return (None, None)
        return (coerce_count(payload.get("diagnostics")), coerce_count(payload.get("files")))

    def classify(self, raw: RawRun) -> ResultClass:
        return default_classify(raw)

    def clear_cache(self, project: str) -> None:
        return None

    def prepare_command(self, project: str) -> str | None:
        return None  # stateless: no checker cache to clear between runs
