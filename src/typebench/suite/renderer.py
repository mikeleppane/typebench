"""Pure renderers from results models to README markdown and GH Pages trends JSON.

No filesystem I/O here (the CLI does it); golden-tested. Hard rules: diagnostics
counts are NEVER a headline column; kLOC/s uses the canonical code-LOC denominator
and is withheld for over-reporting tools. The README results block is one compact
table per project (ecosystem library), grouped into size tiers, folding the
all-cores and constrained 1/4/8-core walls into columns.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, assert_never

from typebench.contracts.taxonomy import LocDenominator

if TYPE_CHECKING:
    from typebench.contracts.models import ResultsEnvelope, RunResult

_README_BEGIN = "<!-- TYPEBENCH:BEGIN -->"
_README_END = "<!-- TYPEBENCH:END -->"


def _format_generated(iso: str) -> str:
    """Render the envelope's ISO-8601 stamp as a clean 'YYYY-MM-DD HH:MM UTC'. Falls
    back to the raw string if it is not parseable, so render never crashes on it."""
    try:
        return datetime.fromisoformat(iso).astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return iso


def _corrected_wall_s(record: RunResult, harness_wall_overhead_s: float | None) -> float | None:
    if record.timing is None:
        return None
    if harness_wall_overhead_s is None:
        return record.timing.median_s
    return max(record.timing.median_s - harness_wall_overhead_s, 0.0)


def _corrected_peak_bytes(record: RunResult, harness_mem_baseline_bytes: int | None) -> int | None:
    if record.memory is None:
        return None
    if harness_mem_baseline_bytes is None:
        return record.memory.peak_bytes_median
    return max(record.memory.peak_bytes_median - harness_mem_baseline_bytes, 0)


def _peak_mem_mb(record: RunResult, harness_mem_baseline_bytes: int | None) -> str:
    peak_bytes = _corrected_peak_bytes(record, harness_mem_baseline_bytes)
    if peak_bytes is None:
        return "—"
    marker = "!" if record.memory is not None and record.memory.mem_under_swap else ""
    return f"{peak_bytes / 1_000_000:.1f}{marker}"


def _code_loc_or_withheld(record: RunResult) -> int | None:
    """Code-LOC for the headline kLOC/s, or None to WITHHOLD the row.

    The headline column is code-LOC throughput with one denominator shared across
    tools (the neutrality guarantee). Only the CODE denominator is comparable in
    that column; a PHYSICAL fallback (tokei absent) or an unknown denominator is a
    *different* denominator, so it is withheld (rendered —) rather than mislabeled
    as code. Exhaustive: a new LocDenominator member forces a decision here."""
    match record.loc_denominator:
        case LocDenominator.CODE:
            return record.canonical_code_loc
        case LocDenominator.PHYSICAL | None:
            return None
        case _ as unreachable:
            assert_never(unreachable)


def _kloc_s(record: RunResult, harness_wall_overhead_s: float | None) -> str:
    """Headline throughput = canonical code-LOC / wall median. Withheld (—*) for
    over-reporters (their analyzed set diverges from the canonical denominator) and
    (—) for physical-fallback / unknown denominators, which are not code-LOC and so
    are not comparable in this column."""
    if record.over_reports:
        return "—*"
    loc = _code_loc_or_withheld(record)
    wall = _corrected_wall_s(record, harness_wall_overhead_s)
    if loc is None or wall is None or wall <= 0:
        return "—"
    return f"{(loc / 1000) / wall:.1f}"


def _wall(record: RunResult, harness_wall_overhead_s: float | None) -> str:
    wall = _corrected_wall_s(record, harness_wall_overhead_s)
    return f"{wall:.3f}" if wall is not None else "—"


def _checker_id(record: RunResult) -> str:
    if record.checker_id is not None:
        return record.checker_id
    return f"{record.tool}@{record.tool_version}"


def _ab_display(checker_id: str) -> str:
    """Friendly A/B identity '<tool> (<label>)' from '<tool>@<version>+<label>',
    dropping the noisy raw --version string from the display. Falls back to the
    full id when there is no label."""
    tool = checker_id.split("@", 1)[0]
    label = checker_id.rsplit("+", 1)[1] if "+" in checker_id else ""
    return f"{tool} ({label})" if label else checker_id


def _cores_label(cores: int | None) -> str:
    return "all-cores" if cores is None else f"cores={cores}"


def _sort_key(record: RunResult) -> tuple[int, float, str]:
    # Measured-success first (fastest wall first); failures sink to the bottom,
    # then alphabetical by checker identity for stable ordering.
    if record.timing is not None:
        return (0, record.timing.median_s, _checker_id(record))
    return (1, float("inf"), _checker_id(record))


_CONSTRAINED_CORES: tuple[int, ...] = (1, 4, 8)
_TIER_ORDER: dict[str, int] = {"Small": 0, "Medium": 1, "Large": 2}
# A column winner is only meaningful with at least this many comparable values.
_MIN_BOLD_CONTENDERS = 2
# Tier cutoffs on canonical code-LOC. Presentation-only grouping, not the corpus's
# declared buckets (which the envelope does not carry); chosen to read small->large.
_SMALL_MAX_LOC = 20_000
_MEDIUM_MAX_LOC = 70_000


def _size_tier(code_loc: int | None) -> str:
    """Group projects by analyzed code size so the README reads small -> large."""
    loc = code_loc or 0
    if loc < _SMALL_MAX_LOC:
        return "Small"
    if loc < _MEDIUM_MAX_LOC:
        return "Medium"
    return "Large"


def _loc_headline(record: RunResult) -> str:
    """The 'N code LOC analyzed across M files' line under a project heading. Uses the
    canonical code-LOC (the shared kLOC/s denominator); falls back to physical LOC when
    tokei was unavailable."""
    loc = (
        record.canonical_code_loc if record.canonical_code_loc is not None else record.canonical_loc
    )
    loc_str = f"{loc:,}" if loc is not None else "unknown"
    files = (
        f" across {record.canonical_files:,} files" if record.canonical_files is not None else ""
    )
    return f"{loc_str} code LOC analyzed{files}"


def _wall_or_status(record: RunResult | None, harness_wall_overhead_s: float | None) -> str:
    """Wall median for a measured cell; the failure class for a cell that ran but failed
    (kept visible, not hidden); — when that thread config was not run."""
    if record is None:
        return "—"
    if record.timing is None:
        return record.result_class.value
    return _wall(record, harness_wall_overhead_s)


def _bold_best(
    cells: dict[str, tuple[float | None, str]], *, higher_is_better: bool
) -> dict[str, str]:
    """Bold the winning cell(s) in one metric column. Only marks a winner when at least
    two checkers have a comparable value, so a lone measurement is never 'best'. Ties are
    all bolded."""
    text = {cid: formatted for cid, (_, formatted) in cells.items()}
    valued = {cid: value for cid, (value, _) in cells.items() if value is not None}
    if len(valued) < _MIN_BOLD_CONTENDERS:
        return text
    best = max(valued.values()) if higher_is_better else min(valued.values())
    return {cid: (f"**{t}**" if valued.get(cid) == best else t) for cid, t in text.items()}


def _compact_table(
    records: list[RunResult],
    *,
    harness_mem_baseline_bytes: int | None,
    harness_wall_overhead_s: float | None,
) -> str:
    """One row per checker for a single project: all-cores wall + the constrained
    1/4/8-core sweep folded into columns, plus peak mem and kLOC/s from the all-cores
    pass. Fastest (all-cores wall) first; the best cell in each metric column is bold."""
    by_checker: dict[str, dict[tuple[str, int | None], RunResult]] = {}
    for r in records:
        by_checker.setdefault(_checker_id(r), {})[(r.thread_mode.value, r.cores)] = r

    def all_cores(checker_id: str) -> RunResult | None:
        return by_checker[checker_id].get(("all-cores", None))

    def sort_key(checker_id: str) -> tuple[int, float]:
        rec = all_cores(checker_id)
        if rec is not None and rec.timing is not None:
            return (0, rec.timing.median_s)
        return (1, float("inf"))

    cids = sorted(by_checker, key=sort_key)

    def wall_cell(rec: RunResult | None) -> tuple[float | None, str]:
        value = (
            _corrected_wall_s(rec, harness_wall_overhead_s)
            if rec is not None and rec.timing is not None
            else None
        )
        return (value, _wall_or_status(rec, harness_wall_overhead_s))

    def mem_cell(rec: RunResult | None) -> tuple[float | None, str]:
        peak = _corrected_peak_bytes(rec, harness_mem_baseline_bytes) if rec is not None else None
        return (
            float(peak) if peak is not None else None,
            _peak_mem_mb(rec, harness_mem_baseline_bytes) if rec is not None else "—",
        )

    def kloc_cell(rec: RunResult | None) -> tuple[float | None, str]:
        return (
            _kloc_value(rec, harness_wall_overhead_s) if rec is not None else None,
            _kloc_s(rec, harness_wall_overhead_s) if rec is not None else "—",
        )

    all_col = _bold_best({cid: wall_cell(all_cores(cid)) for cid in cids}, higher_is_better=False)
    sweep_cols = {
        c: _bold_best(
            {cid: wall_cell(by_checker[cid].get(("constrained", c))) for cid in cids},
            higher_is_better=False,
        )
        for c in _CONSTRAINED_CORES
    }
    mem_col = _bold_best({cid: mem_cell(all_cores(cid)) for cid in cids}, higher_is_better=False)
    kloc_col = _bold_best({cid: kloc_cell(all_cores(cid)) for cid in cids}, higher_is_better=True)

    header = (
        "| Checker | All-cores | 1c | 4c | 8c | Peak mem (MB) | kLOC/s |\n"
        "|------|--:|--:|--:|--:|--:|--:|\n"
    )
    rows: list[str] = []
    for cid in cids:
        cells = [all_col[cid]] + [sweep_cols[c][cid] for c in _CONSTRAINED_CORES]
        cells += [mem_col[cid], kloc_col[cid]]
        rows.append(f"| {cid} | " + " | ".join(cells) + " |")
    return header + "\n".join(rows) + "\n"


_FOOTNOTE = (
    "\n> Wall is the hyperfine median in seconds, fastest first; the best cell in each metric "
    "column is in **bold**. **All-cores** uses the whole machine; **1c/4c/8c** are the "
    "constrained track pinned to that many cores. Peak mem and kLOC/s are from the all-cores "
    "pass. kLOC/s denominator is the canonical analyzed code-LOC, identical across tools. "
    "`—*` = throughput withheld because the tool over-reports its analyzed set vs the canonical "
    "denominator. `!` = swap observed during the memory pass, so peak memory may be "
    "understated. Checker issue counts are intentionally omitted — they are not comparable "
    "across tools and are not a ranking.\n"
)


def _provenance(envelope: ResultsEnvelope) -> str:
    """Machine/run caveat built from existing envelope fields. Returns '' when the
    envelope carries no runs (degraded store) so render stays crash-free. Makes the
    provenance explicit so absolute seconds are not cross-machine-compared."""
    if not envelope.runs:
        return ""
    env = envelope.runs[0].env
    machine = f"{env.cpu_model} ({env.core_count} cores), {env.os} {env.kernel}"
    cfg = envelope.run_config
    run_clause = f"; {cfg.runs} timed runs, {cfg.warmup} warmup" if cfg is not None else ""
    return (
        f"\n> Measured on {machine}{run_clause}. Absolute times are machine-specific — "
        "compare rows within the same suite, or the normalized trend lines on the site; "
        "do not compare raw seconds across machines.\n"
    )


def _result_tables(envelope: ResultsEnvelope) -> list[str]:
    """One section per project (ecosystem library), grouped into size tiers and ordered
    small -> large. Each section states the analyzed code-LOC and renders a single compact
    table folding the thread-mode/core sweep into columns. Shared by the README block and
    the terminal summary so the two never drift."""
    by_project: dict[str, list[RunResult]] = {}
    for record in envelope.runs:
        by_project.setdefault(record.project, []).append(record)
    rep = {project: recs[0] for project, recs in by_project.items()}

    def project_key(project: str) -> tuple[int, int, str]:
        rec = rep[project]
        loc = rec.canonical_code_loc or rec.canonical_loc or 0
        return (_TIER_ORDER[_size_tier(rec.canonical_code_loc)], loc, project)

    parts: list[str] = []
    current_tier: str | None = None
    for project in sorted(by_project, key=project_key):
        rec = rep[project]
        tier = _size_tier(rec.canonical_code_loc)
        if tier != current_tier:
            parts.append(f"\n### {tier} projects\n")
            current_tier = tier
        parts.append(f"\n#### {project}\n")
        parts.append(f"{_loc_headline(rec)}\n")
        parts.append(
            _compact_table(
                by_project[project],
                harness_mem_baseline_bytes=envelope.harness_mem_baseline_bytes,
                harness_wall_overhead_s=envelope.harness_wall_overhead_s,
            )
        )
    return parts


def _dataset_line(envelope: ResultsEnvelope) -> str:
    """The italic one-liner above the tables: corpus snapshot + clean measured time."""
    when = _format_generated(envelope.generated_at)
    return f"_Corpus snapshot {envelope.suite_version} · measured {when}_"


def render_readme(envelope: ResultsEnvelope) -> str:
    """Markdown block (between the TYPEBENCH markers) — one table per
    (project, thread-mode), ordered fastest-first. Includes the suite version,
    generated timestamp, and the caveat footnotes."""
    parts = [
        _README_BEGIN,
        f"\n{_dataset_line(envelope)}\n",
        *_result_tables(envelope),
        _FOOTNOTE,
        _provenance(envelope),
        _README_END,
    ]
    return "\n".join(parts)


def render_terminal(envelope: ResultsEnvelope) -> str:
    """The README's grouped tables + footnote without the HTML markers, for
    printing a readable summary at the end of a `suite` run (markdown renders
    fine as plain text in a terminal)."""
    parts = [
        f"{_dataset_line(envelope)}\n",
        *_result_tables(envelope),
        _FOOTNOTE,
        _provenance(envelope),
    ]
    return "\n".join(parts)


def build_report_html(
    template: str,
    *,
    app_js: str,
    chart_js: str,
    trends: dict[str, object],
) -> str:
    """Fold the site assets + trend data into one self-contained HTML file.

    The published site fetches `./data/trends.json` and loads `./app.js` plus the
    vendored Chart.js over the network. A local report has no server, so all three
    are inlined and the data is handed to app.js via a global it prefers over
    fetch. A raw `</script>` inside the inlined JS or JSON would close the wrapping
    `<script>` early, so it is neutralized (harmless: `<\\/script>` is identical to
    `</script>` inside JS strings/regex and a valid `/` escape in JSON)."""

    def _safe(text: str) -> str:
        return text.replace("</script", "<\\/script")

    html = template.replace(
        '<script src="./vendor/chart.umd.min.js"></script>',
        f"<script>{_safe(chart_js)}</script>",
    )
    return html.replace(
        '<script src="./app.js"></script>',
        f"<script>window.__TYPEBENCH_TRENDS__ = {_safe(json.dumps(trends))};\n"
        f"{_safe(app_js)}</script>",
    )


def _calib_median(record: RunResult) -> float | None:
    return record.calibration.raw_median_s if record.calibration is not None else None


def cpu_model_anchors(history: list[ResultsEnvelope]) -> dict[str, float]:
    """Fixed per-CPU-model calibration anchor: for each CPU model, the
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


def _kloc_value(record: RunResult, harness_wall_overhead_s: float | None = None) -> float | None:
    if record.over_reports or record.timing is None or record.timing.median_s <= 0:
        return None
    # Withhold physical-fallback / unknown denominators from the code-LOC trend so
    # GH Pages never mixes physical-LOC and code-LOC throughput as one metric.
    loc = _code_loc_or_withheld(record)
    wall = _corrected_wall_s(record, harness_wall_overhead_s)
    return (loc / 1000) / wall if loc is not None and wall is not None and wall > 0 else None


def _peak_mb_value(
    record: RunResult, harness_mem_baseline_bytes: int | None = None
) -> float | None:
    peak = _corrected_peak_bytes(record, harness_mem_baseline_bytes)
    return peak / 1_000_000 if peak is not None else None


def _files_degraded(records: list[RunResult]) -> bool:
    """A/B file-count sanity gate. The delta is trustworthy only if every arm
    analyzed a non-empty, equal file set; otherwise (missing deps, divergent
    traversal) the number is misleading and the row is marked degraded."""
    counts = [r.files for r in records]
    if any(c is None or c <= 0 for c in counts):
        return True
    return len(set(counts)) > 1


def _delta_pct(baseline: float | None, value: float | None) -> str:
    if baseline is None or value is None or baseline == 0:
        return "—"
    return f"{((value - baseline) / baseline * 100):+.1f}%"


def render_compare(envelope: ResultsEnvelope, baseline: str | None = None) -> str:
    """Terminal delta table for compare runs, grouped by project/mode/cores."""
    if not envelope.runs:
        return "_no records to compare_"

    base_id = baseline or _checker_id(envelope.runs[0])
    groups: dict[tuple[str, str, int | None], list[RunResult]] = {}
    for record in envelope.runs:
        groups.setdefault((record.project, record.thread_mode.value, record.cores), []).append(
            record
        )

    parts = [f"_compare · baseline `{base_id}` · suite `{envelope.suite_version}`_\n"]
    for project, mode, cores in sorted(
        groups, key=lambda group: (group[0], group[1], -1 if group[2] is None else group[2])
    ):
        records = groups[(project, mode, cores)]
        baseline_record = next(
            (record for record in records if _checker_id(record) == base_id), None
        )
        base_wall = (
            _corrected_wall_s(baseline_record, envelope.harness_wall_overhead_s)
            if baseline_record is not None
            else None
        )
        base_kloc = (
            _kloc_value(baseline_record, envelope.harness_wall_overhead_s)
            if baseline_record is not None
            else None
        )
        base_mem = (
            _peak_mb_value(baseline_record, envelope.harness_mem_baseline_bytes)
            if baseline_record is not None
            else None
        )

        parts.append(f"\n#### {project} — {mode} · {_cores_label(cores)}\n")
        # Header + separator + data rows must be ONE join-free block: a blank line
        # between the separator and the first row terminates the table in GFM.
        rows = [
            "| Checker | Wall median (s) | Δ wall | kLOC/s | Δ kLOC/s | Peak mem (MB) | Δ mem |",
            "|---------|-----------------|--------|--------|----------|---------------|-------|",
        ]
        for record in sorted(records, key=_sort_key):
            checker_id = _checker_id(record)
            wall = _corrected_wall_s(record, envelope.harness_wall_overhead_s)
            kloc = _kloc_value(record, envelope.harness_wall_overhead_s)
            mem = _peak_mb_value(record, envelope.harness_mem_baseline_bytes)
            is_baseline = checker_id == base_id
            delta_wall = "baseline" if is_baseline else _delta_pct(base_wall, wall)
            delta_kloc = "baseline" if is_baseline else _delta_pct(base_kloc, kloc)
            delta_mem = "baseline" if is_baseline else _delta_pct(base_mem, mem)
            wall_text = f"{wall:.2f}" if wall is not None else "—"
            kloc_text = f"{kloc:.1f}" if kloc is not None else "—"
            mem_text = f"{mem:.1f}" if mem is not None else "—"
            rows.append(
                f"| {checker_id} | {wall_text} | {delta_wall} | {kloc_text} | "
                f"{delta_kloc} | {mem_text} | {delta_mem} |"
            )
        parts.append("\n".join(rows) + "\n")
    return "\n".join(parts)


def render_ab(envelope: ResultsEnvelope, baseline: str | None = None) -> str:
    """Wall-only A/B delta table for the GitHub Action: one section per
    (project, thread-mode, cores). Columns: Checker | Wall median (s) | Δ wall |
    runs | spread. Memory and kLOC/s are intentionally omitted (local-only
    metrics). A target whose arms analyzed unequal/zero files is marked degraded."""
    if not envelope.runs:
        return "_no records to compare_"

    base_id = baseline or _checker_id(envelope.runs[0])
    groups: dict[tuple[str, str, int | None], list[RunResult]] = {}
    for record in envelope.runs:
        groups.setdefault((record.project, record.thread_mode.value, record.cores), []).append(
            record
        )

    parts = [f"_A/B · baseline {_ab_display(base_id)} · wall-time only_\n"]
    for project, mode, cores in sorted(
        groups, key=lambda group: (group[0], group[1], -1 if group[2] is None else group[2])
    ):
        records = groups[(project, mode, cores)]
        degraded = (
            " — ⚠ degraded (file counts differ across arms)" if _files_degraded(records) else ""
        )
        baseline_record = next(
            (record for record in records if _checker_id(record) == base_id), None
        )
        base_wall = (
            _corrected_wall_s(baseline_record, envelope.harness_wall_overhead_s)
            if baseline_record is not None
            else None
        )
        parts.append(f"\n#### {project} — {mode} · {_cores_label(cores)}{degraded}\n")
        rows = [
            "| Checker | Wall median (s) | Δ wall | runs | spread (min..max s) |",
            "|---------|-----------------|--------|------|---------------------|",
        ]
        for record in sorted(records, key=_sort_key):
            checker_id = _checker_id(record)
            wall = _corrected_wall_s(record, envelope.harness_wall_overhead_s)
            is_baseline = checker_id == base_id
            delta = "baseline" if is_baseline else _delta_pct(base_wall, wall)
            wall_text = f"{wall:.3f}" if wall is not None else "—"
            if record.timing is not None:
                runs_text = str(record.timing.runs)
                spread = f"{record.timing.min_s:.3f}..{record.timing.max_s:.3f}"
            else:
                runs_text = "—"
                spread = "—"
            rows.append(
                f"| {_ab_display(checker_id)} | {wall_text} | {delta} | {runs_text} | {spread} |"
            )
        parts.append("\n".join(rows) + "\n")
    parts.append(
        "\n> Wall-time deltas on a shared CI runner. Treat small deltas (≲ a few %) as "
        "noise; arms run sequentially per target. Memory/throughput are measured locally, "
        "not here.\n"
    )
    return "\n".join(parts)


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
            wall = _corrected_wall_s(record, envelope.harness_wall_overhead_s)
            wall_norm = (
                wall * anchor / calib
                if wall is not None and anchor is not None and calib is not None and calib > 0
                else None
            )
            peak_mb = _peak_mb_value(record, envelope.harness_mem_baseline_bytes)
            checker_id = _checker_id(record)
            points.append(
                {
                    "date": date,
                    "suite_version": envelope.suite_version,
                    "project": record.project,
                    "thread_mode": record.thread_mode.value,
                    "cores": record.cores,
                    "checker_id": checker_id,
                    "tool": record.tool,
                    "version": record.tool_version,
                    "label": checker_id,
                    "cpu_model": record.env.cpu_model,
                    "wall_median_s": wall,
                    "wall_median_s_norm": wall_norm,
                    "peak_mem_mb": peak_mb,
                    "kloc_s": _kloc_value(record, envelope.harness_wall_overhead_s),
                    "calib_median_s": calib,
                    "calib_anchor_s": anchor,
                }
            )
    return {
        "cpu_models": sorted(anchors),
        "points": points,
        "corpus_markers": markers,
    }
