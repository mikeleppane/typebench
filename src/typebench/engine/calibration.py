"""Calibration baseline (spec §5.7). A fixed, dep-free CPU-bound Python workload
timed alongside each run so weekly trends can be normalized against the VM-to-VM
hardware lottery. Pydantic-free except the final stats construction is done by the
caller (collector); this module returns plain floats via `calibrate`.

The workload identity is LOCKED by WORKLOAD_ID + ITERATIONS — changing either is a
new workload id (the manifest records it). Normalization (raw / reference) is a
render-time transform (Plan 5); we store RAW seconds only (Decision C)."""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Annotation-only import: keeps `import typebench.engine.calibration` pydantic-free at
    # runtime (the import-guard test asserts this) while letting pyrefly resolve the
    # `calibrate` return type. The real construction import is lazy, inside
    # calibrate(), so the runtime import graph never pulls pydantic.
    from typebench.contracts.models import CalibrationStats

# Locked workload identity. Bump the version suffix if the loop body or ITERATIONS
# changes — trend continuity depends on a stable workload.
WORKLOAD_ID = "calib-pyloop-v1"
# Tuned for ~0.2-0.4 s on a modern single core. The ABSOLUTE time is irrelevant
# (it is a relative hardware scalar); only stability + determinism matter.
ITERATIONS = 5_000_000


def _run_workload() -> None:
    """Deterministic integer/float CPU-bound loop. No allocation growth, no I/O,
    no randomness — pure ALU work whose wall-time scales inversely with core speed.
    A documented limitation (§5.7): this calibrates Python/CPU speed, not the Rust
    (pyrefly/ty) or Node (pyright) runtimes; inter-checker ratios + CPU-model
    segmentation cover the residual. It is a coarse hardware scalar by design."""
    acc = 0
    x = 1.0
    for i in range(ITERATIONS):
        acc = (acc + i * 2654435761) & 0xFFFFFFFF
        x = x * 1.0000001 + 1.0
    # Consume results so the loop cannot be optimized away (CPython does not, but
    # be explicit for clarity / future runtimes).
    if acc == -1 and x == 0.0:  # pragma: no cover - never true
        raise RuntimeError("unreachable")


def calibrate(runs: int = 5) -> CalibrationStats:
    """Time the workload `runs` times and return raw min/median/max seconds.
    `CalibrationStats` is imported lazily so this module stays pydantic-free on
    import; the return annotation resolves via the TYPE_CHECKING import above."""
    if runs < 1:
        raise ValueError(f"calibration runs must be >= 1, got {runs}")
    # Lazy import keeps `import typebench.engine.calibration` pydantic-free (guard test);
    # only an actual calibrate() call pays pydantic's import cost.
    from typebench.contracts.models import CalibrationStats  # noqa: PLC0415

    samples: list[float] = []
    for _ in range(runs):
        start = time.perf_counter()
        _run_workload()
        samples.append(time.perf_counter() - start)
    return CalibrationStats(
        workload_id=WORKLOAD_ID,
        iterations=ITERATIONS,
        runs=runs,
        raw_min_s=min(samples),
        raw_median_s=statistics.median(samples),
        raw_max_s=max(samples),
    )


def main(raw_args: list[str] | None = None) -> int:
    """CLI: `python -m typebench.engine.calibration` prints the calibration JSON. Useful
    for a standalone calibration probe / debugging."""
    parser = argparse.ArgumentParser(prog="typebench.engine.calibration")
    parser.add_argument("--runs", type=int, default=5)
    ns = parser.parse_args(raw_args)
    stats = calibrate(runs=ns.runs)
    print(stats.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
