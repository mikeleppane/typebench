"""Golden snapshots of each adapter's standard-posture command output.

Locks the exact argv + env + generated config-file text BEFORE the posture
refactor, so the refactor is verified byte-identical, not hopefully-equivalent.

Determinism contract (why this is portable, not just "works on my machine"):
- Every path lives under a single per-test root (`tmp_path`); the snapshot
  collapses that root to `<ROOT>`. workdir AND src sit at FIXED relative depth
  under the root, so pyright's workdir-relative `include`/`exclude` render to a
  stable `../project/src` regardless of where the OS placed tmp_path. (A raw
  absolute src + tmp_path workdir would leak machine-specific `../` walk-up
  *depth* into the golden via os.path.relpath -> fails on a different $TMPDIR.)
- CONSTRAINED + cores=1 is the captured mode on purpose: it avoids os.cpu_count()
  and mypy's workers>1 cache-dir branch, both non-deterministic. Posture is
  thread-independent, so one mode fully covers the posture surface under test.
- mypy.command() probes `mypy --version` to size --num-workers; we monkeypatch
  it to a fixed string so capture is fully OFFLINE — no toolchain dependency,
  no subprocess, no flake. (At cores=1 the worker branch is never taken, so the
  value cannot affect argv anyway.)

Regenerate intentionally with TB_UPDATE_GOLDENS=1 (review the diff in review).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from typebench.adapters.mypy import MypyAdapter
from typebench.adapters.pyrefly import PyreflyAdapter
from typebench.adapters.pyright import PyrightAdapter
from typebench.adapters.ty import TyAdapter
from typebench.adapters.zuban import ZubanAdapter
from typebench.contracts.config import NormalizedConfig
from typebench.contracts.taxonomy import ThreadMode  # AGENTS.md: enums from taxonomy

_GOLDENS = Path(__file__).parent / "goldens"
_ROOT_PLACEHOLDER = "<ROOT>"

# Pinned so MypyAdapter.command() never shells out during capture (see docstring).
_FAKE_MYPY_VERSION = "mypy 1.18.2 (compiled: yes)"

_ADAPTERS = {
    "mypy": MypyAdapter,
    "pyright": PyrightAdapter,
    "ty": TyAdapter,
    "pyrefly": PyreflyAdapter,
    "zuban": ZubanAdapter,
}


def _fake_version(_self: object) -> str:
    return _FAKE_MYPY_VERSION


def _config(root: Path) -> NormalizedConfig:
    # src and venv are FIXED-depth children of `root`; workdir is too (see
    # _capture). That fixed relative geometry is what makes pyright's
    # workdir-relative path rendering deterministic across machines.
    return NormalizedConfig(
        src_roots=(str(root / "project" / "src"),),
        exclude_globs=("**/tests/**", "**/_vendor/**"),
        python_version="3.12",
        python_platform="linux",
        venv_python=str(root / "venv" / "bin" / "python"),
        cores=1,
    )


def _capture(adapter_cls: type, root: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    monkeypatch.setattr(MypyAdapter, "version", _fake_version)
    workdir = root / "work"
    workdir.mkdir()
    adapter = adapter_cls()
    argv, env = adapter.command("proj", _config(root), ThreadMode.CONSTRAINED, workdir)

    def norm(s: str) -> str:
        # One substitution collapses every absolute path (src, venv, workdir, the
        # generated config path) to <ROOT>. pyright's relative include/exclude are
        # already root-independent (../project/src) thanks to the fixed geometry.
        return s.replace(str(root), _ROOT_PLACEHOLDER)

    config_files = {p.name: norm(p.read_text()) for p in sorted(workdir.iterdir()) if p.is_file()}
    return {
        "argv": [norm(a) for a in argv],
        "env": {k: norm(v) for k, v in sorted(env.items())},
        "config_files": config_files,
    }


@pytest.mark.parametrize("tool", sorted(_ADAPTERS))
def test_standard_posture_matches_golden(
    tool: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture(_ADAPTERS[tool], tmp_path, monkeypatch)
    golden_path = _GOLDENS / f"{tool}.json"

    if os.environ.get("TB_UPDATE_GOLDENS") == "1":
        golden_path.write_text(json.dumps(captured, indent=2, sort_keys=True) + "\n")
        pytest.skip(f"regenerated golden for {tool}")

    assert golden_path.is_file(), f"missing golden {golden_path}; run TB_UPDATE_GOLDENS=1"
    expected = json.loads(golden_path.read_text())
    assert captured == expected
