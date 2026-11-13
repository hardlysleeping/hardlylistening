#!/usr/bin/env node
/* The player's build step, and its whole build: copy an explicit allow-list of the app's
   existing sources into `player/www/`, which is the only thing the Tauri shell serves.

   The list below is the single source of truth for what a player bundles. Adding a file is
   one line here; excluding acquisition is one line of absence, and the file that is left out
   is named in `EXCLUDED` so the omission reads as a decision rather than a forgotten copy.
   Nothing is rewritten on the way in: every file is copied byte for byte and then hashed to
   prove it, because the point of the player is that it runs the library the server serves,
   not a second hand-maintained copy of it that can drift.

   `node assemble.mjs` writes `player/www/` (gitignored). `--out <dir>` writes elsewhere,
   `--list` prints the plan as JSON for the tests, and `--include-web <rel>` exists only so a
   test can prove the guards fail loudly — no build uses it.

   Standard library only: no bundler, no package.json, nothing to install. */
import { createHash } from 'node:crypto';
import { copyFile, mkdir, readFile, readdir, rm, stat, writeFile } from 'node:fs/promises';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB = resolve(HERE, '..', 'web');
const SHELL = join(HERE, 'shell');
const DEFAULT_OUT = join(HERE, 'www');
const MANIFEST = '.assembly.json';

/* What the app's own front end is made of, in the order the page loads it. Index markup and
   the classic scripts it names, the stylesheets `styles/app.css` imports, and the two icons
   the head points at. Everything else under `web/` belongs to a surface the player does not
   carry: the mixer, party, the swipe page, the login page, the service worker. */
const WEB_ALLOW = [
  'index.html',
  'preferences.js',
  'genres.js',
  'lyrics.js',
  'lifecycle.js',
  'js/library.js',
  'js/playback-queue.js',
  'js/social-feed.js',
  'styles/app.css',
  'styles/base.css',
  'styles/tokens.css',
  'styles/layout.css',
  'styles/motion.css',
  'styles/utilities.css',
  'styles/components/activity.css',
  'styles/components/app-chrome.css',
  'styles/components/artist.css',
  'styles/components/dialogs.css',
  'styles/components/feed.css',
  'styles/components/genres.css',
  'styles/components/library-cards.css',
  'styles/components/library-controls.css',
  'styles/components/library-row.css',
  'styles/components/lyrics.css',
  'styles/components/playback-queue.css',
  'styles/components/player.css',
  'styles/components/preferences.css',
  'styles/components/queue-view.css',
  'styles/components/social.css',
  'styles/components/status.css',
];

/* The shell's own two files and nothing else. `connect.html` is the window's first page; it
   links the assembled stylesheets above, so the gate looks like the app rather than like a
   second design. */
const SHELL_ALLOW = ['connect.html', 'connect.css', 'connect.js'];

/* Stated so the exclusion is checkable, not merely implied by absence. */
const EXCLUDED = [
  ['js/repo-discovery.js',
   'Repo acquisition: the player asks a server for music, it never runs a downloader'],
];

/* What may never land in the output, whoever put it there. The first line of defence is the
   allow-list (a file not listed is not copied); this is the second, run over the plan and
   again over the finished tree. */
const FORBIDDEN = ['repo-discovery.js', 'src-tauri', 'node_modules', 'electron', '*.py'];

function fail(message) {
  process.stderr.write(`assemble: ${message}\n`);
  process.exit(1);
}

function sha(buffer) {
  return createHash('sha256').update(buffer).digest('hex');
}

function forbiddenReason(rel) {
  const parts = rel.split(/[\\/]+/).filter(Boolean);
  const name = parts[parts.length - 1] || '';
  if (parts.includes('src-tauri')) return 'the host sources are not part of the served app';
  if (parts.includes('node_modules')) return 'the player has no dependencies to ship';
  if (parts.includes('electron') || /^electron(\..*)?$/i.test(name)) {
    return 'the player is a Tauri shell with the OS WebView, never Electron';
  }
  if (name === 'repo-discovery.js') return 'Repo acquisition is excluded by policy';
  if (name.endsWith('.py')) return 'the player ships no server code';
  return null;
}

async function isFile(path) {
  try {
    return (await stat(path)).isFile();
  } catch {
    return false;
  }
}

async function walk(dir, base = dir) {
  const found = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) found.push(...(await walk(full, base)));
    else found.push(relative(base, full).split(sep).join('/'));
  }
  return found.sort();
}

async function assemble({ out = DEFAULT_OUT, include = [] } = {}) {
  if (out === WEB || out.startsWith(WEB + sep)) {
    fail(`refusing to assemble into web/ (${out}): the app's sources are not build output`);
  }
  if (WEB.startsWith(out + sep)) {
    fail(`refusing to assemble into ${out}: that directory contains web/`);
  }

  const entries = [
    ...WEB_ALLOW.map(rel => ({ rel, from: join(WEB, rel) })),
    ...include.map(rel => ({ rel, from: join(WEB, rel) })),
    ...SHELL_ALLOW.map(rel => ({ rel, from: join(SHELL, rel) })),
  ];
  const claimed = new Set();
  for (const entry of entries) {
    if (claimed.has(entry.rel)) fail(`two sources claim ${entry.rel}`);
    claimed.add(entry.rel);
  }

  /* Both guards run before a byte is written, so a bad plan leaves no half-built tree. */
  const refusals = entries
    .map(entry => [entry.rel, forbiddenReason(entry.rel)])
    .filter(([, why]) => why);
  if (refusals.length) {
    fail('the allow-list would ship files the player must not contain:\n' +
      refusals.map(([rel, why]) => `  ${rel} — ${why}`).join('\n'));
  }

  const missing = [];
  for (const entry of entries) {
    if (!(await isFile(entry.from))) missing.push(`  ${entry.rel} (expected ${entry.from})`);
  }
  if (missing.length) {
    fail(`the allow-list names files that are not there:\n${missing.join('\n')}`);
  }

  /* A directory this script did not write is never replaced. The manifest is the receipt
     from a previous run; anything else is somebody's work and the person can remove it. */
  const existing = await readdir(out).catch(() => null);
  if (existing && existing.length && !existing.includes(MANIFEST)) {
    fail(`${out} holds files this script did not assemble (no ${MANIFEST}); ` +
      'point --out at an empty directory, or remove it yourself');
  }
  if (existing) await rm(out, { recursive: true, force: true });

  const files = [];
  for (const entry of entries) {
    const dest = join(out, entry.rel);
    await mkdir(dirname(dest), { recursive: true });
    await copyFile(entry.from, dest);
    const [source, copy] = await Promise.all([readFile(entry.from), readFile(dest)]);
    if (sha(source) !== sha(copy)) {
      fail(`copying ${entry.rel} changed it; the player must run the app's own bytes`);
    }
    files.push({ path: entry.rel, sha256: sha(copy), bytes: copy.length });
  }

  const shipped = (await walk(out))
    .map(rel => [rel, forbiddenReason(rel)])
    .filter(([, why]) => why);
  if (shipped.length) {
    fail('the assembled tree contains files the player must not contain:\n' +
      shipped.map(([rel, why]) => `  ${rel} — ${why}`).join('\n'));
  }

  await writeFile(join(out, MANIFEST), JSON.stringify({
    assembled: files.map(file => file.path),
    sources: { web: 'web/', shell: 'player/shell/' },
    files,
  }, null, 2) + '\n');
  return { out, files };
}

function parseArgs(argv) {
  const args = { out: DEFAULT_OUT, include: [], list: false };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--out' || arg === '--include-web') {
      const value = argv[++i];
      if (!value) fail(`${arg} needs a value`);
      if (arg === '--out') args.out = resolve(value);
      else args.include.push(value);
    } else if (arg === '--list') {
      args.list = true;
    } else {
      fail(`unknown argument ${arg}`);
    }
  }
  return args;
}

const args = parseArgs(process.argv.slice(2));
if (args.list) {
  process.stdout.write(JSON.stringify({
    web: WEB_ALLOW, shell: SHELL_ALLOW, excluded: EXCLUDED, forbidden: FORBIDDEN,
  }, null, 2) + '\n');
} else {
  const { out, files } = await assemble(args);
  process.stdout.write(`assembled ${files.length} files into ${out}\n`);
  for (const [rel, why] of EXCLUDED) process.stdout.write(`excluded ${rel} — ${why}\n`);
}