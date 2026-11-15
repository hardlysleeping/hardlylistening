// Playwright is installed wherever the browser driver lives on this box, not in this repo (the
// app has no node_modules and no build step). Tests used to spell that path out one file at a
// time. Resolve it in one place instead: an explicit PW_PLAYWRIGHT, then normal node
// resolution (a local install, or NODE_PATH), then the shared install under ~/apps.
const path = require("path"), os = require("os");

const candidates = [
  process.env.PW_PLAYWRIGHT,
  "playwright",
  path.join(os.homedir(), "apps", "ada-bot", "node_modules", "playwright"),
].filter(Boolean);

let mod = null, tried = [];
for (const candidate of candidates) {
  try { mod = require(candidate); break; } catch (_) { tried.push(candidate); }
}
if (!mod) throw new Error(`playwright not found (tried ${tried.join(", ")}) — set PW_PLAYWRIGHT to its directory`);

module.exports = mod;