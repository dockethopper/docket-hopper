// L2: every bill-title template, checked against the record it was built from.
import { test } from "node:test";
import assert from "node:assert/strict";
import { billClaim, cosClause, shortName } from "../src/lib/bill.js";

const base = { id: "119-hr-6644", c: 119, cur: 119, t: "hr", n: 6644, title: "21st Century ROAD to Housing Act", intro: "2025-01-10", origin: "House",
  cos: { n: 10, D: 5, R: 5 }, ms: { intro: "2025-01-10", passed_one: "2025-03-01", first_chamber: "house", law: "2025-07-10" } };

test("law claim counts days from introduction", () => {
  assert.equal(billClaim({ ...base, status: "became_law" }), "21st Century ROAD to Housing Act became law 181 days after introduction, with cosponsors split almost evenly between the parties");
});
test("passed one chamber, live vs. past Congress", () => {
  assert.match(billClaim({ ...base, status: "passed_one" }), /passed the House 50 days after introduction and awaits a vote in the Senate$/);
  assert.match(billClaim({ ...base, c: 117, status: "passed_one" }), /passed the House but died in the Senate$/);
});
test("introduced bills", () => {
  assert.match(billClaim({ ...base, status: "introduced" }), /has not moved since its introduction on Jan 10, 2025/);
  assert.equal(billClaim({ ...base, c: 116, status: "introduced" }), "21st Century ROAD to Housing Act never left committee in the 116th Congress");
});
test("every status yields a sentence", () => {
  for (const s of ["became_law", "veto_overridden", "vetoed", "pocket_vetoed", "to_president", "passed_both", "agreed_to", "passed_one", "failed", "reported", "introduced"]) {
    const c = billClaim({ ...base, status: s });
    assert.ok(c.startsWith("21st Century ROAD") && !/undefined|null|NaN/.test(c), `${s}: ${c}`);
  }
});
test("cosponsor clause branches", () => {
  assert.equal(cosClause({ n: 0 }), "with no cosponsors");
  assert.equal(cosClause({ n: 10, D: 9, R: 1 }), "with cosponsors from both parties");
  assert.equal(cosClause({ n: 3, D: 3, R: 0 }), "with cosponsors, all Democrats");
  assert.equal(cosClause({ n: 1, D: 0, R: 1 }), "with one cosponsor, a Republican");
});
test("long or boilerplate titles fall back to the bill number", () => {
  assert.equal(shortName({ id: "119-hr-1", title: "An act to provide for reconciliation pursuant to title II of H. Con. Res. 14." }), "H.R. 1");
});

test("glossary links in action text (H9)", async () => {
  const { glossify } = await import("../src/lib/ui.js");
  assert.equal(glossify("Pocket Vetoed by President.", "/x/"), '<a href="/x/how-it-works/#g-pocket-veto">Pocket Vetoed</a> by President.');
  assert.equal(glossify("Passed Senate without amendment by Unanimous Consent.", "/"), 'Passed Senate without amendment by <a href="/how-it-works/#g-unanimous-consent">Unanimous Consent</a>.');
  assert.equal(glossify("Reported by the Committee. Reported again.", "/").match(/<a /g).length, 1);
  assert.equal(glossify("Introduced in House", "/"), "Introduced in House");
});
