from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from typebench.contracts.proc import RawRun

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class HostCall:
    argv: tuple[str, ...]
    cwd: Path | None
    env: Mapping[str, str] | None
    timeout: float | None


type RunScript = RawRun | Callable[[HostCall], RawRun] | Sequence[RawRun]


class FakeHost:
    """Scripted ProcessHost for tests.

    `runs` keys are argv prefixes; the longest matching prefix wins.
    """

    def __init__(
        self,
        runs: Mapping[tuple[str, ...], RunScript] | None = None,
        *,
        which: Mapping[str, str | None] | None = None,
        default: RawRun | None = None,
    ) -> None:
        self._runs = dict(runs or {})
        self._which = dict(which or {})
        self._default = default or RawRun(0, None, False, False, "", "")
        self.calls: list[HostCall] = []

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> RawRun:
        call = HostCall(tuple(argv), cwd, env, timeout)
        self.calls.append(call)
        script = self._script_for(call.argv)
        if callable(script):
            return script(call)
        if isinstance(script, RawRun):
            return script
        if script:
            return script[0]
        return self._default

    def which(self, name: str) -> str | None:
        return self._which.get(name)

    def _script_for(self, argv: tuple[str, ...]) -> RunScript:
        matches = [prefix for prefix in self._runs if argv[: len(prefix)] == prefix]
        if not matches:
            return self._default
        prefix = max(matches, key=len)
        return self._runs[prefix]


def fake_raw(
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    env_error: bool = False,
    timed_out: bool = False,
) -> RawRun:
    return RawRun(
        exit_code=exit_code,
        signal=None,
        timed_out=timed_out,
        oom=False,
        stdout=stdout,
        stderr=stderr,
        env_error=env_error,
    )
