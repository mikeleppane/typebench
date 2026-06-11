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

// Latest-date comparison: one bar per checker, sorted best-first, with the
// slowdown (or throughput gap) relative to the best as a multiplier.
function renderSnapshot(sel, metricKey, meta, budgetLabel) {
  const card = document.getElementById("snap-card");
  const barsEl = document.getElementById("snap-bars");
  const dates = [...new Set(sel.map((p) => p.date))].sort();
  const latest = dates[dates.length - 1];

  const rows = [...new Set(sel.map((p) => p.checker_id))]
    .map((id) => {
      const hit = sel.find((p) => p.date === latest && p.checker_id === id);
      const value = hit ? hit[metricKey] : null;
      return value === null || value === undefined ? null : { id, value };
    })
    .filter(Boolean)
    .sort((a, b) => (meta.better === "lower" ? a.value - b.value : b.value - a.value));

  card.classList.toggle("is-empty", rows.length === 0);
  setText("snap-title", latest ? `Latest snapshot — ${latest}` : "Latest snapshot");
  setText("snap-meta", rows.length ? `${budgetLabel} · ${meta.better} is better` : "");
  if (!rows.length) {
    barsEl.innerHTML = "";
    return;
  }

  const best = rows[0].value;
  const max = Math.max(...rows.map((r) => r.value));
  barsEl.innerHTML = rows
    .map((r) => {
      const mult = meta.better === "lower" ? r.value / best : best / r.value;
      const multText =
        r === rows[0] ? "best" : `${mult.toFixed(mult >= 10 ? 0 : 1)}× vs best`;
      return `
        <div class="bar-row">
          <span class="bar-row__label" title="${escapeHtml(r.id)}">${escapeHtml(r.id)}</span>
          <span class="bar-row__track">
            <span class="bar-row__fill" data-w="${((r.value / max) * 100).toFixed(2)}%"
              style="background:${colorFor(r.id)}"></span>
          </span>
          <span class="bar-row__value">${r.value.toFixed(meta.digits)} ${meta.unit}</span>
          <span class="bar-row__mult${r === rows[0] ? " bar-row__mult--best" : ""}">${multText}</span>
        </div>`;
    })
    .join("");

  // Widths start at 0 (stylesheet) and are set a frame later so the CSS
  // transition animates the bars in on every re-render.
  requestAnimationFrame(() => {
    for (const fill of barsEl.querySelectorAll(".bar-row__fill")) {
      fill.style.width = fill.dataset.w;
    }
  });
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

  const projects = [...new Set(points.map((p) => p.project))].sort();
  const budgets = [...new Map(points.map((p) => [budgetKey(p), p])).values()]
    .map((p) => ({ key: budgetKey(p), cores: p.cores ?? null, label: budgetText(p) }))
    .sort((a, b) => {
      if (a.cores === null) return -1; // all-cores budget always leads
      if (b.cores === null) return 1;
      return a.cores - b.cores;
    });

  document.getElementById("project").innerHTML = projects
    .map((v) => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`)
    .join("");
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
    const project = document.getElementById("project").value;
    const budget = document.getElementById("budget").value;
    const meta = METRICS[metricKey];
    const budgetLabel =
      budgets.find((b) => b.key === budget)?.label || budget.replace("|", " · ");

    const sel = points.filter((p) => p.project === project && budgetKey(p) === budget);
    renderSnapshot(sel, metricKey, meta, budgetLabel);

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
    titleEl.textContent = `${project} — ${meta.axis.replace(/ \(.*\)/, "")}`;
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

  for (const id of ["metric", "project", "budget"]) {
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
