// Fetch the committed trends.json and render the latest-snapshot bars plus a
// per-checker trend line chart. Series are keyed by checker_id and viewed per
// CPU budget (thread mode + cores), so same-day version or cores sweeps stay
// visually distinct. No build step: vanilla JS + vendored Chart.js.

// Per-tool base colors (close to each tool's brand), with a deterministic
// lightness shift per checker_id so two versions of one tool never collide.
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
  wall_median_s_norm: { axis: "Wall, normalized (s)", unit: "s", digits: 3, better: "lower" },
  wall_median_s: { axis: "Wall (s)", unit: "s", digits: 3, better: "lower" },
  peak_mem_mb: { axis: "Peak memory (MB)", unit: "MB", digits: 1, better: "lower" },
  kloc_s: { axis: "Throughput (kLOC/s)", unit: "kLOC/s", digits: 1, better: "higher" },
};

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function colorFor(checkerId) {
  const tool = checkerId.split("@")[0];
  const base = TOOL_COLORS[tool] || "#5c6470";
  let hash = 0;
  for (let i = 0; i < checkerId.length; i += 1) {
    hash = (hash * 31 + checkerId.charCodeAt(i)) >>> 0;
  }
  // ±28 lightness spread across versions of the same tool, centered near base.
  return shiftLightness(base, (hash % 3) * 22 - 22);
}

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
  setText("stat-date", dates.length ? dates[dates.length - 1] : "—");
  setText("stat-projects", new Set(points.map((p) => p.project)).size || "—");
  setText("stat-checkers", new Set(points.map((p) => p.checker_id)).size || "—");
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

// Latest-date comparison as a projects × checkers matrix for one metric + budget:
// rows are every project (tiered small -> large), columns the checkers, each cell
// the metric value. The best cell in a row is highlighted; cells are rank-tinted.
// Clicking a row calls onSelect(project) so the trend chart below can follow it.
function renderMatrix(sel, metricKey, meta, budgetLabel, selected, onSelect) {
  const card = document.getElementById("snap-card");
  const headEl = document.getElementById("snap-head");
  const bodyEl = document.getElementById("snap-body");
  const dates = [...new Set(sel.map((p) => p.date))].sort();
  const latest = dates[dates.length - 1];
  // Build rows, columns, and cells from the latest date only. Checker versions or
  // corpus projects that existed solely in earlier runs must not leave stale,
  // all-dashes lines in the snapshot once history accrues.
  const latestRows = sel.filter((p) => p.date === latest);

  const checkers = [...new Set(latestRows.map((p) => p.checker_id))].sort();
  const projMeta = new Map();
  for (const p of latestRows) {
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
  setText("snap-title", latest ? `Latest snapshot — ${latest}` : "Latest snapshot");
  setText("snap-meta", projects.length ? `${budgetLabel} · ${meta.better} is better` : "");
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
  for (const project of projects) {
    const { tier, loc } = projMeta.get(project);
    if (tier !== currentTier) {
      currentTier = tier;
      html += `<tr class="tier-row"><td colspan="${checkers.length + 1}">${escapeHtml(
        tier
      )} projects</td></tr>`;
    }

    const values = checkers.map((c) => {
      const hit = latestRows.find((p) => p.project === project && p.checker_id === c);
      const v = hit ? hit[metricKey] : null;
      return v === null || v === undefined ? null : v;
    });
    const present = values.filter((v) => v !== null);
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
    for (const v of values) {
      if (v === null) {
        html += `<td class="cell na"><span class="c">—</span></td>`;
        continue;
      }
      const t =
        span > 0 ? (meta.better === "lower" ? (v - lo) / span : (hi - v) / span) : 0;
      const isBest = present.length > 1 && v === best;
      html += `<td class="cell${isBest ? " best" : ""}"><span class="c" style="background:${rankTint(
        t
      )}">${v.toFixed(meta.digits)}</span></td>`;
    }
    html += `</tr>`;
  }
  bodyEl.innerHTML = html;

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
  fillStats(points, data.corpus_markers || [], data.cpu_models || []);

  // Projects ordered small -> large (by analyzed code-LOC) so the trend defaults
  // to the smallest project; the matrix re-derives the same order for its rows.
  const locOf = (project) => points.find((p) => p.project === project)?.code_loc ?? 0;
  const projectOrder = [...new Set(points.map((p) => p.project))].sort(
    (a, b) => locOf(a) - locOf(b) || a.localeCompare(b)
  );
  let selectedProject = projectOrder[0] || null;

  const budgets = [...new Map(points.map((p) => [budgetKey(p), p])).values()]
    .map((p) => ({ key: budgetKey(p), cores: p.cores ?? null, label: budgetText(p) }))
    .sort((a, b) => {
      if (a.cores === null) return -1; // all-cores budget always leads
      if (b.cores === null) return 1;
      return a.cores - b.cores;
    });

  document.getElementById("budget").innerHTML = budgets
    .map((b) => `<option value="${escapeHtml(b.key)}">${escapeHtml(b.label)}</option>`)
    .join("");

  const card = document.getElementById("chart-card");
  const titleEl = document.getElementById("chart-title");
  const metaEl = document.getElementById("chart-meta");
  const ctx = document.getElementById("chart").getContext("2d");
  let chart = null;

  function render() {
    const metricKey = document.getElementById("metric").value;
    const budget = document.getElementById("budget").value;
    const meta = METRICS[metricKey];
    const budgetLabel =
      budgets.find((b) => b.key === budget)?.label || budget.replace("|", " · ");

    // The matrix sees every project at this budget; clicking a row repoints the
    // trend chart below. The trend stays pinned to one project (its time axis).
    const selByBudget = points.filter((p) => budgetKey(p) === budget);
    renderMatrix(selByBudget, metricKey, meta, budgetLabel, selectedProject, (project) => {
      selectedProject = project;
      render();
    });

    const project = selectedProject;
    const sel = selByBudget.filter((p) => p.project === project);
    const dates = [...new Set(sel.map((p) => p.date))].sort();
    const checkerIds = [...new Set(sel.map((p) => p.checker_id))].sort();

    const datasets = checkerIds.map((checkerId) => {
      const color = colorFor(checkerId);
      return {
        label: checkerId,
        borderColor: color,
        backgroundColor: color,
        pointBackgroundColor: color,
        pointBorderColor: cssVar("--surface"),
        pointBorderWidth: 1.5,
        pointRadius: 3.5,
        pointHoverRadius: 5,
        borderWidth: 2,
        tension: 0.25,
        spanGaps: true,
        data: dates.map((d) => {
          // sel is already pinned to one (project, thread_mode, cores) budget,
          // so date + checker_id uniquely identifies a point.
          const hit = sel.find((p) => p.date === d && p.checker_id === checkerId);
          return hit ? hit[metricKey] : null;
        }),
      };
    });

    // Update title + meta and toggle the empty state.
    const hasData = datasets.some((ds) => ds.data.some((v) => v !== null && v !== undefined));
    titleEl.textContent = `${project ?? "—"} — ${meta.axis.replace(/ \(.*\)/, "")}`;
    metaEl.textContent = hasData
      ? `${budgetLabel} · ${meta.better} is better` +
        (dates.length === 1 ? " · history accrues with each official run" : "")
      : "";
    card.classList.toggle("is-empty", !hasData);

    if (chart) chart.destroy();
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
                const v = item.parsed.y;
                if (v === null || v === undefined) return `${item.dataset.label}: —`;
                return `${item.dataset.label}: ${v.toFixed(meta.digits)} ${meta.unit}`;
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

  for (const id of ["metric", "budget"]) {
    document.getElementById(id).addEventListener("change", render);
  }

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
