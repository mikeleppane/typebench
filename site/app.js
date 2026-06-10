// Fetch the committed trends.json and render a per-checker line chart. Series are
// keyed by checker_id and viewed per cores value so same-day version or cores
// sweeps remain distinct.
const COLORS = {
  mypy: "#3572A5",
  pyright: "#178600",
  pyrefly: "#DEA584",
  ty: "#000000",
  stub: "#999",
};

function colorFor(checkerId) {
  const tool = checkerId.split("@")[0];
  const base = COLORS[tool] || "#555";
  let hash = 0;
  for (let i = 0; i < checkerId.length; i += 1) {
    hash = (hash * 31 + checkerId.charCodeAt(i)) >>> 0;
  }
  return shiftLightness(base, (hash % 5) * 18);
}

function shiftLightness(hex, amount) {
  const digits = hex.slice(1);
  const expanded = digits.length === 3 ? digits.replace(/./g, "$&$&") : digits;
  const value = parseInt(expanded, 16);
  const r = Math.min(255, ((value >> 16) & 255) + amount);
  const g = Math.min(255, ((value >> 8) & 255) + amount);
  const b = Math.min(255, (value & 255) + amount);
  return `rgb(${r}, ${g}, ${b})`;
}

function coresLabel(c) {
  return c === null || c === undefined ? "all-cores" : String(c);
}

async function main() {
  const res = await fetch("./data/trends.json");
  const data = await res.json();
  const points = data.points || [];

  const modes = [...new Set(points.map((p) => p.thread_mode))].sort();
  const projects = [...new Set(points.map((p) => p.project))].sort();
  const cores = [...new Set(points.map((p) => coresLabel(p.cores)))].sort();
  fill("mode", modes);
  fill("project", projects);
  fill("cores", cores);

  const ctx = document.getElementById("chart").getContext("2d");
  let chart = null;

  function render() {
    const metric = document.getElementById("metric").value;
    const mode = document.getElementById("mode").value;
    const project = document.getElementById("project").value;
    const cores = document.getElementById("cores").value;
    const sel = points.filter(
      (p) =>
        p.thread_mode === mode && p.project === project && coresLabel(p.cores) === cores,
    );
    const dates = [...new Set(sel.map((p) => p.date))].sort();
    const checkerIds = [...new Set(sel.map((p) => p.checker_id))].sort();
    const datasets = checkerIds.map((checkerId) => ({
      label: checkerId,
      borderColor: colorFor(checkerId),
      backgroundColor: colorFor(checkerId),
      spanGaps: true,
      data: dates.map((d) => {
        const hit = sel.find(
          (p) =>
            p.date === d &&
            p.checker_id === checkerId &&
            coresLabel(p.cores) === cores,
        );
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

  for (const id of ["metric", "mode", "project", "cores"]) {
    document.getElementById(id).addEventListener("change", render);
  }
  render();
}

function fill(id, values) {
  const el = document.getElementById(id);
  el.innerHTML = values.map((v) => `<option value="${v}">${v}</option>`).join("");
}

main();
