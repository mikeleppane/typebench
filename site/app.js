// Fetch the committed trends.json and render a selectable per-snapshot matrix
// plus a per-tool trend line chart. The trend draws ONE line per tool viewed per
// CPU budget (thread mode + cores); version bumps are markers on that line, not
// separate series, so the legend stays bounded as history grows. No build step:
// vanilla JS + vendored Chart.js.

// Per-tool base colors (close to each tool's brand). One line per tool means a
// tool always reads as one color; the active version is shown via the marker
// and tooltip rather than a per-version color shift.
const TOOL_COLORS = {
  mypy: "#3572A5",
  pyright: "#1f8a3b",
  pyrefly: "#d98a2b",
  ty: "#8957e5",
  stub: "#8a929e",
};

// Metric metadata drives the axis title, tooltip units, and the "lower/higher is
// better" hint shown next to the chart title.
const METRICS = {
  wall_median_s_norm: {
    axis: "Wall, normalized (s)",
    unit: "s",
    digits: 3,
    better: "lower",
    band: { min: "wall_min_s_norm", max: "wall_max_s_norm", stddev: "wall_stddev_s_norm" },
  },
  wall_median_s: {
    axis: "Wall (s)",
    unit: "s",
    digits: 3,
    better: "lower",
    band: { min: "wall_min_s", max: "wall_max_s", stddev: "wall_stddev_s" },
  },
  peak_mem_mb: { axis: "Peak memory (MB)", unit: "MB", digits: 1, better: "lower" },
  kloc_s: { axis: "Throughput (kLOC/s)", unit: "kLOC/s", digits: 1, better: "higher" },
};

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function colorFor(tool) {
  return TOOL_COLORS[tool] || "#5c6470";
}

function bandColor(color) {
  const hex = color.startsWith("#") ? color.slice(1) : null;
  if (hex) {
    const expanded = hex.length === 3 ? hex.replace(/./g, "$&$&") : hex;
    const value = parseInt(expanded, 16);
    if (Number.isFinite(value)) {
      return `rgba(${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}, 0.13)`;
    }
  }
  const rgb = /^rgb\((\d+),\s*(\d+),\s*(\d+)\)$/.exec(color);
  if (rgb) return `rgba(${rgb[1]}, ${rgb[2]}, ${rgb[3]}, 0.13)`;
  return "rgba(92, 100, 112, 0.13)";
}

// Only used when one tool has two versions on the SAME date (an A/B comparison):
// the lines split per checker_id and need distinct shades of the tool's color.
function shiftLightness(hex, amount) {
  const digits = hex.slice(1);
  const expanded = digits.length === 3 ? digits.replace(/./g, "$&$&") : digits;
  const value = parseInt(expanded, 16);
  const clamp = (n) => Math.max(0, Math.min(255, n));
  const r = clamp(((value >> 16) & 255) + amount);
  const g = clamp(((value >> 8) & 255) + amount);
  const b = clamp((value & 255) + amount);
  return `rgb(${r}, ${g}, ${b})`;
}

// Trend range presets bound the x-axis as history accrues. Anchored to the latest
// date in the series, so the window is stable regardless of which project is shown.
const WINDOW_DAYS = { "90d": 90, "1y": 365, all: null };

function windowDates(dates, key) {
  const days = WINDOW_DAYS[key];
  if (!days || dates.length === 0) return dates;
  const cutoff = new Date(dates[dates.length - 1] + "T00:00:00Z");
  cutoff.setUTCDate(cutoff.getUTCDate() - days);
  const iso = cutoff.toISOString().slice(0, 10);
  return dates.filter((d) => d >= iso);
}

function coresLabel(c) {
  return c === null || c === undefined ? "all-cores" : String(c);
}

// A "CPU budget" collapses (thread_mode, cores) into one selectable axis, so
// impossible combinations (all-cores mode + 1 core) can never be selected.
function budgetKey(p) {
  return `${p.thread_mode}|${coresLabel(p.cores)}`;
}

function budgetText(p) {
  if (p.cores === null || p.cores === undefined) return p.thread_mode.replace("-", " ");
  return `${p.cores} core${p.cores === 1 ? "" : "s"}`;
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (ch) => `&#${ch.charCodeAt(0)};`);
}

function isFiniteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function formatMetric(value, meta) {
  return `${value.toFixed(meta.digits)} ${meta.unit}`;
}

// Relative-speed (speedup) view. The reader anchors the field to one tool (mypy
// by default, the reference checker) or to the field average; every other tool
// reads as a multiple of that anchor's wall — higher = faster. Geometric mean
// averages the per-project speedups so no single big project dominates.
const GEOMEAN_ANCHOR = "__geomean__";

function geomean(xs) {
  return Math.exp(xs.reduce((a, x) => a + Math.log(x), 0) / xs.length);
}

// Power-of-two axis ticks that always ENCLOSE the data (so an extreme bar can
// never render past the axis) and keep 1× on a tick. Chart.js auto log ticks
// crowd illegibly near 1×; pinning this clean set keeps the scale readable and
// symmetric around the baseline. Collapsed input (everything at 1×) widens by a
// tick each way so the axis is never degenerate.
function ratioScale(values) {
  const lo = Math.min(1, ...values);
  const hi = Math.max(1, ...values);
  let minExp = Math.floor(Math.log2(lo));
  let maxExp = Math.ceil(Math.log2(hi));
  if (minExp === maxExp) {
    minExp -= 1;
    maxExp += 1;
  }
  const ticks = [];
  for (let e = minExp; e <= maxExp; e += 1) ticks.push(2 ** e);
  return { min: 2 ** minExp, max: 2 ** maxExp, ticks };
}

const FAILURE_LABELS = {
  "failed{crash}": { glyph: "✗", label: "crash" },
  "failed{oom}": { glyph: "⚠", label: "OOM" },
  "failed{timeout}": { glyph: "⧖", label: "timeout" },
  "failed{env}": { glyph: "⚙", label: "env" },
};

function failureMeta(resultClass) {
  return FAILURE_LABELS[resultClass] || { glyph: "✗", label: resultClass || "failed" };
}

function failureTitle(failure) {
  const parts = [failure.result_class, `exit code ${failure.real_exit_code}`];
  if (failure.failure_phase) parts.push(`phase ${failure.failure_phase}`);
  if (failure.signal !== null && failure.signal !== undefined) {
    parts.push(`signal ${failure.signal}`);
  }
  if (failure.timed_out) parts.push("timed out");
  if (failure.oom) parts.push("oom");
  if (failure.error_detail) parts.push(failure.error_detail);
  return parts.join(" · ");
}

function coverageText(measured, failed, total, failures) {
  if (failed === 0) return `Coverage: ${measured}/${total} measured`;
  const byClass = new Map();
  for (const failure of failures) {
    byClass.set(failure.result_class, (byClass.get(failure.result_class) || 0) + 1);
  }
  const groups = Object.keys(FAILURE_LABELS)
    .filter((resultClass) => byClass.has(resultClass))
    .map((resultClass) => `${failureMeta(resultClass).glyph}${byClass.get(resultClass)}`);
  return `Coverage: ${measured}/${total} measured · ${failed} failed (${groups.join(" · ")})`;
}

function setText(id, text) {
  document.getElementById(id).textContent = text;
}

function applyChartTheme() {
  if (typeof Chart === "undefined") return;
  Chart.defaults.font.family =
    "'IBM Plex Mono', ui-monospace, 'SF Mono', Menlo, Consolas, monospace";
  Chart.defaults.font.size = 11;
  Chart.defaults.color = cssVar("--text-muted");
  Chart.defaults.borderColor = cssVar("--grid");
}

function fillStats(points, markers, cpuModels) {
  const dates = [...new Set(points.map((p) => p.date))].sort();
  const latest = dates.length ? dates[dates.length - 1] : null;
  setText("stat-date", latest || "—");
  setText("stat-projects", new Set(points.map((p) => p.project)).size || "—");
  // Count distinct *tools* in the latest snapshot, not checker_ids: a version
  // bump (ty@0.0.48 -> ty@0.0.49) is the same checker, so it must not inflate
  // the headline count. Within one snapshot each tool runs a single version.
  const latestTools = new Set(
    points.filter((p) => p.date === latest).map((p) => p.tool)
  );
  setText("stat-checkers", latestTools.size || "—");
  setText("stat-runs", markers.length || "—");

  const prov = document.getElementById("provenance");
  if (cpuModels.length) {
    prov.hidden = false;
    prov.textContent =
      `measured on ${cpuModels.join(" · ")} — absolute times are machine-specific; ` +
      "compare rows within one suite, or the normalized series across time.";
  }
}

// Presentation-only tiers, ordered small -> large. The cutoffs live in Python
// (renderer._size_tier); the site only orders the labels it is handed.
const TIER_ORDER = { Small: 0, Medium: 1, Large: 2 };

// Per-row rank tint: t in [0,1] maps best (green) -> worst (red). The best cell
// also gets an accent ring via CSS, so the winner never blends into the ramp.
function rankTint(t) {
  const h = 152 + (6 - 152) * t;
  const s = 55 + (65 - 55) * t;
  const l = 46 + (52 - 46) * t;
  return `hsla(${h}, ${s}%, ${l}%, 0.3)`;
}

// Short CPU label (e.g. "i9-14900K") for a delta tooltip, so a cross-machine
// comparison names the other machine instead of a full marketing string.
function shortCpu(model) {
  if (!model) return "?";
  const m = /i\d-\d{3,5}\w*/i.exec(model);
  return m ? m[0] : model;
}

// Version-over-version delta for one cell: the most recent EARLIER run that ran
// the same tool on the same project + budget at a DIFFERENT version and has a
// value for the active metric. Returns null when there is no such prior, so a
// first-ever version simply shows no delta. CPU is ignored on purpose; a pair on
// different machines is flagged (crossCpu) because raw times are not comparable
// across machines — the calibration-normalized wall metric is the one that is.
function versionDelta(sel, cur, metricKey) {
  if (!cur || cur[metricKey] === null || cur[metricKey] === undefined) return null;
  const priors = sel.filter(
    (p) =>
      p.project === cur.project &&
      p.tool === cur.tool &&
      p.date < cur.date &&
      p.version !== cur.version &&
      p[metricKey] !== null &&
      p[metricKey] !== undefined
  );
  if (!priors.length) return null;
  const prior = priors.reduce((a, b) => (b.date > a.date ? b : a));
  const base = prior[metricKey];
  if (!base) return null;
  return {
    pct: ((cur[metricKey] - base) / base) * 100,
    prior,
    crossCpu: prior.cpu_model !== cur.cpu_model,
  };
}

// Latest-date comparison as a projects × checkers matrix for one metric + budget:
// rows are every project (tiered small -> large), columns the checkers, each cell
// the metric value. The best cell in a row is highlighted; cells are rank-tinted.
// Clicking a row calls onSelect(project) so the trend chart below can follow it.
function renderMatrix(
  sel,
  failures,
  metricKey,
  meta,
  budgetLabel,
  selected,
  snapshotDate,
  onSelect
) {
  const card = document.getElementById("snap-card");
  const headEl = document.getElementById("snap-head");
  const bodyEl = document.getElementById("snap-body");
  const coverageEl = document.getElementById("snap-coverage");
  const dates = [
    ...new Set([...sel.map((p) => p.date), ...failures.map((p) => p.date)]),
  ].sort();
  // Render the picked snapshot date; fall back to the latest available in this
  // budget when that date has no rows here (e.g. a budget that started later).
  const date = dates.includes(snapshotDate) ? snapshotDate : dates[dates.length - 1];
  // Build rows, columns, and cells from this one date only. Checker versions or
  // corpus projects that existed solely in other runs must not leave stale,
  // all-dashes lines in the snapshot.
  const latestRows = sel.filter((p) => p.date === date);
  const latestFailures = failures.filter((p) => p.date === date);

  const checkers = [
    ...new Set([
      ...latestRows.map((p) => p.checker_id),
      ...latestFailures.map((p) => p.checker_id),
    ]),
  ].sort();
  const projMeta = new Map();
  for (const p of [...latestRows, ...latestFailures]) {
    if (!projMeta.has(p.project)) {
      projMeta.set(p.project, { tier: p.size_tier || "Large", loc: p.code_loc ?? 0 });
    }
  }
  const projects = [...projMeta.keys()].sort((a, b) => {
    const ma = projMeta.get(a);
    const mb = projMeta.get(b);
    return (
      (TIER_ORDER[ma.tier] ?? 9) - (TIER_ORDER[mb.tier] ?? 9) ||
      ma.loc - mb.loc ||
      a.localeCompare(b)
    );
  });

  card.classList.toggle("is-empty", projects.length === 0 || checkers.length === 0);
  setText("snap-title", date ? `Snapshot — ${date}` : "Snapshot");
  setText("snap-meta", projects.length ? `${budgetLabel} · ${meta.better} is better` : "");
  coverageEl.textContent = "";
  if (!projects.length || !checkers.length) {
    headEl.innerHTML = "";
    bodyEl.innerHTML = "";
    return;
  }

  headEl.innerHTML =
    `<tr><th>Project</th>` +
    checkers.map((c) => `<th>${escapeHtml(c)}</th>`).join("") +
    `</tr>`;

  let html = "";
  let currentTier = null;
  let anyDelta = false;
  let approxShown = false;
  let measured = 0;
  const visibleFailures = [];
  for (const project of projects) {
    const { tier, loc } = projMeta.get(project);
    if (tier !== currentTier) {
      currentTier = tier;
      html += `<tr class="tier-row"><td colspan="${checkers.length + 1}">${escapeHtml(
        tier
      )} projects</td></tr>`;
    }

    const cells = checkers.map((c) => {
      const hit = latestRows.find((p) => p.project === project && p.checker_id === c);
      const v =
        hit && hit[metricKey] !== null && hit[metricKey] !== undefined ? hit[metricKey] : null;
      const failure = latestFailures.find((p) => p.project === project && p.checker_id === c);
      // Coverage counts measured-successes vs failures by RUN outcome, not by whether
      // the selected metric is populated: a withheld metric (e.g. throughput for an
      // over-reporter, or wall-normalized without a calibration anchor) is still a
      // successful run, not a coverage gap. So count the success point (`hit`), not a
      // non-null metric value, and keep the count stable across metric changes.
      if (hit) measured += 1;
      else if (failure) visibleFailures.push(failure);
      return { failure, hit, v };
    });
    const present = cells.map((c) => c.v).filter((v) => v !== null);
    const best = present.length
      ? meta.better === "lower"
        ? Math.min(...present)
        : Math.max(...present)
      : null;
    const lo = Math.min(...present);
    const hi = Math.max(...present);
    const span = hi - lo;

    const locHint = loc ? ` <small>${(loc / 1000).toFixed(0)}k</small>` : "";
    const isSel = project === selected;
    // The row acts as a button that repoints the trend chart; expose it to the
    // keyboard and assistive tech (the project <select> it replaced was focusable).
    html +=
      `<tr class="proj-row${isSel ? " is-selected" : ""}" data-project="${escapeHtml(project)}" ` +
      `role="button" tabindex="0" aria-pressed="${isSel}" ` +
      `aria-label="Show ${escapeHtml(project)} trend over time">`;
    html += `<td class="proj">${escapeHtml(project)}${locHint}</td>`;
    for (const { failure, hit, v } of cells) {
      if (v === null) {
        if (failure) {
          const fail = failureMeta(failure.result_class);
          const detail = failureTitle(failure);
          html +=
            `<td class="cell fail" title="${escapeHtml(detail)}" ` +
            `aria-label="${escapeHtml(detail)}"><span class="c">` +
            `<span aria-hidden="true">${fail.glyph}</span> ${escapeHtml(fail.label)}</span></td>`;
          continue;
        }
        html += `<td class="cell na"><span class="c">—</span></td>`;
        continue;
      }
      const t =
        span > 0 ? (meta.better === "lower" ? (v - lo) / span : (hi - v) / span) : 0;
      const isBest = present.length > 1 && v === best;
      let titleAttr = "";
      let spreadHtml = "";
      if (meta.band && hit) {
        const spreadMin = hit[meta.band.min];
        const spreadMax = hit[meta.band.max];
        const spreadStddev = hit[meta.band.stddev];
        if (isFiniteNumber(spreadMin) && isFiniteNumber(spreadMax)) {
          titleAttr =
            ` title="${escapeHtml(
              `min–max ${formatMetric(spreadMin, meta)}–${formatMetric(spreadMax, meta)}`
            )}"`;
        }
        if (isFiniteNumber(spreadStddev) && spreadStddev > 0) {
          spreadHtml = `<span class="spread">± ${formatMetric(spreadStddev, meta)}</span>`;
        }
      }
      let deltaHtml = "";
      const d = versionDelta(sel, hit, metricKey);
      if (d) {
        anyDelta = true;
        // A cross-machine pair is only comparable on the calibration-normalized
        // metric; for raw wall / memory / throughput the delta is shown neutral
        // and dotted ("approximate") rather than a confident better/worse, so a
        // machine-specific number is never read as a real version regression.
        const approx = d.crossCpu && metricKey !== "wall_median_s_norm";
        const flat = Math.abs(d.pct) < 0.05;
        const better = meta.better === "lower" ? d.pct < 0 : d.pct > 0;
        const cls = approx ? " approx" : flat ? "" : better ? " good" : " bad";
        const arrow = d.pct > 0 ? "▲" : d.pct < 0 ? "▼" : "•";
        if (approx) approxShown = true;
        const machine = d.crossCpu ? ` · ${shortCpu(d.prior.cpu_model)}` : "";
        const title = approx
          ? `vs ${d.prior.version} · ${d.prior.date}${machine} — different machine; ` +
            `raw values aren't comparable, use normalized wall`
          : `vs ${d.prior.version} · ${d.prior.date}${machine}`;
        deltaHtml =
          `<span class="delta${cls}" title="${escapeHtml(title)}">` +
          `${approx ? "≈" : ""}${arrow}${Math.abs(d.pct).toFixed(1)}%</span>`;
      }
      // Spread (± tolerance) and the version delta share one sub-row under the
      // value so they read side by side instead of stacking two lines tall.
      const submeta =
        spreadHtml || deltaHtml ? `<span class="submeta">${spreadHtml}${deltaHtml}</span>` : "";
      html += `<td class="cell${isBest ? " best" : ""}"${titleAttr}><span class="c" style="background:${rankTint(
        t
      )}">${v.toFixed(meta.digits)}</span>${submeta}</td>`;
    }
    html += `</tr>`;
  }
  bodyEl.innerHTML = html;
  coverageEl.textContent = coverageText(
    measured,
    visibleFailures.length,
    projects.length * checkers.length,
    visibleFailures
  );

  // The delta key only appears when at least one cell shows a delta, so the
  // legend teaches the encoding exactly when it is on screen and stays quiet
  // otherwise. The arrow→better mapping follows the current metric's polarity,
  // so the reader never has to know whether up or down is good. The "≈ other
  // machine" chip is added only when an approximate (cross-machine) delta shows.
  const keyEl = document.getElementById("snap-delta-key");
  if (anyDelta) {
    const betterArrow = meta.better === "lower" ? "▼" : "▲";
    const worseArrow = meta.better === "lower" ? "▲" : "▼";
    keyEl.hidden = false;
    keyEl.innerHTML =
      `<span class="matrix-legend__hint">Δ vs previous version</span>` +
      `<span class="delta good">${betterArrow} better</span>` +
      `<span class="delta bad">${worseArrow} worse</span>` +
      (approxShown ? `<span class="delta approx">≈ other machine</span>` : "");
  } else {
    keyEl.hidden = true;
  }

  for (const row of bodyEl.querySelectorAll(".proj-row")) {
    const pick = () => onSelect(row.dataset.project);
    row.addEventListener("click", pick);
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        pick();
      }
    });
  }
}

async function main() {
  applyChartTheme();

  // The self-contained local report (typebench report) injects the data as a
  // global; the published site has no such global and fetches the committed file.
  let data = window.__TYPEBENCH_TRENDS__ || { points: [] };
  if (!window.__TYPEBENCH_TRENDS__) {
    try {
      const res = await fetch("./data/trends.json");
      if (res.ok) data = await res.json();
    } catch {
      // No committed trends yet — the empty state covers this.
    }
  }
  const points = data.points || [];
  const failures = data.failures || [];
  fillStats(points, data.corpus_markers || [], data.cpu_models || []);

  // Projects ordered small -> large (by analyzed code-LOC) so the trend defaults
  // to the smallest project; the matrix re-derives the same order for its rows.
  const locOf = (project) => points.find((p) => p.project === project)?.code_loc ?? 0;
  const projectOrder = [...new Set(points.map((p) => p.project))].sort(
    (a, b) => locOf(a) - locOf(b) || a.localeCompare(b)
  );
  let selectedProject = projectOrder[0] || null;

  const budgetRows = [...points, ...failures];
  const budgets = [...new Map(budgetRows.map((p) => [budgetKey(p), p])).values()]
    .map((p) => ({ key: budgetKey(p), cores: p.cores ?? null, label: budgetText(p) }))
    .sort((a, b) => {
      if (a.cores === null) return -1; // all-cores budget always leads
      if (b.cores === null) return 1;
      return a.cores - b.cores;
    });

  document.getElementById("budget").innerHTML = budgets
    .map((b) => `<option value="${escapeHtml(b.key)}">${escapeHtml(b.label)}</option>`)
    .join("");

  // Anchor selector for the speedup card: the field average (neutral) plus every
  // tool present, so the reader can re-anchor freely. Default to mypy — the
  // reference checker everyone knows — falling back to the field average if this
  // corpus has no mypy rows at all.
  const ratioAnchorEl = document.getElementById("ratio-anchor");
  const ratioAnchorOptions = [
    { value: GEOMEAN_ANCHOR, label: "field average (geomean)" },
    ...[...new Set(points.map((p) => p.tool))]
      .sort()
      .map((tool) => ({ value: tool, label: tool })),
  ];
  ratioAnchorEl.innerHTML = ratioAnchorOptions
    .map((option) => `<option value="${escapeHtml(option.value)}">${escapeHtml(option.label)}</option>`)
    .join("");
  ratioAnchorEl.value = points.some((p) => p.tool === "mypy") ? "mypy" : GEOMEAN_ANCHOR;

  // Snapshot picker drives the matrix date (newest first, latest selected). The
  // matrix no longer hardcodes "latest", so older official runs are reachable from
  // the page itself instead of only via the committed JSON on GitHub.
  const budgetEl = document.getElementById("budget");
  const snapshotEl = document.getElementById("snapshot");
  let selectedSnapshot = null;

  // Options are scoped to the SELECTED budget: a date with no rows for the current
  // budget (e.g. a 4-core sweep absent from a later run) must not appear, so the
  // picker can never name a date the table is not actually showing. Called on init
  // and whenever the budget changes; the selection is clamped to an available date.
  function syncSnapshots() {
    const avail = [
      ...new Set(budgetRows.filter((p) => budgetKey(p) === budgetEl.value).map((p) => p.date)),
    ].sort();
    if (!avail.includes(selectedSnapshot)) {
      selectedSnapshot = avail[avail.length - 1] || null;
    }
    snapshotEl.innerHTML = avail
      .slice()
      .reverse()
      .map((d) => `<option value="${escapeHtml(d)}">${escapeHtml(d)}</option>`)
      .join("");
    if (selectedSnapshot) snapshotEl.value = selectedSnapshot;
  }

  // Open on a budget that actually covers the latest snapshot, so the newest run
  // is visible by default even when it dropped a budget the older runs carried
  // (e.g. a desktop sweep with no all-cores pass). Budgets keep their order, so
  // the default returns to all-cores automatically once a run re-includes it.
  const latestDate = [...new Set(budgetRows.map((p) => p.date))].sort().pop();
  budgetEl.value = (
    budgets.find((b) => budgetRows.some((p) => p.date === latestDate && budgetKey(p) === b.key)) ||
    budgets[0]
  ).key;

  syncSnapshots();

  const card = document.getElementById("chart-card");
  const titleEl = document.getElementById("chart-title");
  const metaEl = document.getElementById("chart-meta");
  const ctx = document.getElementById("chart").getContext("2d");
  let chart = null;

  const scalingCard = document.getElementById("scaling-card");
  const scalingTitleEl = document.getElementById("scaling-title");
  const scalingMetaEl = document.getElementById("scaling-meta");
  const scalingCtx = document.getElementById("scaling").getContext("2d");
  let scalingChart = null;

  const ratioCard = document.getElementById("ratio-card");
  const ratioTitleEl = document.getElementById("ratio-title");
  const ratioMetaEl = document.getElementById("ratio-meta");
  const ratioCtx = document.getElementById("ratio").getContext("2d");
  let ratioChart = null;

  function renderScaling() {
    const project = selectedProject;
    const rows = points.filter(
      (p) =>
        p.project === project &&
        p.date === selectedSnapshot &&
        p.thread_mode === "constrained" &&
        p.cores !== null &&
        p.cores !== undefined &&
        isFiniteNumber(p.parallel_efficiency)
    );
    const coreCounts = [...new Set(rows.map((p) => p.cores))].sort((a, b) => a - b);
    const hasData = coreCounts.length >= 2;

    scalingTitleEl.textContent = `${project ?? "—"} — core scaling`;
    scalingMetaEl.textContent =
      hasData && selectedSnapshot
        ? `${selectedSnapshot} · parallel efficiency = CPU-time ÷ wall; higher = more cores utilized`
        : "";
    scalingCard.classList.toggle("is-empty", !hasData);

    if (scalingChart) scalingChart.destroy();
    if (!hasData) return;

    const byChecker = new Map();
    for (const row of rows) {
      if (!byChecker.has(row.checker_id)) byChecker.set(row.checker_id, []);
      byChecker.get(row.checker_id).push(row);
    }

    const datasets = [...byChecker.keys()].sort().map((checkerId) => {
      const recs = byChecker.get(checkerId).sort((a, b) => a.cores - b.cores);
      const color = colorFor(recs[0].tool);
      return {
        label: checkerId,
        borderColor: color,
        backgroundColor: color,
        pointBackgroundColor: color,
        pointBorderColor: color,
        pointBorderWidth: 1.5,
        pointRadius: 3.5,
        pointHoverRadius: 6,
        borderWidth: 2,
        tension: 0.25,
        data: recs.map((p) => ({ x: p.cores, y: p.parallel_efficiency })),
      };
    });

    scalingChart = new Chart(scalingCtx, {
      type: "line",
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "nearest", intersect: false },
        plugins: {
          legend: {
            position: "bottom",
            labels: {
              usePointStyle: true,
              pointStyle: "circle",
              boxWidth: 8,
              boxHeight: 8,
              padding: 16,
            },
          },
          tooltip: {
            backgroundColor: cssVar("--surface"),
            titleColor: cssVar("--text"),
            bodyColor: cssVar("--text-muted"),
            borderColor: cssVar("--border-strong"),
            borderWidth: 1,
            padding: 10,
            usePointStyle: true,
            boxPadding: 4,
            callbacks: {
              label: (item) => {
                const cores = item.parsed.x;
                const eff = item.parsed.y;
                return `${item.dataset.label}: ${eff.toFixed(2)}× at ${cores}c`;
              },
            },
          },
        },
        scales: {
          x: {
            type: "linear",
            grid: { display: false },
            min: coreCounts[0],
            max: coreCounts[coreCounts.length - 1],
            afterBuildTicks: (axis) => {
              axis.ticks = coreCounts.map((value) => ({ value }));
            },
            title: { display: true, text: "Cores", color: cssVar("--text-faint") },
            ticks: {
              maxRotation: 0,
              callback: (value) => {
                const cores = Number(value);
                return coreCounts.includes(cores) ? `${cores}c` : "";
              },
            },
          },
          y: {
            beginAtZero: true,
            border: { display: false },
            title: {
              display: true,
              text: "Parallel efficiency (CPU-time ÷ wall)",
              color: cssVar("--text-faint"),
            },
            ticks: { padding: 6 },
          },
        },
      },
    });
  }

  // Aggregate one snapshot+budget into per-tool speedups against `anchor`. Within
  // a snapshot a tool has one version, so one wall per tool per project (a stray
  // duplicate keeps the first by checker_id). speedup = anchor_wall / tool_wall,
  // so a tool that runs in half the anchor's time reads 2.00×. A stale named
  // anchor absent from this view falls back to the field average rather than
  // blanking the card. n/denom surfaces tools that skipped (or failed) projects.
  function ratioRowsFor(anchor, rows) {
    const projects = [...new Set(rows.map((p) => p.project))];
    const projectWalls = new Map();
    for (const project of projects) {
      const recs = rows
        .filter((p) => p.project === project)
        .sort((a, b) => a.checker_id.localeCompare(b.checker_id));
      const wallsByTool = new Map();
      for (const p of recs) {
        if (!wallsByTool.has(p.tool)) wallsByTool.set(p.tool, p.wall_median_s);
      }
      projectWalls.set(project, wallsByTool);
    }

    let effectiveAnchor = anchor;
    if (anchor !== GEOMEAN_ANCHOR && !rows.some((p) => p.tool === anchor)) {
      effectiveAnchor = GEOMEAN_ANCHOR;
    }

    const speedups = new Map();
    let denom = 0;
    for (const project of projects) {
      const wallsByTool = projectWalls.get(project);
      let base;
      if (effectiveAnchor === GEOMEAN_ANCHOR) {
        const walls = [...wallsByTool.values()];
        if (!walls.length) continue;
        base = geomean(walls);
      } else if (wallsByTool.has(effectiveAnchor)) {
        base = wallsByTool.get(effectiveAnchor);
      } else {
        continue; // anchor tool didn't run this project — skip it
      }
      denom += 1;
      for (const [tool, wall] of wallsByTool) {
        if (!speedups.has(tool)) speedups.set(tool, []);
        speedups.get(tool).push(base / wall);
      }
    }

    const results = [...speedups.entries()]
      .filter(([, values]) => values.length > 0)
      .map(([tool, values]) => ({ tool, speedup: geomean(values), n: values.length }))
      .sort((a, b) => b.speedup - a.speedup || a.tool.localeCompare(b.tool));
    return { results, denom, effectiveAnchor };
  }

  function renderRatio() {
    const budget = budgetEl.value;
    const budgetLabel =
      budgets.find((b) => b.key === budget)?.label || budget.replace("|", " · ");
    const requestedAnchor = ratioAnchorEl.value || GEOMEAN_ANCHOR;
    const rows = points.filter(
      (p) =>
        p.date === selectedSnapshot &&
        budgetKey(p) === budget &&
        isFiniteNumber(p.wall_median_s) &&
        p.wall_median_s > 0
    );
    const { results, denom, effectiveAnchor } = ratioRowsFor(requestedAnchor, rows);
    const hasData = results.length >= 2;
    const anchorLabel = effectiveAnchor === GEOMEAN_ANCHOR ? "field average" : effectiveAnchor;

    ratioTitleEl.textContent = selectedSnapshot
      ? `Relative speed — ${selectedSnapshot}`
      : "Relative speed";
    ratioMetaEl.textContent = hasData
      ? `${budgetLabel} · speedup vs ${anchorLabel} · higher = faster · geomean across ${denom} projects`
      : "";
    ratioCard.classList.toggle("is-empty", !hasData);

    if (ratioChart) ratioChart.destroy();
    if (!hasData) return;

    // Floating bars emanate from the 1× baseline: faster tools (>1) extend right,
    // slower (<1) left, on a log axis so 0.5× and 2× are visually symmetric.
    const { min, max, ticks } = ratioScale(results.map((r) => r.speedup));

    ratioChart = new Chart(ratioCtx, {
      type: "bar",
      data: {
        labels: results.map((r) => r.tool),
        datasets: [
          {
            label: "Speedup",
            data: results.map((r) => [Math.min(1, r.speedup), Math.max(1, r.speedup)]),
            backgroundColor: results.map((r) => colorFor(r.tool)),
            borderColor: results.map((r) => colorFor(r.tool)),
            borderWidth: 1,
            results,
          },
        ],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "nearest", intersect: true },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: cssVar("--surface"),
            titleColor: cssVar("--text"),
            bodyColor: cssVar("--text-muted"),
            borderColor: cssVar("--border-strong"),
            borderWidth: 1,
            padding: 10,
            callbacks: {
              label: (item) => {
                const r = item.dataset.results[item.dataIndex];
                if (r.tool === effectiveAnchor) return `${r.tool}: 1.00× (reference)`;
                // Within rounding of the baseline "faster/slower" reads as a
                // contradiction (e.g. "1.00× faster"), so call it level instead.
                if (Math.abs(r.speedup - 1) < 0.005) {
                  return `${r.tool}: ~1.00× (≈ same as ${anchorLabel}, ${r.n}/${denom} projects)`;
                }
                const rel = r.speedup >= 1 ? "faster" : "slower";
                return `${r.tool}: ${r.speedup.toFixed(2)}× ${rel} than ${anchorLabel} (${r.n}/${denom} projects)`;
              },
            },
          },
        },
        scales: {
          x: {
            type: "logarithmic",
            min,
            max,
            title: {
              display: true,
              text: "× speedup (1 = baseline)",
              color: cssVar("--text-faint"),
            },
            // Pin the readable tick set instead of Chart.js's crowded auto log ticks.
            afterBuildTicks: (axis) => {
              axis.ticks = ticks.map((value) => ({ value }));
            },
            ticks: { callback: (value) => `${value}×` },
          },
          y: {
            border: { display: false },
            ticks: { padding: 6 },
          },
        },
      },
    });
  }

  function render() {
    const metricKey = document.getElementById("metric").value;
    const budget = document.getElementById("budget").value;
    const meta = METRICS[metricKey];
    const budgetLabel =
      budgets.find((b) => b.key === budget)?.label || budget.replace("|", " · ");

    // The matrix sees every project at this budget; clicking a row repoints the
    // trend chart below. The trend stays pinned to one project (its time axis).
    const selByBudget = points.filter((p) => budgetKey(p) === budget);
    const failByBudget = failures.filter((p) => budgetKey(p) === budget);
    renderMatrix(
      selByBudget,
      failByBudget,
      metricKey,
      meta,
      budgetLabel,
      selectedProject,
      selectedSnapshot,
      (project) => {
        selectedProject = project;
        render();
      }
    );

    const project = selectedProject;
    const sel = selByBudget.filter((p) => p.project === project);
    const allDates = [...new Set(sel.map((p) => p.date))].sort();
    const dates = windowDates(allDates, document.getElementById("window").value);

    // Default to ONE line per tool, so version bumps across dates collapse into a
    // single series (markers flag where the version changed) and the legend stays
    // bounded as history grows. Exception: when a tool has >1 version on the SAME
    // date (an A/B comparison), that tool splits into one line per checker_id so
    // neither version is silently hidden — build_trends keeps both rows (see
    // tests/suite/test_renderer.py::test_build_trends_distinguishes_same_day_versions).
    const byTool = new Map();
    for (const p of sel) {
      if (!byTool.has(p.tool)) byTool.set(p.tool, []);
      byTool.get(p.tool).push(p);
    }
    const lines = [];
    for (const tool of [...byTool.keys()].sort()) {
      const recs = byTool.get(tool);
      const versionsByDate = new Map();
      let split = false;
      for (const p of recs) {
        if (!versionsByDate.has(p.date)) versionsByDate.set(p.date, new Set());
        const ids = versionsByDate.get(p.date);
        ids.add(p.checker_id);
        if (ids.size > 1) split = true;
      }
      const base = colorFor(tool);
      if (!split) {
        lines.push({ label: tool, color: base, match: (p) => p.tool === tool });
      } else {
        const cids = [...new Set(recs.map((r) => r.checker_id))].sort();
        cids.forEach((cid, i) => {
          const color = shiftLightness(base, (i - (cids.length - 1) / 2) * 34);
          lines.push({ label: cid, color, match: (p) => p.checker_id === cid });
        });
      }
    }

    const datasets = [];
    for (const line of lines) {
      const color = line.color;
      // sel is pinned to one (project, thread_mode, cores) budget, and each line
      // matches a single row per date; the version lives in checker_id, carried for
      // the tooltip and for marking bumps on the collapsed per-tool lines.
      const rows = dates.map((d) => sel.find((p) => p.date === d && line.match(p)) || null);
      const cids = rows.map((r) => (r ? r.checker_id : null));
      // A "bump" is a point whose version changed vs the previous present date on
      // this line; the first present point is the baseline, not a bump. Split
      // (per-checker_id) lines hold one version, so they never flag a bump.
      let prev = null;
      const bumps = cids.map((cid) => {
        if (cid === null) return false;
        const changed = prev !== null && cid !== prev;
        prev = cid;
        return changed;
      });
      if (meta.band) {
        const bandPairs = rows.map((r) => {
          const min = r ? r[meta.band.min] : null;
          const max = r ? r[meta.band.max] : null;
          return isFiniteNumber(min) && isFiniteNumber(max) ? { min, max } : null;
        });
        const upper = bandPairs.map((pair) => (pair ? pair.max : null));
        const lower = bandPairs.map((pair) => (pair ? pair.min : null));
        const hasBand = upper.some(
          (max, i) => isFiniteNumber(max) && isFiniteNumber(lower[i]) && max !== lower[i]
        );
        if (hasBand) {
          const fill = bandColor(color);
          datasets.push(
            {
              label: `${line.label} spread upper`,
              data: upper,
              borderWidth: 0,
              pointRadius: 0,
              pointHoverRadius: 0,
              backgroundColor: fill,
              borderColor: fill,
              fill: false,
              spanGaps: true,
              // Match the median line's curvature so the band's curved edges
              // always enclose the (also curved) median between points; with
              // mismatched tension the smooth line bows outside the straight band.
              tension: 0.25,
              isBand: true,
              order: 2,
            },
            {
              label: `${line.label} spread lower`,
              data: lower,
              borderWidth: 0,
              pointRadius: 0,
              pointHoverRadius: 0,
              backgroundColor: fill,
              borderColor: fill,
              fill: "-1",
              spanGaps: true,
              tension: 0.25,
              isBand: true,
              order: 2,
            }
          );
        }
      }
      datasets.push({
        label: line.label,
        borderColor: color,
        backgroundColor: color,
        pointBackgroundColor: rows.map((_, i) => (bumps[i] ? cssVar("--surface") : color)),
        pointBorderColor: color,
        pointBorderWidth: rows.map((_, i) => (bumps[i] ? 2 : 1.5)),
        pointStyle: rows.map((_, i) => (bumps[i] ? "rectRot" : "circle")),
        pointRadius: rows.map((r, i) => (r === null ? 0 : bumps[i] ? 5.5 : 3.5)),
        pointHoverRadius: 6,
        borderWidth: 2,
        tension: 0.25,
        spanGaps: true,
        data: rows.map((r) => (r ? r[metricKey] : null)),
        cids,
        bumps,
        points: rows,
        order: 1,
      });
    }

    // Update title + meta and toggle the empty state.
    const hasData = datasets.some((ds) => ds.data.some((v) => v !== null && v !== undefined));
    titleEl.textContent = `${project ?? "—"} — ${meta.axis.replace(/ \(.*\)/, "")}`;
    metaEl.textContent = hasData
      ? `${budgetLabel} · ${meta.better} is better` +
        (allDates.length === 1 ? " · history accrues with each official run" : "")
      : "";
    card.classList.toggle("is-empty", !hasData);

    if (chart) chart.destroy();
    renderScaling();
    renderRatio();
    if (!hasData) return;

    chart = new Chart(ctx, {
      type: "line",
      data: { labels: dates, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: {
            position: "bottom",
            labels: {
              usePointStyle: true,
              pointStyle: "circle",
              boxWidth: 8,
              boxHeight: 8,
              padding: 16,
              filter: (item, data) => !data.datasets[item.datasetIndex]?.isBand,
            },
          },
          tooltip: {
            filter: (item) => !item.dataset.isBand,
            backgroundColor: cssVar("--surface"),
            titleColor: cssVar("--text"),
            bodyColor: cssVar("--text-muted"),
            borderColor: cssVar("--border-strong"),
            borderWidth: 1,
            padding: 10,
            usePointStyle: true,
            boxPadding: 4,
            callbacks: {
              // Legend shows the tool; the tooltip surfaces the exact checker_id
              // (tool@version) active at that date, so version is never lost.
              label: (item) => {
                const name = item.dataset.cids?.[item.dataIndex] || item.dataset.label;
                const v = item.parsed.y;
                if (v === null || v === undefined) return `${name}: —`;
                return `${name}: ${v.toFixed(meta.digits)} ${meta.unit}`;
              },
              afterLabel: (item) => {
                const lines = [];
                if (item.dataset.bumps?.[item.dataIndex]) lines.push("↑ new version");
                const point = item.dataset.points?.[item.dataIndex];
                if (!meta.band || !point) return lines.length ? lines : undefined;
                const spreadMin = point[meta.band.min];
                const spreadMax = point[meta.band.max];
                const spreadStddev = point[meta.band.stddev];
                if (
                  !isFiniteNumber(spreadMin) ||
                  !isFiniteNumber(spreadMax) ||
                  !isFiniteNumber(spreadStddev)
                ) {
                  return lines.length ? lines : undefined;
                }
                lines.push(
                  `± ${formatMetric(spreadStddev, meta)} (min–max ${formatMetric(
                    spreadMin,
                    meta
                  )}–${formatMetric(spreadMax, meta)})`
                );
                return lines;
              },
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: { maxRotation: 0, autoSkipPadding: 16 },
          },
          y: {
            beginAtZero: true,
            border: { display: false },
            title: { display: true, text: meta.axis, color: cssVar("--text-faint") },
            ticks: { padding: 6 },
          },
        },
      },
    });
  }

  for (const id of ["metric", "window"]) {
    document.getElementById(id).addEventListener("change", render);
  }
  // Budget changes the set of available snapshot dates, so rebuild + clamp the
  // picker before re-rendering — otherwise it could name a date this budget lacks.
  budgetEl.addEventListener("change", () => {
    syncSnapshots();
    render();
  });
  snapshotEl.addEventListener("change", () => {
    selectedSnapshot = snapshotEl.value;
    render();
  });
  // Re-anchoring only re-scales the speedup card, but render() is cheap and keeps
  // every view in sync from one path.
  ratioAnchorEl.addEventListener("change", render);

  // Re-theme on light/dark switch so the chart matches the page live.
  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
      applyChartTheme();
      render();
    });
  }

  render();
}

main();
