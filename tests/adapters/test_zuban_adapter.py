import configparser
import shutil
from pathlib import Path

import pytest

from typebench._internal.test_fakes import FakeHost, fake_raw
from typebench.adapters.base import Adapter
from typebench.adapters.zuban import ZubanAdapter
from typebench.contracts.config import MeasurementPlan, NormalizedConfig
from typebench.contracts.models import ResultClass, RunResult, ThreadMode
from typebench.engine.collector import run_single
from typebench.engine.wrapper import RawRun, run_command

_HAS_ZUBAN = shutil.which("zuban") is not None


def test_zuban_is_an_adapter() -> None:
    assert isinstance(ZubanAdapter(), Adapter)


def test_parse_errors_and_files_from_mypy_style_summary() -> None:
    stdout = "x.py:1: error: bad  [assignment]\nFound 3 errors in 1 file (checked 7 source files)\n"
    assert ZubanAdapter().parse(stdout, "", 1) == (3, 7)


def test_parse_clean_summary() -> None:
    assert ZubanAdapter().parse("Success: no issues found in 5 source files\n", "", 0) == (0, 5)


def test_parse_singular_error_and_file() -> None:
    assert ZubanAdapter().parse("Found 1 error in 1 file (checked 1 source file)\n", "", 1) == (
        1,
        1,
    )


def test_parse_is_graceful_on_garbage() -> None:
    assert ZubanAdapter().parse("???", "???", 1) == (None, None)


def test_classify_exit_map() -> None:
    a = ZubanAdapter()
    clean = "Success: no issues found in 4 source files\n"
    assert a.classify(RawRun(0, None, False, False, clean, "")) == ResultClass.CLEAN
    assert (
        a.classify(
            RawRun(1, None, False, False, "Found 2 errors in 1 file (checked 4 source files)\n", "")
        )
        == ResultClass.DIAGNOSTICS
    )
    # exit 2 = env/usage ("No Python files found", bad flag, config error).
    assert (
        a.classify(RawRun(2, None, False, False, "", "No Python files found"))
        == ResultClass.FAILED_ENV
    )
    # Rust panic.
    assert a.classify(RawRun(101, None, False, False, "", "panicked")) == ResultClass.FAILED_CRASH
    assert a.classify(RawRun(0, None, True, False, clean, "")) == ResultClass.FAILED_TIMEOUT


def test_classify_clean_without_files_count_is_env_failure() -> None:
    # zuban's "Success" line always carries the count (reliable, like mypy/pyright);
    # exit 0 with no parseable count means broken output -> failed{env}, NOT clean.
    assert ZubanAdapter().classify(RawRun(0, None, False, False, "", "")) == ResultClass.FAILED_ENV


def test_classify_zero_files_on_exit0_is_env_failure() -> None:
    raw = RawRun(0, None, False, False, "Success: no issues found in 0 source files\n", "")
    assert ZubanAdapter().classify(raw) == ResultClass.FAILED_ENV


def test_command_writes_neutral_config_and_builds_argv(tmp_path: Path) -> None:
    cfg = NormalizedConfig(
        src_roots=("/abs/repo/pkg",), python_version="3.11", venv_python="/v/bin/python"
    )
    argv, env = ZubanAdapter().command("demo", cfg, ThreadMode.CONSTRAINED, tmp_path)
    # 1-core constrained -> hard rayon cap.
    assert env == {"RAYON_NUM_THREADS": "1"}
    # Pins zuban's own default mode despite the [mypy] config (which would flip it).
    assert argv[0] == "zuban" and argv[1] == "check"
    assert "--mode" in argv and argv[argv.index("--mode") + 1] == "default"
    # Generated neutral config in the workdir; src_roots passed; venv resolved.
    cfg_path = tmp_path / "zuban.ini"
    assert "--config-file" in argv and str(cfg_path) in argv
    assert "/abs/repo/pkg" in argv
    assert "--python-version" in argv and "3.11" in argv
    assert "--platform" in argv and "linux" in argv
    assert "--python-executable" in argv and "/v/bin/python" in argv
    assert "--exclude" in argv
    # The config suppresses project config AND carries mypy_path = src_root PARENT
    # (so an out-of-tree config still discovers files + resolves first-party imports).
    parser = configparser.ConfigParser()
    parser.read_string(cfg_path.read_text())
    assert parser.has_section("mypy")
    assert parser["mypy"]["mypy_path"] == "/abs/repo"


def test_command_dedupes_mypy_path_parents(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=("/abs/repo/a", "/abs/repo/b", "/abs/other/c"))
    _argv, _env = ZubanAdapter().command("demo", cfg, ThreadMode.CONSTRAINED, tmp_path)
    parser = configparser.ConfigParser()
    parser.read_string((tmp_path / "zuban.ini").read_text())
    # Distinct parents, first-seen order; shared parent collapsed to one entry.
    assert parser["mypy"]["mypy_path"] == "/abs/repo, /abs/other"


def test_command_constrained_scales_cap_to_cores(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=("/abs/src",), cores=4)
    _argv, env = ZubanAdapter().command("demo", cfg, ThreadMode.CONSTRAINED, tmp_path)
    assert env == {"RAYON_NUM_THREADS": "4"}


def test_command_all_cores_omits_parallelism_cap(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=("/abs/src",), cores=4)  # cores ignored for ALL_CORES
    _argv, env = ZubanAdapter().command("demo", cfg, ThreadMode.ALL_CORES, tmp_path)
    assert "RAYON_NUM_THREADS" not in env


def test_parallelism_cap_is_hard() -> None:
    cap = ZubanAdapter().parallelism_cap(ThreadMode.CONSTRAINED, 1)
    assert cap.hard_cap is True


def test_version_is_no_raise_when_binary_absent() -> None:
    host = FakeHost({("zuban", "--version"): fake_raw(stderr="missing", env_error=True)})
    assert ZubanAdapter(host=host).version() == "unknown"


def test_missing_zuban_yields_schema_valid_failed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    cfg = NormalizedConfig(src_roots=("/abs/src",))
    result = run_single(
        ZubanAdapter(),
        project="demo",
        config=cfg,
        thread_mode=ThreadMode.CONSTRAINED,
        plan=MeasurementPlan(warmup=1, runs=2, timeout_s=10),
    )
    assert isinstance(result, RunResult)
    assert result.result_class == ResultClass.FAILED_ENV


@pytest.mark.skipif(not _HAS_ZUBAN, reason="zuban not installed")
def test_live_clean_project(tmp_path: Path, fixtures_dir: Path) -> None:
    cfg = NormalizedConfig(src_roots=(str(fixtures_dir / "clean_project"),))
    argv, env = ZubanAdapter().command("clean", cfg, ThreadMode.CONSTRAINED, tmp_path)
    raw = run_command(argv, timeout=120, env=env)
    assert ZubanAdapter().classify(raw) == ResultClass.CLEAN, raw.stdout + raw.stderr
    diags, _files = ZubanAdapter().parse(raw.stdout, raw.stderr, raw.exit_code)
    assert diags == 0


@pytest.mark.skipif(not _HAS_ZUBAN, reason="zuban not installed")
def test_live_error_project(tmp_path: Path, fixtures_dir: Path) -> None:
    cfg = NormalizedConfig(src_roots=(str(fixtures_dir / "error_project"),))
    argv, env = ZubanAdapter().command("err", cfg, ThreadMode.CONSTRAINED, tmp_path)
    raw = run_command(argv, timeout=120, env=env)
    assert ZubanAdapter().classify(raw) == ResultClass.DIAGNOSTICS, raw.stdout + raw.stderr
    diags, _ = ZubanAdapter().parse(raw.stdout, raw.stderr, raw.exit_code)
    assert diags is not None and diags > 0


@pytest.mark.skipif(not _HAS_ZUBAN, reason="zuban not installed")
def test_live_default_mode_checks_untyped_def_bodies(tmp_path: Path) -> None:
    # analyze_untyped_defs is satisfied NATIVELY by default mode; the [mypy] config
    # must NOT flip zuban into mypy mode (which skips untyped bodies). An untyped
    # function with a body type error must therefore surface as DIAGNOSTICS.
    src = tmp_path / "proj"
    src.mkdir()
    (src / "__init__.py").write_text("")
    (src / "m.py").write_text("def f():\n    x: int = 'oops'\n    return x\n")
    workdir = tmp_path / "work"
    workdir.mkdir()
    cfg = NormalizedConfig(src_roots=(str(src),))
    argv, env = ZubanAdapter().command("untyped", cfg, ThreadMode.CONSTRAINED, workdir)
    raw = run_command(argv, timeout=120, env=env)
    body = raw.stdout + raw.stderr
    assert "annotation-unchecked" not in body, body  # the mypy-mode tell
    assert ZubanAdapter().classify(raw) == ResultClass.DIAGNOSTICS, body


@pytest.mark.skipif(not _HAS_ZUBAN, reason="zuban not installed")
def test_live_first_party_package_imports_resolve(tmp_path: Path) -> None:
    # The package dir IS the src_root and its modules import each other (absolute
    # first-party). zuban must resolve these via the mypy_path = parent instead of
    # emitting spurious import-not-found. Guards the mypy_path fix end to end.
    pkg = tmp_path / "proj" / "mypkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("from mypkg.sub import VALUE\n\nx: int = VALUE\n")
    (pkg / "sub.py").write_text("VALUE: int = 1\n")
    workdir = tmp_path / "work"
    workdir.mkdir()
    cfg = NormalizedConfig(src_roots=(str(pkg),))
    argv, env = ZubanAdapter().command("fp", cfg, ThreadMode.CONSTRAINED, workdir)
    raw = run_command(argv, timeout=120, env=env)
    body = raw.stdout + raw.stderr
    assert "import-not-found" not in body, body
    assert ZubanAdapter().classify(raw) == ResultClass.CLEAN, body
