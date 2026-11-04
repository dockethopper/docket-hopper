// robots.txt with an absolute sitemap address, built for whatever domain Pages reports.
export function GET({ site }: { site: URL }) {
  const map = new URL(`${import.meta.env.BASE_URL}sitemap-index.xml`, site).href;
  return new Response(`User-agent: *\nAllow: /\n\nSitemap: ${map}\n`, { headers: { "Content-Type": "text/plain; charset=utf-8" } });
}
