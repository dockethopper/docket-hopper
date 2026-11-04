import { defineConfig } from "astro/config";
import sitemap from "@astrojs/sitemap";

// The nightly workflow sets SITE_URL and BASE_PATH from GitHub Pages (actions/configure-pages):
// "/docket-hopper" on dockethopper.github.io, "" once the dockethopper.com custom domain is set.
const base = (process.env.BASE_PATH || "").replace(/\/$/, "") + "/";

export default defineConfig({
  site: process.env.SITE_URL || "https://dockethopper.com",
  base,
  trailingSlash: "always",
  build: { format: "directory", concurrency: 4 },
  compressHTML: true,
  // Lists every pre-rendered page (current Congress, all laws and vetoes, the other pages).
  // Older bills render in the browser from JSON, so search engines see them less reliably.
  integrations: [sitemap()],
  vite: { build: { chunkSizeWarningLimit: 800 } },
});
