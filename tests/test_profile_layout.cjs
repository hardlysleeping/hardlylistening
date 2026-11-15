// My profile / Account dialog: one column, labels above their field, and a Close that
// is on screen at every scroll position. The dialog shipped with no layout at all, so
// every label and input flowed inline (labels wrapping mid-word on a phone) and the only
// close control sat at the far end of a long scroll on a device with no Escape key.
const fs = require("fs"), assert = require("assert"), { chromium } = require("playwright");
const html = fs.readFileSync("web/index.html", "utf8");
const polish = require("./_css.cjs").appCss();
const prefs = "", inlineStyle = "";
const doc = html.replace(/<script[\s\S]*?<\/script>/g, "").replace(/<link[^>]*>/g, "")
  .replace("</head>", `<style>${inlineStyle}${polish}${prefs}</style></head>`);

const FIELDS = ["profile-name", "profile-icon", "account-email", "account-current", "account-new", "account-confirm"];

const measure = (FIELDS) => {
  const dlg = document.getElementById("dlg-profile");
  const d = dlg.getBoundingClientRect();
  const head = dlg.querySelector(".prof-head");
  if (!head) throw new Error("the dialog has no sticky .prof-head header");
  const close = head.querySelector("button");
  if (!close) throw new Error("the sticky header has no Close control");
  const c = close.getBoundingClientRect();
  const nav = document.getElementById("appnav");
  const navRect = nav.getBoundingClientRect();
  const hit = document.elementFromPoint(c.left + c.width / 2, c.top + c.height / 2);
  const stacked = [];
  for (const id of FIELDS) {
    const field = document.getElementById(id);
    if (field.hidden) continue;
    const label = document.querySelector(`label[for="${id}"]`);
    if (!label || label.hidden) continue;
    const f = field.getBoundingClientRect(), l = label.getBoundingClientRect();
    stacked.push({ id, labelBottom: Math.round(l.bottom), fieldTop: Math.round(f.top),
      leftGap: Math.round(f.left - l.left), fieldWidth: Math.round(f.width) });
  }
  const overflow = [...dlg.querySelectorAll("input,select,button,label,p,div,section,form")]
    .map(el => el.getBoundingClientRect().right - d.right).filter(v => v > 1);
  return {
    stacked,
    contentWidth: dlg.clientWidth - 36,
    dialog: { left: Math.round(d.left), right: Math.round(d.right), top: Math.round(d.top), bottom: Math.round(d.bottom) },
    nav: { position: getComputedStyle(nav).position, top: Math.round(navRect.top) },
    overflow,
    close: { top: Math.round(c.top), bottom: Math.round(c.bottom), insideDialog: c.top >= d.top - 1 && c.bottom <= d.bottom + 1,
      hit: hit === close ? "close" : (hit ? (hit.id || hit.tagName) : "none") },
    scrollable: dlg.scrollHeight > dlg.clientHeight, scrollTop: Math.round(dlg.scrollTop),
  };
};

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const [w, h] of [[390, 844], [390, 660], [1280, 800]]) {
      const page = await browser.newPage({ viewport: { width: w, height: h } });
      await page.route("**/*", r => r.abort());
      await page.setContent(doc);
      await page.evaluate(() => {
        const dlg = document.getElementById("dlg-profile");
        dlg.showModal();
        // Worst realistic case: the account fields, providers and an invite link all open.
        document.getElementById("account-current").hidden = false;
        document.getElementById("account-emailnew").hidden = false;
        document.getElementById("account-providers").hidden = false;
        document.getElementById("account-provider-list").textContent = "Google";
        document.getElementById("invite-link-row").hidden = false;
        document.getElementById("invite-url").value = "https://example.com/signup#invite=abcdefghijklmnop";
      });
      const top = await page.evaluate(measure, FIELDS);
      for (const row of top.stacked) {
        assert(row.labelBottom <= row.fieldTop + 1, `${w}x${h} ${row.id}: label and field share a line (label bottom ${row.labelBottom}, field top ${row.fieldTop})`);
        assert(Math.abs(row.leftGap) <= 2, `${w}x${h} ${row.id}: field starts ${row.leftGap}px from its label`);
        assert(row.fieldWidth >= top.contentWidth * 0.8, `${w}x${h} ${row.id}: field only ${row.fieldWidth}px of ${top.contentWidth}px`);
      }
      assert(top.stacked.length >= 4, `${w}x${h}: expected the account fields to be visible`);
      assert.deepStrictEqual(top.overflow, [], `${w}x${h}: content overflows the dialog: ${JSON.stringify(top.overflow)}`);
      assert.strictEqual(top.close.hit, "close", `${w}x${h}: Close is not the top element at the top of the scroll (hit ${top.close.hit})`);
      assert(top.close.insideDialog, `${w}x${h}: Close sits outside the dialog box`);
      if (top.nav.position === "fixed") assert(top.dialog.bottom <= top.nav.top, `${w}x${h}: dialog ends ${top.dialog.bottom - top.nav.top}px under the phone nav bar`);
      await page.evaluate(() => { const d = document.getElementById("dlg-profile"); d.scrollTop = d.scrollHeight; });
      const bottom = await page.evaluate(measure, FIELDS);
      assert.strictEqual(bottom.close.hit, "close", `${w}x${h}: Close is not reachable at the end of the scroll (hit ${bottom.close.hit})`);
      assert(bottom.scrollTop > top.scrollTop, `${w}x${h}: the dialog did not scroll`);
      console.log(`ok   ${w}x${h}: ${top.stacked.length} fields stacked, Close reachable at both ends (scroll ${bottom.scrollTop}px)`);
      await page.close();
    }
    console.log("Profile dialog layout PASS");
  } finally { await browser.close(); }
})().catch(e => { console.error("FAIL " + e.message); process.exitCode = 1; });