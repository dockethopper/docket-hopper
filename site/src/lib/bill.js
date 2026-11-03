// One bill page, as an HTML string. Used at build time for pre-rendered bills and in the
// browser for every other bill, so both look identical.
import { esc, fmt, pct, ord, billLabel, billPath, mapPath, fdate, daysBetween, STAGES, stageName, isLaw, isVeto, party, track, LEG } from "./ui.js";

const CG_TYPE = { hr: "house-bill", s: "senate-bill", hjres: "house-joint-resolution", sjres: "senate-joint-resolution", hres: "house-resolution", sres: "senate-resolution", hconres: "house-concurrent-resolution", sconres: "senate-concurrent-resolution" };
export const congressGovUrl = (d) => `https://www.congress.gov/bill/${ord(d.c)}-congress/${CG_TYPE[d.t] || d.t}/${d.n}`;
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);

export function shortName(d) {
  const t = d.title || "";
  if (!t || t.length > 80 || /^(to |a bill|an act|a joint resolution|a resolution|providing for|expressing|making )/i.test(t)) return billLabel(d.id);
  return t;
}

export function cosClause(cos) {
  const n = cos.n || 0;
  if (!n) return "with no cosponsors";
  const D = cos.D || 0, R = cos.R || 0;
  if (D && R) return Math.abs(D - R) <= 0.2 * (D + R) ? "with cosponsors split almost evenly between the parties" : "with cosponsors from both parties";
  if (D) return `with ${n === 1 ? "one cosponsor" : "cosponsors"}, all Democrats`.replace("all Democrats", n === 1 ? "a Democrat" : "all Democrats");
  if (R) return `with ${n === 1 ? "one cosponsor" : "cosponsors"}, ${n === 1 ? "a Republican" : "all Republicans"}`;
  return `with ${n === 1 ? "one cosponsor" : n + " cosponsors"}`;
}

const CH = (c) => (c === "house" ? "House" : c === "senate" ? "Senate" : null);
const other = (c) => (c === "house" ? "Senate" : c === "senate" ? "House" : "other chamber");

// L2: the page title is a sentence built from the record, with a branch for every status.
export function billClaim(d) {
  const name = shortName(d), ms = d.ms || {}, live = d.c === d.cur, s = d.status;
  const since = (x) => (x && d.intro ? daysBetween(d.intro, x) : null);
  const ch = CH(ms.first_chamber) || (d.origin === "Senate" ? "Senate" : d.origin === "House" ? "House" : null);
  const days = since(ms.law);
  switch (s) {
    case "became_law":
      return days != null ? `${name} became law ${fmt(days)} ${days === 1 ? "day" : "days"} after introduction, ${cosClause(d.cos)}` : `${name} became law, ${cosClause(d.cos)}`;
    case "veto_overridden":
      return `${name} became law over a presidential veto${days != null ? `, ${fmt(days)} days after introduction` : ""}`;
    case "vetoed":
      return `${name} passed both chambers and was vetoed${ms.veto ? " on " + fdate(ms.veto) : ""}`;
    case "pocket_vetoed":
      return `${name} passed both chambers and was pocket vetoed`;
    case "to_president":
      return `${name} has passed both chambers and is on the president's desk`;
    case "passed_both":
      return live ? `${name} has passed both chambers, ${cosClause(d.cos)}` : `${name} passed both chambers but did not become law`;
    case "agreed_to":
      return `${name} was agreed to by the ${ch || "chamber"}${ms.passed_one ? " on " + fdate(ms.passed_one) : ""}`;
    case "passed_one": {
      const n = since(ms.passed_one);
      if (!live) return `${name} passed the ${ch || "first chamber"} but died in the ${other(ms.first_chamber)}`;
      return `${name} passed the ${ch || "first chamber"}${n != null ? ` ${fmt(n)} days after introduction` : ""} and awaits a vote in the ${other(ms.first_chamber)}`;
    }
    case "failed":
      return `${name} failed a floor vote`;
    case "reported":
      return live ? `${name} was reported by committee and awaits a floor vote` : `${name} was reported by committee but never got a floor vote`;
    default:
      return live ? `${name} has not moved since its introduction on ${fdate(d.intro)}, ${cosClause(d.cos)}` : `${name} never left committee in the ${ord(d.c)} Congress`;
  }
}

function summaryBlocks(text) {
  if (!text) return { paras: [], titles: [], words: 0 };
  const parts = text.split(/(?=TITLE [IVXL]+--|\(Sec\. \d+\))/).map((p) => p.trim()).filter(Boolean);
  const titles = [];
  const paras = parts.map((p, i) => {
    const m = p.match(/^TITLE ([IVXL]+)--(.+?)(?= \(Sec|$)/);
    if (m) titles.push({ n: m[1], name: m[2].trim().toLowerCase().replace(/(^|\s)\S/g, (x) => x.toUpperCase()), i });
    return p;
  });
  return { paras, titles, words: text.split(/\s+/).length };
}

function relatedHTML(d, base) {
  if (!d.rel?.length) return `<p class="muted" style="font-size:13.5px">The bill status record lists no related bills.</p>`;
  const total = d.rel.reduce((a, g) => a + g.n, 0);
  return `<p class="note" style="margin:2px 0 4px">Each bill appears once, under its strongest link. ${fmt(total)} in all.</p>` +
    d.rel.map((g, i) => `<details class="rel-group" ${i < 2 && g.name !== "Related, per CRS or House" ? "open" : ""}><summary>${esc(g.name)} · ${fmt(g.n)}</summary><ul>${g.items.map(([id, title, status, p]) => `<li><a class="mono" href="${base}${billPath(id)}">${ord(+id.split("-")[0])} ${billLabel(id)}</a> ${esc(title || "Not in our records (before 2013)")} ${party(p)} ${status ? `<span class="muted">· ${stageName(status)}</span>` : ""}</li>`).join("")}${g.n > g.items.length ? `<li class="muted">+${fmt(g.n - g.items.length)} more on the network map</li>` : ""}</ul></details>`).join("");
}

export function billHTML(d, base) {
  const ms = d.ms || {};
  const lawLike = isLaw(d.status), vet = isVeto(d.status);
  const steps = [ms.intro || d.intro, ms.reported, ms.passed_one, ms.passed_both, lawLike ? ms.law : vet ? ms.veto : null];
  const statusLine = steps.map((dt, i) => {
    const veto = i === 4 && vet;
    return `<div class="${dt ? (veto ? "x" : "on") : ""}"><b>${veto ? (d.status === "pocket_vetoed" ? "Pocket vetoed" : "Vetoed") : STAGES[i]}</b>${dt ? fdate(dt) : "—"}</div>`;
  }).join("");
  const { paras, titles, words } = summaryBlocks(d.summary);
  const long = words > 400;
  const shownParas = long ? paras.slice(0, Math.max(1, Math.min(5, paras.findIndex((p, i) => i > 0 && /^TITLE/.test(p)) > 0 ? paras.findIndex((p, i) => i > 0 && /^TITLE/.test(p)) : 3))) : paras;
  const pid = (i) => `s-${i}`;
  const sponsor = d.sponsor || {};
  const cos = d.cos || {};
  const ar = d.area_rec;
  const rec = sponsor.rec;
  const cong = `${ord(d.c)} Congress`;
  const chips = [lawLike && d.law ? `<span class="chip acc">${esc(d.law_type || "Public law")} ${esc(d.law)}</span>` : "", vet ? `<span class="chip amb">${stageName(d.status)}</span>` : "", d.area ? `<span class="chip">${esc(d.area)}</span>` : "", !LEG.includes(d.t) ? `<span class="chip">Resolution</span>` : ""].join("");
  const cosSplit = cos.n ? `${fmt(cos.n)} ${cos.n === 1 ? "cosponsor" : "cosponsors"}: ${fmt(cos.D)} ${cos.D === 1 ? "Democrat" : "Democrats"}, ${fmt(cos.R)} ${cos.R === 1 ? "Republican" : "Republicans"}${cos.I ? `, ${fmt(cos.I)} independent` : ""}` : "no cosponsors";

  const summary = paras.length ? `
     <p class="note" style="margin:2px 0 10px">Written by the Congressional Research Service [2]. ${fmt(words)} words${long ? "; opening shown" : ""}.</p>
     ${titles.length > 1 ? `<div class="toc" style="margin-bottom:10px">${titles.slice(0, 14).map((t) => `<a class="chip" href="#${pid(t.i)}" data-open="full-summary">Title ${t.n}: ${esc(t.name)}</a>`).join("")}${titles.length > 14 ? `<span class="chip">+${titles.length - 14} more titles</span>` : ""}</div>` : ""}
     <div class="summary">${long
       ? `${shownParas.map((p) => `<p>${esc(p)}</p>`).join("")}<details id="full-summary"><summary>Read the full summary (${fmt(words)} words)</summary>${paras.slice(shownParas.length).map((p, j) => `<p id="${pid(j + shownParas.length)}">${esc(p)}</p>`).join("")}</details>`
       : paras.map((p, i) => `<p id="${pid(i)}">${esc(p)}</p>`).join("")}</div>`
    : `<p class="muted">CRS has not published a summary of this bill yet. ${d.text_url ? `<a href="${esc(d.text_url)}">Read the bill text</a>.` : ""}</p>`;

  return `<div class="bill-head"><div class="meta"><span class="mono" style="font-size:14px;color:var(--ink)">${billLabel(d.id)} · ${cong}</span>${chips}</div>
    <h1 class="claim" style="max-width:34ch">${esc(billClaim(d))}</h1>
    ${shortName(d) !== d.title ? `<p class="sub" style="margin-top:0">${esc(d.title)}</p>` : ""}
    <div class="meta">${sponsor.name ? `Sponsored by ${esc(sponsor.name)} ${party(sponsor.party, sponsor.state)} · ` : ""}${cosSplit}</div>
    <div class="status-line" aria-label="Progress: ${stageName(d.status)}">${statusLine}</div></div>
   <div class="grid2"><div style="min-width:0">
     <div class="label">What it changes · CRS summary</div>
     ${summary}
   </div><div style="display:flex;flex-direction:column;gap:18px;min-width:0">
     <div class="sowhat"><div class="label">Context</div><dl class="facts">
      ${d.committees?.length ? `<dt>Committees</dt><dd>${d.committees.map(esc).join("<br>")}</dd>` : ""}
      ${d.area && ar ? `<dt>Policy area</dt><dd>${esc(d.area)}: ${fmt(ar[1])} of ${fmt(ar[0])} bills became law, 113th–${ord(d.cur - 1)} (${pct(ar[0] ? ar[1] / ar[0] : 0)})</dd>` : ""}
      ${rec ? `<dt>Sponsor</dt><dd>${fmt(rec[1])} of ${fmt(rec[0])} bills enacted since 2013</dd>` : ""}
      ${d.versions?.length ? `<dt>Text versions</dt><dd>${d.versions.length}${d.versions.length > 1 ? `, from ${esc(d.versions[0][0].toLowerCase())} to ${esc(d.versions.at(-1)[0].toLowerCase())}` : `: ${esc(d.versions[0][0].toLowerCase())}`}${d.words ? ` · ${fmt(d.words)} words` : ""}</dd>` : ""}
      ${d.subjects?.length ? `<dt>Subjects</dt><dd>${d.subjects.slice(0, 6).map((s) => `<span class="chip" style="margin:0 4px 4px 0">${esc(s)}</span>`).join("")} ${d.n_subjects > 6 ? `<span class="muted">+${d.n_subjects - 6} more</span>` : ""}</dd>` : ""}
      <dt>Latest action</dt><dd>${fdate(d.latest?.[0])}: ${esc(d.latest?.[1] || "")}</dd>
      <dt>Records</dt><dd><a href="${congressGovUrl(d)}">Congress.gov</a>${d.text_url ? ` · <a href="${esc(d.text_url)}">Bill text</a>` : ""}</dd>
     </dl></div>
     <div><div class="exh-h"><span class="n">Exhibit 1</span><b>Passage odds band at each stage,</b> <span class="u">rated when the bill reached it [4]</span></div>
      <div class="chart" id="c-oddshist"></div></div>
   </div></div>
   <div class="grid2e"><div style="min-width:0"><div class="label">Related bills · ${fmt((d.rel || []).reduce((a, g) => a + g.n, 0))}</div>
     ${relatedHTML(d, base)}
     ${d.net ? `<p style="margin-top:12px"><a href="${base}${mapPath(d.id)}">See ${d.net === 1 ? "this link" : `these ${fmt(d.net)} bills`} on the network map →</a></p>` : ""}</div>
   <div style="min-width:0"><div class="exh-h"><span class="n">Exhibit 2</span><b>Articles naming the bill per day,</b> <span class="u">last 90 days, US sources [3]</span></div><div class="chart" id="c-billnews"></div><ul class="heads" id="bill-heads"></ul></div></div>`;
}

export const billNote = "Summary text from CRS [2]; stages and dates from the bill status record [1]. Odds are bands from the passage model [4], recorded each time the bill reaches a new stage.";
