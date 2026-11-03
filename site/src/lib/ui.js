// Shared formatting helpers. Pure string functions so the same code renders a bill
// at build time (Astro) and in the browser (404 fallback for older bills).

export const LABEL = { hr: "H.R.", s: "S.", hjres: "H.J.Res.", sjres: "S.J.Res.", hres: "H.Res.", sres: "S.Res.", hconres: "H.Con.Res.", sconres: "S.Con.Res." };
export const LEG = ["hr", "s", "hjres", "sjres"];

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
export const fmt = (n) => (n == null ? "—" : Number(n).toLocaleString("en-US"));
export const pct = (x, d = 1) => (x * 100).toFixed(d).replace(/\.0+$/, "") + "%";
export const ord = (n) => { const s = ["th", "st", "nd", "rd"], v = n % 100; return n + (s[(v - 20) % 10] || s[v] || s[0]); };
const WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];
export const word = (n) => WORDS[n] ?? String(n);

export function billLabel(id) {
  const [, t, n] = id.split("-");
  return `${LABEL[t] || t} ${n}`;
}
export const billPath = (id) => { const [c, t, n] = id.split("-"); return `bills/${c}/${t}/${n}/`; };
export const mapPath = (id) => { const [c, t, n] = id.split("-"); return `map/${c}/${t}/${n}/`; };

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// Dates are plain YYYY-MM-DD strings; format without timezones so build and browser agree.
export function fdate(s) {
  if (!s) return "—";
  const [y, m, d] = s.slice(0, 10).split("-").map(Number);
  return `${MONTHS[m - 1]} ${d}, ${y}`;
}
export function daysBetween(a, b) {
  const p = (s) => { const [y, m, d] = s.slice(0, 10).split("-").map(Number); return Date.UTC(y, m - 1, d); };
  return Math.round((p(b) - p(a)) / 864e5);
}

export const STAGES = ["Introduced", "Reported", "Passed one chamber", "Passed both", "Law"];
const STAGE_OF = { introduced: 0, reported: 1, passed_one: 2, failed: 2, agreed_to: 2, passed_both: 3, to_president: 3, vetoed: 3, pocket_vetoed: 3, became_law: 4, veto_overridden: 4 };
export const stageOf = (s) => STAGE_OF[s] ?? 0;
const STAGE_NAME = { introduced: "Introduced", reported: "Reported", passed_one: "Passed one chamber", failed: "Failed vote", agreed_to: "Agreed to", passed_both: "Passed both", to_president: "On president's desk", vetoed: "Vetoed", pocket_vetoed: "Pocket vetoed", became_law: "Law", veto_overridden: "Law over veto" };
export const stageName = (s) => STAGE_NAME[s] || s || "Unknown";
export const isLaw = (s) => s === "became_law" || s === "veto_overridden";
export const isVeto = (s) => s === "vetoed" || s === "pocket_vetoed";

// D1: five-step track; vetoes and failed votes turn the last pip amber.
export function track(status) {
  const k = stageOf(status), law = k === 4, x = isVeto(status) || status === "failed";
  let h = `<span class="track ${law ? "law" : ""}" aria-hidden="true">`;
  for (let i = 0; i < 5; i++) h += `<i class="${i <= k ? (x && i === k ? "x" : "on") : ""}"></i>`;
  return h + `</span><span class="stage">${stageName(status)}</span>`;
}
// H9: link Congress terms in action text to the glossary on the How Congress works page.
// Takes already-escaped text; links the first mention of each term.
const GLOSS = [["suspend the rules", "suspension"], ["unanimous consent", "unanimous-consent"], ["cloture", "cloture"], ["discharged", "discharged"],
  ["conference report", "conference"], ["pocket vetoed", "pocket-veto"], ["vetoed", "veto"], ["veto message", "veto"], ["reported", "reported"],
  ["referred to", "referred"], ["presented to president", "enrolled"]];
const GLOSS_RX = new RegExp(`\\b(${GLOSS.map(([w]) => w).join("|")})\\b`, "gi");
export function glossify(html, base = "/") {
  const used = new Set();
  return html.replace(GLOSS_RX, (m) => {
    const id = GLOSS.find(([w]) => w === m.toLowerCase())[1];
    if (used.has(id)) return m;
    used.add(id);
    return `<a href="${base}how-it-works/#g-${id}">${m}</a>`;
  });
}
export const party = (p, st) => (p ? `<span class="party">${esc(p)}${st ? "-" + esc(st) : ""}</span>` : "");

// D2: odds as four bands, never a percentage.
export const BANDS = ["Long shot", "Typical", "Better than most", "Strong"];
export function odds(band, status) {
  if (isLaw(status)) return `<span class="odds muted">Enacted</span>`;
  if (isVeto(status)) return `<span class="odds muted">Vetoed</span>`;
  if (band == null) return `<span class="muted">—</span>`;
  let h = `<span class="odds"><span class="dots" aria-hidden="true">`;
  for (let j = 0; j < 4; j++) h += `<i class="${j <= band ? "on" : ""}"></i>`;
  return h + `</span>${BANDS[band]}</span>`;
}

export const SECTIONS = { home: "Home", how: "Primer · How Congress works", bills: "Part I · Bills", map: "Part II · Connections", coverage: "Part III · Coverage", outcomes: "Part IV · Outcomes", about: "Notes · About the data", search: "Part I · Bills · Search" };
export const SOURCES = "Source: [1] Congress.gov bill status, via govinfo bulk data. [2] Congressional Research Service summaries. [3] GDELT Project. [4] Docket Hopper passage model. [5] congress-legislators dataset.";
