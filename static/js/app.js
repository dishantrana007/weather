const $ = (s) => document.querySelector(s);
const state = { data: null, unit: "C", activity: "Running" };
try { state.unit = localStorage.getItem("unit") || "C"; } catch (e) {}

const temp = (c) => Math.round(state.unit === "C" ? c : c * 9 / 5 + 32) + "°";
const colorFor = (s) => (s >= 70 ? "var(--good)" : s >= 40 ? "var(--ok)" : "var(--bad)");

function setStatus(msg, isError = false) {
  const el = $("#status");
  el.textContent = msg;
  el.className = isError ? "error" : "";
  el.hidden = !msg;
}

const esc = (t) => String(t).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function load(params) {
  $("#results").hidden = true;
  setStatus("Loading…");
  try {
    const res = await fetch("/api/weather?" + new URLSearchParams(params));
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Something went wrong.");
    state.data = data;
    if (!data.activities[state.activity]) state.activity = Object.keys(data.activities)[0];
    setStatus("");
    render();
    try { localStorage.setItem("last", JSON.stringify(params)); } catch (e) {}
  } catch (err) {
    setStatus(err.message, true);
  }
}

// Best two-hour stretch: the pair of neighbouring hours with the highest average score.
function bestWindow(scores) {
  let best = 0, bestAvg = -1;
  for (let i = 0; i < scores.length - 1; i++) {
    const avg = (scores[i] + scores[i + 1]) / 2;
    if (avg > bestAvg) { bestAvg = avg; best = i; }
  }
  return { start: best, avg: Math.round(bestAvg) };
}

function render() {
  const d = state.data;
  document.body.dataset.sky = d.mood;
  ["now", "planner", "farm", "wear", "week"].forEach((id) => ($("#" + id).hidden = false));

  $("#icon").textContent = d.now.icon;
  $("#place").textContent = d.place;
  $("#temp").textContent = temp(d.now.temp);
  $("#desc").textContent = d.now.desc;
  $("#meta").textContent = `Feels like ${temp(d.now.feels)} · Wind ${Math.round(d.now.wind)} km/h · Humidity ${d.now.humidity}%`;
  $("#unit").textContent = "°" + state.unit;
  $("#rain").textContent = "☔ " + d.rain_watch;
  $("#sun").textContent = `🌅 Sunrise ${d.sun.rise} · 🌇 Sunset ${d.sun.set}`;
  const f = d.farm;
  $("#farmstats").innerHTML =
    `<div><b>${f.soil == null ? "–" : temp(f.soil)}</b><span>Soil temperature</span></div>` +
    `<div><b>${f.rain48} mm</b><span>Rain, next 48 h</span></div>` +
    `<div><b>${f.et0} mm</b><span>Water lost, 24 h</span></div>`;
  $("#tips").innerHTML = f.tips.map((t) => `<li>${esc(t)}</li>`).join("");

  $("#tabs").innerHTML = "";
  Object.keys(d.activities).forEach((name) => {
    const b = document.createElement("button");
    b.type = "button"; b.setAttribute("role", "tab"); b.textContent = name;
    b.setAttribute("aria-selected", name === state.activity);
    b.onclick = () => { state.activity = name; render(); };
    $("#tabs").appendChild(b);
  });

  const scores = d.activities[state.activity];
  const w = bestWindow(scores);
  const end = d.labels[w.start + 1].slice(0, 2) + ":59";
  $("#verdict").textContent = w.avg < 35
    ? `Not a great day for ${state.activity.toLowerCase()}. The least bad time is ${d.labels[w.start]}.`
    : `Best window for ${state.activity.toLowerCase()}: ${d.labels[w.start]} to ${end} (score ${w.avg}/100).`;

  $("#timeline").innerHTML = scores.map((s, i) =>
    `<div class="bar ${i === w.start || i === w.start + 1 ? "best" : ""}" style="height:${Math.max(s, 3)}%;background:${colorFor(s)}" title="${d.labels[i]} – ${s}/100"></div>`).join("");
  $("#ticks").innerHTML = d.labels.map((l, i) => `<span>${i % 3 === 0 ? l.slice(0, 2) : ""}</span>`).join("");

  $("#outfit").textContent = d.outfit;
  $("#daily").innerHTML = d.daily.map((x, i) => {
    const day = i === 0 ? "Today" : new Date(x.date + "T12:00").toLocaleDateString(undefined, { weekday: "short" });
    return `<li><div class="day">${day}</div><div class="ic">${x.icon}</div><div>${temp(x.hi)} / ${temp(x.lo)}</div><div class="rain">${x.rain}% rain</div></li>`;
  }).join("");
}

function showResults(list) {
  const ul = $("#results");
  if (!list.length) {
    ul.hidden = true;
    return setStatus("No place found. Add the district or state (for example: Rampur Meerut), or type latitude, longitude.", true);
  }
  if (list.length === 1) return pick(list[0]);
  setStatus("Pick your place:");
  ul.hidden = false;
  ul.innerHTML = list.map((p, i) => `<li><button type="button" data-i="${i}"><b>${esc(p.name)}</b> <span>${esc(p.where)}</span></button></li>`).join("");
  ul.onclick = (e) => {
    const b = e.target.closest("button");
    if (b) pick(list[b.dataset.i]);
  };
}

function pick(p) {
  $("#city").value = p.name;
  load({ lat: p.lat, lon: p.lon, name: p.name + (p.where ? ", " + p.where : "") });
}

$("#search").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#city").value.trim();
  if (!q) return;
  const m = q.match(/^(-?\d+(\.\d+)?)\s*[, ]\s*(-?\d+(\.\d+)?)$/); // typed "lat, lon"
  if (m) return load({ lat: m[1], lon: m[3], name: `${m[1]}, ${m[3]}` });
  setStatus("Searching…");
  try {
    showResults(await (await fetch("/api/search?q=" + encodeURIComponent(q))).json());
  } catch (err) {
    setStatus("Could not search. Check your internet and try again.", true);
  }
});

$("#locate").addEventListener("click", () => {
  if (!navigator.geolocation) return setStatus("Your browser can't share location. Search a city instead.", true);
  setStatus("Finding your location…");
  navigator.geolocation.getCurrentPosition(
    (p) => load({ lat: p.coords.latitude, lon: p.coords.longitude }),
    () => setStatus("Location was blocked. Allow it in your browser or search a city.", true));
});

$("#unit").addEventListener("click", () => {
  state.unit = state.unit === "C" ? "F" : "C";
  try { localStorage.setItem("unit", state.unit); } catch (e) {}
  $("#unit").textContent = "°" + state.unit;
  if (state.data) render();
});

$("#unit").textContent = "°" + state.unit;
try { const last = JSON.parse(localStorage.getItem("last")); if (last) load(last); } catch (e) {}
