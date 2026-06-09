from pathlib import Path

import pytest

from typebench import env
from typebench.contracts.models import EnvFingerprint


def test_detect_env_returns_populated_fingerprint() -> None:
    fp = env.detect_env()
    assert isinstance(fp, EnvFingerprint)
    assert fp.os  # e.g. "Linux"
    assert fp.core_count >= 1
    assert fp.python_version.count(".") >= 2
    assert fp.cpu_model  # never empty; falls back to a placeholder


def test_detect_env_populates_runtime_and_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    # Stub the seams so the test is hermetic (no real node/npm/uv required).
    monkeypatch.setattr(env, "_cmd_version", lambda argv: f"{argv[0]}-9.9", raising=True)
    monkeypatch.setattr(env, "_mem_total_bytes", lambda: 16_000_000_000, raising=True)
    monkeypatch.setattr(env, "_cgroup_v2", lambda: True, raising=True)
    fp = env.detect_env()
    assert fp.node_version == "node-9.9"
    assert fp.npm_version == "npm-9.9"
    assert fp.uv_version == "uv-9.9"
    assert fp.mem_total_bytes == 16_000_000_000
    assert fp.cgroup_v2 is True


def test_mem_total_bytes_parses_meminfo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       16384000 kB\nMemFree: 100 kB\n")
    monkeypatch.setattr(env, "_MEMINFO", meminfo, raising=True)
    assert env._mem_total_bytes() == 16384000 * 1024
