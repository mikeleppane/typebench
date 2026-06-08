import json
import subprocess as _sp
import sys
from pathlib import Path

import pytest

from typebench import measure
from typebench.measure import CgroupSample, read_cgroup_stats


def _write_cgroup(tmp: Path, *, peak: int, oom_kill: int = 0) -> Path:
    (tmp / "memory.peak").write_text(f"{peak}\n")
    (tmp / "cpu.stat").write_text(
        "usage_usec 44116\nuser_usec 25734\nsystem_usec 18381\nnr_periods 0\n"
    )
    (tmp / "memory.stat").write_text("anon 1000\nfile 2000\nslab 30\n")
    (tmp / "memory.events").write_text(f"low 0\nhigh 0\nmax 0\noom 0\noom_kill {oom_kill}\n")
    return tmp


def test_read_cgroup_stats_parses_all_files(tmp_path: Path) -> None:
    _write_cgroup(tmp_path, peak=44998656)
    s = read_cgroup_stats(tmp_path)
    assert isinstance(s, CgroupSample)
    assert s.peak_bytes == 44998656
    assert s.cpu_usage_usec == 44116
    assert s.cpu_user_usec == 25734
    assert s.cpu_system_usec == 18381
    assert s.oom_kill == 0
    assert s.mem_stat["anon"] == 1000
    assert s.mem_stat["file"] == 2000


def test_read_cgroup_stats_flags_oom(tmp_path: Path) -> None:
    _write_cgroup(tmp_path, peak=1, oom_kill=2)
    assert read_cgroup_stats(tmp_path).oom_kill == 2


def test_read_cgroup_stats_tolerates_missing_optional_keys(tmp_path: Path) -> None:
    # A kernel that omits user_usec/system_usec must not crash the reader.
    (tmp_path / "memory.peak").write_text("500\n")
    (tmp_path / "cpu.stat").write_text("usage_usec 10\n")
    (tmp_path / "memory.stat").write_text("anon 5\n")
    (tmp_path / "memory.events").write_text("oom_kill 0\n")
    s = read_cgroup_stats(tmp_path)
    assert s.peak_bytes == 500
    assert s.cpu_usage_usec == 10
    assert s.cpu_user_usec == 0
    assert s.cpu_system_usec == 0


def test_measure_main_runs_command_and_writes_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Point _self_cgroup_dir at a fixture dir so the test needs no real cgroup.
    cg = tmp_path / "cg"
    cg.mkdir()
    _write_cgroup(cg, peak=12345)
    monkeypatch.setattr(measure, "_self_cgroup_dir", lambda: cg)
    out = tmp_path / "payload.json"
    rc = measure.main(
        [
            "--out",
            str(out),
            "--timeout",
            "30",
            "--",
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('hi'); sys.exit(1)",
        ]
    )
    assert rc == 0  # wrapper always exits 0; outcome is in the payload
    payload = json.loads(out.read_text())
    assert payload["exit_code"] == 1
    assert payload["stdout"] == "hi"
    assert payload["cgroup"]["peak_bytes"] == 12345
    assert payload["cgroup"]["oom_kill"] == 0


def test_measure_main_payload_cgroup_none_when_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom() -> Path:
        raise OSError("no cgroup")

    monkeypatch.setattr(measure, "_self_cgroup_dir", _boom)
    out = tmp_path / "p.json"
    measure.main(
        [
            "--out",
            str(out),
            "--timeout",
            "30",
            "--",
            sys.executable,
            "-c",
            "pass",
        ]
    )
    payload = json.loads(out.read_text())
    assert payload["exit_code"] == 0
    assert payload["cgroup"] is None


def test_measure_import_does_not_pull_pydantic() -> None:
    # measure runs as a child process under systemd-run; pydantic startup cost
    # would tax every scoped run. Keep it stdlib + typebench.wrapper only.
    code = (
        "import sys, typebench.measure\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] == 'pydantic')\n"
        "print(','.join(bad))\n"
    )
    out = _sp.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "", f"pydantic leaked into measure import: {out.stdout!r}"
