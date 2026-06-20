import shutil
from pathlib import Path

import pytest

from typebench.adapters.base import Adapter
from typebench.adapters.mypy import MypyAdapter
from typebench.adapters.pyrefly import PyreflyAdapter
from typebench.adapters.pyright import PyrightAdapter
from typebench.adapters.ty import TyAdapter
from typebench.adapters.zuban import ZubanAdapter
from typebench.contracts.config import NormalizedConfig
from typebench.contracts.models import ResultClass, ThreadMode
from typebench.engine.wrapper import run_command

# (name, adapter, discovered-config filename, body that WOULD hide all diagnostics
# if the tool honored the project's own config instead of our generated one).
_CASES: list[tuple[str, Adapter, str, str]] = [
    ("mypy", MypyAdapter(), "mypy.ini", "[mypy]\nignore_errors = True\n"),
    ("ty", TyAdapter(), "ty.toml", '[src]\nexclude = ["**/*.py"]\n'),
    ("pyrefly", PyreflyAdapter(), "pyrefly.toml", 'project-excludes = ["**/*.py"]\n'),
    ("pyright", PyrightAdapter(), "pyrightconfig.json", '{"typeCheckingMode": "off"}\n'),
    ("zuban", ZubanAdapter(), "mypy.ini", "[mypy]\nignore_errors = True\n"),
]
_IDS = [c[0] for c in _CASES]


def _classify(adapter: Adapter, src_root: Path, workdir: Path) -> ResultClass:
    cfg = NormalizedConfig(src_roots=(str(src_root),))
    argv, env = adapter.command("neutral", cfg, ThreadMode.CONSTRAINED, workdir)
    raw = run_command(argv, timeout=120, env=env)
    return adapter.classify(raw)


@pytest.mark.parametrize(("name", "adapter", "_fn", "_body"), _CASES, ids=_IDS)
def test_excludes_drop_nested_tests_dir(
    name: str,
    adapter: Adapter,
    _fn: str,
    _body: str,
    tmp_path: Path,
    fixtures_dir: Path,
) -> None:
    if shutil.which(name) is None:
        pytest.skip(f"{name} not installed")
    # exclude_project = clean ok.py + a type error under tests/. The §6 excludes
    # MUST drop **/tests/** so the error never surfaces -> CLEAN. A broken exclusion
    # (e.g. ty without --force-exclude) checks tests/broken.py -> DIAGNOSTICS, which
    # fails here. This is the live regression guard for the exclude contract.
    result = _classify(adapter, fixtures_dir / "exclude_project", tmp_path)
    assert result == ResultClass.CLEAN, f"{name} did not exclude tests/ (got {result})"


@pytest.mark.parametrize(("name", "adapter", "filename", "body"), _CASES, ids=_IDS)
def test_hostile_project_config_is_suppressed(
    name: str,
    adapter: Adapter,
    filename: str,
    body: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if shutil.which(name) is None:
        pytest.skip(f"{name} not installed")
    # A project with a REAL type error plus a hostile config that would silence it
    # if discovered. cwd is set to the project so each tool's normal config
    # discovery (cwd / walk-up) WOULD find the hostile file — making the test
    # non-trivial. Our adapter suppresses the project config (mypy --config-file=,
    # ty/pyrefly/pyright explicit generated config), so the error MUST still be
    # reported -> DIAGNOSTICS. If the tool honored the hostile config we'd get
    # CLEAN -> neutrality breach -> failure here.
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "__init__.py").write_text("")
    (proj / "broken.py").write_text("bad: str = 123\n")
    (proj / filename).write_text(body)
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(proj)  # so discovery WOULD find the hostile config absent suppression
    result = _classify(adapter, proj, workdir)
    assert result == ResultClass.DIAGNOSTICS, (
        f"{name} appears to have honored {filename} (got {result}); project config not suppressed"
    )
