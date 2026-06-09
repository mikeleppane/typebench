"""Renderer (spec §8/§11) — pure functions from results models to the README
markdown block and the GH Pages trends.json. No filesystem I/O here (the CLI does
it); golden-tested. Hard rules: diagnostics counts are NEVER a headline column
(§8); kLOC/s uses the canonical code-LOC denominator and is withheld for
over-reporting tools; parallel_efficiency is labelled cross-pass (cold-cpu ÷
warm-wall), not a within-run figure."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typebench.models import ResultsEnvelope, RunResult

_README_BEGIN = "<!-- TYPEBENCH:BEGIN -->"
_README_END = "<!-- TYPEBENCH:END -->"


def _peak_mem_mb(record: RunResult) -> str:
    if record.memory is None:
        return "—"
    return f"{record.memory.peak_bytes_median / 1_000_000:.1f}"


def _kloc_s(record: RunResult) -> str:
    """Headline throughput = canonical code-LOC / wall median. Withheld (—*) for
    over-reporters (their analyzed set diverges from the canonical denominator, §8).
    Physical-denominator rows are footnoted by the caller via loc_denominator."""
    if record.over_reports:
        return "—*"
    loc = record.canonical_code_loc if record.loc_denominator == "code" else record.canonical_loc
    if loc is None or record.timing is None or record.timing.median_s <= 0:
        return "—"
    return f"{(loc / 1000) / record.timing.median_s:.1f}"


def _wall(record: RunResult) -> str:
    return f"{record.timing.median_s:.3f}" if record.timing is not None else "—"


def _sort_key(record: RunResult) -> tuple[int, float, str]:
    # Measured-success first (fastest wall first); failures sink to the bottom,
    # then alphabetical by tool for stable ordering.
    if record.timing is not None:
        return (0, record.timing.median_s, record.tool)
    return (1, float("inf"), record.tool)


def _table(records: list[RunResult]) -> str:
    header = (
        "| Tool | Result | Wall median (s) | Peak cgroup mem (MB) | "
        "CPU time (s) | Parallel eff. (cross-pass) | kLOC/s (code) |\n"
        "|------|--------|-----------------|----------------------|"
        "--------------|----------------------------|---------------|\n"
    )
    rows = []
    for r in sorted(records, key=_sort_key):
        cpu = f"{r.cpu_time_s:.3f}" if r.cpu_time_s is not None else "—"
        peff = f"{r.parallel_efficiency:.2f}" if r.parallel_efficiency is not None else "—"
        rows.append(
            f"| {r.tool} | {r.result_class.value} | {_wall(r)} | {_peak_mem_mb(r)} | "
            f"{cpu} | {peff} | {_kloc_s(r)} |"
        )
    return header + "\n".join(rows) + "\n"


def render_readme(envelope: ResultsEnvelope) -> str:
    """Markdown block (between the TYPEBENCH markers) — one table per
    (project, thread-mode), ordered fastest-first (ranking by the measured metric,
    §11). Includes the suite version, generated timestamp, and the caveat footnotes."""
    groups: dict[tuple[str, str], list[RunResult]] = {}
    for record in envelope.runs:
        groups.setdefault((record.project, record.thread_mode.value), []).append(record)

    parts = [
        _README_BEGIN,
        f"\n_Suite `{envelope.suite_version}` · generated {envelope.generated_at}_\n",
    ]
    for project, mode in sorted(groups):
        parts.append(f"\n#### {project} — {mode}\n")
        parts.append(_table(groups[(project, mode)]))
    parts.append(
        "\n> kLOC/s denominator is the canonical analyzed code-LOC (tokei; blanks+"
        "comments excluded), identical across tools. `—*` = throughput withheld "
        "because the tool over-reports its analyzed set vs the canonical denominator. "
        "Parallel efficiency is cross-pass (cold cgroup CPU-time ÷ warm hyperfine wall). "
        "Checker issue counts are intentionally omitted — they are not comparable across "
        "tools and are not a ranking (spec §8).\n"
    )
    parts.append(_README_END)
    return "\n".join(parts)


def _calib_median(record: RunResult) -> float | None:
    return record.calibration.raw_median_s if record.calibration is not None else None


def cpu_model_anchors(history: list[ResultsEnvelope]) -> dict[str, float]:
    """Fixed per-CPU-model calibration anchor (Decision I): for each CPU model, the
    calibration raw_median_s of the EARLIEST envelope (by generated_at) that has a
    run on that model with a calibration. Anchors only ever add, so a published
    point's normalized value never changes when later data arrives."""
    anchors: dict[str, float] = {}
    for envelope in sorted(history, key=lambda e: e.generated_at):
        for record in envelope.runs:
            calib = _calib_median(record)
            if calib is None or calib <= 0:
                continue
            anchors.setdefault(record.env.cpu_model, calib)
    return anchors


def _kloc_value(record: RunResult) -> float | None:
    if record.over_reports or record.timing is None or record.timing.median_s <= 0:
        return None
    loc = record.canonical_code_loc if record.loc_denominator == "code" else record.canonical_loc
    return (loc / 1000) / record.timing.median_s if loc is not None else None


def build_trends(history: list[ResultsEnvelope]) -> dict[str, object]:
    """Flatten history to fully-labelled points + per-CPU-model-normalized variants.
    The GH Pages app groups points into series and derives inter-checker ratios
    client-side (slowest per date/project/mode/metric). Only measured-success records
    contribute points; failures are visible in the README, not the trend lines."""
    anchors = cpu_model_anchors(history)
    points: list[dict[str, object]] = []
    markers: list[dict[str, object]] = []
    for envelope in sorted(history, key=lambda e: e.generated_at):
        date = envelope.generated_at[:10]
        markers.append({"date": date, "suite_version": envelope.suite_version})
        for record in envelope.runs:
            if not record.result_class.is_measured_success or record.timing is None:
                continue
            calib = _calib_median(record)
            anchor = anchors.get(record.env.cpu_model)
            wall = record.timing.median_s
            wall_norm = (
                wall * anchor / calib
                if anchor is not None and calib is not None and calib > 0
                else None
            )
            peak_mb = record.memory.peak_bytes_median / 1_000_000 if record.memory else None
            points.append(
                {
                    "date": date,
                    "suite_version": envelope.suite_version,
                    "project": record.project,
                    "thread_mode": record.thread_mode.value,
                    "tool": record.tool,
                    "cpu_model": record.env.cpu_model,
                    "wall_median_s": wall,
                    "wall_median_s_norm": wall_norm,
                    "peak_mem_mb": peak_mb,
                    "kloc_s": _kloc_value(record),
                    "calib_median_s": calib,
                    "calib_anchor_s": anchor,
                }
            )
    return {
        "cpu_models": sorted(anchors),
        "points": points,
        "corpus_markers": markers,
    }
