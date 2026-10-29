// web/static/app.js — 15 saniyede bir /api/status'tan veri cek, sayfayi

const REFRESH_MS = 15000;
let tempChart = null;
let scanChart = null;



document.addEventListener('DOMContentLoaded', () => {
  const themeToggleBtn = document.getElementById('theme-toggle');
  const htmlElement = document.documentElement;

  // Check localStorage for saved theme preference and apply it
  const savedTheme = localStorage.getItem('theme');
  
  if (savedTheme === 'light') {
    htmlElement.classList.add('light-mode');
    themeToggleBtn.textContent = '🌙 Dark Mode';
  } else {
    htmlElement.classList.remove('light-mode');
    themeToggleBtn.textContent = '☀️ Light Mode';
  }

  // Toggle theme on button click and save preference to localStorage
  themeToggleBtn.addEventListener('click', () => {
    htmlElement.classList.toggle('light-mode');
    
    if (htmlElement.classList.contains('light-mode')) {
      localStorage.setItem('theme', 'light');
      themeToggleBtn.textContent = '🌙 Dark Mode';
    } else {
      localStorage.setItem('theme', 'dark');
      themeToggleBtn.textContent = '☀️ Light Mode';
    }
  });
});



function fmt(v, d = 1) {
  return (v === null || v === undefined || Number.isNaN(v)) ? "–" : Number(v).toFixed(d);
}

function setBadge(lastActivity) {
  const b = document.getElementById("badge");
  if (lastActivity === null || lastActivity === undefined) {
    b.textContent = "no data"; b.style.color = "var(--idle)"; return;
  }
  // Dusuk-rate olcumde mtime yaniltici olabilir -> "aktif" yerine yas gosteriyoruz
  if (lastActivity < 15)      { b.textContent = "● active (last seen " + fmt(lastActivity,0) + "s ago)"; b.style.color = "var(--ok)"; }
  else if (lastActivity < 120){ b.textContent = "○ last seen " + fmt(lastActivity,0) + "s ago"; b.style.color = "var(--warn)"; }
  else                        { b.textContent = "○ idle? " + fmt(lastActivity,0) + "s"; b.style.color = "var(--idle)"; }
}

function makeLineChart(ctx, label, color) {
  return new Chart(ctx, {
    type: "line",
    data: { labels: [], datasets: [{ label, data: [], borderColor: color,
             backgroundColor: color + "22", tension: 0.25, pointRadius: 0, borderWidth: 2 }] },
    options: { responsive: true, animation: false,
      scales: { x: { ticks: { color: "#8a97ab", maxTicksLimit: 8 }, grid: { color: "#2a3446" } },
                y: { ticks: { color: "#8a97ab" }, grid: { color: "#2a3446" } } },
      plugins: { legend: { labels: { color: "#e6ecf5" } } } }
  });
}

function updateTempChart(temp) {
  const ctx = document.getElementById("tempChart");
  if (!tempChart) tempChart = makeLineChart(ctx, "Temperature °C", "#4fc3f7");
  if (!temp || !temp.series) return;
  const anchor = temp.anchor_unix ? parseFloat(temp.anchor_unix) : 0;
  const labels = temp.series.map(p => {
    if (!anchor) return fmt(p.t_rel_s, 0) + "s";
    const d = new Date((anchor + p.t_rel_s) * 1000);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  });
  tempChart.data.labels = labels;
  tempChart.data.datasets[0].data = temp.series.map(p => p.temp_C);
  tempChart.update();
}

function updateScanChart(scan) {
  const ctx = document.getElementById("scanChart");
  if (!scanChart) {
    scanChart = new Chart(ctx, {
      type: "scatter",
      data: { datasets: [{ label: "rate (Hz)", data: [], borderColor: "#5ec26a",
               backgroundColor: "#5ec26a", showLine: true, tension: 0.2, pointRadius: 4 }] },
      options: { responsive: true, animation: false,
        scales: { x: { title: { display: true, text: "θ (derece)", color: "#8a97ab" },
                       ticks: { color: "#8a97ab" }, grid: { color: "#2a3446" } },
                  y: { title: { display: true, text: "rate (Hz)", color: "#8a97ab" },
                       ticks: { color: "#8a97ab" }, grid: { color: "#2a3446" } } },
        plugins: { legend: { labels: { color: "#e6ecf5" } } } }
    });
  }
  if (!scan || !scan.points) return;
  scanChart.data.datasets[0].data = scan.points.map(p => ({
    x: parseFloat(p.theta_deg), y: parseFloat(p.rate_Hz) }));
  scanChart.update();
}

function updateScanTable(scan) {
  const thead = document.querySelector("#scanTable thead");
  const tbody = document.querySelector("#scanTable tbody");
  if (!scan || !scan.points || scan.points.length === 0) {
    thead.innerHTML = ""; tbody.innerHTML = "<tr><td> no data</td></tr>"; return;
  }
  const cols = ["theta_deg", "angle_actual_deg", "counts", "livetime_s", "rate_Hz",
                "temp_C", "humidity_RH", "tilt_deg", "flags"];
  thead.innerHTML = "<tr>" + cols.map(c => `<th>${c}</th>`).join("") + "</tr>";
  tbody.innerHTML = scan.points.map(p => "<tr>" + cols.map(c => {
    const v = p[c] ?? "–";
    const cls = c === "flags" ? (v === "ok" ? "flagok" : "flagbad") : "";
    return `<td class="${cls}">${v}</td>`;
  }).join("") + "</tr>").join("");
}

async function tick() {
  try {
    const r = await fetch("/api/status", { cache: "no-store" });
    const s = await r.json();

    setBadge(s.last_activity_s);
    document.getElementById("now-meta").textContent = "server: " + (s.now_iso || "");

    if (s.temp && s.temp.latest) {
      document.getElementById("temp").textContent = fmt(s.temp.latest.temp_C, 1);
      document.getElementById("hum").textContent = fmt(s.temp.latest.humidity_RH, 1);
      document.getElementById("temp-meta").textContent = s.temp.file + "  (" + fmt(s.temp.age_s,0) + "s once)";
      document.getElementById("hum-meta").textContent = s.temp.file;
    }
    if (s.daq) {
      document.getElementById("count").textContent = (s.daq.count ?? "–");
      document.getElementById("count-meta").textContent = s.daq.file + "  (" + fmt(s.daq.age_s,0) + "s once)";
    }
    document.getElementById("activity").textContent = fmt(s.last_activity_s, 0);

    updateTempChart(s.temp);
    updateScanChart(s.scan);
    updateScanTable(s.scan);
  } catch (e) {
    document.getElementById("badge").textContent = "connection error";
    document.getElementById("badge").style.color = "var(--warn)";
  }
}

tick();
setInterval(tick, REFRESH_MS);
