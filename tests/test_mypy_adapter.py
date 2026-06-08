import shutil
from pathlib import Path

import pytest

from typebench.adapters import mypy as mypy_mod
from typebench.adapters.base import Adapter
from typebench.adapters.mypy import MypyAdapter
from typebench.collector import run_single
from typebench.models import ResultClass, RunResult, ThreadMode
from typebench.normalized_config import NormalizedConfig
from typebench.wrapper import RawRun, run_command

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_HAS_MYPY = shutil.which("mypy") is not None


def test_mypy_is_an_adapter() -> None:
    assert isinstance(MypyAdapter(), Adapter)


def test_parse_reads_text_summary_with_errors() -> None:
    out = "sample.py:5: error: ...\nFound 2 errors in 1 file (checked 3 source files)\n"
    assert MypyAdapter().parse(out, "", 1) == (2, 3)


def test_parse_reads_clean_summary() -> None:
    out = "Success: no issues found in 4 source files\n"
    assert MypyAdapter().parse(out, "", 0) == (0, 4)


def test_parse_singular_forms() -> None:
    out = "Found 1 error in 1 file (checked 1 source file)\n"
    assert MypyAdapter().parse(out, "", 1) == (1, 1)


def test_parse_is_graceful_on_garbage() -> None:
    assert MypyAdapter().parse("total nonsense", "", 1) == (None, None)


def test_classify_clean_requires_positive_files() -> None:
    a = MypyAdapter()
    clean = "Success: no issues found in 3 source files\n"
    assert a.classify(RawRun(0, None, False, False, clean, "")) == ResultClass.CLEAN


def test_classify_diagnostics() -> None:
    out = "Found 2 errors in 1 file (checked 3 source files)\n"
    assert MypyAdapter().classify(RawRun(1, None, False, False, out, "")) == ResultClass.DIAGNOSTICS


def test_classify_exit2_usage_is_env_failure() -> None:
    # exit 2 = usage / unreadable target / bad config -> failed{env}.
    raw = RawRun(2, None, False, False, "", "mypy: error: Missing target")
    assert MypyAdapter().classify(raw) == ResultClass.FAILED_ENV


def test_classify_exit2_internal_error_is_crash() -> None:
    # exit 2 WITH "INTERNAL ERROR" is a mypy crash, NOT an env failure.
    raw = RawRun(2, None, False, False, "sample.py: error: INTERNAL ERROR -- ...", "")
    assert MypyAdapter().classify(raw) == ResultClass.FAILED_CRASH


def test_classify_zero_files_on_exit0_is_env_failure() -> None:
    # exit 0 but checked 0 files = mis-scoped target, not a clean project.
    raw = RawRun(0, None, False, False, "Success: no issues found in 0 source files\n", "")
    assert MypyAdapter().classify(raw) == ResultClass.FAILED_ENV


def test_command_builds_first_party_only_argv(tmp_path: Path) -> None:
    cfg = NormalizedConfig(
        src_roots=("/abs/src",), python_version="3.11", venv_python="/v/bin/python"
    )
    argv, env = MypyAdapter().command("demo", cfg, ThreadMode.ONE_CORE, tmp_path)
    assert env == {}
    assert argv[0] == "mypy"
    assert "--config-file=" in argv  # suppress project config
    assert "--follow-imports=silent" in argv  # resolve deps, report first-party only
    assert "--check-untyped-defs" in argv  # analyze all bodies (§6)
    assert "--no-incremental" in argv
    assert "--cache-dir=/dev/null" in argv  # write no cache (cold)
    assert "--python-version" in argv and "3.11" in argv
    assert "--platform" in argv and "linux" in argv
    assert "--python-executable" in argv and "/v/bin/python" in argv
    assert "/abs/src" in argv  # src root analyzed (absolute, mypy accepts it)
    # exclude is a REGEX for mypy, matching the §6 excluded dir names.
    exclude_idx = argv.index("--exclude")
    assert "tests" in argv[exclude_idx + 1]


def test_command_no_venv_omits_python_executable(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=("/abs/src",))
    argv, _env = MypyAdapter().command("demo", cfg, ThreadMode.ONE_CORE, tmp_path)
    assert "--python-executable" not in argv


def test_version_is_no_raise_when_binary_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_k: object) -> object:
        raise FileNotFoundError("mypy")

    monkeypatch.setattr(mypy_mod.subprocess, "run", _boom)
    assert MypyAdapter().version() == "unknown"


def test_missing_mypy_yields_schema_valid_failed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    cfg = NormalizedConfig(src_roots=("/abs/src",))
    result = run_single(
        MypyAdapter(),
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


@pytest.mark.skipif(not _HAS_MYPY, reason="mypy not installed")
def test_live_clean_project(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=(str(_FIXTURES / "clean_project"),))
    argv, env = MypyAdapter().command("clean", cfg, ThreadMode.ONE_CORE, tmp_path)
    raw = run_command(argv, timeout=120, env=env)
    assert MypyAdapter().classify(raw) == ResultClass.CLEAN
    diags, files = MypyAdapter().parse(raw.stdout, raw.stderr, raw.exit_code)
    assert diags == 0
    assert files is not None and files > 0


@pytest.mark.skipif(not _HAS_MYPY, reason="mypy not installed")
def test_live_error_project(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=(str(_FIXTURES / "error_project"),))
    argv, env = MypyAdapter().command("err", cfg, ThreadMode.ONE_CORE, tmp_path)
    raw = run_command(argv, timeout=120, env=env)
    assert MypyAdapter().classify(raw) == ResultClass.DIAGNOSTICS
    diags, _ = MypyAdapter().parse(raw.stdout, raw.stderr, raw.exit_code)
    assert diags is not None and diags > 0


@pytest.mark.skipif(not _HAS_MYPY, reason="mypy not installed")
def test_install_records_compiled_flag() -> None:
    # Reproducibility (§9): the default PyPI mypy wheel is mypyc-compiled and its
    # --version ends with "(compiled: yes)". The lock manifest depends on this, so
    # a NON-compiled mypy in the gate is a real reproducibility regression the test
    # must catch — assert the marker, not merely "not unknown". (If a future build
    # ships non-compiled by design, downgrade this to a warning then, deliberately.)
    info = MypyAdapter().install()
    assert info and info != "unknown"
    assert "(compiled: yes)" in info, (
        f"expected mypyc-compiled mypy for §9 reproducibility, got: {info!r}"
    )
