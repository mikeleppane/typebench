from collections.abc import Callable
from pathlib import Path

from typebench.adapters.base import CheckerHandle
from typebench.adapters.registry import create_adapter
from typebench.contracts.config import MeasurementPlan
from typebench.contracts.identity import CheckerRuntime, CheckerSpec
from typebench.contracts.models import EnvFingerprint
from typebench.contracts.taxonomy import ThreadMode
from typebench.suite.ab import run_ab

type EnvFactory = Callable[..., EnvFingerprint]


class _FakeBaselineResolver:
    """Resolve the baseline to the builtin stub without uv/network."""

    def resolve(self, spec: CheckerSpec) -> CheckerHandle:
        adapter = create_adapter(spec.tool)
        runtime = CheckerRuntime(
            checker_id=f"{spec.tool}@1.0+{spec.label}",
            tool=spec.tool,
            binary="",  # stub adapter uses sys.executable, ignores binary
            version="1.0",
            lock_hash="fake",
            install_source="pypi",
        )
        return CheckerHandle(spec=spec, adapter=adapter, runtime=runtime)


def test_run_ab_produces_two_records_per_target(tmp_path: Path) -> None:
    target = tmp_path / "demo"
    target.mkdir()
    fake_bin = tmp_path / "stubbin"
    fake_bin.write_bytes(b"stub")

    envelope = run_ab(
        checker="stub",
        candidate_bin=str(fake_bin),
        candidate_label="pr",
        baseline_spec=CheckerSpec(tool="stub", label="release"),
        targets=[target],
        plan=MeasurementPlan(runs=2, warmup=1, measure=False),
        thread_mode=ThreadMode.ALL_CORES,
        cores=1,
        generated_at="2026-06-11T00:00:00Z",
        baseline_resolver=_FakeBaselineResolver(),
    )

    assert len(envelope.runs) == 2
    ids = {r.checker_id for r in envelope.runs}
    assert ids == {"stub@stub-1.0+pr", "stub@1.0+release"}
    assert envelope.suite_version.startswith("ab-")
    assert envelope.runs[0].checker_id == "stub@stub-1.0+pr"  # candidate first (alternation)
    assert {rc.checker_id for rc in envelope.resolved_checkers} == ids
