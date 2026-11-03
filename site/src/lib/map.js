// Network map (N1–N4). Positions come precomputed from the nightly export; the browser
// only draws, opens the click-through card, and on phones shows a grouped list instead.
import { select, css, showTip, hideTip } from "./charts.js";
import { esc, fmt, ord, billLabel, billPath, stageOf, stageName, isVeto, track, party } from "./ui.js";

export function rows(J) {
  const ix = Object.fromEntries(J.cols.map((c, i) => [c, i]));
  return J.nodes.map((r) => ({ id: r[ix.id], d: r[ix.d], title: r[ix.title] || billLabel(r[ix.id]), status: r[ix.status], party: r[ix.party], area: r[ix.area], cos: r[ix.cos] || 0, x: r[ix.x], y: r[ix.y] }));
}

export function networkClaim(name, J) {
  const nodes = rows(J).filter((n) => n.id !== J.center);
  const n = nodes.length;
  if (!n) return `The ${name} has no recorded links to other bills`;
  const counts = {};
  nodes.forEach((x) => { if (x.area) counts[x.area] = (counts[x.area] || 0) + 1; });
  const top = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
  const base = `The ${name} connects to ${fmt(n)} other ${n === 1 ? "bill" : "bills"}`;
  return top && top[1] >= 2 && top[1] / n >= 0.25 ? `${base}, ${fmt(top[1])} of them in ${top[0].toLowerCase()} policy` : base;
}

const fill = (d) => (isVeto(d.status) ? css("--amber") : stageOf(d.status) === 4 ? css("--accent") : stageOf(d.status) >= 2 ? css("--paper") : css("--grey"));
const stroke = (d, center) => (d.id === center ? css("--ink") : stageOf(d.status) >= 2 && stageOf(d.status) < 4 && !isVeto(d.status) ? css("--accent") : css("--paper"));

export function drawMap(wrap, J, { base, centerLabel, listEl }) {
  const W = 900, H = 560;
  const nodes = rows(J), center = J.center;
  const byIdx = nodes;
  const links = J.edges.map(([a, b, k]) => ({ s: byIdx[a], t: byIdx[b], k: k ? "identical" : "related" }));
  const r = (d) => (d.id === center ? 14 : 4 + Math.sqrt(d.cos || 0) * 0.9);
  const s = select(wrap).insert("svg", ":first-child").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "group").attr("aria-label", centerLabel ? `Network of bills linked to ${centerLabel}` : "Network of bills");
  s.append("g").attr("aria-hidden", "true").selectAll("line").data(links).join("line").attr("x1", (l) => l.s.x).attr("y1", (l) => l.s.y).attr("x2", (l) => l.t.x).attr("y2", (l) => l.t.y)
    .attr("stroke", css("--ink")).attr("stroke-opacity", 0.35).attr("stroke-width", (l) => (l.k === "identical" ? 1.8 : 0.8));
  const deg = new Map();
  links.forEach((l) => { deg.set(l.s.id, (deg.get(l.s.id) || 0) + 1); deg.set(l.t.id, (deg.get(l.t.id) || 0) + 1); });
  const g = s.append("g").selectAll("g").data(nodes).join("g").attr("transform", (d) => `translate(${d.x},${d.y})`).style("cursor", "pointer")
    .attr("tabindex", 0).attr("role", "button").attr("aria-label", (d) => `${billLabel(d.id)}, ${ord(+d.id.split("-")[0])} Congress: ${d.title}. ${stageName(d.status)}`);
  g.append("circle").attr("r", (d) => r(d) + 6).attr("fill", "transparent");
  g.append("circle").attr("r", r).attr("fill", fill).attr("stroke", (d) => stroke(d, center)).attr("stroke-width", (d) => (d.id === center ? 3 : 2));
  const c = nodes.find((n) => n.id === center);
  if (c && centerLabel) s.append("text").attr("class", "halo").attr("x", c.x).attr("y", Math.min(H - 6, c.y + 30)).attr("text-anchor", "middle").attr("font-size", 13).attr("font-weight", 600).text(centerLabel);
  const ov = wrap.querySelector(".overlay");
  const open = (d) => {
    ov.hidden = false;
    ov.innerHTML = `<button class="close" aria-label="Close">×</button><div class="mono">${ord(+d.id.split("-")[0])} Congress · ${billLabel(d.id)}</div><h3>${esc(d.title)}</h3>
      <div style="font-size:13.5px;display:flex;flex-direction:column;gap:4px"><div>${track(d.status)}</div><div>${d.party ? party(d.party) + " sponsor · " : ""}${fmt(d.cos)} cosponsors · ${esc(d.area || "No policy area")}</div>
      <div class="muted">${deg.get(d.id) || 0} links on this map</div></div><a class="btn go" href="${base}${billPath(d.id)}">Go to bill →</a>`;
    ov.querySelector(".close").onclick = () => { ov.hidden = true; };
  };
  g.on("click", (e, d) => open(d)).on("keydown", (e, d) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(d); } })
    .on("mousemove", (e, d) => showTip(e, `<b>${billLabel(d.id)}</b> (${ord(+d.id.split("-")[0])})<br>${esc(d.title)}`)).on("mouseleave", hideTip);
  const first = nodes.find((n) => n.id !== center && stageOf(n.status) === 4) || nodes.find((n) => n.id !== center);
  if (first && matchMedia("(min-width: 641px)").matches) open(first);
  // N4: grouped list for phones (hidden on wider screens by CSS)
  if (listEl) {
    const groups = center
      ? [["Linked directly", nodes.filter((n) => n.d === 1)], ["One more step out", nodes.filter((n) => n.d === 2)]]
      : Object.entries(nodes.reduce((a, n) => { const k = `${ord(+n.id.split("-")[0])} Congress`; (a[k] ||= []).push(n); return a; }, {}));
    listEl.innerHTML = groups.filter(([, v]) => v.length).map(([name, v]) => `<h3>${name} · ${v.length}</h3><ul>${v.sort((a, b) => stageOf(b.status) - stageOf(a.status) || b.cos - a.cos).map((n) => `<li><a href="${base}${billPath(n.id)}">${esc(n.title)}</a><br><span class="mono muted">${ord(+n.id.split("-")[0])} ${billLabel(n.id)}</span> ${track(n.status)}</li>`).join("")}</ul>`).join("");
  }
}

export const LEGEND = `<div class="legend" style="margin-top:10px"><span><i class="sw" style="background:var(--accent)"></i>Became law</span><span><i class="sw" style="border:2px solid var(--accent);background:var(--paper)"></i>Passed one or both chambers</span><span><i class="sw" style="background:var(--grey)"></i>Introduced or reported</span><span><i class="sw" style="background:var(--amber)"></i>Vetoed</span><span><i class="ln" style="opacity:.6"></i>Identical bill</span><span><i class="ln" style="border-top-width:1px;opacity:.4"></i>Related bill</span></div>`;
