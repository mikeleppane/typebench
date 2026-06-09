import json
import shutil
import tomllib
from pathlib import Path

import pytest

from typebench.adapters import pyrefly as pyrefly_mod
from typebench.adapters.base import Adapter
from typebench.adapters.pyrefly import PyreflyAdapter
from typebench.collector import run_single
from typebench.models import ResultClass, RunResult, ThreadMode
from typebench.normalized_config import NormalizedConfig
from typebench.wrapper import RawRun, run_command

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_HAS_PYREFLY = shutil.which("pyrefly") is not None


def test_pyrefly_is_an_adapter() -> None:
    assert isinstance(PyreflyAdapter(), Adapter)


def test_parse_counts_only_error_severity() -> None:
    # The errors[] array includes non-error directives (e.g. reveal_type info);
    # diagnostics = count of severity == "error" only.
    blob = json.dumps(
        {
            "errors": [
                {"severity": "error", "name": "bad-assignment"},
                {"severity": "error", "name": "bad-argument"},
                {"severity": "info", "name": "reveal-type"},
            ]
        }
    )
    stderr = "INFO 3 modules\n"
    assert PyreflyAdapter().parse(blob, stderr, 1) == (2, 3)


def test_parse_clean_zero_errors() -> None:
    assert PyreflyAdapter().parse(json.dumps({"errors": []}), "INFO 5 modules\n", 0) == (0, 5)


def test_parse_counts_singular_module() -> None:
    # pyrefly prints "1 module" (singular) for a one-module project; the regex
    # must tolerate it or single-module fixtures parse files=None (then false-clean
    # detection silently weakens). Locally observed on pyrefly 1.0.0.
    assert PyreflyAdapter().parse(json.dumps({"errors": []}), "INFO 1 module\n", 0) == (0, 1)


def test_parse_files_none_without_summary() -> None:
    assert PyreflyAdapter().parse(json.dumps({"errors": []}), "", 0) == (0, None)


def test_parse_is_graceful_on_garbage() -> None:
    assert PyreflyAdapter().parse("not json", "", 1) == (None, None)


def test_classify_exit1_with_parseable_errors_is_diagnostics() -> None:
    blob = json.dumps({"errors": [{"severity": "error", "name": "x"}]})
    raw = RawRun(1, None, False, False, blob, "INFO 2 modules\n")
    assert PyreflyAdapter().classify(raw) == ResultClass.DIAGNOSTICS


def test_classify_exit1_without_parseable_json_is_env_failure() -> None:
    # exit 1 is overloaded: diagnostics OR fatal config/IO. No parseable JSON ->
    # it was a fatal config error, NOT diagnostics -> failed{env}.
    raw = RawRun(
        1, None, False, False, "Fatal configuration error", "error finding Python interpreter"
    )
    assert PyreflyAdapter().classify(raw) == ResultClass.FAILED_ENV


def test_classify_clean_zero_modules_is_env_failure() -> None:
    raw = RawRun(0, None, False, False, json.dumps({"errors": []}), "INFO 0 modules\n")
    assert PyreflyAdapter().classify(raw) == ResultClass.FAILED_ENV


def test_classify_exit3_is_env_and_101_is_crash() -> None:
    a = PyreflyAdapter()
    assert a.classify(RawRun(3, None, False, False, "", "")) == ResultClass.FAILED_ENV
    assert a.classify(RawRun(101, None, False, False, "", "")) == ResultClass.FAILED_CRASH


def test_command_writes_pyrefly_toml_with_default_preset(tmp_path: Path) -> None:
    cfg = NormalizedConfig(
        src_roots=("/abs/src",), python_version="3.11", venv_python="/v/bin/python"
    )
    argv, env = PyreflyAdapter().command("demo", cfg, ThreadMode.CONSTRAINED, tmp_path)
    assert env == {}
    cfg_path = tmp_path / "pyrefly.toml"
    assert "--config" in argv and str(cfg_path) in argv
    written = tomllib.loads(cfg_path.read_text())
    assert written["preset"] == "default"  # stock-neutral, NOT basic/strict
    assert written["project-includes"] == ["/abs/src"]
    assert written["python-version"] == "3.11"
    assert written["python-platform"] == "linux"
    assert written["check-unannotated-defs"] is True
    assert written["python-interpreter-path"] == "/v/bin/python"
    assert "--output-format" in argv and "json" in argv
    assert "--summary=full" in argv
    assert "--threads" in argv and "1" in argv  # 1-core HARD cap
    assert "--check-all" not in argv and "-a" not in argv  # would report deps too


def test_command_all_cores_omits_threads(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=("/abs/src",))
    argv, _env = PyreflyAdapter().command("demo", cfg, ThreadMode.ALL_CORES, tmp_path)
    assert "--threads" not in argv


def test_parallelism_cap_is_hard() -> None:
    assert PyreflyAdapter().parallelism_cap(ThreadMode.CONSTRAINED).hard_cap is True


def test_version_is_no_raise_when_binary_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_k: object) -> object:
        raise FileNotFoundError("pyrefly")

    monkeypatch.setattr(pyrefly_mod.subprocess, "run", _boom)
    assert PyreflyAdapter().version() == "unknown"


def test_missing_pyrefly_yields_schema_valid_failed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    cfg = NormalizedConfig(src_roots=("/abs/src",))
    result = run_single(
        PyreflyAdapter(),
        project="demo",
        config=cfg,
        thread_mode=ThreadMode.CONSTRAINED,
        warmup=1,
        runs=2,
        timeout=10,
    )
    assert isinstance(result, RunResult)
    assert result.result_class == ResultClass.FAILED_ENV


@pytest.mark.skipif(not _HAS_PYREFLY, reason="pyrefly not installed")
def test_live_clean_project(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=(str(_FIXTURES / "clean_project"),))
    argv, env = PyreflyAdapter().command("clean", cfg, ThreadMode.CONSTRAINED, tmp_path)
    raw = run_command(argv, timeout=120, env=env)
    assert PyreflyAdapter().classify(raw) == ResultClass.CLEAN
    diags, files = PyreflyAdapter().parse(raw.stdout, raw.stderr, raw.exit_code)
    assert diags == 0
    # The --summary=full "N module(s)" line must parse on the real tool, else the
    # false-clean guard silently degrades to "tolerate None". Assert it works live.
    assert files is not None and files > 0


@pytest.mark.skipif(not _HAS_PYREFLY, reason="pyrefly not installed")
def test_live_error_project(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=(str(_FIXTURES / "error_project"),))
    argv, env = PyreflyAdapter().command("err", cfg, ThreadMode.CONSTRAINED, tmp_path)
    raw = run_command(argv, timeout=120, env=env)
    assert PyreflyAdapter().classify(raw) == ResultClass.DIAGNOSTICS
    diags, files = PyreflyAdapter().parse(raw.stdout, raw.stderr, raw.exit_code)
    assert diags is not None and diags > 0
    assert files is not None and files > 0


def test_command_sets_search_path_to_src_roots(tmp_path: Path) -> None:
    # Regression: the generated config lives in the run-scoped workdir, so without
    # an explicit search-path pyrefly infers its import root from /tmp and a
    # src-layout project's first-party imports become spurious `missing-import`
    # diagnostics (neutrality leak). search-path must be pinned to the src roots.
    cfg = NormalizedConfig(src_roots=("/abs/src",))
    PyreflyAdapter().command("demo", cfg, ThreadMode.CONSTRAINED, tmp_path)
    written = tomllib.loads((tmp_path / "pyrefly.toml").read_text())
    assert written["search-path"] == ["/abs/src"]


@pytest.mark.skipif(not _HAS_PYREFLY, reason="pyrefly not installed")
def test_live_resolves_first_party_imports_in_src_layout(tmp_path: Path) -> None:
    # pkg_project is a clean src-layout package whose pkg/b.py does
    # `from pkg.a import X`. With search-path pinned to the src root this resolves
    # and the project is CLEAN; without the fix pyrefly reports `missing-import`
    # (DIAGNOSTICS) purely because the config sits in a temp dir. Live guard for
    # the import-root neutrality fix.
    cfg = NormalizedConfig(src_roots=(str(_FIXTURES / "pkg_project"),))
    argv, env = PyreflyAdapter().command("pkg", cfg, ThreadMode.CONSTRAINED, tmp_path)
    raw = run_command(argv, timeout=120, env=env)
    assert PyreflyAdapter().classify(raw) == ResultClass.CLEAN, raw.stdout + raw.stderr
