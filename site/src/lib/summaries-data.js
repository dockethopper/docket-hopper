// Reads the AI summaries written by `python -m ingest summaries`. Missing file (no key yet,
// sample builds) means no drop-downs. Build-time only.
import fs from "node:fs";
import path from "node:path";

export function loadSummaries() {
  try {
    return JSON.parse(fs.readFileSync(path.join(process.cwd(), "src", "data", "summaries.json"), "utf8"));
  } catch {
    return { live: false, sections: {} };
  }
}
