// Bill page exhibits: odds band at each stage (B3) and news coverage (B5).
import { scaleLinear, scalePoint, scaleUtc } from "d3-scale";
import { line, area } from "d3-shape";
import { max } from "d3-array";
import { utcFormat } from "d3-time-format";
import { select, css, svg } from "./charts.js";
import { BANDS, esc, fdate, fmt, isLaw, isVeto } from "./ui.js";

const STAGE_SHORT = ["Intro", "Committee", "One chamber", "Both", "Law"];

export function drawOddsHist(el, D) {
  const node = typeof el === "string" ? document.querySelector(el) : el;
  if (!node) return;
  const hist = D.odds?.hist || [];
  // Frozen at each stage: the first band recorded once the bill reached it.
  const byStage = new Map();
  for (const [day, st, band] of hist) if (!byStage.has(st)) byStage.set(st, { st, band, day });
  const pts = [...byStage.values()].sort((a, b) => a.st - b.st);
  if (isLaw(D.status) && pts.length) pts.push({ st: 4, band: 4, day: D.ms?.law });
  if (!pts.length) {
    node.innerHTML = `<p class="muted" style="font-size:13.5px;margin:0">${isLaw(D.status) || isVeto(D.status) ? "This bill was decided before Docket Hopper started recording odds, so it has no rating history. We never score a bill after the fact." : D.past ? "Bills from past Congresses are not rated. Ratings started when Docket Hopper launched and cover the current Congress." : "No rating yet. Bills are rated each night once they appear in the record."}</p>`;
    return;
  }
  const W = 320, H = 160, m = { t: 26, r: 12, b: 34, l: 12 };
  const x = scalePoint().domain(pts.map((p) => p.st)).range(pts.length === 1 ? [W / 2, W / 2] : [m.l + 34, W - m.r - 34]);
  const y = scaleLinear().domain([0, 4]).range([H - m.b, m.t]);
  const s = svg(node, W, H, "Passage odds band at each stage");
  s.append("g").selectAll("line").data([0, 1, 2, 3]).join("line").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y).attr("y2", y).attr("stroke", css("--rule"));
  if (pts.length > 1) s.append("path").attr("d", line().x((p) => x(p.st)).y((p) => y(p.band))(pts)).attr("fill", "none").attr("stroke", css("--accent")).attr("stroke-width", 2);
  s.selectAll("circle").data(pts).join("circle").attr("cx", (p) => x(p.st)).attr("cy", (p) => y(p.band)).attr("r", 4).attr("fill", css("--accent")).attr("stroke", css("--accent-l")).attr("stroke-width", 2);
  s.selectAll("text.l").data(pts).join("text").attr("class", "halo").attr("x", (p) => x(p.st)).attr("y", (p) => y(p.band) - 10).attr("text-anchor", "middle").attr("font-size", 11.5).text((p) => (p.band === 4 ? "Enacted" : BANDS[p.band]));
  s.selectAll("text.k").data(pts).join("text").attr("class", "t2").attr("x", (p) => x(p.st)).attr("y", H - 14).attr("text-anchor", "middle").attr("font-size", 11).text((p) => STAGE_SHORT[p.st]);
  s.selectAll("text.d").data(pts).join("text").attr("class", "t2").attr("x", (p) => x(p.st)).attr("y", H - 2).attr("text-anchor", "middle").attr("font-size", 10).text((p) => (p.day ? fdate(p.day).replace(/, \d{4}$/, "") : ""));
}

export function drawBillNews(el, headsEl, D) {
  const node = typeof el === "string" ? document.querySelector(el) : el;
  const heads = typeof headsEl === "string" ? document.querySelector(headsEl) : headsEl;
  if (!node) return;
  const M = D.media;
  if (!M) {
    node.innerHTML = `<p class="muted" style="font-size:13.5px;margin:0">Not tracked yet. Coverage is collected a few hundred bills a night, starting with the most active, and only goes back about three months, so history builds up from launch.</p>`;
    return;
  }
  if (M.generic) {
    node.innerHTML = `<p style="font-size:13.5px;margin:0"><span class="flag">Name too generic</span> This bill's name also matches unrelated stories, so we show no count rather than a wrong one.</p>`;
    return;
  }
  const start = Date.parse(M.start + "T00:00:00Z");
  const series = M.series.map((v, i) => ({ t: new Date(start + i * 864e5), v }));
  if (!M.total) {
    node.innerHTML = `<p class="muted" style="font-size:13.5px;margin:0">No US articles named this bill in the last 90 days.</p>`;
  } else {
    const W = 420, H = 120, m = { t: 10, r: 10, b: 22, l: 28 };
    const x = scaleUtc().domain([series[0].t, series.at(-1).t]).range([m.l, W - m.r]);
    const y = scaleLinear().domain([0, max(series, (p) => p.v) || 1]).nice(3).range([H - m.b, m.t]);
    const s = svg(node, W, H, `Articles per day, ${fmt(M.total)} in 90 days`);
    s.append("g").selectAll("line").data(y.ticks(3)).join("line").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y).attr("y2", y).attr("stroke", css("--rule"));
    s.append("g").selectAll("text").data(y.ticks(3)).join("text").attr("class", "t2").attr("x", m.l - 6).attr("y", (d) => y(d) + 4).attr("text-anchor", "end").attr("font-size", 11).text((d) => d);
    s.append("path").datum(series).attr("d", area().x((p) => x(p.t)).y0(y(0)).y1((p) => y(p.v))).attr("fill", css("--accent-l"));
    s.append("path").datum(series).attr("d", line().x((p) => x(p.t)).y((p) => y(p.v))).attr("fill", "none").attr("stroke", css("--accent")).attr("stroke-width", 2);
    s.append("g").selectAll("text.x").data(x.ticks(3)).join("text").attr("class", "t2").attr("x", x).attr("y", H - 6).attr("text-anchor", "middle").attr("font-size", 11).text(utcFormat("%b %-d"));
  }
  if (heads) heads.innerHTML = (M.arts || []).slice(0, 4).map((a) => `<li><a href="${esc(a.url)}" rel="noopener nofollow" target="_blank">${esc(a.title || a.url)}</a><span>${esc(a.domain || "")} · ${fdate(a.date)}</span></li>`).join("");
}

export function drawBillCharts(D) {
  drawOddsHist("#c-oddshist", D);
  drawBillNews("#c-billnews", "#bill-heads", D);
}

// Section chips jump into the collapsed full summary (B1, B2).
export function wireSummary() {
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[data-open]");
    if (!a) return;
    const det = document.getElementById(a.dataset.open);
    if (det) det.open = true;
  });
}
export { select };
