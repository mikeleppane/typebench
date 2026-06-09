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
    argv, env = MypyAdapter().command("demo", cfg, ThreadMode.CONSTRAINED, tmp_path)
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
    argv, _env = MypyAdapter().command("demo", cfg, ThreadMode.CONSTRAINED, tmp_path)
    assert "--python-executable" not in argv


def _pin_version(monkeypatch: pytest.MonkeyPatch, version: str) -> None:
    monkeypatch.setattr(MypyAdapter, "version", lambda _self: version)


def test_constrained_default_cores_omits_num_workers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # cores=1 (default) must stay single-process: no -n flag, no cache, byte-identical
    # to the pre-parallel command even on a parallel-capable mypy.
    _pin_version(monkeypatch, "mypy 2.1.0 (compiled: yes)")
    cfg = NormalizedConfig(src_roots=("/abs/src",))  # cores defaults to 1
    argv, _env = MypyAdapter().command("demo", cfg, ThreadMode.CONSTRAINED, tmp_path)
    assert "--num-workers" not in argv
    assert "--no-incremental" in argv
    assert "--cache-dir=/dev/null" in argv


def test_constrained_multi_core_sets_num_workers_and_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Parallel mode REQUIRES a real cache: -n N present, /dev/null + --no-incremental
    # gone, a writable --cache-dir present (mypy errors "Cache must be enabled in
    # parallel mode" otherwise).
    _pin_version(monkeypatch, "mypy 2.1.0 (compiled: yes)")
    cfg = NormalizedConfig(src_roots=("/abs/src",), cores=4)
    argv, _env = MypyAdapter().command("demo", cfg, ThreadMode.CONSTRAINED, tmp_path)
    idx = argv.index("--num-workers")
    assert argv[idx + 1] == "4"
    assert "--no-incremental" not in argv
    assert "--cache-dir=/dev/null" not in argv
    cache_idx = argv.index("--cache-dir")
    assert argv[cache_idx + 1] == mypy_mod._cache_dir("demo")


def test_clear_cache_wipes_parallel_cache_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []
    monkeypatch.setattr(mypy_mod.shutil, "rmtree", lambda p, **_kw: captured.append(p))
    MypyAdapter().clear_cache("demo")
    assert captured == [mypy_mod._cache_dir("demo")]


def test_prepare_command_wipes_parallel_cache_dir() -> None:
    cmd = MypyAdapter().prepare_command("demo")
    assert cmd is not None
    assert mypy_mod._cache_dir("demo") in cmd
    assert cmd.startswith("rm -rf ")


def test_all_cores_uses_cpu_count(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _pin_version(monkeypatch, "mypy 2.1.0 (compiled: yes)")
    monkeypatch.setattr(mypy_mod.os, "cpu_count", lambda: 6)
    cfg = NormalizedConfig(src_roots=("/abs/src",))  # cores irrelevant for ALL_CORES
    argv, _env = MypyAdapter().command("demo", cfg, ThreadMode.ALL_CORES, tmp_path)
    idx = argv.index("--num-workers")
    assert argv[idx + 1] == "6"


def test_pre_2_0_mypy_never_passes_num_workers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # --num-workers does not exist before 2.0; passing it would hard-fail the run.
    _pin_version(monkeypatch, "mypy 1.13.0 (compiled: yes)")
    cfg = NormalizedConfig(src_roots=("/abs/src",), cores=4)
    argv, _env = MypyAdapter().command("demo", cfg, ThreadMode.ALL_CORES, tmp_path)
    assert "--num-workers" not in argv


def test_unparsable_version_disables_parallel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _pin_version(monkeypatch, "unknown")
    cfg = NormalizedConfig(src_roots=("/abs/src",), cores=4)
    argv, _env = MypyAdapter().command("demo", cfg, ThreadMode.ALL_CORES, tmp_path)
    assert "--num-workers" not in argv


def test_parallelism_cap_mechanism_is_version_aware(monkeypatch: pytest.MonkeyPatch) -> None:
    _pin_version(monkeypatch, "mypy 2.1.0 (compiled: yes)")
    cap = MypyAdapter().parallelism_cap(ThreadMode.CONSTRAINED)
    assert cap.hard_cap is True
    assert "--num-workers" in cap.mechanism
    _pin_version(monkeypatch, "mypy 1.13.0 (compiled: yes)")
    cap_old = MypyAdapter().parallelism_cap(ThreadMode.CONSTRAINED)
    assert cap_old.hard_cap is True
    assert "single-process" in cap_old.mechanism


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
        thread_mode=ThreadMode.CONSTRAINED,
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
    argv, env = MypyAdapter().command("clean", cfg, ThreadMode.CONSTRAINED, tmp_path)
    raw = run_command(argv, timeout=120, env=env)
    assert MypyAdapter().classify(raw) == ResultClass.CLEAN
    diags, files = MypyAdapter().parse(raw.stdout, raw.stderr, raw.exit_code)
    assert diags == 0
    assert files is not None and files > 0


@pytest.mark.skipif(not _HAS_MYPY, reason="mypy not installed")
def test_live_error_project(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=(str(_FIXTURES / "error_project"),))
    argv, env = MypyAdapter().command("err", cfg, ThreadMode.CONSTRAINED, tmp_path)
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
