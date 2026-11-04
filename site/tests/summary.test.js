import { test } from "node:test";
import assert from "node:assert/strict";
import { summaryHTML } from "../src/lib/summary.js";

test("links named bills and escapes text", () => {
  const h = summaryHTML({ paras: ["H.R. 17 and S. 5 passed <quickly>.", "S. 56 did not."], links: { "H.R. 17": "119-hr-17", "S. 5": "119-s-5" } }, "/");
  assert.match(h, /<a href="\/bills\/119\/hr\/17\/">H\.R\. 17<\/a>/);
  assert.match(h, /<a href="\/bills\/119\/s\/5\/">S\. 5<\/a> passed &lt;quickly&gt;/);
  assert.match(h, /<p>S\. 56 did not\.<\/p>/); // "S. 5" must not swallow "S. 56"
});

test("no links, no paragraphs", () => {
  assert.equal(summaryHTML({ paras: [] }), "");
  assert.equal(summaryHTML({ paras: ["Quiet week."] }), "<p>Quiet week.</p>");
});
