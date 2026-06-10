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


def _diagnostics(binary: str | None, tmp_path: Path) -> int | None:
    adapter = StubAdapter(diagnostics=7, files=3)
    workdir = tmp_path / f"work-{binary is None}"
    workdir.mkdir()
    argv, env = adapter.command(
        "demo", _config(tmp_path), ThreadMode.CONSTRAINED, workdir, binary=binary
    )
    raw = run_command(argv, timeout=30, env=env)
    diags, _files = adapter.parse(raw.stdout, raw.stderr, raw.exit_code)
    return diags


def test_stub_count_invariant_to_launcher_path(tmp_path: Path) -> None:
    bare = _diagnostics(None, tmp_path)
    venv_like = _diagnostics(sys.executable, tmp_path)
    assert bare == venv_like == 7


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
