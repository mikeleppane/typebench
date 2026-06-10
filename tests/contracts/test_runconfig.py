import pytest
from pydantic import ValidationError

from typebench.contracts.config import MeasurementPlan
from typebench.contracts.identity import CheckerSpec
from typebench.contracts.policy import Policy
from typebench.contracts.runconfig import RunConfig, merge_tool_override
from typebench.contracts.taxonomy import ThreadMode


@pytest.mark.parametrize(
    "overrides",
    [
        {"cores": (0,)},  # a config `tracks.cores = [0]` would render `--threads 0`
        {"cores": (1, 0)},
        {"cores": ()},  # empty sweep produces no constrained cells
        {"runs": 0},
        {"warmup": -1},
        {"mem_runs": 0},
        {"timeout": 0},
        {"timeout": -1.0},
        {"calib_runs": 0},
    ],
)
def test_runconfig_rejects_out_of_range_knobs(overrides: dict[str, object]) -> None:
    payload: dict[str, object] = {"checkers": (CheckerSpec(tool="mypy"),), **overrides}
    with pytest.raises(ValidationError):
        RunConfig.model_validate(payload)


def test_runconfig_defaults_match_current_cli() -> None:
    cfg = RunConfig(checkers=(CheckerSpec(tool="mypy"),))
    # Both tracks by default (matches the current CLI suite default).
    assert cfg.thread_modes == (ThreadMode.ALL_CORES, ThreadMode.CONSTRAINED)
    assert cfg.cores == (1,)  # single-core constrained floor
    assert cfg.policy is Policy.STANDARD
    assert cfg.runs == 10 and cfg.warmup == 3 and cfg.mem_runs == 3
    assert cfg.timeout == 900.0 and cfg.measure is True
    assert cfg.calibrate is True and cfg.calib_runs == 5
    assert cfg.projects == () and cfg.buckets == ()  # empty selection = whole corpus
    assert cfg.corpus is None  # None -> resolved repo-root-anchored at load, NOT cwd


def test_runconfig_builds_measurement_plan() -> None:
    cfg = RunConfig(
        checkers=(CheckerSpec(tool="mypy"),),
        runs=4,
        warmup=1,
        timeout=30.5,
        mem_runs=2,
        measure=False,
    )
    assert cfg.measurement_plan() == MeasurementPlan(
        runs=4,
        warmup=1,
        timeout_s=30.5,
        mem_runs=2,
        measure=False,
    )


def test_measurement_plan_rejects_invalid_ranges() -> None:
    with pytest.raises(ValueError, match="timeout_s"):
        MeasurementPlan(timeout_s=0)


def test_runconfig_forbids_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        RunConfig.model_validate({"checkers": [], "bogus": 1})


def test_merge_tool_override_bare_name_keeps_configured_version() -> None:
    # `--tool mypy` with a configured `mypy@1.18.2` keeps the configured pin.
    configured = (CheckerSpec(tool="mypy", version="1.18.2"), CheckerSpec(tool="ty"))
    out = merge_tool_override(configured, ["mypy"])
    assert out == (CheckerSpec(tool="mypy", version="1.18.2"),)


def test_merge_tool_override_bare_name_keeps_all_configured_versions() -> None:
    # A config can intentionally benchmark two versions of one checker.
    configured = (
        CheckerSpec(tool="mypy", version="1.18.2"),
        CheckerSpec(tool="mypy", version="1.19.0"),
    )
    out = merge_tool_override(configured, ["mypy"])
    assert out == (
        CheckerSpec(tool="mypy", version="1.18.2"),
        CheckerSpec(tool="mypy", version="1.19.0"),
    )


def test_merge_tool_override_at_version_overrides() -> None:
    # `--tool mypy@1.19.0` overrides the configured version.
    configured = (CheckerSpec(tool="mypy", version="1.18.2"),)
    out = merge_tool_override(configured, ["mypy@1.19.0"])
    assert out == (CheckerSpec(tool="mypy", version="1.19.0"),)


def test_merge_tool_override_no_version_anywhere_resolves_latest() -> None:
    # `--tool ty` with no configured ty and no pin -> latest (version None, recorded later).
    out = merge_tool_override((), ["ty"])
    assert out == (CheckerSpec(tool="ty", version=None),)


def test_merge_tool_override_with_label() -> None:
    out = merge_tool_override((), ["mypy@1.19.0+rc"])
    assert out == (CheckerSpec(tool="mypy", version="1.19.0", label="rc"),)
