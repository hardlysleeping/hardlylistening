#!/usr/bin/env node
/* The player's build step, and its whole build: copy an explicit allow-list of the app's
   existing sources into `player/www/`, which is the only thing the Tauri shell serves.

   The list below is the single source of truth for what a player bundles. Adding a file is
   one line here; excluding acquisition is one line of absence, and the file that is left out
   is named in `EXCLUDED` so the omission reads as a decision rather than a forgotten copy.
   Nothing is rewritten except the entry document, and only its references to EXCLUDED files:
   every other file is copied byte for byte and then hashed to prove it, because the point of
   the player is that it runs the library the server serves, not a second hand-maintained copy
   of it that can drift. A reference in a document to a file that is neither assembled nor
   excluded fails the build — silence there is how a shell ships with a page that asks for a
   file it does not have.

   `node assemble.mjs` writes `player/www/` (gitignored). `--out <dir>` writes elsewhere,
   `--list` prints the plan as JSON for the tests, and `--include-web <rel>` / `--forget-web
   <rel>` exist only so a test can prove the guards fail loudly — no build uses them.

   Standard library only: no bundler, no package.json, nothing to install. */
import { createHash } from 'node:crypto';
import { readdirSync } from 'node:fs';
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

/* The shell is the player's own code, so the shell directory *is* the list: whatever sits in
   `player/shell/` ships, and an allow-list over it would only be a way to write a file that is
   then silently missing from the build. The gate looks like the app rather than a second design
   because `connect.html` links the assembled stylesheets above it. What must never ship is
   stated in EXCLUDED instead, because that is the list where absence is a decision. */
const SHELL_ALLOW = readdirSync(SHELL, { withFileTypes: true })
  .filter(entry => entry.isFile() && !entry.name.startsWith('.'))
  .map(entry => entry.name)
  .sort();

/* Stated so the exclusion is checkable, not merely implied by absence. The entry document is
   assembled from the app's markup, so a reference to one of these would be a request for a
   file the player does not carry — the assembly drops the reference and records it in the
   receipt, which is why this list is also the list of what the player deliberately lacks. */
const EXCLUDED = [
  ['js/repo-discovery.js',
   'Repo acquisition: the player asks a server for music, it never runs a downloader'],
  ['app.webmanifest',
   'the installable-web-app manifest belongs to the browser tab the Python host serves'],
  ['icon-192.png',
   'the web app icon belongs to that manifest; the window icon is the Tauri bundle’s'],
  ['party.html',
   'the party page is a host surface the player does not carry'],
  ['dj.html',
   'the mixer is a host surface the player does not carry'],
  ['swipe.html',
   'the swipe page is a host surface the player does not carry'],
];

/* The one document that is assembled from the app's markup. Only it is rewritten, and only to
   remove references to EXCLUDED files. Everything else is copied byte for byte. */
const ENTRY_DOCUMENT = 'index.html';

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

/* `(?:^|\s)` keeps `.href=` inside an inline handler from reading as an attribute. */
function attribute(tag, name) {
  const pattern = new RegExp(
    '(?:^|\\s)' + name + '\\s*=\\s*(?:"([^"]*)"|\'([^\']*)\'|([^\\s>]+))', 'i');
  const match = pattern.exec(tag);
  if (!match) return null;
  if (match[1] !== undefined) return match[1];
  if (match[2] !== undefined) return match[2];
  return match[3];
}

/* A document is markup and script bodies. Only markup can reference a file the window loads;
   an inline template that writes `<a href="…">` is code, not a reference, so script bodies are
   carried through untouched and never scanned for tags. */
function splitDocument(html) {
  const parts = [];
  const opening = /<script\b[^>]*>/gi;
  let cursor = 0;
  let match;
  while ((match = opening.exec(html))) {
    if (match.index > cursor) parts.push({ markup: true, text: html.slice(cursor, match.index) });
    const close = html.indexOf('</script', match.index + match[0].length);
    if (close < 0) {
      parts.push({ markup: false, tag: match[0], text: html.slice(match.index) });
      return parts;
    }
    const end = html.indexOf('>', close) + 1 || html.length;
    parts.push({ markup: false, tag: match[0], text: html.slice(match.index, end) });
    cursor = end;
    opening.lastIndex = end;
  }
  if (cursor < html.length) parts.push({ markup: true, text: html.slice(cursor) });
  return parts;
}

/* What one reference in the entry document resolves to, given what is being assembled: a file
   that is there, a file excluded by policy, or — the case that must never pass silently — a
   file nobody accounted for. */
function classifyReference(value, assembled, excluded) {
  const raw = value == null ? '' : String(value).trim();
  // A scheme (http:, data:, mailto:), a protocol-relative URL, or a fragment in this page.
  if (!raw || /^(?:[a-z][a-z0-9+.-]*:|\/\/|#)/i.test(raw)) return { kind: 'skip' };
  const path = raw.split(/[?#]/)[0].replace(/^\.?\//, '');
  if (!path) return { kind: 'skip' };
  // The host serves `/party` from `party.html`; the player reads the same route as the file.
  const candidates = /\.[a-z0-9]+$/i.test(path) ? [path] : [path, path + '.html'];
  if (candidates.some(candidate => assembled.has(candidate))) return { kind: 'kept', path };
  const excludedPath = candidates.find(candidate => excluded.has(candidate));
  if (excludedPath) return { kind: 'drop', path: excludedPath, why: excluded.get(excludedPath) };
  return { kind: 'missing', path };
}

const REFERENCE_ATTRIBUTES = ['src', 'href', 'data'];
const TAG = /<([a-zA-Z][\w:-]*)\b([^<>]*)>/g;

/* Drop references to excluded files and report references to unexplained ones. The removed
   text is kept so the receipt can show exactly what changed and a test can prove that nothing
   else did. */
function rewriteDocument(rel, html, assembled, excluded) {
  const drops = [];
  const missing = [];
  const kept = [];

  const record = (element, name, value, verdict, removed) => drops.push({
    file: rel,
    element,
    attribute: name,
    value: String(value).trim(),
    path: verdict.path,
    why: verdict.why,
    removed,
  });

  for (const part of splitDocument(html)) {
    if (!part.markup) {
      const src = attribute(part.tag, 'src');
      if (src !== null) {
        const verdict = classifyReference(src, assembled, excluded);
        if (verdict.kind === 'drop') {
          record('script', 'src', src, verdict, part.text);
          continue;
        }
        if (verdict.kind === 'missing') {
          missing.push(`${rel} loads src="${String(src).trim()}" (${verdict.path}), which is ` +
            'neither assembled nor excluded');
        }
      }
      kept.push(part.text);
      continue;
    }
    kept.push(part.text.replace(TAG, (tag, element, attributes) => {
      for (const name of REFERENCE_ATTRIBUTES) {
        const value = attribute(attributes, name);
        if (value === null) continue;
        const verdict = classifyReference(value, assembled, excluded);
        if (verdict.kind === 'drop') {
          record(element.toLowerCase(), name, value, verdict, tag);
          return '';
        }
        if (verdict.kind === 'missing') {
          missing.push(`${rel} references ${name}="${String(value).trim()}" (${verdict.path}), ` +
            'which is neither assembled nor excluded — add it to the allow-list, or to EXCLUDED ' +
            'with the reason it is not carried');
        }
      }
      return tag;
    }));
  }
  return { html: kept.join(''), drops, missing };
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

async function assemble({ out = DEFAULT_OUT, include = [], forget = [] } = {}) {
  if (out === WEB || out.startsWith(WEB + sep)) {
    fail(`refusing to assemble into web/ (${out}): the app's sources are not build output`);
  }
  if (WEB.startsWith(out + sep)) {
    fail(`refusing to assemble into ${out}: that directory contains web/`);
  }

  const forgotten = new Set(forget);
  const unknown = [...forgotten].filter(rel => !WEB_ALLOW.includes(rel));
  if (unknown.length) {
    fail(`--forget-web names files that are not in the allow-list: ${unknown.join(', ')}`);
  }
  const entries = [
    ...WEB_ALLOW.filter(rel => !forgotten.has(rel)).map(rel => ({ rel, from: join(WEB, rel) })),
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

  /* The documents are read and rewritten before anything is written, so a reference nobody
     accounted for fails here rather than shipping a page that asks for a missing file. */
  const excluded = new Map(EXCLUDED);
  const assembled = new Set([...entries.map(entry => entry.rel), MANIFEST]);
  const documents = new Map();
  const unexplained = [];
  const dropped = [];
  for (const entry of entries) {
    if (!/\.html?$/i.test(entry.rel)) continue;
    const report = rewriteDocument(entry.rel, await readFile(entry.from, 'utf8'), assembled, excluded);
    if (entry.rel !== ENTRY_DOCUMENT && report.drops.length) {
      fail(`${entry.rel} references excluded files ` +
        `(${report.drops.map(drop => drop.value).join(', ')}); only ${ENTRY_DOCUMENT} is rewritten`);
    }
    documents.set(entry.rel, report);
    unexplained.push(...report.missing);
    dropped.push(...report.drops);
  }
  if (unexplained.length) {
    fail('the assembled tree would point at files it does not contain:\n  ' +
      unexplained.join('\n  '));
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
    const document = documents.get(entry.rel);
    if (entry.rel === ENTRY_DOCUMENT && document.drops.length) {
      await writeFile(dest, document.html);
    } else {
      await copyFile(entry.from, dest);
    }
    const [source, copy] = await Promise.all([readFile(entry.from), readFile(dest)]);
    /* The entry document is the one file allowed to differ, and only by the drops above. */
    if (sha(source) !== sha(copy) && !documents.get(entry.rel)?.drops.length) {
      fail(`copying ${entry.rel} changed it; the player must run the app's own bytes`);
    }
    files.push({
      path: entry.rel,
      sha256: sha(copy),
      bytes: copy.length,
      ...(entry.rel === ENTRY_DOCUMENT && document.drops.length
        ? { rewritten: document.drops.map(drop => drop.path) } : {}),
    });
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
    dropped,
    sources: { web: 'web/', shell: 'player/shell/' },
    files,
  }, null, 2) + '\n');
  return { out, files, dropped };
}

function parseArgs(argv) {
  const args = { out: DEFAULT_OUT, include: [], forget: [], list: false };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--out' || arg === '--include-web' || arg === '--forget-web') {
      const value = argv[++i];
      if (!value) fail(`${arg} needs a value`);
      if (arg === '--out') args.out = resolve(value);
      else if (arg === '--include-web') args.include.push(value);
      else args.forget.push(value);
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
  const { out, files, dropped } = await assemble(args);
  process.stdout.write(`assembled ${files.length} files into ${out}\n`);
  for (const drop of dropped) {
    process.stdout.write(`dropped ${drop.file} ${drop.element}[${drop.attribute}="${drop.value}"]` +
      ` — ${drop.why}\n`);
  }
}