import { defineConfig } from "astro/config";

// The nightly workflow sets SITE_URL and BASE_PATH from GitHub Pages (actions/configure-pages):
// "/billdistill" on zuzwan.github.io, "" once the dockethopper.com custom domain is set.
const base = (process.env.BASE_PATH || "").replace(/\/$/, "") + "/";

export default defineConfig({
  site: process.env.SITE_URL || "https://dockethopper.com",
  base,
  trailingSlash: "always",
  build: { format: "directory", concurrency: 4 },
  compressHTML: true,
  vite: { build: { chunkSizeWarningLimit: 800 } },
});
