from collections.abc import Mapping, Sequence
from pathlib import Path

from typebench.contracts.proc import ProcessHost, RawRun
from typebench.engine.wrapper import RawRun as WrapperRawRun


def test_raw_run_is_reexported_from_wrapper() -> None:
    assert WrapperRawRun is RawRun


def test_process_host_protocol_accepts_structural_implementation() -> None:
    class Host:
        def run(
            self,
            argv: Sequence[str],
            *,
            cwd: Path | None = None,
            env: Mapping[str, str] | None = None,
            timeout: float | None = None,
        ) -> RawRun:
            return RawRun(0, None, False, False, " ".join(argv), "")

        def which(self, name: str) -> str | None:
            return f"/bin/{name}"

    assert isinstance(Host(), ProcessHost)
