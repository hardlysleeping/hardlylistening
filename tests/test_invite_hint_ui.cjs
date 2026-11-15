// The Account panel's invite line. A regular member holds one outstanding link; an
// administrator is not capped (the same person also mints in Settings → People), so the
// panel must keep the button and say so rather than hiding it behind "one person".
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const html = fs.readFileSync("web/index.html", "utf8");
const source = html.slice(html.indexOf("function inviteExpiry"), html.indexOf("async function makeInvite"));
const nodes = {
  "#invite-link-row": { hidden: false }, "#invite-url": { value: "stale" },
  "#invite-make": { hidden: false }, "#invite-state": { textContent: "" },
};
let state = { active: false, expires_at: null, unlimited: false };
const context = vm.createContext({
  $: id => nodes[id],
  fetch: async () => ({ json: async () => state }),
});
vm.runInContext(source, context);

(async () => {
  // A member: one outstanding link, button hidden once it exists.
  await vm.runInContext("loadInvite()", context);
  assert.strictEqual(nodes["#invite-state"].textContent, "You can invite one person.");
  assert.strictEqual(nodes["#invite-make"].hidden, false);
  assert.strictEqual(nodes["#invite-url"].value, "", "a stale secret is cleared on load");

  state = { active: true, expires_at: Math.floor(Date.now() / 1000) + 86400, unlimited: false };
  await vm.runInContext("loadInvite()", context);
  assert(/already have an active invite/i.test(nodes["#invite-state"].textContent));
  assert.strictEqual(nodes["#invite-make"].hidden, true, "a member gets one link at a time");

  // An administrator: unlimited, so the button survives an active link and the line says so.
  state = { active: false, expires_at: null, unlimited: true };
  await vm.runInContext("loadInvite()", context);
  const adminLine = nodes["#invite-state"].textContent;
  assert(/as many signup links/i.test(adminLine), "admin wording: " + adminLine);
  assert(!/invite one person/i.test(adminLine), "admin wording must not claim a one-person limit: " + adminLine);
  assert.strictEqual(nodes["#invite-make"].hidden, false);

  state = { active: true, expires_at: Math.floor(Date.now() / 1000) + 86400, unlimited: true };
  await vm.runInContext("loadInvite()", context);
  assert.strictEqual(nodes["#invite-make"].hidden, false, "an admin keeps minting with a link outstanding");
  assert(/can make another/i.test(nodes["#invite-state"].textContent), "admin line offers another link");

  console.log("Invite hint PASS: member one-link rule, admin unlimited keeps the button");
})().catch(e => { console.error("FAIL " + e.message); process.exitCode = 1; });