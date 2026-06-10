import os
import shutil
import sys
from pathlib import Path

import pytest

from typebench.adapters.mypy import MypyAdapter
from typebench.adapters.stub import StubAdapter
from typebench.contracts.config import NormalizedConfig
from typebench.contracts.identity import CheckerSpec
from typebench.contracts.taxonomy import ThreadMode
from typebench.corpus.checkerenv import prepare_checker
from typebench.engine.wrapper import run_command


def _config(root: Path) -> NormalizedConfig:
    return NormalizedConfig(
        src_roots=(str(root / "src"),),
        python_version="3.12",
        python_platform="linux",
        cores=1,
    )


def _run_stub(binary: str | None, workdir: Path) -> tuple[list[str], int | None]:
    workdir.mkdir()
    adapter = StubAdapter(diagnostics=7, files=3)
    argv, env = adapter.command(
        "demo", _config(workdir.parent), ThreadMode.CONSTRAINED, workdir, binary=binary
    )
    raw = run_command(argv, timeout=30, env=env)
    diags, _files = adapter.parse(raw.stdout, raw.stderr, raw.exit_code)
    return argv, diags


def test_stub_command_uses_binary_as_launcher(tmp_path: Path) -> None:
    # Structural falsifiability: the resolved per-version binary IS argv[0]. Build the
    # argv only (do not run a fake path) — this proves the launcher seam is real, so the
    # count-invariance test below cannot pass as a tautology if `binary=` were ignored.
    # (A naked symlink to the interpreter can't actually RUN — Python derives its prefix
    # from the launcher path — so the seam is asserted structurally, the count by running.)
    adapter = StubAdapter(diagnostics=7, files=3)
    workdir = tmp_path / "argv"
    workdir.mkdir()
    default_argv, _ = adapter.command("demo", _config(tmp_path), ThreadMode.CONSTRAINED, workdir)
    pinned_argv, _ = adapter.command(
        "demo", _config(tmp_path), ThreadMode.CONSTRAINED, workdir, binary="/opt/venv/bin/python"
    )
    assert default_argv[0] == sys.executable
    assert pinned_argv[0] == "/opt/venv/bin/python"  # the resolved-venv launcher lands at argv[0]


def test_stub_count_invariant_to_launcher_path(tmp_path: Path) -> None:
    # The launcher path does not change the emitted diagnostic count: a per-version venv
    # changes WHERE the interpreter lives, never WHAT it counts. Both arms use a runnable
    # interpreter (sys.executable); the seam itself is proven structurally above.
    bare_argv, bare = _run_stub(None, tmp_path / "bare")
    explicit_argv, explicit = _run_stub(sys.executable, tmp_path / "explicit")

    assert bare_argv[0] == sys.executable
    assert explicit_argv[0] == sys.executable
    assert bare == explicit == 7


def _mypy_diagnostics(binary: str | None, project_root: Path, workdir: Path) -> int | None:
    adapter = MypyAdapter()
    config = NormalizedConfig(
        src_roots=(str(project_root),),
        python_version="3.12",
        python_platform="linux",
        cores=1,
    )
    argv, env = adapter.command(
        "neutrality-mypy", config, ThreadMode.CONSTRAINED, workdir, binary=binary
    )
    raw = run_command(argv, timeout=120, env=env)
    diagnostics, _files = adapter.parse(raw.stdout, raw.stderr, raw.exit_code)
    return diagnostics


@pytest.mark.skipif(
    os.environ.get("TYPEBENCH_INTEGRATION") != "1",
    reason="real-checker venv build needs uv+network; opt in with TYPEBENCH_INTEGRATION=1",
)
@pytest.mark.skipif(shutil.which("mypy") is None, reason="mypy not installed")
@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not installed")
def test_mypy_prepared_venv_count_equals_bare_path(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "sample.py").write_text("bad: str = 123\n", encoding="utf-8")

    runtime = prepare_checker(
        CheckerSpec(tool="mypy", version="1.18.2"),
        tmp_path / "checker-cache",
        install_source=MypyAdapter.install_source,
    )

    assert runtime.version == "1.18.2"
    assert Path(runtime.binary).is_file()

    bare = _mypy_diagnostics(None, project, tmp_path / "bare-work")
    prepared = _mypy_diagnostics(runtime.binary, project, tmp_path / "prepared-work")

    assert bare == prepared == 1
