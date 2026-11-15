// Phone row layout: save + like + pin share one line and never push Pin onto its own row.
// Real styles and renderer, fixture data, no audio and no network.
const path = require('path'), assert = require('assert'), { chromium } = require('playwright');

const PAGE = 'file://' + path.resolve('web/index.html');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const vp of [{ width: 360, height: 900 }, { width: 390, height: 900 }, { width: 430, height: 900 }, { width: 1280, height: 900 }]) {
      const page = await browser.newPage({ viewport: vp });
      const errors = [];
      page.on('pageerror', e => errors.push(String(e)));
      await page.goto(PAGE, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(500);
      const out = await page.evaluate(() => {
        libAll = [{ id: 7, title: 'Hands All Over Me', artist_name: 'Whales', album: 'Monstercat', album_id: 'a1', playable: true, status: 'done', duration: 214, artwork: '', playlists: [] }];
        lib = libAll; currentRows = libAll; libView = 'songs'; libraryScope = 'shared';
        listeningSaves = { tracks: [], playlists: [], playlist_tracks: [] };
        document.querySelector('#view-library').hidden = false;
        document.querySelectorAll('#view-queue,#view-feed,#view-following').forEach(n => n.hidden = true);
        // Stand-in for preferences.js with a loaded state (the fixtures have none).
        const orig = listeningSaveControl;
        listeningSaveControl = (kind, id) => orig(kind, id) +
          '<span class="pref-pair"><button type="button" class="preference">♡</button><button type="button" class="preference">Pin</button></span>';
        document.querySelectorAll('#liblist').forEach(n => n.innerHTML = '');
        renderLibrary();
        const row = document.querySelector('.songrow');
        const acts = row.querySelector('.rowactions');
        const save = acts.querySelector('.lsave');
        const pair = acts.querySelector('.pref-pair');
        const kids = [...acts.children].filter(el => el.getBoundingClientRect().width > 0);
        // Centres, not tops: buttons of different heights are still one line.
        const centres = kids.map(el => { const b = el.getBoundingClientRect(); return b.top + b.height / 2; });
        const lines = centres.reduce((acc, y) => {
          const line = acc.find(l => Math.abs(l - y) < 8);
          return line === undefined ? acc.concat(y) : acc;
        }, []).length;
        const r = el => el.getBoundingClientRect();
        return {
          rowHeight: Math.round(r(row).height),
          actionLines: lines,
          pairIsOneBox: !!pair && pair.querySelectorAll('button').length === 2,
          saveWidth: Math.round(r(save).width),
          saveHeight: Math.round(r(save).height),
          rowOverflow: row.scrollWidth > row.clientWidth + 1,
          docOverflow: document.documentElement.scrollWidth > innerWidth,
          pairInsideRow: r(pair).right <= r(row).right + 1,
          // the actions line sits above the vote line on a phone, not below the fold
          actionsAboveVotes: r(acts).top < r(row.querySelector('.votes')).top,
        };
      });
      assert.deepEqual(errors, [], 'page errors: ' + errors.join('; '));
      assert.equal(out.actionLines, 1, `${vp.width}px: save + like + pin wrapped onto ${out.actionLines} lines`);
      assert(out.pairIsOneBox, `${vp.width}px: heart and pin are not one nowrap group`);
      assert(!out.rowOverflow, `${vp.width}px: row overflows horizontally`);
      assert(!out.docOverflow, `${vp.width}px: document overflows horizontally`);
      assert(out.pairInsideRow, `${vp.width}px: pin runs past the row edge`);
      if (vp.width <= 760) assert(out.actionsAboveVotes, `${vp.width}px: actions are not on the row's own line`);
      if (vp.width > 760) {
        assert(out.saveWidth >= 28 && out.saveWidth <= 40 && out.saveWidth === out.saveHeight,
          'desktop save box is a square icon button: ' + out.saveWidth + 'x' + out.saveHeight);
      } else {
        assert(out.saveWidth >= 30 && out.saveWidth <= 40 && out.saveWidth === out.saveHeight,
          'phone save box is a square icon button: ' + out.saveWidth + 'x' + out.saveHeight);
        assert(out.rowHeight <= 160, 'phone row too tall: ' + out.rowHeight);
      }
      await page.close();
      console.log(`Row actions ${vp.width}: one line for save/like/pin, pair never split, no overflow, row ${out.rowHeight}px, save ${out.saveWidth}px PASS`);
    }
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
