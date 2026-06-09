import shutil
from pathlib import Path

import pytest

from typebench.adapters.stub import StubAdapter
from typebench.contracts.config import NormalizedConfig
from typebench.contracts.models import ThreadMode, TimingStats
from typebench.timing import parse_hyperfine_json, run_timing


def test_parse_hyperfine_json_builds_timing_stats() -> None:
    data = {
        "results": [
            {
                "command": "x",
                "mean": 0.12,
                "stddev": 0.01,
                "median": 0.11,
                "min": 0.10,
                "max": 0.14,
                "times": [0.10, 0.11, 0.14],
            }
        ]
    }
    stats = parse_hyperfine_json(data)
    assert isinstance(stats, TimingStats)
    assert stats.runs == 3
    assert stats.min_s == 0.10
    assert stats.median_s == 0.11
    assert stats.times_s == [0.10, 0.11, 0.14]


def test_parse_hyperfine_json_rejects_empty_results() -> None:
    with pytest.raises(ValueError):
        parse_hyperfine_json({"results": []})


@pytest.mark.skipif(shutil.which("hyperfine") is None, reason="hyperfine not installed")
def test_run_timing_against_stub(tmp_path: Path) -> None:
    adapter = StubAdapter(exit_code=0, sleep=0.02)
    argv, env = adapter.command("demo", NormalizedConfig(), ThreadMode.ALL_CORES, tmp_path)
    stats = run_timing(argv, prepare_cmd=None, extra_env=env, warmup=1, runs=3, timeout=30)
    assert stats.runs == 3
    assert stats.min_s > 0
    assert stats.max_s >= stats.min_s
