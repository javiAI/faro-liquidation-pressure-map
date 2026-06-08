// Optional Cloudflare Worker — lets the page's "↻" button trigger a REAL
// on-demand pipeline run, without ever exposing a token in the public page.
//
// Why a proxy at all? A static GitHub Pages site cannot call the GitHub API to start
// a workflow, because that needs a token — and any token committed to a public repo is
// auto-revoked by secret scanning. So the token lives ONLY inside this Worker.
//
// Setup (free, ~5 min):
//   1. Create a fine-grained PAT with "Actions: read and write" on ONLY this repo.
//   2. Deploy this file as a Cloudflare Worker; add a secret  GH_TOKEN = <that PAT>.
//   3. Put the Worker URL into build_site.py -> _JS -> const DISPATCH_URL = "...";
//      and redeploy the page.
//
// Note: the update workflow uses concurrency: cancel-in-progress, so a dispatch
// restarts the live loop; the first fresh point lands after one pipeline pass
// (~4-5 min for 2,000 wallets). The button keeps polling and updates when it arrives.

const OWNER = "javiAI";
const REPO = "faro-liquidation-pressure-map";
const WORKFLOW = "update.yml";
const REF = "main";

export default {
  async fetch(request, env) {
    const cors = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "content-type",
    };
    if (request.method === "OPTIONS") return new Response(null, { headers: cors });
    if (request.method !== "POST")
      return new Response("POST only", { status: 405, headers: cors });

    const resp = await fetch(
      `https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GH_TOKEN}`,
          Accept: "application/vnd.github+json",
          "User-Agent": "liq-map-dispatch",
          "X-GitHub-Api-Version": "2022-11-28",
        },
        body: JSON.stringify({ ref: REF }),
      },
    );
    return new Response(JSON.stringify({ dispatched: resp.status === 204 }), {
      status: resp.ok ? 200 : 502,
      headers: { ...cors, "content-type": "application/json" },
    });
  },
};
