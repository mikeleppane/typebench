import json
import subprocess as _sp
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from typebench import measure
from typebench.measure import CgroupSample, ResourceResult, read_cgroup_stats, scoped_probe

Runner = Callable[..., _sp.CompletedProcess[str]]


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


def _fake_runner_factory(payloads: list[dict[str, object]]) -> Runner:
    """Return a runner that writes the next payload to the embedded --out path."""
    calls = {"i": 0}

    def runner(cmd: list[str], **kwargs: object) -> _sp.CompletedProcess[str]:
        out_path = cmd[cmd.index("--out") + 1]
        Path(out_path).write_text(json.dumps(payloads[calls["i"]]))
        calls["i"] += 1
        return _sp.CompletedProcess(cmd, 0, "", "")

    return runner


def _payload(
    peak: int, usage: int = 1000, user: int = 600, system: int = 400, oom: int = 0
) -> dict[str, object]:
    return {
        "exit_code": 1,
        "signal": None,
        "timed_out": False,
        "oom": False,
        "env_error": False,
        "stdout": "found 3 errors",
        "stderr": "",
        "cgroup": {
            "peak_bytes": peak,
            "cpu_usage_usec": usage,
            "cpu_user_usec": user,
            "cpu_system_usec": system,
            "oom_kill": oom,
            "mem_stat": {"anon": peak},
        },
    }


def test_scoped_probe_aggregates_min_median_max() -> None:
    runner = _fake_runner_factory([_payload(100), _payload(140), _payload(120)])
    res = scoped_probe(["mypy", "."], extra_env={}, timeout=60, repeats=3, runner=runner)
    assert isinstance(res, ResourceResult)
    assert res.raw.exit_code == 1
    assert res.raw.stdout == "found 3 errors"
    assert res.memory is not None
    assert res.memory.peak_bytes_min == 100
    assert res.memory.peak_bytes_median == 120
    assert res.memory.peak_bytes_max == 140
    assert res.cpu_time_s == 0.001
    assert res.oom is False


def test_scoped_probe_first_run_is_the_probe() -> None:
    runner = _fake_runner_factory(
        [{**_payload(100), "stdout": "RUN1"}, {**_payload(100), "stdout": "RUN2"}]
    )
    res = scoped_probe(["t"], extra_env={}, timeout=60, repeats=2, runner=runner)
    assert res.raw.stdout == "RUN1"


def test_scoped_probe_oom_killed_repeat_folds_into_raw_oom() -> None:
    runner = _fake_runner_factory([_payload(100), _payload(100, oom=1)])
    res = scoped_probe(["t"], extra_env={}, timeout=60, repeats=2, runner=runner)
    assert res.oom is True
    assert res.raw.oom is True


def test_scoped_probe_first_generic_failure_becomes_authoritative() -> None:
    timed_out = {**_payload(100), "timed_out": True, "exit_code": -1}
    runner = _fake_runner_factory([{**_payload(100), "stdout": "OK1"}, timed_out])
    res = scoped_probe(["t"], extra_env={}, timeout=60, repeats=2, runner=runner)
    assert res.raw.timed_out is True


def test_scoped_probe_memory_none_when_cgroup_missing() -> None:
    runner = _fake_runner_factory([{**_payload(100), "cgroup": None}])
    res = scoped_probe(["t"], extra_env={}, timeout=60, repeats=1, runner=runner)
    assert res.memory is None
    assert res.cpu_time_s is None
    assert res.raw.exit_code == 1


def test_scoped_probe_prepare_runs_before_every_repeat() -> None:
    runner = _fake_runner_factory([_payload(100), _payload(100), _payload(100)])
    calls = {"n": 0}

    def prepare() -> None:
        calls["n"] += 1

    scoped_probe(["t"], extra_env={}, timeout=60, repeats=3, runner=runner, prepare=prepare)
    assert calls["n"] == 3


def test_scoped_probe_skips_repeat_that_raises_but_uses_survivors() -> None:
    good = [_payload(100), _payload(140)]
    state = {"i": 0}

    def runner(cmd: list[str], **kwargs: object) -> _sp.CompletedProcess[str]:
        i = state["i"]
        state["i"] += 1
        if i == 1:
            raise OSError("transient scope race")
        out_path = cmd[cmd.index("--out") + 1]
        Path(out_path).write_text(json.dumps(good.pop(0)))
        return _sp.CompletedProcess(cmd, 0, "", "")

    res = scoped_probe(["t"], extra_env={}, timeout=60, repeats=3, runner=runner)
    assert res.memory is not None
    assert res.memory.runs == 2


def test_scoped_probe_raises_measure_error_when_no_payload() -> None:
    def runner(cmd: list[str], **kwargs: object) -> _sp.CompletedProcess[str]:
        return _sp.CompletedProcess(cmd, 1, "", "scope failed")

    with pytest.raises(measure.MeasureError):
        scoped_probe(["t"], extra_env={}, timeout=60, repeats=3, runner=runner)


def test_scoped_probe_rejects_zero_repeats() -> None:
    with pytest.raises(ValueError, match="repeats must be >= 1"):
        scoped_probe(["t"], extra_env={}, timeout=60, repeats=0)


def test_scoped_probe_malformed_cgroup_dict_skips_repeat_not_raises() -> None:
    # Review finding: a cgroup dict present but missing peak_bytes (truncated
    # payload / schema drift / custom runner) must SKIP that repeat, never raise a
    # KeyError out of scoped_probe -> the collector's fallback except did not list
    # KeyError, so an escape would drop the record (§12, Decision J).
    bad = _payload(999)
    bad_cgroup = bad["cgroup"]
    assert isinstance(bad_cgroup, dict)
    del bad_cgroup["peak_bytes"]
    runner = _fake_runner_factory([_payload(100), bad])
    res = scoped_probe(["t"], extra_env={}, timeout=60, repeats=2, runner=runner)
    assert res.memory is not None
    assert res.memory.runs == 1  # only the well-formed repeat contributes memory
    assert res.memory.peak_bytes_median == 100


def test_scoped_probe_all_malformed_cgroup_raises_measure_error() -> None:
    # When every repeat is malformed, no usable payload survives -> MeasureError,
    # which the collector catches and turns into a plain-probe fallback.
    bad = _payload(1)
    bad_cgroup = bad["cgroup"]
    assert isinstance(bad_cgroup, dict)
    del bad_cgroup["peak_bytes"]
    runner = _fake_runner_factory([bad])
    with pytest.raises(measure.MeasureError):
        scoped_probe(["t"], extra_env={}, timeout=60, repeats=1, runner=runner)


def test_measure_main_cgroup_none_on_noninteger_peak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review finding: a present-but-non-integer memory.peak raises ValueError from
    # _read_int; measure.main must degrade to cgroup=None and STILL write the
    # payload (the checker outcome for this repeat is never lost, §5.5).
    cg = tmp_path / "cg"
    cg.mkdir()
    (cg / "memory.peak").write_text("not-a-number\n")
    (cg / "cpu.stat").write_text("usage_usec 1\n")
    (cg / "memory.stat").write_text("anon 1\n")
    (cg / "memory.events").write_text("oom_kill 0\n")
    monkeypatch.setattr(measure, "_self_cgroup_dir", lambda: cg)
    out = tmp_path / "p.json"
    measure.main(["--out", str(out), "--timeout", "30", "--", sys.executable, "-c", "pass"])
    payload = json.loads(out.read_text())
    assert payload["exit_code"] == 0
    assert payload["cgroup"] is None


@pytest.mark.skipif(not measure.capable(), reason="no cgroup v2 / systemd-run user scope")
def test_scoped_probe_real_scope_measures_nonzero_peak() -> None:
    argv = [sys.executable, "-c", "x = bytearray(40_000_000); print(len(x))"]
    res = measure.scoped_probe(argv, extra_env={}, timeout=60, repeats=3)
    assert res.raw.exit_code == 0
    assert res.memory is not None
    assert res.memory.peak_bytes_max >= 30_000_000
    assert res.cpu_time_s is not None and res.cpu_time_s > 0
    assert res.oom is False
