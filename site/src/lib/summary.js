// Renders one AI summary section: escaped paragraphs, with each bill or law number it names
// linked to that bill's page. Pure string function so it can be tested without a build.
import { esc, billPath } from "./ui.js";

const rx = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export function summaryHTML(sec, base = "/") {
  const labels = Object.keys(sec.links || {}).sort((a, b) => b.length - a.length);
  const re = labels.length ? new RegExp(`(${labels.map((l) => rx(esc(l))).join("|")})(?![\\d])`, "g") : null;
  return (sec.paras || [])
    .map((p) => {
      let h = esc(p);
      if (re) h = h.replace(re, (m) => {
        const id = sec.links[labels.find((l) => esc(l) === m)];
        return id ? `<a href="${base}${billPath(id)}">${m}</a>` : m;
      });
      return `<p>${h}</p>`;
    })
    .join("");
}
