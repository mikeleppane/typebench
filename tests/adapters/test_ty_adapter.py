import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from typebench._internal.test_fakes import FakeHost, fake_raw
from typebench.adapters.base import Adapter
from typebench.adapters.ty import TyAdapter
from typebench.contracts.config import MeasurementPlan, NormalizedConfig
from typebench.contracts.models import ResultClass, RunResult, ThreadMode
from typebench.engine.collector import run_single
from typebench.engine.wrapper import RawRun, run_command

_HAS_TY = shutil.which("ty") is not None


def test_ty_is_an_adapter() -> None:
    assert isinstance(TyAdapter(), Adapter)


def test_parse_diagnostics_from_stdout_and_files_from_stderr() -> None:
    stdout = "error[...]: ...\nFound 3 diagnostics\n"
    stderr = "INFO Indexed 7 file(s)\n"
    assert TyAdapter().parse(stdout, stderr, 1) == (3, 7)


def test_parse_clean_with_indexed_files() -> None:
    assert TyAdapter().parse("All checks passed!\n", "INFO Indexed 5 file(s)\n", 0) == (0, 5)


def test_parse_files_none_when_no_verbose_line() -> None:
    # Without the -v "Indexed N" line the files count is unknowable -> None (tolerated).
    assert TyAdapter().parse("All checks passed!\n", "", 0) == (0, None)


def test_parse_singular_diagnostic_and_file() -> None:
    assert TyAdapter().parse("Found 1 diagnostic\n", "INFO Indexed 1 file(s)\n", 1) == (1, 1)


def test_parse_is_graceful_on_garbage() -> None:
    assert TyAdapter().parse("???", "???", 1) == (None, None)


def test_classify_exit_map() -> None:
    a = TyAdapter()
    clean = "All checks passed!\n"
    assert (
        a.classify(RawRun(0, None, False, False, clean, "INFO Indexed 4 file(s)\n"))
        == ResultClass.CLEAN
    )
    assert (
        a.classify(RawRun(1, None, False, False, "Found 2 diagnostics\n", ""))
        == ResultClass.DIAGNOSTICS
    )
    assert a.classify(RawRun(2, None, False, False, "", "")) == ResultClass.FAILED_ENV
    assert a.classify(RawRun(101, None, False, False, "", "")) == ResultClass.FAILED_CRASH
    assert a.classify(RawRun(0, None, True, False, clean, "")) == ResultClass.FAILED_TIMEOUT


def test_classify_clean_without_files_count_stays_clean() -> None:
    # ty's files count is best-effort (stderr -v). exit 0 + files None is NOT
    # promoted to env (unlike pyright/mypy, whose counts are reliable). Only a
    # CONFIRMED 0 is a false-clean.
    assert (
        TyAdapter().classify(RawRun(0, None, False, False, "All checks passed!\n", ""))
        == ResultClass.CLEAN
    )


def test_classify_zero_indexed_files_on_exit0_is_env_failure() -> None:
    raw = RawRun(0, None, False, False, "All checks passed!\n", "INFO Indexed 0 file(s)\n")
    assert TyAdapter().classify(raw) == ResultClass.FAILED_ENV


def test_command_writes_ty_toml_and_builds_argv(tmp_path: Path) -> None:
    cfg = NormalizedConfig(
        src_roots=("/abs/src",), python_version="3.11", venv_python="/v/bin/python"
    )
    argv, env = TyAdapter().command("demo", cfg, ThreadMode.CONSTRAINED, tmp_path)
    assert env == {"TY_MAX_PARALLELISM": "1"}  # 1-core soft cap
    cfg_path = tmp_path / "ty.toml"
    assert "--config-file" in argv and str(cfg_path) in argv
    written = tomllib.loads(cfg_path.read_text())
    assert written["environment"]["python-version"] == "3.11"
    assert written["environment"]["python-platform"] == "linux"
    # §6 excludes rendered into [src].exclude (gitignore-style, project-anchored).
    assert written["src"]["exclude"]
    assert any("tests" in e for e in written["src"]["exclude"])
    assert argv[0] == "ty" and argv[1] == "check"
    assert "/abs/src" in argv
    assert "--force-exclude" in argv  # excludes MUST apply to command-line paths
    assert "--python" in argv and "/v/bin/python" in argv
    assert "--output-format" in argv and "concise" in argv
    assert "-v" in argv  # needed for the Indexed-files count
    assert "--color" in argv and "never" in argv


def test_command_constrained_scales_cap_to_cores(tmp_path: Path) -> None:
    # The constrained soft cap tracks --cores N, not a hardcoded 1.
    cfg = NormalizedConfig(src_roots=("/abs/src",), cores=4)
    _argv, env = TyAdapter().command("demo", cfg, ThreadMode.CONSTRAINED, tmp_path)
    assert env == {"TY_MAX_PARALLELISM": "4"}


def test_command_all_cores_omits_parallelism_cap(tmp_path: Path) -> None:
    cfg = NormalizedConfig(src_roots=("/abs/src",), cores=4)  # cores ignored for ALL_CORES
    _argv, env = TyAdapter().command("demo", cfg, ThreadMode.ALL_CORES, tmp_path)
    assert "TY_MAX_PARALLELISM" not in env  # all cores -> no cap


def test_parallelism_cap_is_soft() -> None:
    cap = TyAdapter().parallelism_cap(ThreadMode.CONSTRAINED, 1)
    assert cap.hard_cap is False


def test_version_is_no_raise_when_binary_absent() -> None:
    host = FakeHost({("ty", "--version"): fake_raw(stderr="missing", env_error=True)})

    assert TyAdapter(host=host).version() == "unknown"


def test_missing_ty_yields_schema_valid_failed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    cfg = NormalizedConfig(src_roots=("/abs/src",))
    result = run_single(
        TyAdapter(),
        project="demo",
        config=cfg,
        thread_mode=ThreadMode.CONSTRAINED,
        plan=MeasurementPlan(warmup=1, runs=2, timeout_s=10),
    )
    assert isinstance(result, RunResult)
    assert result.result_class == ResultClass.FAILED_ENV


@pytest.mark.skipif(not _HAS_TY, reason="ty not installed")
def test_live_clean_project(tmp_path: Path, fixtures_dir: Path) -> None:
    cfg = NormalizedConfig(src_roots=(str(fixtures_dir / "clean_project"),))
    argv, env = TyAdapter().command("clean", cfg, ThreadMode.CONSTRAINED, tmp_path)
    raw = run_command(argv, timeout=120, env=env)
    assert TyAdapter().classify(raw) == ResultClass.CLEAN
    diags, _files = TyAdapter().parse(raw.stdout, raw.stderr, raw.exit_code)
    assert diags == 0


@pytest.mark.skipif(not _HAS_TY, reason="ty not installed")
def test_live_error_project(tmp_path: Path, fixtures_dir: Path) -> None:
    cfg = NormalizedConfig(src_roots=(str(fixtures_dir / "error_project"),))
    argv, env = TyAdapter().command("err", cfg, ThreadMode.CONSTRAINED, tmp_path)
    raw = run_command(argv, timeout=120, env=env)
    assert TyAdapter().classify(raw) == ResultClass.DIAGNOSTICS
    diags, _ = TyAdapter().parse(raw.stdout, raw.stderr, raw.exit_code)
    assert diags is not None and diags > 0


def test_command_disables_ignore_file_filtering() -> None:
    # Regression: ty respects a project's .gitignore by default, so first-party
    # files a real (git) project ignores would be SKIPPED by ty while the other
    # tools analyze them -> non-neutral file set / false-clean. The normalized
    # file set must derive only from src_roots + exclude_globs.
    cfg = NormalizedConfig(src_roots=("/abs/src",))
    argv, _env = TyAdapter().command("demo", cfg, ThreadMode.CONSTRAINED, Path("/tmp"))
    assert "--no-respect-ignore-files" in argv


def test_command_adds_parent_as_first_party_search_path(tmp_path: Path) -> None:
    # Regression: ty resolves FIRST-PARTY modules from its search paths, not the
    # checked path. When a src_root IS the package dir, ty needs the PARENT on the
    # search path to map <pkg>/x.py -> module <pkg>.x; without it ty fails the
    # project's OWN imports (flood of spurious unresolved-import, far less real
    # work -> non-neutral). Each src_root's parent must be an --extra-search-path.
    cfg = NormalizedConfig(src_roots=("/abs/repo/httpx",))
    argv, _env = TyAdapter().command("demo", cfg, ThreadMode.CONSTRAINED, tmp_path)
    assert "--extra-search-path" in argv
    assert argv[argv.index("--extra-search-path") + 1] == "/abs/repo"


def test_command_dedupes_search_paths_across_roots(tmp_path: Path) -> None:
    # Multiple roots sharing a parent yield ONE search path; distinct parents both
    # appear, in first-seen order.
    cfg = NormalizedConfig(src_roots=("/abs/repo/a", "/abs/repo/b", "/abs/other/c"))
    argv, _env = TyAdapter().command("demo", cfg, ThreadMode.CONSTRAINED, tmp_path)
    paths = [argv[i + 1] for i, a in enumerate(argv) if a == "--extra-search-path"]
    assert paths == ["/abs/repo", "/abs/other"]


@pytest.mark.skipif(not _HAS_TY, reason="ty not installed")
def test_live_first_party_package_imports_resolve(tmp_path: Path) -> None:
    # The package dir IS the src_root and its modules import each other (absolute
    # first-party). ty must resolve these via the parent search path instead of
    # emitting spurious unresolved-import. Guards the search-path fix end to end.
    pkg = tmp_path / "proj" / "mypkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("from mypkg.sub import VALUE\n\nx: int = VALUE\n")
    (pkg / "sub.py").write_text("VALUE: int = 1\n")
    workdir = tmp_path / "work"
    workdir.mkdir()
    cfg = NormalizedConfig(src_roots=(str(pkg),))
    argv, env = TyAdapter().command("fp", cfg, ThreadMode.CONSTRAINED, workdir)
    raw = run_command(argv, timeout=120, env=env)
    body = raw.stdout + raw.stderr
    assert "unresolved-import" not in body, body
    assert TyAdapter().classify(raw) == ResultClass.CLEAN, body


@pytest.mark.skipif(not _HAS_TY, reason="ty not installed")
def test_live_gitignored_first_party_file_is_still_checked(tmp_path: Path) -> None:
    # In a real git repo, a .gitignore'd source file with a type error must STILL
    # be flagged (ty default would skip it -> false clean). Live guard for the
    # ignore-file neutrality fix.
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text("bad: str = 123\n")
    (tmp_path / ".gitignore").write_text("src/m.py\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    workdir = tmp_path / "work"
    workdir.mkdir()
    cfg = NormalizedConfig(src_roots=(str(src),))
    argv, env = TyAdapter().command("gi", cfg, ThreadMode.CONSTRAINED, workdir)
    raw = run_command(argv, timeout=120, env=env)
    assert TyAdapter().classify(raw) == ResultClass.DIAGNOSTICS, raw.stdout + raw.stderr
    diags, _files = TyAdapter().parse(raw.stdout, raw.stderr, raw.exit_code)
    assert diags is not None and diags > 0
