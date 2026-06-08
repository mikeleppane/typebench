import json
import os
import shutil
from pathlib import Path

import pytest

from typebench.adapters import pyright as pyright_mod
from typebench.adapters.base import Adapter
from typebench.adapters.pyright import PyrightAdapter
from typebench.collector import run_single
from typebench.models import ResultClass, RunResult, ThreadMode
from typebench.normalized_config import NormalizedConfig
from typebench.wrapper import RawRun, run_command

# Fixtures live at the REPO ROOT (../fixtures), not tests/fixtures.
_FIXTURES = Path(__file__).parent.parent / "fixtures"
_HAS_PYRIGHT = shutil.which("pyright") is not None


def test_pyright_is_an_adapter() -> None:
    assert isinstance(PyrightAdapter(), Adapter)


def test_parse_reads_summary_counts() -> None:
    blob = json.dumps(
        {
            "summary": {"errorCount": 3, "warningCount": 1, "filesAnalyzed": 7},
            "generalDiagnostics": [],
        }
    )
    assert PyrightAdapter().parse(blob, "", 1) == (3, 7)


def test_parse_is_graceful_on_garbage() -> None:
    assert PyrightAdapter().parse("not json", "", 2) == (None, None)


def test_parse_rejects_bool_counts() -> None:
    blob = json.dumps({"summary": {"errorCount": True, "filesAnalyzed": False}})
    assert PyrightAdapter().parse(blob, "", 1) == (None, None)


def test_classify_exit_map() -> None:
    a = PyrightAdapter()
    assert a.classify(RawRun(0, None, False, False, "", "")) == ResultClass.CLEAN
    assert a.classify(RawRun(1, None, False, False, "", "")) == ResultClass.DIAGNOSTICS
    assert a.classify(RawRun(2, None, False, False, "", "")) == ResultClass.FAILED_CRASH
    assert a.classify(RawRun(3, None, False, False, "", "")) == ResultClass.FAILED_ENV
    assert a.classify(RawRun(4, None, False, False, "", "")) == ResultClass.FAILED_ENV
    assert a.classify(RawRun(0, None, True, False, "", "")) == ResultClass.FAILED_TIMEOUT


def test_classify_zero_files_on_exit0_is_env_failure() -> None:
    blob = json.dumps({"summary": {"errorCount": 0, "filesAnalyzed": 0}})
    raw = RawRun(0, None, False, False, blob, "")
    assert PyrightAdapter().classify(raw) == ResultClass.FAILED_ENV


def test_command_writes_pyrightconfig_and_targets_project(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=("/abs/src",), python_version="3.11")
    argv, env = PyrightAdapter().command("demo", cfg, ThreadMode.ONE_CORE, tmp_path)
    assert env == {}
    written = json.loads((tmp_path / "pyrightconfig.json").read_text())
    # pyright drops absolute include entries, so the adapter renders src_roots
    # relative to the config-file dir (the workdir / tmp_path).
    assert written["include"] == [os.path.relpath("/abs/src", tmp_path)]
    assert written["typeCheckingMode"] == "standard"
    assert written["pythonVersion"] == "3.11"
    assert written["pythonPlatform"] == "Linux"
    assert "--project" in argv and str(tmp_path) in argv
    assert "--outputjson" in argv
    assert "--skipunannotated" not in argv


def test_command_maps_python_platform(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=("/abs/src",), python_platform="darwin")
    argv, _env = PyrightAdapter().command("demo", cfg, ThreadMode.ONE_CORE, tmp_path)
    written = json.loads((tmp_path / "pyrightconfig.json").read_text())
    assert written["pythonPlatform"] == "Darwin"
    assert "Darwin" in argv


def test_command_derives_venv_path_layout(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=("/abs/src",), venv_python="/proj/.venv/bin/python")
    PyrightAdapter().command("demo", cfg, ThreadMode.ONE_CORE, tmp_path)
    written = json.loads((tmp_path / "pyrightconfig.json").read_text())
    assert written["venvPath"] == "/proj"
    assert written["venv"] == ".venv"


def test_version_is_no_raise_when_binary_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_k: object) -> object:
        raise FileNotFoundError("pyright")

    monkeypatch.setattr(pyright_mod.subprocess, "run", _boom)
    assert PyrightAdapter().version() == "unknown"


def test_missing_pyright_yields_schema_valid_failed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    cfg = NormalizedConfig(src_roots=("/abs/src",))
    result = run_single(
        PyrightAdapter(),
        project="demo",
        config=cfg,
        thread_mode=ThreadMode.ONE_CORE,
        warmup=1,
        runs=2,
        timeout=10,
    )
    assert isinstance(result, RunResult)
    assert result.result_class == ResultClass.FAILED_ENV
    assert result.tool_version == "unknown"


@pytest.mark.skipif(not _HAS_PYRIGHT, reason="pyright not installed")
def test_live_clean_project(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=(str(_FIXTURES / "clean_project"),))
    argv, env = PyrightAdapter().command("clean", cfg, ThreadMode.ONE_CORE, tmp_path)
    raw = run_command(argv, timeout=120, env=env)
    assert PyrightAdapter().classify(raw) == ResultClass.CLEAN
    diags, files = PyrightAdapter().parse(raw.stdout, raw.stderr, raw.exit_code)
    assert diags == 0
    assert files is not None and files > 0


@pytest.mark.skipif(not _HAS_PYRIGHT, reason="pyright not installed")
def test_live_error_project(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=(str(_FIXTURES / "error_project"),))
    argv, env = PyrightAdapter().command("err", cfg, ThreadMode.ONE_CORE, tmp_path)
    raw = run_command(argv, timeout=120, env=env)
    assert PyrightAdapter().classify(raw) == ResultClass.DIAGNOSTICS
    diags, _ = PyrightAdapter().parse(raw.stdout, raw.stderr, raw.exit_code)
    assert diags is not None and diags > 0


@pytest.mark.skipif(not _HAS_PYRIGHT, reason="pyright not installed")
def test_version_probe() -> None:
    v = PyrightAdapter().version()
    assert v.startswith("pyright") or v[0].isdigit()


@pytest.mark.skipif(not _HAS_PYRIGHT, reason="pyright not installed")
def test_install_records_node_version() -> None:
    info = PyrightAdapter().install()
    assert "node" in info.lower()
