// Fetch the committed trends.json and render a per-checker line chart. Series are
// keyed by checker_id and viewed per cores value, so same-day version or cores
// sweeps stay visually distinct. No build step: vanilla JS + vendored Chart.js.

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

function fill(id, values) {
  const el = document.getElementById(id);
  el.innerHTML = values.map((v) => `<option value="${v}">${v}</option>`).join("");
}

function applyChartTheme() {
  if (typeof Chart === "undefined") return;
  Chart.defaults.font.family =
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";
  Chart.defaults.font.size = 12;
  Chart.defaults.color = cssVar("--text-muted");
  Chart.defaults.borderColor = cssVar("--grid");
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

  const modes = [...new Set(points.map((p) => p.thread_mode))].sort();
  const projects = [...new Set(points.map((p) => p.project))].sort();
  const cores = [...new Set(points.map((p) => coresLabel(p.cores)))].sort();
  fill("mode", modes);
  fill("project", projects);
  fill("cores", cores);

  const card = document.getElementById("chart-card");
  const titleEl = document.getElementById("chart-title");
  const metaEl = document.getElementById("chart-meta");
  const ctx = document.getElementById("chart").getContext("2d");
  let chart = null;

  function render() {
    const metricKey = document.getElementById("metric").value;
    const mode = document.getElementById("mode").value;
    const project = document.getElementById("project").value;
    const coresSel = document.getElementById("cores").value;
    const meta = METRICS[metricKey];

    const sel = points.filter(
      (p) =>
        p.thread_mode === mode &&
        p.project === project &&
        coresLabel(p.cores) === coresSel,
    );
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
          const hit = sel.find(
            (p) =>
              p.date === d &&
              p.checker_id === checkerId &&
              coresLabel(p.cores) === coresSel,
          );
          return hit ? hit[metricKey] : null;
        }),
      };
    });

    // Update title + meta and toggle the empty state.
    const hasData = datasets.some((ds) => ds.data.some((v) => v !== null && v !== undefined));
    titleEl.textContent = `${project} — ${meta.axis.replace(/ \(.*\)/, "")}`;
    metaEl.textContent = hasData
      ? `${mode} · ${coresSel === "all-cores" ? "all cores" : coresSel + " core" + (coresSel === "1" ? "" : "s")} · ${meta.better} is better`
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

  for (const id of ["metric", "mode", "project", "cores"]) {
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
