// Fetch the committed trends.json and render a per-tool line chart. Inter-checker
// ratios and grouping are derived client-side; trends.json is the source of truth.
const COLORS = { mypy: "#3572A5", pyright: "#178600", pyrefly: "#DEA584", ty: "#000000", stub: "#999" };

async function main() {
  const res = await fetch("./data/trends.json");
  const data = await res.json();
  const points = data.points || [];

  const modes = [...new Set(points.map((p) => p.thread_mode))].sort();
  const projects = [...new Set(points.map((p) => p.project))].sort();
  fill("mode", modes);
  fill("project", projects);

  const ctx = document.getElementById("chart").getContext("2d");
  let chart = null;

  function render() {
    const metric = document.getElementById("metric").value;
    const mode = document.getElementById("mode").value;
    const project = document.getElementById("project").value;
    const sel = points.filter((p) => p.thread_mode === mode && p.project === project);
    const dates = [...new Set(sel.map((p) => p.date))].sort();
    const tools = [...new Set(sel.map((p) => p.tool))].sort();
    const datasets = tools.map((tool) => ({
      label: tool,
      borderColor: COLORS[tool] || "#555",
      backgroundColor: COLORS[tool] || "#555",
      spanGaps: true,
      data: dates.map((d) => {
        const hit = sel.find((p) => p.date === d && p.tool === tool);
        return hit ? hit[metric] : null;
      }),
    }));
    if (chart) chart.destroy();
    chart = new Chart(ctx, {
      type: "line",
      data: { labels: dates, datasets },
      options: {
        responsive: true,
        interaction: { mode: "index", intersect: false },
        scales: { y: { beginAtZero: true } },
      },
    });
  }

  for (const id of ["metric", "mode", "project"]) {
    document.getElementById(id).addEventListener("change", render);
  }
  render();
}

function fill(id, values) {
  const el = document.getElementById(id);
  el.innerHTML = values.map((v) => `<option value="${v}">${v}</option>`).join("");
}

main();
