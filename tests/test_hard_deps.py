import shutil

import pytest

# pyrefly is already a dev dep; mypy + ty are added in 2B; pyright came in 2A.
_HARD_DEPS = ["mypy", "ty", "pyrefly", "pyright"]


@pytest.mark.parametrize("binary", _HARD_DEPS)
def test_hard_dependency_is_installed(binary: str) -> None:
    # These are declared dev deps, so the gate MUST have them on PATH. This test
    # FAILS (does not skip) when one is missing — turning the per-tool live tests'
    # silent `skipif` skips into one loud, intentional failure. hyperfine is the
    # ONLY tool allowed to be absent (it stays a skipif system binary).
    assert shutil.which(binary) is not None, (
        f"{binary} is a hard dev dep but is not on PATH; run `uv sync` and retry"
    )
