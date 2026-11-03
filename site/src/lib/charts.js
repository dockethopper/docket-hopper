// Small D3 helpers shared by the page scripts (browser only).
import { select } from "d3-selection";
export { select };
export const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const tipEl = () => document.getElementById("tip");
export function showTip(e, html) {
  const tip = tipEl(); if (!tip) return;
  tip.innerHTML = html; tip.hidden = false;
  const w = tip.offsetWidth;
  tip.style.left = Math.min(e.clientX + 14, innerWidth - w - 8) + "px";
  tip.style.top = e.clientY + 14 + "px";
}
export function hideTip() { const t = tipEl(); if (t) t.hidden = true; }
export function svg(el, W, H, label) {
  return select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img").attr("aria-label", label);
}
export const dataOf = (id) => JSON.parse(document.getElementById(id)?.textContent || "null");
